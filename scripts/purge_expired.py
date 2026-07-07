"""Retention purge: delete durable profiles past the retention window.

Run on a schedule (e.g. daily cron) in a persistent deployment:

    python -m scripts.purge_expired

Redis session/token/checkpoint state expires via its own TTLs; this handles the
durable Postgres profile records. Right-to-erasure deletion is immediate and
independent of this backstop.
"""

import logging

from api.session_manager import get_session_store
from config.settings import settings

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("purge_expired")


def main() -> None:
    store = get_session_store()
    deleted = store.purge_expired_profiles(settings.data_retention_days)
    logger.info("retention purge complete: %s profile(s) older than %s days deleted",
                deleted, settings.data_retention_days)


if __name__ == "__main__":
    main()
