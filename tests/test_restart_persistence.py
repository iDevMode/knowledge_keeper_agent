"""An interview survives a process restart (review finding H3).

This is the test H3 exists for. Every Railway redeploy restarts the container,
and until now that destroyed every in-flight interview: sessions, LangGraph
checkpoints and the graph registry all lived in process memory, so a manager or
employee mid-conversation got 404 on their next message with no way back.

_restart() below drops every piece of process state the app holds — session
store, checkpointer, graph registry, connection pools — exactly as a redeploy
would, then continues the same interview through the same public API.

Requires TEST_DATABASE_URL; there is nothing to test without a durable backend.
The final class asserts the in-memory path genuinely does NOT survive, so the
Postgres result cannot be a false positive from state lingering in the process.

    docker run -d --name kk-test-pg -e POSTGRES_PASSWORD=kktest \
        -e POSTGRES_DB=kk -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql://postgres:kktest@localhost:55432/kk pytest
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.auth import MANAGER, issue_token
from models.role_intelligence_profile import RoleIntelligenceProfile

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set TEST_DATABASE_URL to run the restart-persistence tests",
)

NO_FOLLOWUP = json.dumps({"needs_followup": False, "reason": "clear", "suggested_followup": ""})


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open("tests/fixtures/sample_role_profiles.json") as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _classifier(*a, **k):
    llm = MagicMock()

    def respond(m):
        r = MagicMock()
        r.content = "[]" if "risk flag classifier" in m[0].content else NO_FOLLOWUP
        return r

    llm.invoke.side_effect = respond
    return llm


def _primary(*a, **k):
    llm = MagicMock()
    r = MagicMock()
    r.content = "Understood. What happens next?"
    llm.invoke.return_value = r
    return llm


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _restart() -> TestClient:
    """Throw away every piece of process state, as a redeploy does."""
    import api.document_store as doc_mod
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    for module in (sm_mod, doc_mod):
        store = module._store
        if store is not None and hasattr(store, "close"):
            store.close()

    sm_mod.reset_session_store()
    doc_mod.reset_document_store()
    routes_mod.reset_checkpointer()
    routes_mod._registry = routes_mod.GraphRegistry()

    return TestClient(routes_mod.app)


@pytest.fixture
def durable(monkeypatch):
    """Point the app at Postgres and start from a clean database."""
    from config.settings import settings

    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL, raising=False)
    monkeypatch.setattr(settings, "api_secret_key", "restart-test-key", raising=False)

    import psycopg

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE kk_sessions, kk_session_links, kk_profiles, "
            "kk_documents, kk_generation_errors"
        )
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            try:
                conn.execute(f"TRUNCATE {table}")
            except psycopg.errors.UndefinedTable:
                pass  # created lazily by PostgresSaver.setup()

    client = _restart()
    yield client
    _restart()


class TestStage1SurvivesRestart:
    def test_the_manager_can_carry_on_after_a_restart(self, durable):
        with patch("agents.stage1_business_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage1_business_interview.nodes._get_classifier_llm",
                   side_effect=_classifier):
            created = durable.post("/api/sessions/stage1").json()
            session_id, token = created["session_id"], created["token"]

            for i in range(3):
                r = durable.post(
                    f"/api/sessions/{session_id}/message",
                    json={"message": f"Answer {i}."},
                    headers=_bearer(token),
                )
                assert r.status_code == 200

            before = durable.get(
                f"/api/sessions/{session_id}/status", headers=_bearer(token)
            ).json()

            client = _restart()

            after = client.get(
                f"/api/sessions/{session_id}/status", headers=_bearer(token)
            )
            assert after.status_code == 200, "the session vanished across the restart"
            assert after.json()["current_question_index"] == before["current_question_index"]
            assert after.json()["current_block"] == before["current_block"]

            resumed = client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "And one more after the restart."},
                headers=_bearer(token),
            )
            assert resumed.status_code == 200, "the interview could not be resumed"
            assert resumed.json()["message"]

    def test_answers_given_before_the_restart_are_still_there(self, durable):
        import api.routes as routes_mod

        with patch("agents.stage1_business_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage1_business_interview.nodes._get_classifier_llm",
                   side_effect=_classifier):
            created = durable.post("/api/sessions/stage1").json()
            session_id, token = created["session_id"], created["token"]
            for i in range(4):
                durable.post(
                    f"/api/sessions/{session_id}/message",
                    json={"message": f"Answer {i}."},
                    headers=_bearer(token),
                )

            _restart()

            instance = routes_mod._registry.get(session_id)
            answers = instance.graph.get_state(instance.config).values.get("answers", {})

        assert len(answers) >= 4, f"answers were lost across the restart: {answers}"


class TestStage2SurvivesRestart:
    def _start_engagement(self, client):
        from api.session_manager import get_session_store

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        store.store_profile(stage1_id, _load_profile())
        manager_token = issue_token(stage1_id, MANAGER)

        r = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
            headers=_bearer(manager_token),
        )
        assert r.status_code == 200, r.text
        return r.json()["session_id"], r.json()["employee_token"], manager_token

    def test_the_employee_can_carry_on_after_a_restart(self, durable):
        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=_classifier):
            stage2_id, employee_token, _ = self._start_engagement(durable)

            for i in range(3):
                durable.post(
                    f"/api/sessions/{stage2_id}/message",
                    json={"message": f"Answer {i}."},
                    headers=_bearer(employee_token),
                )

            before = durable.get(
                f"/api/sessions/{stage2_id}/status", headers=_bearer(employee_token)
            ).json()

            client = _restart()

            after = client.get(
                f"/api/sessions/{stage2_id}/status", headers=_bearer(employee_token)
            )
            assert after.status_code == 200
            assert after.json()["current_question_index"] == before["current_question_index"]

            resumed = client.post(
                f"/api/sessions/{stage2_id}/message",
                json={"message": "Carrying on after the restart."},
                headers=_bearer(employee_token),
            )
            assert resumed.status_code == 200

    def test_the_profile_handoff_survives_a_restart(self, durable):
        """The Stage 1 profile drives every Stage 2 decision — it must persist."""
        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=_classifier):
            stage2_id, employee_token, manager_token = self._start_engagement(durable)

            client = _restart()

            from api.session_manager import get_session_store

            stage1_id = get_session_store().get_linked_session(stage2_id)
            assert stage1_id, "the session link did not survive"
            assert get_session_store().get_profile(stage1_id) is not None, (
                "the Role Intelligence Profile did not survive"
            )

            assert client.get(
                f"/api/sessions/{stage2_id}/status", headers=_bearer(manager_token)
            ).status_code == 200

    def test_risk_flags_gathered_before_the_restart_are_kept(self, durable):
        import api.routes as routes_mod

        flagging = MagicMock()

        def respond(m):
            r = MagicMock()
            if "risk flag classifier" in m[0].content:
                r.content = json.dumps([{
                    "flag_type": "single_point_of_failure",
                    "severity": "critical",
                    "description": "Sole owner of reconciliation",
                    "recommended_action": "Cross-train a second person",
                }])
            else:
                r.content = NO_FOLLOWUP
            return r

        flagging.invoke.side_effect = respond

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   return_value=flagging):
            stage2_id, employee_token, _ = self._start_engagement(durable)
            for i in range(2):
                durable.post(
                    f"/api/sessions/{stage2_id}/message",
                    json={"message": f"Answer {i}."},
                    headers=_bearer(employee_token),
                )

            _restart()

            instance = routes_mod._registry.get(stage2_id)
            flags = instance.graph.get_state(instance.config).values.get("risk_flags", [])

        assert len(flags) == 2, f"risk flags were lost across the restart: {len(flags)}"


class TestTheHandoverPackSurvivesRestart:
    """A generated pack used to be a file on the container filesystem.

    The next container did not have it, and the six dictionaries that recorded
    its existence were gone too — so the manager's page went from "preparing
    your pack" to nothing, with no record that a document had ever been made.
    """

    def test_a_completed_pack_is_downloadable_after_a_restart(self, durable):
        from api.auth import MANAGER, issue_token
        from api.document_store import get_document_store
        from api.routes import DOCX_MEDIA_TYPE
        from api.session_manager import get_session_store

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        stage2_id = store.create_session(stage=2)
        store.link_sessions(stage1_id, stage2_id)
        manager_token = issue_token(stage1_id, MANAGER)

        documents = get_document_store()
        documents.start_job("pack-1", stage2_id)
        documents.complete_job(
            "pack-1", "handover.docx", DOCX_MEDIA_TYPE, b"RISK SUMMARY: sole owner"
        )

        client = _restart()

        response = client.get("/api/documents/pack-1", headers=_bearer(manager_token))
        assert response.status_code == 200, "the handover pack was lost on restart"
        assert response.content == b"RISK SUMMARY: sole owner"

    def test_the_manager_still_sees_the_document_on_status(self, durable):
        from api.auth import MANAGER, issue_token
        from api.document_store import get_document_store
        from api.routes import DOCX_MEDIA_TYPE
        from api.session_manager import get_session_store

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        stage2_id = store.create_session(stage=2)
        store.link_sessions(stage1_id, stage2_id)
        manager_token = issue_token(stage1_id, MANAGER)

        documents = get_document_store()
        documents.start_job("pack-2", stage2_id)
        documents.complete_job("pack-2", "handover.docx", DOCX_MEDIA_TYPE, b"x")

        client = _restart()

        body = client.get(
            f"/api/sessions/{stage2_id}/status", headers=_bearer(manager_token)
        ).json()
        assert body["document_id"] == "pack-2"

    def test_a_recorded_generation_failure_survives(self, durable):
        from api.auth import MANAGER, issue_token
        from api.document_store import get_document_store
        from api.session_manager import get_session_store

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        stage2_id = store.create_session(stage=2)
        store.link_sessions(stage1_id, stage2_id)
        manager_token = issue_token(stage1_id, MANAGER)

        get_document_store().set_generation_error(stage2_id, "Stage 1 profile not found")

        client = _restart()

        body = client.get(
            f"/api/sessions/{stage2_id}/status", headers=_bearer(manager_token)
        ).json()
        assert body["generation_error"] == "Stage 1 profile not found", (
            "the manager would be back to an unexplained empty progress line"
        )


class TestTheInMemoryPathGenuinelyDoesNotSurvive:
    """Guards against a false positive.

    If the Postgres result came from state lingering in the process rather than
    from the database, the in-memory path would 'survive' too. It must not.
    """

    def test_without_a_database_the_session_is_lost(self, monkeypatch):
        from config.settings import settings

        monkeypatch.setattr(settings, "database_url", "", raising=False)
        monkeypatch.setattr(settings, "api_secret_key", "restart-test-key", raising=False)

        client = _restart()
        with patch("agents.stage1_business_interview.nodes._get_primary_llm",
                   side_effect=_primary), \
             patch("agents.stage1_business_interview.nodes._get_classifier_llm",
                   side_effect=_classifier):
            created = client.post("/api/sessions/stage1").json()
            session_id, token = created["session_id"], created["token"]

            restarted = _restart()
            after = restarted.get(
                f"/api/sessions/{session_id}/status", headers=_bearer(token)
            )

        assert after.status_code == 404, (
            "the in-memory store appeared to survive a restart, so the Postgres "
            "result above proves nothing"
        )
        _restart()
