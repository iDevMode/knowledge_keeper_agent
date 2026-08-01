"""Stage-scoped session tokens (review finding H5).

Access control previously rested on possession of a session UUID. Because the
Stage 2 link is designed to be forwarded to the departing employee, that handed
the employee the manager's session id — enough to read and write the manager's
interview — and let them generate and download their own handover pack, Risk
Summary included.

These tests use a bare TestClient and set Authorization explicitly. The
AuthedTestClient used elsewhere attaches tokens automatically, which is exactly
what must not happen when the boundary itself is under test.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import (
    EMPLOYEE,
    MANAGER,
    InvalidToken,
    issue_token,
    verify_token,
)
from api.routes import parse_allowed_origins
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURE = "tests/fixtures/sample_role_profiles.json"
NO_FOLLOWUP = json.dumps({"needs_followup": False, "reason": "clear", "suggested_followup": ""})


@pytest.fixture(autouse=True)
def fixed_secret():
    """Pin the signing key so tokens are deterministic across the module."""
    from config.settings import settings

    original = settings.api_secret_key
    object.__setattr__(settings, "api_secret_key", "test-signing-key-do-not-use")
    yield
    object.__setattr__(settings, "api_secret_key", original)


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._document_store.clear()
    routes_mod._document_owner.clear()
    routes_mod._session_document.clear()
    routes_mod._generation_jobs.clear()
    yield


@pytest.fixture
def client():
    from api.routes import app
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURE) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _classifier(*a, **k):
    llm = MagicMock()

    def respond(messages):
        r = MagicMock()
        r.content = "[]" if "risk flag classifier" in messages[0].content else NO_FOLLOWUP
        return r

    llm.invoke.side_effect = respond
    return llm


def _primary(*a, **k):
    llm = MagicMock()
    r = MagicMock()
    r.content = "Understood. What happens next?"
    llm.invoke.return_value = r
    return llm


@pytest.fixture
def engagement(client):
    """A linked Stage 1 / Stage 2 pair with both tokens."""
    from api.session_manager import get_session_store

    store = get_session_store()
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, _load_profile())
    manager_token = issue_token(stage1_id, MANAGER)

    with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
         patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier):
        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
            headers=_bearer(manager_token),
        )
    assert response.status_code == 200, response.text
    body = response.json()

    return {
        "stage1_id": stage1_id,
        "stage2_id": body["session_id"],
        "manager_token": manager_token,
        "employee_token": body["employee_token"],
    }


# ---- Token primitives ----

class TestTokenIntegrity:
    def test_round_trip_preserves_session_and_scope(self):
        claims = verify_token(issue_token("sess-1", MANAGER))
        assert claims.session_id == "sess-1"
        assert claims.scope == MANAGER
        assert claims.is_manager

    def test_employee_scope_is_not_manager(self):
        assert not verify_token(issue_token("sess-1", EMPLOYEE)).is_manager

    def test_tampered_payload_is_refused(self):
        token = issue_token("sess-1", EMPLOYEE)
        payload, signature = token.split(".")
        forged = issue_token("sess-1", MANAGER).split(".")[0]

        with pytest.raises(InvalidToken):
            verify_token(f"{forged}.{signature}")

    def test_tampered_signature_is_refused(self):
        payload, signature = issue_token("sess-1", MANAGER).split(".")
        flipped = ("B" if signature[0] != "B" else "C") + signature[1:]

        with pytest.raises(InvalidToken):
            verify_token(f"{payload}.{flipped}")

    def test_expired_token_is_refused(self):
        with pytest.raises(InvalidToken):
            verify_token(issue_token("sess-1", MANAGER, ttl_seconds=-1))

    def test_token_signed_with_another_key_is_refused(self):
        from config.settings import settings

        token = issue_token("sess-1", MANAGER)
        object.__setattr__(settings, "api_secret_key", "a-different-key")
        with pytest.raises(InvalidToken):
            verify_token(token)

    @pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "...", "abc."])
    def test_malformed_tokens_are_refused(self, garbage):
        with pytest.raises(InvalidToken):
            verify_token(garbage)

    def test_unknown_scope_cannot_be_issued(self):
        with pytest.raises(ValueError):
            issue_token("sess-1", "admin")


# ---- The original attack ----

class TestEmployeeCannotReachTheManagersInterview:
    """The reported hole: the shared link handed the employee the manager's id."""

    def test_employee_token_does_not_open_the_managers_session(self, client, engagement):
        response = client.post(
            f"/api/sessions/{engagement['stage1_id']}/message",
            json={"message": "I am not the manager."},
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_employee_token_does_not_read_the_managers_status(self, client, engagement):
        response = client.get(
            f"/api/sessions/{engagement['stage1_id']}/status",
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_bare_session_id_is_no_longer_a_credential(self, client, engagement):
        for path in (
            f"/api/sessions/{engagement['stage1_id']}/status",
            f"/api/sessions/{engagement['stage2_id']}/status",
        ):
            assert client.get(path).status_code == 401

    def test_manager_token_does_open_the_linked_employee_session(self, client, engagement):
        response = client.get(
            f"/api/sessions/{engagement['stage2_id']}/status",
            headers=_bearer(engagement["manager_token"]),
        )
        assert response.status_code == 200

    def test_creating_a_stage2_session_requires_the_manager(self, client, engagement):
        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": engagement["stage1_id"]},
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_creating_a_stage2_session_unauthenticated_is_refused(self, client, engagement):
        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": engagement["stage1_id"]},
        )
        assert response.status_code == 401


# ---- The document boundary ----

class TestEmployeeCannotObtainTheHandoverPack:
    @pytest.fixture
    def document(self, tmp_path, engagement):
        import api.routes as routes_mod

        path = tmp_path / "pack.docx"
        path.write_bytes(b"RISK SUMMARY: sole owner of reconciliation")

        doc_id = "doc-under-test"
        routes_mod._document_store[doc_id] = str(path)
        routes_mod._document_owner[doc_id] = engagement["stage2_id"]
        routes_mod._generation_jobs[doc_id] = {
            "status": "complete",
            "download_url": f"/api/documents/{doc_id}",
            "error": None,
        }
        return doc_id

    def test_employee_cannot_generate(self, client, engagement):
        response = client.post(
            f"/api/sessions/{engagement['stage2_id']}/generate",
            json={"format": "docx"},
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_employee_cannot_download(self, client, engagement, document):
        response = client.get(
            f"/api/documents/{document}",
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403
        assert b"RISK SUMMARY" not in response.content

    def test_employee_cannot_poll_generation_status(self, client, engagement, document):
        response = client.get(
            f"/api/documents/{document}/status",
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_manager_can_download(self, client, engagement, document):
        response = client.get(
            f"/api/documents/{document}",
            headers=_bearer(engagement["manager_token"]),
        )
        assert response.status_code == 200
        assert b"RISK SUMMARY" in response.content

    def test_manager_can_download_via_query_token(self, client, engagement, document):
        """<a href> downloads cannot set headers, so the query fallback must work."""
        response = client.get(
            f"/api/documents/{document}?token={engagement['manager_token']}"
        )
        assert response.status_code == 200
        assert b"RISK SUMMARY" in response.content

    def test_a_managers_token_for_another_engagement_is_refused(self, client, document):
        response = client.get(
            f"/api/documents/{document}",
            headers=_bearer(issue_token("some-other-session", MANAGER)),
        )
        assert response.status_code == 403

    def test_unauthenticated_download_is_refused(self, client, document):
        assert client.get(f"/api/documents/{document}").status_code == 401


# ---- Cross-session isolation ----

class TestTokensAreBoundToOneSession:
    def test_a_token_does_not_open_an_unrelated_session(self, client, engagement):
        from api.session_manager import get_session_store

        other = get_session_store().create_session(stage=1)
        response = client.get(
            f"/api/sessions/{other}/status",
            headers=_bearer(engagement["employee_token"]),
        )
        assert response.status_code == 403

    def test_expired_token_is_refused_by_the_api(self, client, engagement):
        response = client.get(
            f"/api/sessions/{engagement['stage2_id']}/status",
            headers=_bearer(issue_token(engagement["stage2_id"], EMPLOYEE, ttl_seconds=-1)),
        )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "header",
        ["Bearer", "Bearer ", "Basic abc", "token-without-scheme"],
    )
    def test_malformed_authorization_headers_are_refused(self, client, engagement, header):
        response = client.get(
            f"/api/sessions/{engagement['stage2_id']}/status",
            headers={"Authorization": header},
        )
        assert response.status_code == 401


# ---- CORS origin parsing ----

class TestAllowedOriginParsing:
    def test_whitespace_after_commas_is_stripped(self):
        assert parse_allowed_origins("https://a.com, https://b.com") == [
            "https://a.com",
            "https://b.com",
        ]

    def test_empty_entries_are_dropped(self):
        assert parse_allowed_origins("https://a.com,,  ,https://b.com") == [
            "https://a.com",
            "https://b.com",
        ]

    def test_single_origin_is_unchanged(self):
        assert parse_allowed_origins("http://localhost:3000") == ["http://localhost:3000"]
