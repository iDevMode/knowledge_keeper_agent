"""Outbound email.

One small protocol with three implementations:

* ``SendGridMailer`` — production. Calls the SendGrid v3 ``/mail/send`` API.
* ``LogMailer`` — used when no API key is configured. Logs that an email would
  have been sent (recipient count and subject only), never the body or the
  addresses: a handover pack is about a named, departing employee.
* ``OutboxMailer`` — test helper that records messages in memory.

Every recipient gets their own SendGrid personalization, so nobody on a
handover email sees who else received it.
"""

import base64
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Protocol

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"

# Deliberately conservative: anything this rejects is a typo the manager should
# fix, not an address worth gambling a Risk Summary on.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


class MailerError(Exception):
    """The email provider refused or could not be reached."""


@dataclass
class Attachment:
    filename: str
    content: bytes
    media_type: str


@dataclass
class EmailMessage:
    to: List[str]
    subject: str
    text: str
    html: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> None: ...


def extract_emails(values: Iterable[str]) -> List[str]:
    """Every distinct email address found in ``values``, in order, lower-cased.

    Accepts bare addresses and free text ("Sarah, HR <sarah@acme.co.uk>"), so it
    works on both ``recipient_emails`` and older ``recipients`` entries.
    """
    seen: List[str] = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        for match in _EMAIL_RE.findall(value):
            address = match.strip(".").lower()
            if address not in seen:
                seen.append(address)
    return seen


class SendGridMailer:
    def __init__(
        self,
        api_key: str,
        from_address: str,
        from_name: str = "KnowledgeKeeper",
        client: Optional[httpx.Client] = None,
        timeout: float = 20.0,
    ):
        if not api_key:
            raise ValueError("SendGrid API key is required")
        if not from_address:
            raise ValueError("A verified sender address is required")
        self._api_key = api_key
        self._from = {"email": from_address, "name": from_name}
        self._client = client or httpx.Client(timeout=timeout)

    def build_payload(self, message: EmailMessage) -> dict:
        content = [{"type": "text/plain", "value": message.text}]
        if message.html:
            content.append({"type": "text/html", "value": message.html})
        payload = {
            "personalizations": [{"to": [{"email": address}]} for address in message.to],
            "from": self._from,
            "subject": message.subject,
            "content": content,
            # Links in these emails are one-time sign-in and document links;
            # SendGrid's click tracking would rewrite them through its own domain.
            "tracking_settings": {
                "click_tracking": {"enable": False, "enable_text": False},
                "open_tracking": {"enable": False},
            },
        }
        if message.attachments:
            payload["attachments"] = [
                {
                    "content": base64.b64encode(a.content).decode("ascii"),
                    "filename": a.filename,
                    "type": a.media_type,
                    "disposition": "attachment",
                }
                for a in message.attachments
            ]
        return payload

    def send(self, message: EmailMessage) -> None:
        if not message.to:
            raise MailerError("No recipients")
        try:
            response = self._client.post(
                SENDGRID_SEND_URL,
                json=self.build_payload(message),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as e:
            raise MailerError(f"Could not reach SendGrid: {type(e).__name__}") from e

        if response.status_code >= 300:
            raise MailerError(_describe_sendgrid_failure(response))
        logger.info(
            "email sent provider=sendgrid recipients=%d attachments=%d",
            len(message.to), len(message.attachments),
        )


def _describe_sendgrid_failure(response: httpx.Response) -> str:
    """A message a person can act on, without echoing addresses back."""
    status = response.status_code
    detail = ""
    try:
        errors = response.json().get("errors") or []
        detail = "; ".join(e.get("message", "") for e in errors if e.get("message"))
    except ValueError:
        pass
    if status == 401:
        return "SendGrid rejected the API key (401) — check SENDGRID_API_KEY"
    if status == 403:
        return (
            "SendGrid refused to send (403) — the sender address is probably not "
            "verified, or the API key lacks Mail Send permission"
            + (f": {detail}" if detail else "")
        )
    if status == 413:
        return "SendGrid rejected the email as too large (413)"
    if status == 429:
        return "SendGrid rate limit reached (429) — try again shortly"
    return f"SendGrid returned {status}" + (f": {detail}" if detail else "")


class LogMailer:
    """Stands in for a real provider when none is configured."""

    def send(self, message: EmailMessage) -> None:
        logger.warning(
            "email NOT sent (SENDGRID_API_KEY unset) recipients=%d subject=%r attachments=%d",
            len(message.to), message.subject, len(message.attachments),
        )


class OutboxMailer:
    """Test helper: keeps every message instead of sending it."""

    def __init__(self):
        self.messages: List[EmailMessage] = []
        self.fail_with: Optional[Exception] = None
        self._lock = threading.Lock()

    def send(self, message: EmailMessage) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        with self._lock:
            self.messages.append(message)


_mailer: Optional[Mailer] = None
_guard = threading.Lock()


def get_mailer() -> Mailer:
    global _mailer
    with _guard:
        if _mailer is None:
            if settings.sendgrid_api_key and settings.email_from_address:
                _mailer = SendGridMailer(
                    settings.sendgrid_api_key,
                    settings.email_from_address,
                    settings.email_from_name,
                )
                logger.info("mailer: sendgrid")
            else:
                _mailer = LogMailer()
                logger.warning("mailer: log only — set SENDGRID_API_KEY to send email")
        return _mailer


def set_mailer(mailer: Optional[Mailer]) -> None:
    """Swap the mailer (tests). ``None`` re-reads settings on next use."""
    global _mailer
    with _guard:
        _mailer = mailer
