import logging

logger = logging.getLogger(__name__)


def on_stage1_complete(session_id: str) -> None:
    """Called when Stage 1 (business interview) finishes and profile is confirmed.

    Phase 2: email notification to manager, Slack alert, etc.
    """
    logger.info("session=%s webhook=stage1_complete", session_id)


def on_stage2_complete(session_id: str) -> None:
    """Called when Stage 2 (employee interview) session completes.

    Phase 2: auto-trigger document generation, notify manager, etc.
    """
    logger.info("session=%s webhook=stage2_complete", session_id)


def on_document_generated(session_id: str, document_id: str, file_path: str) -> None:
    """Called when Stage 3 document has been generated and exported.

    Phase 2: email delivery to recipients, upload to Notion/Confluence, etc.
    """
    logger.info(
        "session=%s webhook=document_generated document_id=%s path=%s",
        session_id, document_id, file_path,
    )
