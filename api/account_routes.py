"""Manager sign-in (magic link) and the handover dashboard.

    POST /api/auth/login                     email a one-time sign-in link
    POST /api/auth/verify                    exchange the link's token for an account token
    GET  /api/account/me                     who is signed in
    GET  /api/handovers                      the manager's handovers, with status
    POST /api/handovers/{stage1_id}/access   a manager token for one of them
    GET  /api/handovers/{stage1_id}/document download the pack, even once the
                                             interview data has been deleted
    (DELETE /api/handovers/{stage1_id} lives in routes.py: it needs the
     checkpointer and session locks.)

The account token never touches a session directly. To work on a handover the
dashboard exchanges it for the same stage-scoped manager token the rest of the
API already understands, so none of the existing authorisation changes.
"""

import html
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from api.accounts import get_account_store
from api.auth import ACCOUNT, MANAGER, InvalidToken, TokenClaims, extract_bearer, issue_token, verify_token
from api.document_store import COMPLETE, FAILED, GENERATING, get_document_store
from api.mailer import EmailMessage, MailerError, extract_emails, get_mailer
from api.session_manager import get_session_store
from config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

LOGIN_ACCEPTED = (
    "If that address can sign in, a link is on its way. It works once and "
    "expires in {minutes} minutes."
)


# ---- Auth dependency ----

def require_account(authorization: Optional[str] = Header(default=None)) -> TokenClaims:
    try:
        claims = verify_token(extract_bearer(authorization))
    except InvalidToken as e:
        raise HTTPException(status_code=401, detail="Please sign in again") from e
    if not claims.is_account:
        raise HTTPException(status_code=401, detail="Please sign in again")
    return claims


# ---- Models ----

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class LoginResponse(BaseModel):
    message: str


class VerifyRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)


class VerifyResponse(BaseModel):
    account_token: str
    email: str
    expires_at: float


class MeResponse(BaseModel):
    email: str


class HandoverDelivery(BaseModel):
    status: str
    recipients: List[str] = []
    error: Optional[str] = None


class HandoverSummary(BaseModel):
    stage1_session_id: str
    title: Optional[str] = None
    company: Optional[str] = None
    created_at: float
    # setup | awaiting_employee | interview_complete | generating | ready |
    # archived | generation_failed | expired
    status: str
    status_label: str
    stage2_session_id: Optional[str] = None
    document_id: Optional[str] = None
    document_error: Optional[str] = None
    delivery: Optional[HandoverDelivery] = None
    # When this handover (and its pack) will be deleted automatically.
    kept_until: Optional[float] = None
    # False once the interview sessions and transcripts have been deleted:
    # the pack can still be downloaded, but not reopened or regenerated.
    interview_available: bool = True


class HandoverAccessResponse(BaseModel):
    session_id: str
    token: str


# ---- Sign-in ----

def _sign_in_email(address: str, link: str) -> EmailMessage:
    minutes = settings.login_link_ttl_minutes
    text = (
        "Hello,\n\n"
        f"Use this link to sign in to KnowledgeKeeper. It works once and expires in {minutes} minutes:\n\n"
        f"{link}\n\n"
        "If you didn't ask to sign in, you can ignore this email.\n\n"
        "KnowledgeKeeper by Nukode"
    )
    safe = html.escape(link, quote=True)
    body = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5;color:#222">'
        "<p>Hello,</p>"
        f"<p>Use the button below to sign in to KnowledgeKeeper. It works once and expires in {minutes} minutes.</p>"
        f'<p><a href="{safe}" style="display:inline-block;padding:10px 18px;background:#1b6b61;'
        'color:#fff;border-radius:8px;text-decoration:none">Sign in to KnowledgeKeeper</a></p>'
        f'<p style="font-size:12px;color:#666">Or paste this link into your browser:<br>{html.escape(link)}</p>'
        "<p>If you didn't ask to sign in, you can ignore this email.</p>"
        "<p>KnowledgeKeeper by Nukode</p></div>"
    )
    return EmailMessage(to=[address], subject="Your KnowledgeKeeper sign-in link", text=text, html=body)


