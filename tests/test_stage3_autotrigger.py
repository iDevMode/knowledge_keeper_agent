"""Stage 3 fires automatically on Stage 2 completion (review finding M6).

CLAUDE.md: "Stage 3 is triggered automatically on Stage 2 completion." In code
it was client-driven â€” api/webhooks.py only logged â€” so an employee who closed
the tab on the final question left no document behind, and nobody found out
until the manager went looking for it.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import MANAGER, issue_token
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURE = "tests/fixtures/sample_role_profiles.json"
NO_FOLLOWUP = json.dumps({"needs_followup": False, "reason": "clear", "suggested_followup": ""})


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


def _documents():
    from api.document_store import get_document_store

    return get_document_store()


def _job_count() -> int:
    return len(_documents()._documents)


def _await_job(session_id: str, timeout: float = 10.0) -> str:
    """Wait for the auto-triggered job to appear and settle."""
    deadline = time.time() + timeout
    store = _documents()

    while time.time() < deadline and store.document_for_session(session_id) is None:
        time.sleep(0.02)

    doc_id = store.document_for_session(session_id)
    assert doc_id, "no generation job was started"

    while time.time() < deadline and store.get_job(doc_id)["status"] == "generating":
        time.sleep(0.02)
    return doc_id


class _Engagement:
    def __init__(self, client, tmp_path, profile_id="process_heavy"):
        from api.session_manager import get_session_store

        store = get_session_store()
        self.stage1_id = store.create_session(stage=1)
        store.store_profile(self.stage1_id, _load_profile(profile_id))
        self.manager_token = issue_token(self.stage1_id, MANAGER)
        self.client = client
        self.tmp_path = tmp_path

    def start_stage2(self):
        response = self.client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": self.stage1_id},
            headers=_bearer(self.manager_token),
        )
        assert response.status_code == 200, response.text
        self.stage2_id = response.json()["session_id"]
        self.employee_token = response.json()["employee_token"]
        return self

    def answer_until_complete(self, limit: int = 200) -> dict:
        body = {}
        for i in range(limit):
            response = self.client.post(
                f"/api/sessions/{self.stage2_id}/message",
                json={"message": f"Answer {i}."},
                headers=_bearer(self.employee_token),
            )
            assert response.status_code == 200, response.text
            body = response.json()
            if body.get("session_complete"):
                return body
        raise AssertionError("Stage 2 never completed")


@pytest.fixture
def engagement(client, tmp_path):
    written = {}

    def fake_docx(doc, path):
        with open(path, "wb") as f:
            f.write(b"HANDOVER PACK")
        written["path"] = path
        return path

    with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
         patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier), \
         patch("api.routes.generate_document") as gen, \
         patch("api.routes.parse_llm_output"), \
         patch("api.routes.generate_docx", side_effect=fake_docx):
        gen.return_value = MagicMock(raw_markdown="# Handover")
        yield _Engagement(client, tmp_path).start_stage2()


class TestGenerationStartsWithoutTheClient:
    def test_a_document_is_produced_with_no_generate_call(self, client, engagement):
        """The employee closes the tab at completion â€” the pack still exists."""
        import api.routes as routes_mod

        engagement.answer_until_complete()

        doc_id = _await_job(engagement.stage2_id)
        assert _documents().get_job(doc_id)["status"] == "complete"
        assert _documents().get_content(doc_id) is not None

    def test_the_document_is_attributed_to_the_stage2_session(self, client, engagement):
        import api.routes as routes_mod

        engagement.answer_until_complete()
        doc_id = _await_job(engagement.stage2_id)

        assert _documents().owner_of(doc_id) == engagement.stage2_id
        assert _documents().document_for_session(engagement.stage2_id) == doc_id

    def test_generation_is_not_triggered_before_completion(self, client, engagement):
        import api.routes as routes_mod

        client.post(
            f"/api/sessions/{engagement.stage2_id}/message",
            json={"message": "Only the first answer."},
            headers=_bearer(engagement.employee_token),
        )
        assert _job_count() == 0


class TestOnlyTheManagerLearnsAboutTheDocument:
    def test_completion_response_does_not_carry_the_document_id(self, client, engagement):
        """That response goes to the employee."""
        body = engagement.answer_until_complete()
        assert "document_id" not in body or body.get("document_id") is None

    def test_manager_status_exposes_the_document(self, client, engagement):
        import api.routes as routes_mod

        engagement.answer_until_complete()
        doc_id = _await_job(engagement.stage2_id)

        response = client.get(
            f"/api/sessions/{engagement.stage2_id}/status",
            headers=_bearer(engagement.manager_token),
        )
        assert response.json()["document_id"] == doc_id

    def test_employee_status_does_not_expose_the_document(self, client, engagement):
        import api.routes as routes_mod

        engagement.answer_until_complete()
        _await_job(engagement.stage2_id)

        response = client.get(
            f"/api/sessions/{engagement.stage2_id}/status",
            headers=_bearer(engagement.employee_token),
        )
        assert response.json()["document_id"] is None


class TestAutoGenerationNeverBreaksTheInterview:
    def test_a_generation_failure_still_completes_the_employees_turn(self, client, tmp_path):
        """The employee has finished. A Stage 3 problem is the manager's to retry."""
        with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier), \
             patch("api.routes._start_generation", side_effect=RuntimeError("Stage 3 exploded")):
            engagement = _Engagement(client, tmp_path).start_stage2()
            body = engagement.answer_until_complete()

        assert body["session_complete"] is True
        assert body["message"], "the employee got no closing message"


