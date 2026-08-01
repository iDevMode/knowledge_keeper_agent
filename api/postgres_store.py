"""Postgres-backed session store (review finding H3).

All session state was held in process dictionaries. That is correct for a single
worker but does not survive a restart, so every Railway redeploy destroyed every
in-flight interview — a manager or employee mid-conversation simply got 404 on
their next message, with no way back.

This implements the same SessionStore protocol as InMemorySessionStore, so the
two are interchangeable and the rest of the app does not know which is in use.
Selection happens in session_manager.get_session_store().

Expiry is a column rather than a Redis-style TTL: reads filter on expires_at so
an expired session is invisible immediately, and sweep_expired() reclaims the
rows. That matches the in-memory semantics exactly, which is what lets one
contract test suite cover both.
"""

import logging
import time
from typing import Any, Dict, Optional

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from config.settings import settings
from models.role_intelligence_profile import RoleIntelligenceProfile

logger = logging.getLogger(__name__)

# Tables are prefixed so they can share a database with anything else the
# project grows into, and with the langgraph checkpointer's own tables.
SCHEMA = """
CREATE TABLE IF NOT EXISTS kk_sessions (
    session_id  TEXT PRIMARY KEY,
    stage       INTEGER NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  DOUBLE PRECISION NOT NULL,
    expires_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS kk_sessions_expires_at ON kk_sessions (expires_at);

CREATE TABLE IF NOT EXISTS kk_session_links (
    session_id  TEXT PRIMARY KEY,
    linked_id   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kk_profiles (
    session_id  TEXT PRIMARY KEY,
    profile     JSONB NOT NULL
);
"""


class PostgresSessionStore:
    """SessionStore backed by Postgres. See the protocol in session_manager."""

    def __init__(self, conninfo: Optional[str] = None, ttl_hours: float | None = None,
                 pool: Optional[ConnectionPool] = None):
        self._ttl_seconds = (ttl_hours or settings.session_ttl_hours) * 3600
        if pool is not None:
            self._pool = pool
        else:
            self._pool = ConnectionPool(
                conninfo or settings.database_url,
                min_size=1,
                max_size=10,
                # Fail fast on a bad DATABASE_URL rather than blocking every
                # request until some far-off timeout.
                timeout=10,
                open=True,
            )
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)

    # -- sessions --------------------------------------------------------

    def create_session(self, stage: int, metadata: Dict[str, Any] | None = None) -> str:
        import uuid

        session_id = str(uuid.uuid4())
        now = time.time()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO kk_sessions (session_id, stage, data, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session_id, stage, Jsonb(metadata or {}), now, now + self._ttl_seconds),
            )
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT stage, data, created_at FROM kk_sessions "
                "WHERE session_id = %s AND expires_at > %s",
                (session_id, time.time()),
            ).fetchone()

        if row is None:
            return None

        stage, data, created_at = row
        # Same shape the in-memory store returns, so callers cannot tell them
        # apart.
        return {
            "_created_at": created_at,
            "stage": stage,
            "session_id": session_id,
            **(data or {}),
        }

    def update_session(self, session_id: str, data: Dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE kk_sessions SET data = data || %s "
                "WHERE session_id = %s AND expires_at > %s",
                (Jsonb(data), session_id, time.time()),
            )

    # -- links -----------------------------------------------------------

    def link_sessions(self, stage1_id: str, stage2_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO kk_session_links (session_id, linked_id) VALUES (%s, %s), (%s, %s) "
                "ON CONFLICT (session_id) DO UPDATE SET linked_id = EXCLUDED.linked_id",
                (stage1_id, stage2_id, stage2_id, stage1_id),
            )

    def get_linked_session(self, session_id: str) -> Optional[str]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT linked_id FROM kk_session_links WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return row[0] if row else None

    # -- profiles --------------------------------------------------------

    def store_profile(self, session_id: str, profile: RoleIntelligenceProfile) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO kk_profiles (session_id, profile) VALUES (%s, %s) "
                "ON CONFLICT (session_id) DO UPDATE SET profile = EXCLUDED.profile",
                (session_id, Jsonb(profile.model_dump(mode="json"))),
            )

    def get_profile(self, session_id: str) -> Optional[RoleIntelligenceProfile]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT profile FROM kk_profiles WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return RoleIntelligenceProfile.model_validate(row[0])

    # -- expiry ----------------------------------------------------------

    def sweep_expired(self) -> int:
        """Drop expired sessions with their links and profiles.

        Mirrors InMemorySessionStore.sweep_expired, including removing both
        directions of a link.
        """
        now = time.time()
        with self._pool.connection() as conn:
            expired = [
                r[0]
                for r in conn.execute(
                    "SELECT session_id FROM kk_sessions WHERE expires_at <= %s", (now,)
                ).fetchall()
            ]
            if not expired:
                return 0

            linked = [
                r[0]
                for r in conn.execute(
                    "SELECT linked_id FROM kk_session_links WHERE session_id = ANY(%s)",
                    (expired,),
                ).fetchall()
            ]

            conn.execute("DELETE FROM kk_sessions WHERE session_id = ANY(%s)", (expired,))
            conn.execute("DELETE FROM kk_profiles WHERE session_id = ANY(%s)", (expired,))
            conn.execute(
                "DELETE FROM kk_session_links WHERE session_id = ANY(%s)",
                (expired + linked,),
            )

        return len(expired)

    def close(self) -> None:
        self._pool.close()