@router.post("/api/auth/login", response_model=LoginResponse, status_code=202)
def request_login(request: LoginRequest):
    """Email a one-time sign-in link.

    The response is identical whether or not anything was sent — rate-limited,
    first-time or returning — so the form cannot be used to probe addresses.
    """
    message = LoginResponse(message=LOGIN_ACCEPTED.format(minutes=settings.login_link_ttl_minutes))

    found = extract_emails([request.email])
    if len(found) != 1 or found[0] != request.email.strip().lower():
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    address = found[0]

    store = get_account_store()
    store.sweep_expired()

    window_start = time.time() - settings.login_request_window_minutes * 60
    if store.count_login_tokens_since(address, window_start) >= settings.login_requests_per_window:
        logger.warning("login request rate-limited")
        return message

    raw = store.create_login_token(address, settings.login_link_ttl_minutes * 60)
    # The token rides in the fragment, which browsers never send to a server,
    # so it stays out of access logs and Referer headers.
    link = f"{settings.public_base_url.rstrip('/')}/auth/verify#token={raw}"

    if settings.environment == "development" and not settings.sendgrid_api_key:
        # Local development has no mail provider; this is the only way to sign in.
        logger.warning("DEVELOPMENT sign-in link (not emailed): %s", link)

    try:
        get_mailer().send(_sign_in_email(address, link))
    except MailerError as e:
        logger.error("sign-in email failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="We couldn't send the sign-in email just now. Please try again shortly.",
        ) from e
    logger.info("sign-in link issued")
    return message


@router.post("/api/auth/verify", response_model=VerifyResponse)
def verify_login(request: VerifyRequest):
    store = get_account_store()
    email = store.consume_login_token(request.token)
    if email is None:
        raise HTTPException(
            status_code=400,
            detail="This sign-in link has expired or has already been used. Request a new one.",
        )
    manager_id = store.get_or_create_manager(email)
    ttl = settings.account_session_days * 86400
    token = issue_token(manager_id, ACCOUNT, ttl_seconds=ttl)
    logger.info("manager=%s signed in", manager_id)
    return VerifyResponse(account_token=token, email=email, expires_at=time.time() + ttl)


@router.get("/api/account/me", response_model=MeResponse)
def me(claims: TokenClaims = Depends(require_account)):
    manager = get_account_store().get_manager(claims.manager_id)
    if manager is None:
        raise HTTPException(status_code=401, detail="Please sign in again")
    return MeResponse(email=manager["email"])


# ---- Dashboard ----

_LABELS = {
    "setup": "Setup interview in progress",
    "awaiting_employee": "Waiting for the employee's interview",
    "interview_complete": "Employee interview complete",
    "generating": "Preparing the handover pack",
    "ready": "Handover pack ready",
    "archived": "Handover pack ready",
    "generation_failed": "Handover pack could not be generated",
    "expired": "Expired",
}


def summarise_handover(row: dict) -> HandoverSummary:
    sessions = get_session_store()
    documents = get_document_store()
    stage1_id = row["stage1_session_id"]

    stage1 = sessions.get_session(stage1_id)
    # Recorded on the handover so the pack can be found after the session
    # links are swept; older records fall back to the live link.
    stage2_id = row.get("stage2_session_id") or (
        sessions.get_linked_session(stage1_id) if stage1 else None
    )

    summary = HandoverSummary(
        stage1_session_id=stage1_id,
        title=row.get("title"),
        created_at=row["created_at"],
        status="setup",
        status_label="",
        stage2_session_id=stage2_id,
        kept_until=row.get("retain_until"),
        interview_available=stage1 is not None,
    )

    if stage1 is not None:
        profile = sessions.get_profile(stage1_id)
        if profile is not None:
            summary.title = summary.title or profile.job_title
            summary.company = profile.company_name

    pack_id = documents.completed_document_for_session(stage2_id) if stage2_id else None
    latest_id = documents.document_for_session(stage2_id) if stage2_id else None
    latest = (documents.get_job(latest_id) or {}) if latest_id else {}

    if pack_id:
        pack = documents.get_job(pack_id) or {}
        summary.document_id = pack_id
        if pack.get("delivery"):
            summary.delivery = HandoverDelivery(**pack["delivery"])

    if stage1 is None:
        # Interview data has been deleted. Either the pack survives it, or
        # nothing is left but the record.
        summary.status = "archived" if pack_id else "expired"
    elif pack_id:
        summary.status = "ready"
    elif not stage1.get("session_complete"):
        summary.status = "setup"
    else:
        stage2 = sessions.get_session(stage2_id) if stage2_id else None
        summary.status = "awaiting_employee"
        if stage2 and stage2.get("session_complete"):
            summary.status = "interview_complete"
            summary.document_error = documents.get_generation_error(stage2_id)
            if summary.document_error:
                summary.status = "generation_failed"
        if latest.get("status") == GENERATING:
            summary.status = "generating"
        elif latest.get("status") == FAILED:
            summary.status = "generation_failed"
            summary.document_error = latest.get("error")

    summary.status_label = _LABELS[summary.status]
    return summary


