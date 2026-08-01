"""Stage-scoped signed session tokens (review finding H5).

Access control previously rested entirely on possession of a session UUID. That
model breaks down here because the Stage 2 link is *designed to be forwarded to
the departing employee*: whoever holds it could read and write the manager's
Stage 1 interview, and could generate and download the handover pack — including
the Risk Summary that describes them as a single point of failure.

A token binds a caller to one session and one scope:

    manager   — issued when Stage 1 is created. Authorises that session and its
                linked Stage 2 session, plus generate/download.
    employee  — issued when Stage 2 is created, for the departing employee.
                Authorises only that one session. Never generate or download.

Tokens are `base64url(payload).base64url(hmac_sha256(secret, payload))`, which
keeps them self-contained: no server-side token table to persist, so this works
under the in-process session store as it stands today and continues to work
after H3 moves state to Redis.

They are NOT encrypted — the payload is readable by anyone holding the token.
It carries only a session id, a scope and an expiry, all of which the holder
legitimately knows. The signature is what makes them unforgeable.
"""

import base64
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

Scope = Literal["manager", "employee"]

MANAGER: Scope = "manager"
EMPLOYEE: Scope = "employee"

_VALID_SCOPES = frozenset({MANAGER, EMPLOYEE})

# Generated once per process when API_SECRET_KEY is unset, so local development
# works without configuration. Tokens then die with the process, which is
# acceptable in development and refused outright in production — see
# Settings.validate_for_production.
_ephemeral_secret: Optional[str] = None


class InvalidToken(Exception):
    """Raised when a token is missing, malformed, unsigned, expired or misscoped."""


@dataclass(frozen=True)
class TokenClaims:
    session_id: str
    scope: Scope
    expires_at: float

    @property
    def is_manager(self) -> bool:
        return self.scope == MANAGER


def _secret() -> str:
    global _ephemeral_secret

    if settings.api_secret_key:
        return settings.api_secret_key

    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(32)
        logger.warning(
            "API_SECRET_KEY is not set — using an ephemeral per-process signing "
            "key. Sessions will not survive a restart. Set API_SECRET_KEY before "
            "deploying."
        )
    return _ephemeral_secret


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload_b64: str) -> str:
    digest = hmac.new(_secret().encode("utf-8"), payload_b64.encode("ascii"), sha256).digest()
    return _b64encode(digest)


def issue_token(session_id: str, scope: Scope, ttl_seconds: Optional[float] = None) -> str:
    """Mint a signed token binding `session_id` to `scope`."""
    if scope not in _VALID_SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")

    if ttl_seconds is None:
        ttl_seconds = settings.session_ttl_hours * 3600

    payload = {"sid": session_id, "scope": scope, "exp": time.time() + ttl_seconds}
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_token(token: str) -> TokenClaims:
    """Validate signature and expiry, returning the claims.

    Raises InvalidToken for every failure mode. The message is deliberately
    coarse — a caller learns that their token was rejected, not which check
    rejected it.
    """
    if not token:
        raise InvalidToken("no token supplied")

    parts = token.split(".")
    if len(parts) != 2:
        raise InvalidToken("malformed token")

    payload_b64, signature = parts

    # compare_digest keeps the comparison constant-time, so a caller cannot
    # discover a valid signature byte by byte from response timings.
    if not hmac.compare_digest(_sign(payload_b64), signature):
        raise InvalidToken("bad signature")

    try:
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise InvalidToken("malformed payload") from e

    if not isinstance(payload, dict):
        raise InvalidToken("malformed payload")

    session_id = payload.get("sid")
    scope = payload.get("scope")
    expires_at = payload.get("exp")

    if not isinstance(session_id, str) or not session_id:
        raise InvalidToken("malformed payload")
    if scope not in _VALID_SCOPES:
        raise InvalidToken("malformed payload")
    if not isinstance(expires_at, (int, float)):
        raise InvalidToken("malformed payload")

    if expires_at < time.time():
        raise InvalidToken("token expired")

    return TokenClaims(session_id=session_id, scope=scope, expires_at=float(expires_at))


def extract_bearer(authorization: Optional[str], query_token: Optional[str] = None) -> str:
    """Pull the token from an Authorization header, falling back to a query param.

    The query fallback exists for document download, which the browser triggers
    via a plain <a href> and cannot attach headers to. It is accepted only on
    that endpoint — see routes.require_document_access — because tokens in URLs
    leak through history, logs and referrers.
    """
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            raise InvalidToken("malformed authorization header")
        return value.strip()

    if query_token:
        return query_token

    raise InvalidToken("no token supplied")
