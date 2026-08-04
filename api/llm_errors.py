"""Turning Anthropic SDK failures into something a person can act on.

An unhandled `anthropic` exception reaches the ASGI layer as a bare 500 with the
body "Internal Server Error", so the frontend can only render
`Request failed: 500`. That is true and useless: an expired API key, a
rate limit, and a transient overload are three different problems with three
different fixes, and the person reading the screen cannot tell which one they
have.

Two rules shape the mapping below.

**Never reuse 401, 403 or 404 for an upstream failure.** The frontend already
assigns those meanings: `api/client.js` turns 401 and 403 into "This link is no
longer valid. Ask whoever shared it to send a new one." Passing Anthropic's own
401 through would tell a departing employee their interview link was revoked,
when in fact the deployment's API key is wrong — sending them to the one person
who cannot fix it. Configuration failures surface as 503.

**Say whose problem it is.** A rate limit is worth retrying and the interviewee
should retry it. A rejected API key is not; it needs whoever deployed the app.
The message says which, because "something went wrong" makes every reader try
the same futile refresh.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

# The frontend restores the user's text on a failed send, so this is a promise
# we actually keep — see ChatInput.submit.
NOT_LOST = "Nothing you have written has been lost."


@dataclass(frozen=True)
class LLMFailure:
    """How one Anthropic failure should be reported."""

    status_code: int
    detail: str
    #: Whether retrying the same request could plausibly succeed. Drives both
    #: the wording shown to the user and whether a Retry-After is worth sending.
    retryable: bool
    #: Short, stable label for logs and metrics — never shown to the user.
    kind: str


_CONFIG_PROBLEM = (
    "This is a server configuration problem rather than something you can fix — "
    "please tell whoever set up KnowledgeKeeper."
)


def classify(exc: BaseException) -> Optional[LLMFailure]:
    """Map an Anthropic exception to a response, or None if it isn't one.

    Ordered most specific first: the SDK's exception classes form a hierarchy,
    and `APIStatusError` would otherwise swallow every subclass below it.
    """
    # 401 — the key is absent, malformed, or revoked.
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMFailure(
            status_code=503,
            detail=(
                "The interview assistant is unavailable: this deployment's AI "
                f"credentials were rejected. {_CONFIG_PROBLEM}"
            ),
            retryable=False,
            kind="anthropic_auth",
        )

    # 403 — the key is valid but not entitled to this model or feature.
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMFailure(
            status_code=503,
            detail=(
                "The interview assistant is unavailable: this deployment's AI "
                f"credentials lack access to the configured model. {_CONFIG_PROBLEM}"
            ),
            retryable=False,
            kind="anthropic_permission",
        )

    # 404 — almost always a mistyped or retired model id in configuration.
    if isinstance(exc, anthropic.NotFoundError):
        return LLMFailure(
            status_code=503,
            detail=(
                "The interview assistant is unavailable: the configured AI model "
                f"does not exist. {_CONFIG_PROBLEM}"
            ),
            retryable=False,
            kind="anthropic_model_not_found",
        )

    # 429 — the one case where the person on the screen should just try again.
    if isinstance(exc, anthropic.RateLimitError):
        return LLMFailure(
            status_code=429,
            detail=(
                "The interview assistant is busy right now. Wait a few seconds "
                f"and send your message again. {NOT_LOST}"
            ),
            retryable=True,
            kind="anthropic_rate_limit",
        )

    # 400 — our request was malformed. In this app the realistic cause is a
    # conversation that has outgrown the context window, which is a real
    # possibility on a long interview and is not the user's doing.
    if isinstance(exc, anthropic.BadRequestError):
        return LLMFailure(
            status_code=503,
            detail=(
                "The interview assistant could not process this session. It may "
                f"have grown too long to continue. {_CONFIG_PROBLEM}"
            ),
            retryable=False,
            kind="anthropic_bad_request",
        )

    # Anything else carrying an HTTP status: 5xx and 529 (overloaded) are
    # transient, other 4xx are not.
    if isinstance(exc, anthropic.APIStatusError):
        transient = exc.status_code >= 500
        return LLMFailure(
            status_code=503 if transient else 502,
            detail=(
                (
                    "The interview assistant is temporarily unavailable. Try "
                    f"again in a moment. {NOT_LOST}"
                )
                if transient
                else (
                    "The interview assistant rejected the request. "
                    f"{_CONFIG_PROBLEM}"
                )
            ),
            retryable=transient,
            kind=f"anthropic_http_{exc.status_code}",
        )

    # No HTTP response at all — DNS, TLS, timeout, dropped connection.
    # APITimeoutError is a subclass, so this covers both.
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMFailure(
            status_code=504,
            detail=(
                "Could not reach the interview assistant. This is usually a "
                f"passing network problem — try again in a moment. {NOT_LOST}"
            ),
            retryable=True,
            kind="anthropic_connection",
        )

    return None


def log_failure(exc: BaseException, failure: LLMFailure, **context) -> None:
    """Log the operator-facing detail the response deliberately omits.

    The response says what to do; the log says exactly what happened, including
    Anthropic's own request id so a support conversation has something to quote.
    """
    request_id = getattr(exc, "request_id", None)
    fields = " ".join(f"{k}={v}" for k, v in context.items() if v is not None)
    logger.error(
        "%s llm_failure=%s status=%s request_id=%s: %s",
        fields,
        failure.kind,
        failure.status_code,
        request_id or "-",
        exc,
    )
