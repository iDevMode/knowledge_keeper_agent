"""Manager sign-in (magic link) and the handover dashboard.

    POST /api/auth/login                     email a one-time sign-in link
    POST /api/auth/verify                    exchange the link's token for an account token
    GET  /api/account/me                     who is signed in
    GET  /api/handovers                      the manager's handovers, with status
    POST /api/handovers/{stage1_id}/access   a manager token for one of them

The account token never touches a session directly. To work on a handover the
dashboard exchanges it for the same stage-scoped manager token the rest of the
API already understands, so none of the existing authorisation changes.
"""

import html
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
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
    # generation_failed | expired
    status: str
    status_label: str
    stage2_session_id: Optional[str] = None
    document_id: Optional[str] = None
    document_error: Optional[str] = None
    delivery: Optional[HandoverDelivery] = None


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
    "generation_failed": "Handover pack could not be generated",
    "expired": "Expired",
}


def summarise_handover(row: dict) -> HandoverSummary:
    sessions = get_session_store()
    documents = get_document_store()
    stage1_id = row["stage1_session_id"]

    summary = HandoverSummary(
        stage1_session_id=stage1_id,
        title=row.get("title"),
        created_at=row["created_at"],
        status="setup",
        status_label="",
    )

    stage1 = sessions.get_session(stage1_id)
    if stage1 is None:
        summary.status = "expired"
        summary.status_label = _LABELS["expired"]
        return summary

    profile = sessions.get_profile(stage1_id)
    if profile is not None:
        summary.title = summary.title or profile.job_title
        summary.company = profile.company_name

    if not stage1.get("session_complete"):
        summary.status = "setup"
    else:
        summary.status = "awaiting_employee"
        stage2_id = sessions.get_linked_session(stage1_id)
        summary.stage2_session_id = stage2_id
        stage2 = sessions.get_session(stage2_id) if stage2_id else None
        if stage2 and stage2.get("session_complete"):
            summary.status = "interview_complete"
            summary.document_error = documents.get_generation_error(stage2_id)
            if summary.document_error:
                summary.status = "generation_failed"
            document_id = documents.document_for_session(stage2_id)
            if document_id:
                job = documents.get_job(document_id) or {}
                summary.document_id = document_id
                if job.get("status") == COMPLETE:
                    summary.status = "ready"
                elif job.get("status") == GENERATING:
                    summary.status = "generating"
                elif job.get("status") == FAILED:
                    summary.status = "generation_failed"
                    summary.document_error = job.get("error")
                if job.get("delivery"):
                    summary.delivery = HandoverDelivery(**job["delivery"])

    summary.status_label = _LABELS[summary.status]
    return summary


@router.get("/api/handovers", response_model=List[HandoverSummary])
def list_handovers(claims: TokenClaims = Depends(require_account)):
    rows = get_account_store().list_handovers(claims.manager_id)
    return [summarise_handover(row) for row in rows]


@router.post("/api/handovers/{stage1_session_id}/access", response_model=HandoverAccessResponse)
def handover_access(stage1_session_id: str, claims: TokenClaims = Depends(require_account)):
    """Mint a manager token for a handover this manager owns.

    404 for both "not yours" and "does not exist", so ids cannot be probed.
    """
    owner = get_account_store().owner_of_handover(stage1_session_id)
    if owner != claims.manager_id:
        raise HTTPException(status_code=404, detail="Handover not found")
    if get_session_store().get_session(stage1_session_id) is None:
        raise HTTPException(status_code=410, detail="This handover has expired")
    return HandoverAccessResponse(
        session_id=stage1_session_id,
        token=issue_token(stage1_session_id, MANAGER),
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
            lines = [
                "Hello,",
                f"The employee interview for the {role} role is complete and the handover pack is ready.",
                detail or "",
                f"Open your dashboard to download it: {dashboard}",
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