def _owned_handover(stage1_session_id: str, claims: TokenClaims) -> dict:
    """The caller's handover record, or 404 — never 403, so ids cannot be probed."""
    row = get_account_store().get_handover(stage1_session_id)
    if row is None or row["manager_id"] != claims.manager_id:
        raise HTTPException(status_code=404, detail="Handover not found")
    return row


@router.get("/api/handovers", response_model=List[HandoverSummary])
def list_handovers(claims: TokenClaims = Depends(require_account)):
    rows = get_account_store().list_handovers(claims.manager_id)
    return [summarise_handover(row) for row in rows]


@router.post("/api/handovers/{stage1_session_id}/access", response_model=HandoverAccessResponse)
def handover_access(stage1_session_id: str, claims: TokenClaims = Depends(require_account)):
    """Mint a manager token for a handover this manager owns.

    404 for both "not yours" and "does not exist", so ids cannot be probed.
    """
    _owned_handover(stage1_session_id, claims)
    if get_session_store().get_session(stage1_session_id) is None:
        raise HTTPException(status_code=410, detail="This handover has expired")
    return HandoverAccessResponse(
        session_id=stage1_session_id,
        token=issue_token(stage1_session_id, MANAGER),
    )


@router.get("/api/handovers/{stage1_session_id}/document")
def download_handover_pack(stage1_session_id: str, claims: TokenClaims = Depends(require_account)):
    """Download the newest finished pack.

    Authorised by handover ownership rather than a session token, so it keeps
    working for the whole retention period — after the interview sessions,
    and the manager tokens bound to them, have gone. Header auth only: the
    dashboard fetches it, so no long-lived token ever appears in a URL.
    """
    row = _owned_handover(stage1_session_id, claims)
    documents = get_document_store()
    stage2_id = row.get("stage2_session_id") or get_session_store().get_linked_session(
        stage1_session_id
    )
    document_id = documents.completed_document_for_session(stage2_id) if stage2_id else None
    stored = documents.get_content(document_id) if document_id else None
    if stored is None:
        raise HTTPException(status_code=404, detail="There is no handover pack to download")

    stored_name, media_type, content = stored
    extension = stored_name.rsplit(".", 1)[-1] if "." in stored_name else "docx"
    title = row.get("title") or "Handover pack"
    safe = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:80] or "Handover pack"
    logger.info("handover=%s pack downloaded", stage1_session_id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="Handover - {safe}.{extension}"'},
    )


# ---- Notifications to the owning manager ----

def owner_email_for_stage2(stage2_session_id: str) -> Optional[str]:
    stage1_id = get_session_store().get_linked_session(stage2_session_id)
    if not stage1_id:
        return None
    accounts = get_account_store()
    manager_id = accounts.owner_of_handover(stage1_id)
    if not manager_id:
        return None
    manager = accounts.get_manager(manager_id)
    return manager["email"] if manager else None


def notify_manager(stage2_session_id: str, profile, ready: bool, detail: Optional[str] = None) -> None:
    """Tell the owning manager the pack is ready (or failed). Never raises."""
    try:
        address = owner_email_for_stage2(stage2_session_id)
        if not address:
            return
        role = getattr(profile, "job_title", None) or "the departing role"
        dashboard = f"{settings.public_base_url.rstrip('/')}/dashboard"
        if ready:
            subject = f"Handover pack ready: {role}"
            kept_until = time.strftime(
                "%d %B %Y", time.gmtime(time.time() + settings.document_retention_days * 86400)
            )
            lines = [
                "Hello,",
                f"The employee interview for the {role} role is complete and the handover pack is ready.",
                detail or "",
                f"Open your dashboard to download it: {dashboard}",
                f"The pack is kept until {kept_until}, then deleted automatically. You can delete "
                "it sooner from the dashboard. The interview transcripts behind it are deleted "
                "within a few days.",
                "KnowledgeKeeper by Nukode",
            ]
        else:
            subject = f"Action needed: handover pack for {role}"
            lines = [
                "Hello,",
                f"The employee interview for the {role} role is complete, but the handover pack could not be generated.",
                "Open your dashboard to try again — nothing from the interview has been lost.",
                dashboard,
                "KnowledgeKeeper by Nukode",
            ]
        lines = [l for l in lines if l]
        get_mailer().send(EmailMessage(
            to=[address],
            subject=subject,
            text="\n\n".join(lines),
            html="".join(f"<p>{html.escape(l)}</p>" for l in lines),
        ))
        logger.info("session=%s manager notified ready=%s", stage2_session_id, ready)
    except Exception as e:
        logger.error("session=%s manager notification failed: %s", stage2_session_id, e)
