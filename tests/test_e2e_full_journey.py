"""Full three-stage journey: manager interview -> employee interview -> document.

Covers the handoffs between stages, which no other test exercises end to end:
Stage 1's confirmed profile reaching Stage 2 via the session store, and Stage 2's
answers and risk flags reaching Stage 3's generation request.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.api_client import AuthedTestClient

from agents.stage3_document_generation.generator import (
    _KNOWLEDGE_TRANSFER_MIN_COUNT,
    _REQUIRED_SENTINELS,
)
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"
MAX_TURNS = 80

RISK_FLAG_JSON = json.dumps([{
    "flag_type": "undocumented_critical_process",
    "severity": "high",
    "description": "The month-end reconciliation is undocumented",
    "recommended_action": "Write it up before the last working day",
}])


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _valid_document() -> str:
    """Minimal markdown satisfying the generator's structural validation."""
    parts = [f"### SECTION: {name}\n\nContent for {name}.\n" for name in _REQUIRED_SENTINELS]
    for i in range(_KNOWLEDGE_TRANSFER_MIN_COUNT):
        parts.append(f"### SECTION: Knowledge Transfer\n\nPriority {i + 1} detail.\n")
    return "\n".join(parts)


def _text_llm(content: str) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = content
    llm.invoke.return_value = response
    return llm


def _stage1_primary() -> MagicMock:
    llm = _text_llm("Understood. What comes next?")
    structured = MagicMock()
    structured.invoke.return_value = _load_profile()
    llm.with_structured_output.return_value = structured
    return llm


def _classifier(detect_risk: bool) -> MagicMock:
    llm = MagicMock()

    def respond(messages):
        response = MagicMock()
        if "risk flag classifier" in messages[0].content:
            response.content = RISK_FLAG_JSON if detect_risk else "[]"
        else:
            response.content = json.dumps(
                {"needs_followup": False, "reason": "clear", "suggested_followup": ""}
            )
        return response

    llm.invoke.side_effect = respond
    return llm


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._document_store = {}
    routes_mod._generation_jobs = {}
    yield


@pytest.fixture
def client():
    from api.routes import app
    return AuthedTestClient(app)


@pytest.fixture
def all_stage_llms():
    """Patch every LLM boundary across all three stages."""
    with ExitStack() as stack:
        for target, value in [
            ("agents.stage1_business_interview.nodes._get_primary_llm", _stage1_primary()),
            ("agents.stage1_business_interview.nodes._get_classifier_llm", _classifier(False)),
            ("agents.stage2_employee_interview.nodes._get_primary_llm",
             _text_llm("Thanks. And then what happens?")),
            ("agents.stage2_employee_interview.nodes._get_classifier_llm", _classifier(True)),
        ]:
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(
            patch(
                "agents.stage3_document_generation.generator._stream_generation",
                return_value=_valid_document(),
            )
        )
        stack.enter_context(
            patch("agents.stage3_document_generation.generator._get_client",
                  return_value=MagicMock())
        )
        yield


def _answer(client, session_id, text):
    res = client.post(f"/api/sessions/{session_id}/message", json={"message": text})
    assert res.status_code == 200, f"{res.status_code}: {res.text}"
    return res.json()


def _complete_stage1(client) -> str:
    session_id = client.post("/api/sessions/stage1").json()["session_id"]
    for i in range(MAX_TURNS):
        res = _answer(client, session_id, f"Manager answer {i}.")
        if "ROLE INTELLIGENCE PROFILE" in res["message"]:
            break
    else:
        raise AssertionError("Stage 1 never reached profile review")

    final = _answer(client, session_id, "Looks good, nothing to change.")
    assert final["session_complete"] is True
    return session_id


def _complete_stage2(client, stage1_id: str) -> str:
    created = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id})
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    for i in range(MAX_TURNS):
        if _answer(client, session_id, f"Employee answer {i}.")["session_complete"]:
            return session_id
    raise AssertionError("Stage 2 never completed")


class TestFullJourney:
    def test_manager_interview_to_employee_interview_to_document(
        self, client, all_stage_llms
    ):
        # --- Stage 1: manager configures the profile ---
        stage1_id = _complete_stage1(client)

        from api.session_manager import get_session_store

        store = get_session_store()
        profile = store.get_profile(stage1_id)
        assert profile is not None, "Stage 1 did not persist a confirmed profile"

        # --- Stage 2: employee is interviewed using that profile ---
        stage2_id = _complete_stage2(client, stage1_id)
        assert store.get_linked_session(stage1_id) == stage2_id

        from api.routes import _registry

        instance = _registry.get(stage2_id)
        state = instance.graph.get_state(instance.config).values
        assert state["answers"], "no answers captured in Stage 2"
        assert state["risk_flags"], "risk flags did not reach the final state"

        # --- Stage 3: document generation ---
        started = client.post(f"/api/sessions/{stage2_id}/generate", json={"format": "docx"})
        assert started.status_code == 200, started.text
        document_id = started.json()["document_id"]

        status = None
        for _ in range(60):
            status = client.get(f"/api/documents/{document_id}/status").json()
            if status["status"] in {"complete", "failed"}:
                break
        assert status["status"] == "complete", f"generation failed: {status}"

        downloaded = client.get(f"/api/documents/{document_id}")
        assert downloaded.status_code == 200
        assert len(downloaded.content) > 0

    def test_generation_is_refused_before_stage2_completes(self, client, all_stage_llms):
        stage1_id = _complete_stage1(client)
        created = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id})
        stage2_id = created.json()["session_id"]

        _answer(client, stage2_id, "Only answered one question so far.")
        refused = client.post(f"/api/sessions/{stage2_id}/generate", json={"format": "docx"})

        assert refused.status_code == 400
        assert "not yet complete" in refused.json()["detail"].lower()

    def test_employee_answers_and_risk_flags_reach_the_generator(
        self, client, all_stage_llms
    ):
        """The Stage 2 -> Stage 3 handoff: what the employee said must be passed on."""
        stage1_id = _complete_stage1(client)
        stage2_id = _complete_stage2(client, stage1_id)

        captured = {}

        real_generate = None
        import agents.stage3_document_generation.generator as gen_mod
        real_generate = gen_mod.generate_document

        def capturing(request):
            captured["answers"] = request.answers
            captured["risk_flags"] = request.risk_flags
            captured["profile"] = request.profile
            captured["block_order"] = request.block_order
            return real_generate(request)

        with patch("api.routes.generate_document", side_effect=capturing):
            started = client.post(
                f"/api/sessions/{stage2_id}/generate", json={"format": "docx"}
            )
            document_id = started.json()["document_id"]
            for _ in range(60):
                status = client.get(f"/api/documents/{document_id}/status").json()
                if status["status"] in {"complete", "failed"}:
                    break

        assert captured, "generate_document was never called"
        assert captured["answers"], "employee answers were not passed to Stage 3"
        assert captured["risk_flags"], "risk flags were not passed to Stage 3"
        assert captured["profile"].job_title == "Senior Operations Coordinator"
        assert "internal_processes_workflows" in captured["block_order"]
