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

import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from api.document_store import COMPLETE, FAILED, GENERATING
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

-- Handover packs are stored as bytes, not paths. They contain the Risk Summary
-- written about a departing employee, and keeping them out of the container
-- filesystem removes a whole class of local exposure as well as surviving a
-- redeploy.
CREATE TABLE IF NOT EXISTS kk_documents (
    document_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    filename    TEXT,
    media_type  TEXT,
    content     BYTEA,
    created_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS kk_documents_session_id ON kk_documents (session_id);
CREATE INDEX IF NOT EXISTS kk_documents_created_at ON kk_documents (created_at);
-- Email delivery of the pack, added after the table shipped. Checked first
-- because ALTER TABLE takes an exclusive lock on kk_documents even when the
-- columns already exist, and this runs on every worker start.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'kk_documents' AND column_name = 'delivered_to'
    ) THEN
        ALTER TABLE kk_documents
            ADD COLUMN IF NOT EXISTS delivery_status TEXT,
            ADD COLUMN IF NOT EXISTS delivery_error  TEXT,
            ADD COLUMN IF NOT EXISTS delivered_to    TEXT[];
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS kk_generation_errors (
    session_id  TEXT PRIMARY KEY,
    message     TEXT NOT NULL
);
"""


# Arbitrary, fixed advisory-lock key that serialises schema creation.
SCHEMA_LOCK_KEY = 0x6B6B5F736368656D  # "kk_schem"


def apply_schema(conn) -> None:
    """Create or upgrade the tables, one process at a time.

    Every worker runs SCHEMA when its stores are first built, which is on its
    first request. Under load several workers do that at the same moment, and
    concurrent DDL against the same tables deadlocked in Postgres ("deadlock
    detected ... AccessExclusiveLock"), failing one of those first requests
    with a 500. A transaction-scoped advisory lock makes them queue instead;
    after the first, each run is a cheap no-op.
    """
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        conn.execute(SCHEMA)


# A separate key from SCHEMA_LOCK_KEY: see setup_checkpointer.
CHECKPOINTER_LOCK_KEY = 0x6B6B5F636B707400  # "kk_ckpt"


def setup_checkpointer(saver: Any, pool: ConnectionPool, timeout: float = 120.0) -> None:
    """Run LangGraph's PostgresSaver.setup() one process at a time.

    setup() applies the checkpointer's own table migrations. On a fresh
    database, several workers running it together collide in the catalog
    (DuplicateObject / UniqueViolation), and the worker that loses fails its
    first request with a 500 — the same race as apply_schema, in tables we do
    not own.

    The lock is taken with pg_try_advisory_lock in a polling loop, not the
    blocking form. Those migrations include CREATE INDEX CONCURRENTLY, which
    waits for every transaction in progress — including a worker blocked inside
    pg_advisory_lock, which is itself waiting for the holder to finish. That
    deadlock is invisible to Postgres and simply hangs. Polling means a waiter
    holds no open statement between attempts. A key of its own keeps it clear
    of apply_schema, whose lock is held inside a transaction.
    """
    deadline = time.time() + timeout
    with pool.connection() as conn:
        while True:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (CHECKPOINTER_LOCK_KEY,)
            ).fetchone()
            locked = row["locked"] if isinstance(row, dict) else row[0]
            if locked:
                break
            if time.time() > deadline:
                raise TimeoutError("timed out waiting for another worker's checkpointer setup")
            time.sleep(0.1)
        try:
            saver.setup()
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (CHECKPOINTER_LOCK_KEY,))


_shared_pool: Optional[ConnectionPool] = None
_pool_guard = threading.Lock()


def get_shared_pool() -> ConnectionPool:
    """One pool per worker for short store queries.

    The session store and document store used to open a pool each. With the
    checkpointer and the advisory locks that made four pools per worker, so two
    workers could demand 80 connections against a cap that is commonly 100.
    They issue short queries and share happily.
    """
    global _shared_pool
    if _shared_pool is None:
        with _pool_guard:
            if _shared_pool is None:
                _shared_pool = ConnectionPool(
                    settings.database_url,
                    min_size=1,
                    max_size=settings.db_pool_size,
                    # Fail fast on a bad DATABASE_URL rather than blocking every
                    # request until some far-off timeout.
                    timeout=10,
                    open=True,
                )
    return _shared_pool


def reset_shared_pool() -> None:
    """Close and drop the shared pool. Used by tests simulating a restart."""
    global _shared_pool
    if _shared_pool is not None:
        _shared_pool.close()
        _shared_pool = None


def _pool_for(conninfo: Optional[str], pool: Optional[ConnectionPool],
              max_size: int) -> ConnectionPool:
    """A caller-supplied pool, a dedicated one for an explicit DSN, or the shared one."""
    if pool is not None:
        return pool
    if conninfo:
        return ConnectionPool(conninfo, min_size=1, max_size=max_size, timeout=10, open=True)
    return get_shared_pool()


class PostgresSessionStore:
    """SessionStore backed by Postgres. See the protocol in session_manager."""

    def __init__(self, conninfo: Optional[str] = None, ttl_hours: float | None = None,
                 pool: Optional[ConnectionPool] = None):
        self._ttl_seconds = (ttl_hours or settings.session_ttl_hours) * 3600
        self._owns_pool = pool is None and bool(conninfo)
        self._pool = _pool_for(conninfo, pool, settings.db_pool_size)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            apply_schema(conn)

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
        # Only close a pool we created. The shared one outlives any single
        # store and closing it here would break the others.
        if self._owns_pool:
            self._pool.close()


def advisory_key(session_id: str) -> int:
    """A stable signed 64-bit key for pg_advisory_lock.

    Derived in Python rather than with Postgres hashtext() so the value does not
    depend on the server's hashing, which is not a documented stable interface.
    """
    digest = hashlib.blake2b(session_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


class PostgresSessionLocks:
    """Cross-process serialisation of a session's graph run.

    In-process threading locks only serialise within one worker. Once more than
    one uvicorn worker exists, two requests for the same session land in
    different processes and can drive the same LangGraph thread concurrently,
    interleaving checkpoint writes. An advisory lock is held on the connection
    for the duration of the run, so the second worker waits.
    """

    def __init__(self, conninfo: Optional[str] = None,
                 pool: Optional[ConnectionPool] = None):
        # A dedicated pool, NOT the shared one: a lock connection is held for
        # the whole graph run, so borrowing from the short-query pool would
        # starve it whenever a few interviews were mid-turn.
        self._owns_pool = pool is None
        self._pool = pool or ConnectionPool(
            conninfo or settings.database_url,
            min_size=1, max_size=settings.db_lock_pool_size, timeout=10, open=True,
        )

    @contextmanager
    def lock(self, session_id: str) -> Iterator[None]:
        key = advisory_key(session_id)
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (key,))
            try:
                yield
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))

    def close(self) -> None:
        # Only close a pool we created. The shared one outlives any single
        # store and closing it here would break the others.
        if self._owns_pool:
            self._pool.close()


class PostgresDocumentStore:
    """DocumentStore backed by Postgres. See the protocol in document_store."""

    def __init__(self, conninfo: Optional[str] = None, ttl_hours: float | None = None,
                 pool: Optional[ConnectionPool] = None):
        self._ttl_seconds = (ttl_hours or settings.session_ttl_hours) * 3600
        self._owns_pool = pool is None and bool(conninfo)
        self._pool = _pool_for(conninfo, pool, settings.db_pool_size)
        with self._pool.connection() as conn:
            apply_schema(conn)

    def start_job(self, document_id: str, session_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO kk_documents "
                "(document_id, session_id, status, created_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (document_id) DO UPDATE SET status = EXCLUDED.status",
                (document_id, session_id, GENERATING, time.time()),
            )
            conn.execute(
                "DELETE FROM kk_generation_errors WHERE session_id = %s", (session_id,)
            )

    def complete_job(self, document_id: str, filename: str, media_type: str,
                     content: bytes) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE kk_documents SET status = %s, filename = %s, media_type = %s, "
                "content = %s, error = NULL WHERE document_id = %s",
                (COMPLETE, filename, media_type, content, document_id),
            )

    def fail_job(self, document_id: str, error: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE kk_documents SET status = %s, error = %s WHERE document_id = %s",
                (FAILED, error, document_id),
            )

    def record_delivery(self, document_id: str, status: str, recipients: List[str],
                        error: Optional[str]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE kk_documents SET delivery_status = %s, delivery_error = %s, "
                "delivered_to = %s WHERE document_id = %s",
                (status, error, list(recipients), document_id),
            )

    def get_job(self, document_id: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT status, error, delivery_status, delivery_error, delivered_to "
                "FROM kk_documents WHERE document_id = %s",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        status, error, delivery_status, delivery_error, delivered_to = row
        return {
            "status": status,
            "error": error,
            "download_url": f"/api/documents/{document_id}" if status == COMPLETE else None,
            "delivery": (
                {
                    "status": delivery_status,
                    "recipients": list(delivered_to or []),
                    "error": delivery_error,
                }
                if delivery_status
                else None
            ),
        }

    def get_content(self, document_id: str) -> Optional[tuple[str, str, bytes]]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT filename, media_type, content FROM kk_documents "
                "WHERE document_id = %s AND content IS NOT NULL",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        filename, media_type, content = row
        return filename, media_type, bytes(content)

    def owner_of(self, document_id: str) -> Optional[str]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT session_id FROM kk_documents WHERE document_id = %s",
                (document_id,),
            ).fetchone()
        return row[0] if row else None

    def document_for_session(self, session_id: str) -> Optional[str]:
        """The most recent document for a session — regeneration supersedes."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT document_id FROM kk_documents WHERE session_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return row[0] if row else None

    def set_generation_error(self, session_id: str, message: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO kk_generation_errors (session_id, message) VALUES (%s, %s) "
                "ON CONFLICT (session_id) DO UPDATE SET message = EXCLUDED.message",
                (session_id, message),
            )

    def clear_generation_error(self, session_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM kk_generation_errors WHERE session_id = %s", (session_id,)
            )

    def get_generation_error(self, session_id: str) -> Optional[str]:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT message FROM kk_generation_errors WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        return row[0] if row else None

    def sweep_expired(self) -> int:
        cutoff = time.time() - self._ttl_seconds
        with self._pool.connection() as conn:
            orphaned = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT session_id FROM kk_documents WHERE created_at < %s",
                    (cutoff,),
                ).fetchall()
            ]
            removed = conn.execute(
                "DELETE FROM kk_documents WHERE created_at < %s", (cutoff,)
            ).rowcount
            if orphaned:
                # Only for sessions left with no documents at all.
                conn.execute(
                    "DELETE FROM kk_generation_errors WHERE session_id = ANY(%s) "
                    "AND session_id NOT IN (SELECT session_id FROM kk_documents)",
                    (orphaned,),
                )
        return removed

    def close(self) -> None:
        # Only close a pool we created. The shared one outlives any single
        # store and closing it here would break the others.
        if self._owns_pool:
            self._pool.close()
