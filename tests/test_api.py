import json
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from models.role_intelligence_profile import RoleIntelligenceProfile


# ---- Helpers ----

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        fixtures = json.load(f)
    return RoleIntelligenceProfile.model_validate(fixtures[profile_id])


def _make_mock_llm(response_text="This is a test response."):
    """Create a mock LLM that returns a fixed response."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = response_text
    mock_llm.invoke.return_value = mock_response
    mock_llm.with_structured_output.return_value = mock_llm
    return mock_llm


def _make_mock_classifier_llm():
    """Mock classifier that returns the right structured object per schema."""
    from models.classifier_outputs import ConfirmationIntent, FollowupDecision, RiskFlagBatch

    mock_llm = MagicMock()

    def _structured(schema):
        structured = MagicMock()
        if schema is FollowupDecision:
            structured.invoke.return_value = FollowupDecision(
                needs_followup=False, reason="Answer is clear", suggested_followup=""
            )
        elif schema is RiskFlagBatch:
            structured.invoke.return_value = RiskFlagBatch(flags=[])
        elif schema is ConfirmationIntent:
            structured.invoke.return_value = ConfirmationIntent(intent="confirm")
        else:
            structured.invoke.return_value = MagicMock()
        return structured

    mock_llm.with_structured_output.side_effect = _structured
    return mock_llm


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons between tests."""
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    # Reset session store
    sm_mod._store = None

    # Reset graph registry and document stores
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._doc_registry = routes_mod.create_document_registry()
    routes_mod._blob_store = routes_mod.create_blob_store()

    yield


@pytest.fixture
def client():
    from api.routes import app
    return TestClient(app)


@pytest.fixture
def mock_llms():
    """Patch both _get_primary_llm and _get_classifier_llm in Stage 1 and Stage 2 nodes."""
    primary = _make_mock_llm("Welcome to KnowledgeKeeper! Let's begin.")
    classifier = _make_mock_classifier_llm()

    patches = [
        patch("agents.stage1_business_interview.nodes._get_primary_llm", return_value=primary),
        patch("agents.stage1_business_interview.nodes._get_classifier_llm", return_value=classifier),
        patch("agents.stage2_employee_interview.nodes._get_primary_llm", return_value=primary),
        patch("agents.stage2_employee_interview.nodes._get_classifier_llm", return_value=classifier),
    ]
    mocks = [p.start() for p in patches]
    yield primary, classifier
    for p in patches:
        p.stop()


def _setup_stage1_with_profile():
    """Create a Stage 1 session with a stored profile and tokens."""
    from api.session_manager import get_session_store

    store = get_session_store()
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, _load_profile())
    invite_token = store.create_invite_token(stage1_id)
    manager_token = store.create_manager_token(stage1_id)
    return stage1_id, invite_token, manager_token


# ---- TestGraphRegistry ----