class TestFailureIsVisibleToTheManager:
    """A silent failure is worse than a loud one.

    Auto-generation swallowed its error into a log line, so the manager watched
    "preparing the handover pack..." forever with no way to know it had failed.
    """

    def _complete_with_missing_profile(self, client, tmp_path):
        from api.session_manager import get_session_store

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm", side_effect=_primary), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm", side_effect=_classifier):
            engagement = _Engagement(client, tmp_path).start_stage2()
            # The Stage 1 session and its profile expire mid-interview â€” the
            # real failure when an employee takes longer than the session TTL.
            get_session_store()._profiles.pop(engagement.stage1_id, None)
            engagement.answer_until_complete()
        return engagement

    def test_status_reports_why_no_document_exists(self, client, tmp_path):
        engagement = self._complete_with_missing_profile(client, tmp_path)

        response = client.get(
            f"/api/sessions/{engagement.stage2_id}/status",
            headers=_bearer(engagement.manager_token),
        )
        body = response.json()
        assert body["document_id"] is None
        assert body["generation_error"], "the manager was told nothing"
        assert "profile" in body["generation_error"].lower()

    def test_the_employee_is_not_shown_the_failure(self, client, tmp_path):
        engagement = self._complete_with_missing_profile(client, tmp_path)

        response = client.get(
            f"/api/sessions/{engagement.stage2_id}/status",
            headers=_bearer(engagement.employee_token),
        )
        assert response.json()["generation_error"] is None

    def test_a_successful_regeneration_clears_the_error(self, client, tmp_path, engagement):
        import api.routes as routes_mod

        _documents().set_generation_error(engagement.stage2_id, "an earlier failure")
        engagement.answer_until_complete()
        _await_job(engagement.stage2_id)

        response = client.get(
            f"/api/sessions/{engagement.stage2_id}/status",
            headers=_bearer(engagement.manager_token),
        )
        assert response.json()["generation_error"] is None
        assert response.json()["document_id"]


class TestManagerCanStillRegenerate:
    def test_manager_regeneration_produces_a_second_document(self, client, engagement):
        import api.routes as routes_mod

        engagement.answer_until_complete()
        first = _await_job(engagement.stage2_id)

        response = client.post(
            f"/api/sessions/{engagement.stage2_id}/generate",
            json={"format": "docx"},
            headers=_bearer(engagement.manager_token),
        )
        assert response.status_code == 200
        second = response.json()["document_id"]

        assert second != first
        assert _documents().owner_of(second) == engagement.stage2_id
        # The session now points at the newer document.
        assert _documents().document_for_session(engagement.stage2_id) == second
