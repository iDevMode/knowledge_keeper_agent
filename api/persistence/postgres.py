"""Postgres-backed durable storage for Role Intelligence Profiles.

`psycopg` is imported lazily so the dependency is only required when
STORAGE_BACKEND=persistent. Connections are opened per operation — profile
reads/writes are infrequent (once per session), so a pool is not yet warranted;
revisit if volume grows.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS role_profiles (
    session_id TEXT PRIMARY KEY,
    profile    JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_UPSERT = """
INSERT INTO role_profiles (session_id, profile)
VALUES (%s, %s)
ON CONFLICT (session_id)
DO UPDATE SET profile = EXCLUDED.profile, updated_at = now();
"""

_SELECT = "SELECT profile FROM role_profiles WHERE session_id = %s;"


class PostgresProfileRepo:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _connect(self):
        import psycopg  # noqa: PLC0415 — optional dependency

        return psycopg.connect(self._dsn)

    def ensure_schema(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_CREATE_TABLE)
            conn.commit()

    def upsert(self, session_id: str, profile: Dict[str, Any]) -> None:
        from psycopg.types.json import Json  # noqa: PLC0415

        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_UPSERT, (session_id, Json(profile)))
            conn.commit()

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(_SELECT, (session_id,))
            row = cur.fetchone()
            return row[0] if row else None
