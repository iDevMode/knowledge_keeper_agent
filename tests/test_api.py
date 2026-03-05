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


# ---- Fixtures ----

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset module-level singletons between tests."""
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    # Reset session store
    sm_mod._store = None

    # Reset graph registry and document store
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._document_store = {}

    yield


@pytest.fixture
def client():
    from api.routes import app
    return TestClient(app)


@pytest.fixture
def mock_llms():
    """Patch both _get_primary_llm and _get_classifier_llm in Stage 1 and Stage 2 nodes."""
    primary = _make_mock_llm("Welcome to KnowledgeKeeper! Let's begin.")
    classifier = _make_mock_llm(json.dumps({
        "needs_followup": False,
        "reason": "Answer is clear",
        "suggested_followup": "",
    }))

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

    def test_get_returns_instance(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        registry.create_stage1("test-s1")
        assert registry.get("test-s1") is not None

    def test_get_returns_none_for_unknown(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        assert registry.get("nonexistent") is None

    def test_remove(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        registry.create_stage1("test-s1")
        registry.remove("test-s1")
        assert registry.get("test-s1") is None

    def test_per_session_locks(self):
        from api.routes import GraphRegistry
        registry = GraphRegistry()
        lock1 = registry.get_lock("s1")
        lock2 = registry.get_lock("s2")
        assert lock1 is not lock2
        assert registry.get_lock("s1") is lock1  # same lock returned


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
    def test_creates_linked_session(self, client, mock_llms):
        # First create a Stage 1 session and store a profile
        from api.session_manager import get_session_store
        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        profile = _load_profile()
        store.store_profile(stage1_id, profile)

        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "message" in data

        # Verify linking
        stage2_id = data["session_id"]
        assert store.get_linked_session(stage1_id) == stage2_id
        assert store.get_linked_session(stage2_id) == stage1_id

    def test_rejects_missing_stage1_session(self, client, mock_llms):
        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": "nonexistent-id"},
        )
        assert response.status_code == 404

    def test_rejects_missing_profile(self, client, mock_llms):
        from api.session_manager import get_session_store
        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        # No profile stored

        response = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
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
        instance = routes_mod._registry.get(session_id)
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

    def test_stage2_includes_risk_flag_count(self, client, mock_llms):
        from api.session_manager import get_session_store
        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        profile = _load_profile()
        store.store_profile(stage1_id, profile)

        create_resp = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
        )
        session_id = create_resp.json()["session_id"]

        response = client.get(f"/api/sessions/{session_id}/status")
        data = response.json()
        assert data["stage"] == 2
        assert "risk_flag_count" in data
        assert isinstance(data["risk_flag_count"], int)

    def test_rejects_unknown_session(self, client):
        response = client.get("/api/sessions/nonexistent/status")
        assert response.status_code == 404


# ---- TestGenerateDocument ----

class TestGenerateDocument:
    def _setup_completed_stage2(self, client, mock_llms):
        """Helper: create Stage 1 with profile, Stage 2 with completed state."""
        from api.session_manager import get_session_store
        import api.routes as routes_mod

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        profile = _load_profile()
        store.store_profile(stage1_id, profile)

        create_resp = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
        )
        session_id = create_resp.json()["session_id"]

        # Mark session as complete in graph state
        instance = routes_mod._registry.get(session_id)
        instance.graph.update_state(
            instance.config,
            {
                "session_complete": True,
                "answers": {"role_orientation.0": "I manage the ops team."},
                "risk_flags": [],
                "block_order": ["internal_processes_workflows"],
                "block_depths": {"internal_processes_workflows": "full"},
            },
        )
        return session_id, stage1_id

    @patch("api.routes.generate_document")
    @patch("api.routes.parse_llm_output")
    @patch("api.routes.generate_docx")
    def test_generates_docx(self, mock_docx, mock_parse, mock_gen, client, mock_llms):
        session_id, _ = self._setup_completed_stage2(client, mock_llms)

        # Mock generation pipeline
        mock_result = MagicMock()
        mock_result.raw_markdown = "# Handover Document"
        mock_gen.return_value = mock_result

        mock_doc = MagicMock()
        mock_parse.return_value = mock_doc

        # Create a real temp file for docx
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake docx content")
        tmp.close()
        mock_docx.return_value = tmp.name

        try:
            response = client.post(
                f"/api/sessions/{session_id}/generate",
                json={"format": "docx"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "document_id" in data
            assert "download_url" in data
            assert data["download_url"].startswith("/api/documents/")
        finally:
            os.unlink(tmp.name)

    def test_rejects_incomplete_stage2(self, client, mock_llms):
        from api.session_manager import get_session_store

        store = get_session_store()
        stage1_id = store.create_session(stage=1)
        profile = _load_profile()
        store.store_profile(stage1_id, profile)

        create_resp = client.post(
            "/api/sessions/stage2",
            json={"stage1_session_id": stage1_id},
        )
        session_id = create_resp.json()["session_id"]

        # Don't mark as complete — should be rejected
        response = client.post(
            f"/api/sessions/{session_id}/generate",
            json={"format": "docx"},
        )
        assert response.status_code == 400
        assert "not yet complete" in response.json()["detail"].lower()

    def test_rejects_stage1_session(self, client, mock_llms):
        create_resp = client.post("/api/sessions/stage1")
        session_id = create_resp.json()["session_id"]

        response = client.post(
            f"/api/sessions/{session_id}/generate",
            json={"format": "docx"},
        )
        assert response.status_code == 400
        assert "stage 2" in response.json()["detail"].lower()


# ---- TestDownloadDocument ----

class TestDownloadDocument:
    def test_returns_file(self, client):
        import api.routes as routes_mod

        # Create a real temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake docx content")
        tmp.close()

        doc_id = "test-doc-id"
        routes_mod._document_store[doc_id] = tmp.name

        try:
            response = client.get(f"/api/documents/{doc_id}")
            assert response.status_code == 200
            assert response.content == b"fake docx content"
        finally:
            os.unlink(tmp.name)

    def test_rejects_unknown_document_id(self, client):
        response = client.get("/api/documents/nonexistent")
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
