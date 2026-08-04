"""An Anthropic failure must say which failure it was, and whose problem it is.

Reproduced from a real report: pressing Enter did nothing. The cause was a
rejected API key, but every send returned a bare 500 with the body "Internal
Server Error", so the only thing the frontend could render was
`Request failed: 500` — true, and useless.

Two properties matter more than the wording, and both are asserted here:

  * a rejected API key must NOT surface as 401/403/404. The frontend maps those
    to "This link is no longer valid. Ask whoever shared it to send a new one."
    which would send a departing employee to the one person who cannot fix it;
  * a rate limit must stay retryable and distinguishable from a broken key, so
    the two do not produce the same advice.
"""

import json
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from api import llm_errors
from api.auth import MANAGER, issue_token


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.document_store as doc_mod
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    doc_mod.reset_document_store()
    yield


@pytest.fixture
def client():
    from api.routes import app
    return TestClient(app)


def _response(status: int) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers={"request-id": "req_test123"},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def _err(cls, status: int, message: str = "boom"):
    """Build a real SDK exception rather than a stand-in.

    The mapping switches on the SDK's exception classes, so a hand-rolled stub
    would let a wrong isinstance chain pass. These are the same objects the
    SDK raises.
    """
    return cls(message, response=_response(status), body=None)


class TestClassification:
    def test_a_rejected_key_is_not_reported_as_an_auth_failure(self):
        """The whole point: 401 from Anthropic must not become 401 from us."""
        failure = llm_errors.classify(_err(anthropic.AuthenticationError, 401))

        assert failure is not None
        assert failure.status_code not in (401, 403, 404), (
            "the frontend maps these to 'this link is no longer valid', which "
            "would blame the interview link for a server configuration problem"
        )
        assert failure.status_code == 503
        assert failure.retryable is False

    def test_it_says_who_can_fix_a_rejected_key(self):
        failure = llm_errors.classify(_err(anthropic.AuthenticationError, 401))
        assert "configuration" in failure.detail.lower()
        # Must not invite a retry that cannot possibly work.
        assert "try again" not in failure.detail.lower()

    def test_a_rate_limit_is_retryable_and_says_so(self):
        failure = llm_errors.classify(_err(anthropic.RateLimitError, 429))

        assert failure.status_code == 429
        assert failure.retryable is True
        assert "again" in failure.detail.lower()

    def test_the_two_are_not_the_same_message(self):
        """A retryable and a non-retryable failure must give different advice."""
        auth = llm_errors.classify(_err(anthropic.AuthenticationError, 401))
        limit = llm_errors.classify(_err(anthropic.RateLimitError, 429))

        assert auth.detail != limit.detail
        assert auth.status_code != limit.status_code
        assert auth.retryable != limit.retryable

    def test_a_missing_model_is_a_configuration_problem_not_a_missing_session(self):
        failure = llm_errors.classify(_err(anthropic.NotFoundError, 404))
        assert failure.status_code == 503, (
            "404 would read as 'session not found' to the client"
        )
        assert "model" in failure.detail.lower()

    def test_permission_denied_is_a_configuration_problem(self):
        failure = llm_errors.classify(_err(anthropic.PermissionDeniedError, 403))
        assert failure.status_code == 503
        assert failure.retryable is False

    def test_an_overloaded_service_is_transient(self):
        failure = llm_errors.classify(_err(anthropic.InternalServerError, 529))
        assert failure.status_code == 503
        assert failure.retryable is True

    def test_a_connection_failure_is_transient(self):
        exc = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        failure = llm_errors.classify(exc)
        assert failure.status_code == 504
        assert failure.retryable is True

    def test_every_failure_carries_a_distinct_kind_for_logs(self):
        kinds = {
            llm_errors.classify(_err(anthropic.AuthenticationError, 401)).kind,
            llm_errors.classify(_err(anthropic.PermissionDeniedError, 403)).kind,
            llm_errors.classify(_err(anthropic.NotFoundError, 404)).kind,
            llm_errors.classify(_err(anthropic.RateLimitError, 429)).kind,
            llm_errors.classify(_err(anthropic.BadRequestError, 400)).kind,
        }
        assert len(kinds) == 5

    def test_an_unrelated_exception_is_not_claimed(self):
        """classify() must not swallow errors that have nothing to do with the LLM."""
        assert llm_errors.classify(ValueError("unrelated")) is None
        assert llm_errors.classify(KeyError("session")) is None


class TestThroughTheAPI:
    """The mapping is only worth anything if it reaches the response body."""

    def _session(self, client):
        res = client.post("/api/sessions/stage1")
        assert res.status_code == 200
        return res.json()["session_id"], res.json()["token"]

    def test_a_rejected_key_reaches_the_client_as_a_readable_503(self, client):
        session_id, token = self._session(client)

        with patch("api.routes._run_graph_resume") as run:
            run.side_effect = _err(
                anthropic.AuthenticationError, 401, "API key is invalid."
            )
            res = client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "We are a logistics firm."},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 503
        detail = res.json()["detail"]
        # The frontend reads body.detail; anything else renders as
        # "Request failed: 503", which is where this started.
        assert detail and "configuration" in detail.lower()
        assert "Internal Server Error" not in res.text

    def test_a_rate_limit_reaches_the_client_as_429_with_backoff(self, client):
        session_id, token = self._session(client)

        with patch("api.routes._run_graph_resume") as run:
            run.side_effect = _err(anthropic.RateLimitError, 429)
            res = client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "We are a logistics firm."},
                headers={"Authorization": f"Bearer {token}"},
            )

        assert res.status_code == 429
        assert "Retry-After" in res.headers
        assert int(res.headers["Retry-After"]) >= 1

    def test_a_non_anthropic_failure_still_500s(self, client):
        """The handler must not widen into a catch-all for unrelated bugs."""
        session_id, token = self._session(client)

        with patch("api.routes._run_graph_resume") as run:
            run.side_effect = RuntimeError("a real bug")
            with pytest.raises(RuntimeError):
                client.post(
                    f"/api/sessions/{session_id}/message",
                    json={"message": "We are a logistics firm."},
                    headers={"Authorization": f"Bearer {token}"},
                )


class TestGenerationFailures:
    """Document generation runs in a thread, where the handler cannot reach it."""

    def test_a_rejected_key_during_generation_is_reported_readably(self):
        from api.document_store import get_document_store
        from api.routes import _run_generation_in_background

        store = get_document_store()
        document_id = "doc-under-test"
        store.start_job(document_id, "session-x")

        with patch("api.routes.generate_document") as gen:
            gen.side_effect = _err(
                anthropic.AuthenticationError, 401, "API key is invalid."
            )
            _run_generation_in_background(
                document_id, "session-x", MagicMock(), MagicMock(), "docx"
            )

        job = store.get_job(document_id)
        assert job["status"] == "failed"
        assert "configuration" in job["error"].lower()
        # The raw SDK text is what the manager used to be shown.
        assert "AuthenticationError" not in job["error"]
