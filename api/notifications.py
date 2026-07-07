"""Email delivery of the finished handover, plus the manager digest.

The handover document is delivered to the manager as a tokenised download link
(the link works from any browser, unlike the dashboard which needs the manager
token in local storage). The email also carries a short digest of the session.

Backends: "console" (dev — logs the composed message) or "smtp" (stdlib
smtplib). Delivery is always best-effort — a mail failure must never break
document generation.
"""

import logging
import smtplib
import time
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)


# ---- Senders ----

class ConsoleEmailSender:
    def send(self, to: str, subject: str, body: str) -> None:
        logger.info(
            "EMAIL (console backend)\n  to: %s\n  subject: %s\n---\n%s\n---",
            to, subject, body,
        )


class SmtpEmailSender:
    def __init__(self, host, port, user, password, use_tls, sender):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_tls = use_tls
        self._sender = sender

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(self._host, self._port) as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)


def create_email_sender():
    if settings.email_backend == "smtp":
        if not settings.smtp_host:
            raise RuntimeError("EMAIL_BACKEND=smtp requires SMTP_HOST")
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            sender=settings.email_from,
        )
    return ConsoleEmailSender()


# ---- Digest composition ----

def _public_base_url() -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    # Fall back to the first configured allowed origin.
    first = settings.allowed_origins.split(",")[0].strip()
    return first.rstrip("/")


def _severity_counts(risk_flags: List[Any]) -> Dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0}
    for flag in risk_flags:
        sev = getattr(flag.severity, "value", None) or str(flag.severity)
        if sev in counts:
            counts[sev] += 1
    return counts


def build_manager_digest(
    *,
    profile: Any,
    risk_flags: List[Any],
    answers: Dict[str, Any],
    block_order: List[str],
    duration_minutes: Optional[int],
    download_link: str,
) -> Tuple[str, str]:
    """Compose (subject, body) for the manager's handover email."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile
    role = data.get("job_title", "the role")
    company = data.get("company_name") or data.get("industry", "your organisation")

    sev = _severity_counts(risk_flags)
    blocks_covered = len(block_order)
    questions_answered = len(answers)

    subject = f"Handover ready: {role}"

    lines = [
        f"The knowledge handover for {role} at {company} is ready.",
        "",
        "Download it here (link is private to you — do not forward):",
        download_link,
        "",
        "Session summary",
        "---------------",
    ]
    if duration_minutes is not None:
        lines.append(f"- Interview length: ~{duration_minutes} min")
    lines.append(f"- Knowledge areas covered: {blocks_covered}")
    lines.append(f"- Questions answered: {questions_answered}")
    lines.append(
        f"- Risk flags: {len(risk_flags)} "
        f"({sev['critical']} critical, {sev['high']} high, {sev['medium']} medium)"
    )
    if sev["critical"]:
        lines.append("")
        lines.append(
            f"⚠ {sev['critical']} critical risk flag(s) were identified — see the "
            "Risk Summary at the top of the document."
        )
    lines.append("")
    lines.append("— KnowledgeKeeper")

    return subject, "\n".join(lines)


# ---- Orchestration ----

def deliver_handover(*, store, sender, stage2_session_id: str, document_id: str, gen_request) -> bool:
    """Best-effort: email the completed handover to the manager's delivery
    address, if one has been set. Returns True if an email was sent."""
    try:
        stage1_id = store.get_linked_session(stage2_session_id)
        if not stage1_id:
            return False

        stage1_session = store.get_session(stage1_id) or {}
        manager_email = stage1_session.get("manager_email")
        if not manager_email:
            logger.info(
                "session=%s document=%s delivery skipped — no manager_email set yet",
                stage2_session_id, document_id,
            )
            return False

        token = store.create_manager_token(stage1_id)
        download_link = f"{_public_base_url()}/api/documents/{document_id}?token={token}"

        stage2_session = store.get_session(stage2_session_id) or {}
        created = stage2_session.get("_created_at")
        duration_minutes = int((time.time() - created) / 60) if created else None

        subject, body = build_manager_digest(
            profile=gen_request.profile,
            risk_flags=gen_request.risk_flags,
            answers=gen_request.answers,
            block_order=gen_request.block_order,
            duration_minutes=duration_minutes,
            download_link=download_link,
        )
        sender.send(manager_email, subject, body)
        logger.info("session=%s document=%s handover emailed to manager", stage2_session_id, document_id)
        return True
    except Exception as e:
        logger.error("session=%s document=%s handover delivery failed: %s", stage2_session_id, document_id, e)
        return False
