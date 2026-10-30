"""Data retention and deletion. Policy: docs/data-retention.md.

What is kept, and for how long:

* Interview data — the two sessions, the Role Intelligence Profile, the link
  between them and the LangGraph checkpoints that hold every word of both
  interviews — lives as long as the interview sessions (SESSION_TTL_HOURS).
  The manager's Stage 1 session is extended to match the employee's Stage 2,
  so the profile is still there when the employee finishes.
* The finished handover pack is kept DOCUMENT_RETENTION_DAYS from generation.
* The handover record on the dashboard is kept as long as its pack, or, if it
  never produced one, EXPIRED_HANDOVER_VISIBLE_DAYS past its sessions.

Before this module the checkpoints were never deleted at all: session rows
expired after 72 hours while the full transcripts stayed in the database
indefinitely.
"""

import logging
from contextlib import ExitStack
from typing import Any, Callable, ContextManager, Dict, Iterable, List, Optional

from api.accounts import get_account_store
from api.document_store import get_document_store
from api.session_manager import get_session_store
from config.settings import settings

logger = logging.getLogger(__name__)

# Upper bound on orphaned transcripts cleaned per sweep, so a large backlog
# (from before transcripts were deleted) is worked through without one sweep
# holding a worker for minutes.
ORPHAN_BATCH = 500


def delete_transcripts(checkpointer: Any, session_ids: Iterable[str]) -> int:
    """Delete the LangGraph checkpoints (the interview transcript) of each session."""
    deleted = 0
    for session_id in session_ids:
        try:
            checkpointer.delete_thread(session_id)
            deleted += 1
        except Exception as e:  # one bad thread must not stop the rest
            logger.error("session=%s transcript deletion failed: %s", session_id, e)
    return deleted


def purge_orphaned_transcripts(checkpointer: Any) -> int:
    """Delete transcripts whose session no longer exists (Postgres only).

    Catches up on every transcript left behind before deletion existed, and
    anything a crash interrupted since.
    """
    if not settings.database_url:
        return 0
    from api.postgres_store import get_shared_pool, orphaned_transcript_ids

    orphans = orphaned_transcript_ids(get_shared_pool(), limit=ORPHAN_BATCH)
    return delete_transcripts(checkpointer, orphans)


def purge_handover(
    stage1_session_id: str,
    stage2_session_id: Optional[str],
    *,
    checkpointer: Any,
    lock: Callable[[str], ContextManager],
    forget: Optional[Callable[[str], None]] = None,
) -> None:
    """Delete a handover and everything under it, now.

    Sessions, profile, links, both transcripts, every generated pack, any
    generation error, and the dashboard record. Copies already emailed to
    recipients are outside our reach and are not affected.

    The session locks are held while deleting, so an interview turn that is
    in flight finishes first rather than writing a fresh checkpoint into a
    handover that has just been deleted.
    """
    sessions = get_session_store()
    documents = get_document_store()

    if not stage2_session_id:
        stage2_session_id = sessions.get_linked_session(stage1_session_id)
    session_ids = sorted({s for s in (stage1_session_id, stage2_session_id) if s})

    with ExitStack() as stack:
        # Sorted, so two purges of the same handover cannot deadlock.
        for session_id in session_ids:
            stack.enter_context(lock(session_id))
        for session_id in session_ids:
            documents.delete_for_session(session_id)
            delete_transcripts(checkpointer, [session_id])
            sessions.delete_session(session_id)
        get_account_store().delete_handover(stage1_session_id)

    if forget is not None:
        for session_id in session_ids:
            forget(session_id)
    logger.info("handover=%s purged sessions=%d", stage1_session_id, len(session_ids))


def run_sweep(
    *,
    checkpointer: Any,
    lock: Callable[[str], ContextManager],
    forget: Optional[Callable[[str], None]] = None,
) -> Dict[str, int]:
    """Apply the retention policy once. Safe to run on every worker at once:
    every step is idempotent."""
    expired_sessions: List[str] = get_session_store().sweep_expired_ids()
    transcripts = delete_transcripts(checkpointer, expired_sessions)
    if forget is not None:
        for session_id in expired_sessions:
            forget(session_id)

    documents = get_document_store().sweep_expired()

    accounts = get_account_store()
    handovers = 0
    for row in accounts.expired_handovers():
        try:
            purge_handover(
                row["stage1_session_id"],
                row.get("stage2_session_id"),
                checkpointer=checkpointer,
                lock=lock,
                forget=forget,
            )
            handovers += 1
        except Exception as e:
            logger.error("handover=%s retention purge failed: %s", row["stage1_session_id"], e)

    accounts.sweep_expired()  # used sign-in links
    transcripts += purge_orphaned_transcripts(checkpointer)

    return {
        "sessions": len(expired_sessions),
        "transcripts": transcripts,
        "documents": documents,
        "handovers": handovers,
    }
