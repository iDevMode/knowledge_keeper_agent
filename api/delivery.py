"""Emailing the finished handover pack to the recipients the manager named.

Delivery runs after the pack is stored and never fails generation: the document
exists and the manager can still download it. The outcome — sent, skipped or
failed with a reason — is recorded on the document so the manager can see it.
"""

import html
import logging
from typing import Any, List, Optional

from api.document_store import get_document_store
from api.mailer import Attachment, EmailMessage, MailerError, extract_emails, get_mailer

logger = logging.getLogger(__name__)

SENT = "sent"
SKIPPED = "skipped"
FAILED = "failed"

# SendGrid's limit is 30MB for the whole message, attachments base64-encoded.
# A pack is tens of kilobytes; anything near this is a bug, not a pack.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def recipients_for(profile: Any) -> List[str]:
    """Addresses from the confirmed profile.

    ``recipient_emails`` is the source of truth. ``recipients`` is also scanned
    because profiles created before email delivery may hold addresses there.
    """
    emails = list(getattr(profile, "recipient_emails", None) or [])
    names = list(getattr(profile, "recipients", None) or [])
    return extract_emails(emails + names)


def build_pack_email(
    profile: Any,
    recipients: List[str],
    filename: str,
    media_type: str,
    content: bytes,
) -> EmailMessage:
    job_title = getattr(profile, "job_title", None) or "the departing role"
    company = getattr(profile, "company_name", None)
    role_line = f"{job_title} at {company}" if company else job_title

    subject = f"Handover pack: {job_title}" + (f" — {company}" if company else "")

    paragraphs = [
        "Hello,",
        f"The KnowledgeKeeper handover pack for the {role_line} role is ready and attached.",
        "It was built from interviews with the hiring manager and the departing "
        "employee, and covers the risks that need action, the knowledge, "
        "relationships, systems and in-flight work the new starter needs, and a "
        "four-week onboarding plan.",
        "Please treat it as confidential. The risk summary concerns a named "
        "employee's departure, so share it only with people who need it.",
        "You're receiving this because you were listed as a recipient when this "
        "handover was set up.",
        "KnowledgeKeeper by Nukode",
    ]
    text = "\n\n".join(paragraphs)
    html_body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)

    return EmailMessage(
        to=recipients,
        subject=subject,
        text=text,
        html=f"<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#222\">{html_body}</div>",
        attachments=[Attachment(filename=_friendly_filename(profile, filename), content=content, media_type=media_type)],
    )


def _friendly_filename(profile: Any, stored_filename: str) -> str:
    """'Handover - Senior Operations Coordinator.docx' rather than a UUID."""
    extension = stored_filename.rsplit(".", 1)[-1] if "." in stored_filename else "docx"
    title = getattr(profile, "job_title", None) or "Handover pack"
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip() or "Handover pack"
    return f"Handover - {safe[:80]}.{extension}"


def deliver_handover_pack(
    session_id: str,
    document_id: str,
    profile: Any,
    filename: str,
    media_type: str,
    content: bytes,
) -> str:
    """Email the pack and record the outcome. Returns the delivery status."""
    store = get_document_store()
    recipients = recipients_for(profile)

    if not recipients:
        logger.warning(
            "session=%s document=%s delivery=skipped reason=no_recipient_emails",
            session_id, document_id,
        )
        store.record_delivery(
            document_id, SKIPPED, [],
            "No recipient email addresses were given in the business interview",
        )
        return SKIPPED

    if len(content) > MAX_ATTACHMENT_BYTES:
        error = "The handover pack is too large to email — download it instead"
        logger.error(
            "session=%s document=%s delivery=failed reason=too_large bytes=%d",
            session_id, document_id, len(content),
        )
        store.record_delivery(document_id, FAILED, recipients, error)
        return FAILED

    message = build_pack_email(profile, recipients, filename, media_type, content)
    error: Optional[str] = None
    try:
        get_mailer().send(message)
    except MailerError as e:
        error = str(e)
    except Exception as e:  # never let email break generation
        error = f"Email could not be sent ({type(e).__name__})"
        logger.exception("session=%s document=%s delivery crashed", session_id, document_id)

    if error:
        logger.error(
            "session=%s document=%s delivery=failed recipients=%d error=%s",
            session_id, document_id, len(recipients), error,
        )
        store.record_delivery(document_id, FAILED, recipients, error)
        return FAILED

    logger.info(
        "session=%s document=%s delivery=sent recipients=%d",
        session_id, document_id, len(recipients),
    )
    store.record_delivery(document_id, SENT, recipients, None)
    return SENT
