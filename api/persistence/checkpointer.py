"""LangGraph checkpointer factory.

The checkpointer holds every session's conversation state, keyed by thread_id
(= session_id). A single shared checkpointer is used across all sessions so that
any worker or a freshly restarted process can resume any session by thread_id.

- memory backend     → MemorySaver (shared within the process; lost on restart)
- persistent backend → Postgres saver over a connection pool (survives restarts
  and is shared across workers)

Postgres imports are lazy so the dependency is only required when
STORAGE_BACKEND=persistent.
"""

import logging

from config.settings import settings

logger = logging.getLogger(__name__)


def create_checkpointer():
    if settings.storage_backend == "persistent":
        return _create_postgres_checkpointer(settings.database_url)

    from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

    return MemorySaver()


def _create_postgres_checkpointer(dsn: str):
    from langgraph.checkpoint.postgres import PostgresSaver  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415
    from psycopg_pool import ConnectionPool  # noqa: PLC0415

    pool = ConnectionPool(
        conninfo=dsn,
        max_size=10,
        # PostgresSaver requires autocommit + dict rows; prepare_threshold=0
        # avoids prepared-statement issues behind poolers like PgBouncer.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    checkpointer = PostgresSaver(pool)
    checkpointer.setup()  # idempotent — creates checkpoint tables if absent
    logger.info("Initialised Postgres checkpointer (pool max_size=10)")
    return checkpointer