class TestGraphRegistry:
    def test_create_stage1(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        instance = registry.create_stage1("test-s1")
        assert instance.stage == 1
        assert instance.config == {"configurable": {"thread_id": "test-s1"}}
        assert instance.checkpointer is not None

    def test_create_stage2(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        instance = registry.create_stage2("test-s2")
        assert instance.stage == 2
        assert instance.config == {"configurable": {"thread_id": "test-s2"}}

    def test_instance_for_uses_shared_checkpointer(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        i1 = registry.instance_for("s1", 1)
        i2 = registry.instance_for("s1", 1)
        # Fresh views, but the same underlying checkpointer — this is what lets
        # any process/worker resume a session by thread_id.
        assert i1.checkpointer is i2.checkpointer
        assert i1.config == {"configurable": {"thread_id": "s1"}}

    def test_per_session_locks(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        lock1 = registry.get_lock("s1")
        lock2 = registry.get_lock("s2")
        assert lock1 is not lock2
        assert registry.get_lock("s1") is lock1  # same lock returned


class TestStatelessRehydration:
    def test_session_resumes_from_a_fresh_instance(self, client, mock_llms):
        """A session created in one instance is resumable from a fresh instance
        built later — the conversation state lives in the shared checkpointer,
        not the request-scoped GraphInstance."""
        import api.routes as routes_mod

        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        # Simulate a later, unrelated request building its own instance view.
        fresh = routes_mod._rehydrate_instance(session_id)
        assert fresh is not None
        state = fresh.graph.get_state(fresh.config).values
        assert state.get("conversation_history")  # greeting persisted
        assert state.get("current_block") == "business_context"

    def test_rehydrate_unknown_session_returns_none(self):
        import api.routes as routes_mod
        assert routes_mod._rehydrate_instance("nonexistent") is None


# ---- TestCreateStage1 ----

class TestCreateStage1:
    def test_creates_session_and_returns_greeting(self, client, mock_llms):
        response = client.post("/api/sessions/stage1")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data
        assert len(data["session_id"]) > 0

    def test_session_stored_in_store(self, client, mock_llms):
        response = client.post("/api/sessions/stage1")
        session_id = response.json()["session_id"]

        from api.session_manager import get_session_store
        store = get_session_store()
        session = store.get_session(session_id)
        assert session is not None
        assert session["stage"] == 1


# ---- TestCreateStage2 ----

class TestCreateStage2:
    def test_creates_linked_session_from_invite_token(self, client, mock_llms):
        from api.session_manager import get_session_store
        stage1_id, invite_token, _ = _setup_stage1_with_profile()
        store = get_session_store()

        response = client.post(
            "/api/sessions/stage2",
            json={"invite_token": invite_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data

        # Verify linking
        stage2_id = data["session_id"]
        assert store.get_linked_session(stage1_id) == stage2_id
        assert store.get_linked_session(stage2_id) == stage1_id

    def test_invite_token_is_single_use(self, client, mock_llms):
        _, invite_token, _ = _setup_stage1_with_profile()

        first = client.post("/api/sessions/stage2", json={"invite_token": invite_token})
        assert first.status_code == 200

        second = client.post("/api/sessions/stage2", json={"invite_token": invite_token})
        assert second.status_code == 410
        assert "already been used" in second.json()["detail"].lower()

    def test_rejects_invalid_token(self, client, mock_llms):
        response = client.post(
            "/api/sessions/stage2",
            json={"invite_token": "not-a-real-token"},
        )
        assert response.status_code == 404

    def test_rejects_stage1_session_id_as_token(self, client, mock_llms):
        """The Stage 1 session ID must not work as an invite token."""
        stage1_id, _, _ = _setup_stage1_with_profile()
        response = client.post(
            "/api/sessions/stage2",
            json={"invite_token": stage1_id},
        )
        assert response.status_code == 404

    def test_rejects_missing_profile(self, client, mock_llms):
        from api.session_manager import get_session_store
        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        # Token exists but no profile stored
        invite_token = store.create_invite_token(stage1_id)

        response = client.post(
            "/api/sessions/stage2",
            json={"invite_token": invite_token},
        )
        assert response.status_code == 400
        assert "profile" in response.json()["detail"].lower()


# ---- TestSendMessage ----

class TestSendMessage:
    def test_returns_agent_response(self, client, mock_llms):
        # Create a Stage 1 session first
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        response = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": "We are a manufacturing company."},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "session_complete" in data

    def test_rejects_unknown_session(self, client):
        response = client.post(
            "/api/sessions/nonexistent/message",
            json={"message": "hello"},
        )
        assert response.status_code == 404

    def test_rejects_empty_message(self, client, mock_llms):
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        response = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": ""},
        )
        assert response.status_code == 422  # Pydantic validation

    def test_rejects_completed_session(self, client, mock_llms):
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        # Manually mark graph state as complete
        import api.routes as routes_mod
        instance = routes_mod._registry.instance_for(session_id, 1)
        instance.graph.update_state(
            instance.config,
            {"session_complete": True},
        )

        response = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": "another message"},
        )
        assert response.status_code == 400
        assert "complete" in response.json()["detail"].lower()


# ---- TestSessionStatus ----

class TestSessionStatus:
    def test_returns_status(self, client, mock_llms):
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        response = client.get(f"/api/sessions/{session_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["stage"] == 1
        assert "current_block" in data
        assert "session_complete" in data

    def test_stage2_status_does_not_leak_manager_data(self, client, mock_llms):
        """Employee-visible status must not expose risk flags or linked session IDs."""
        stage1_id, invite_token, _ = _setup_stage1_with_profile()

        create_resp = client.post(
            "/api/sessions/stage2",
            json={"invite_token": invite_token},
        )
        session_id = create_resp.json()["session_id"]

        response = client.get(f"/api/sessions/{session_id}/status")
        data = response.json()
        assert data["stage"] == 2
        assert "risk_flag_count" not in data
        assert "linked_session_id" not in data
        assert stage1_id not in json.dumps(data)

    def test_rejects_unknown_session(self, client):
        response = client.get("/api/sessions/nonexistent/status")
        assert response.status_code == 404


# ---- TestSessionHistory ----

class TestSessionHistory:
    def test_returns_transcript(self, client, mock_llms):
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]
        client.post(f"/api/sessions/{session_id}/message", json={"message": "We make widgets."})

        response = client.get(f"/api/sessions/{session_id}/history")
        assert response.status_code == 200
        data = response.json()
        assert data["stage"] == 1
        roles = [m["role"] for m in data["messages"]]
        # At least the greeting (agent), the user's answer, and the next question
        assert "agent" in roles and "user" in roles
        assert any(m["content"] == "We make widgets." for m in data["messages"])

    def test_history_after_rehydration(self, client, mock_llms):
        """History works even without a request-scoped instance — it rebuilds
        from the checkpointer, the property multi-sitting resume relies on."""
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        response = client.get(f"/api/sessions/{session_id}/history")
        assert response.status_code == 200
        assert len(response.json()["messages"]) >= 1  # greeting persisted

    def test_rejects_unknown_session(self, client):
        response = client.get("/api/sessions/nonexistent/history")
        assert response.status_code == 404


# ---- TestManagerDocumentFlow ----

def _setup_completed_stage2(client):
    """Helper: Stage 1 with profile + tokens, Stage 2 in completed state."""
    import api.routes as routes_mod

    stage1_id, invite_token, manager_token = _setup_stage1_with_profile()

    create_resp = client.post(
        "/api/sessions/stage2",
        json={"invite_token": invite_token},
    )
    session_id = create_resp.json()["session_id"]

    # Mark session as complete in graph state
    instance = routes_mod._registry.instance_for(session_id, 2)
    instance.graph.update_state(
        instance.config,
        {
            "session_complete": True,
            "answers": {"role_orientation.0": ["I manage the ops team."]},
            "risk_flags": [],
            "block_order": ["internal_processes_workflows"],
            "block_depths": {"internal_processes_workflows": "full"},
        },
    )
    return session_id, stage1_id, manager_token


class TestManagerDocumentFlow:
    def test_manager_generates_docx(self, client, mock_llms):
        _, stage1_id, manager_token = _setup_completed_stage2(client)

        response = client.post(
            f"/api/manager/{stage1_id}/generate?token={manager_token}",
            json={"format": "docx"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "document_id" in data
        assert data["status"] == "generating"
        assert data["download_url"].startswith("/api/documents/")

    def test_generate_rejects_bad_token(self, client, mock_llms):
        _, stage1_id, _ = _setup_completed_stage2(client)

        response = client.post(
            f"/api/manager/{stage1_id}/generate?token=wrong-token",
            json={"format": "docx"},
        )
        assert response.status_code == 403

    def test_generate_rejects_incomplete_stage2(self, client, mock_llms):
        stage1_id, invite_token, manager_token = _setup_stage1_with_profile()
        client.post("/api/sessions/stage2", json={"invite_token": invite_token})

        # Stage 2 not complete — should be rejected
        response = client.post(
            f"/api/manager/{stage1_id}/generate?token={manager_token}",
            json={"format": "docx"},
        )
        assert response.status_code == 400
        assert "not yet complete" in response.json()["detail"].lower()

    def test_overview_reports_progress(self, client, mock_llms):
        _, stage1_id, manager_token = _setup_completed_stage2(client)

        response = client.get(f"/api/manager/{stage1_id}/handover?token={manager_token}")
        assert response.status_code == 200
        data = response.json()
        assert data["stage2_status"] == "complete"
        assert data["risk_flag_count"] == 0

    def test_overview_rejects_bad_token(self, client, mock_llms):
        _, stage1_id, _ = _setup_completed_stage2(client)
        response = client.get(f"/api/manager/{stage1_id}/handover?token=wrong")
        assert response.status_code == 403

    def test_overview_before_stage2_starts(self, client, mock_llms):
        stage1_id, _, manager_token = _setup_stage1_with_profile()
        response = client.get(f"/api/manager/{stage1_id}/handover?token={manager_token}")
        assert response.status_code == 200
        assert response.json()["stage2_status"] == "not_started"

    def test_no_employee_facing_generate_endpoint(self, client, mock_llms):
        """The employee session must not be able to trigger generation."""
        session_id, _, _ = _setup_completed_stage2(client)
        response = client.post(
            f"/api/sessions/{session_id}/generate",
            json={"format": "docx"},
        )
        assert response.status_code in (404, 405)


# ---- TestDownloadDocument ----

class TestDownloadDocument:
    def _store_document(self, stage1_id):
        import api.routes as routes_mod

        doc_id = "test-doc-id"
        routes_mod._doc_registry.create(doc_id, owner_id=stage1_id, fmt="docx")
        routes_mod._blob_store.put(f"{doc_id}.docx", b"fake docx content")
        routes_mod._doc_registry.mark_complete(
            doc_id,
            key=f"{doc_id}.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="KnowledgeKeeper-Handover.docx",
        )
        return doc_id

    def test_returns_file_with_manager_token(self, client, mock_llms):
        stage1_id, _, manager_token = _setup_stage1_with_profile()
        doc_id = self._store_document(stage1_id)

        response = client.get(f"/api/documents/{doc_id}?token={manager_token}")
        assert response.status_code == 200
        assert response.content == b"fake docx content"
        assert "KnowledgeKeeper-Handover.docx" in response.headers.get("content-disposition", "")

    def test_rejects_missing_token(self, client, mock_llms):
        stage1_id, _, _ = _setup_stage1_with_profile()
        doc_id = self._store_document(stage1_id)

        response = client.get(f"/api/documents/{doc_id}")
        assert response.status_code == 422  # token query param is required

        response = client.get(f"/api/documents/{doc_id}?token=wrong")
        assert response.status_code == 403

    def test_download_not_ready_returns_404(self, client, mock_llms):
        import api.routes as routes_mod
        stage1_id, _, manager_token = _setup_stage1_with_profile()
        routes_mod._doc_registry.create("pending-doc", owner_id=stage1_id, fmt="docx")

        response = client.get(f"/api/documents/pending-doc?token={manager_token}")
        assert response.status_code == 404

    def test_rejects_unknown_document_id(self, client):
        response = client.get("/api/documents/nonexistent?token=anything")
        assert response.status_code == 404


# ---- TestWebhooks ----

class TestWebhooks:
    def test_on_stage1_complete_logs(self, caplog):
        from api.webhooks import on_stage1_complete
        with caplog.at_level(logging.INFO):
            on_stage1_complete("test-session-1")
        assert "stage1_complete" in caplog.text
        assert "test-session-1" in caplog.text

    def test_on_stage2_complete_logs(self, caplog):
        from api.webhooks import on_stage2_complete
        with caplog.at_level(logging.INFO):
            on_stage2_complete("test-session-2")
        assert "stage2_complete" in caplog.text
        assert "test-session-2" in caplog.text

    def test_on_document_generated_logs(self, caplog):
        from api.webhooks import on_document_generated
        with caplog.at_level(logging.INFO):
            on_document_generated("test-session", "doc-123", "/tmp/doc.docx")
        assert "document_generated" in caplog.text
        assert "doc-123" in caplog.text
