"""End-to-end Stage 2 tests driving the real graph through the API.

Existing Stage 2 coverage force-set completion via graph.update_state(), so the
interview loop itself â€” block ordering, depth handling, answer indexing, risk
flag accumulation, phase transitions â€” was never exercised end to end. These
tests drive it for real with mocked LLMs.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.api_client import AuthedTestClient

from config.constants import (
    LIGHT_TOUCH_MAX_QUESTIONS,
    STAGE2_BLOCK_QUESTION_COUNTS,
    STAGE2_CLOSING_QUESTION_COUNT,
    STAGE2_ROLE_ORIENTATION_QUESTION_COUNT,
    KnowledgeBlock,
)
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"

# Generous cap; tests assert completion happens, not exactly when.
MAX_TURNS = 80

RISK_FLAG_JSON = json.dumps([{
    "flag_type": "single_point_of_failure",
    "severity": "critical",
    "description": "Only person who can run the month-end reconciliation",
    "recommended_action": "Document the steps and cross-train before departure",
}])


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _expected_question_count(block_order, block_depths) -> int:
    """Total questions Stage 2 should ask for a given plan."""
    total = STAGE2_ROLE_ORIENTATION_QUESTION_COUNT + STAGE2_CLOSING_QUESTION_COUNT
    for block in block_order:
        full = STAGE2_BLOCK_QUESTION_COUNTS[KnowledgeBlock(block)]
        if block_depths.get(block) == "light":
            total += min(LIGHT_TOUCH_MAX_QUESTIONS, full)
        else:
            total += full
    return total


def _make_classifier(detect_risk: bool):
    """One mock serves both classifiers â€” they share _get_classifier_llm.

    The two callers expect different shapes (follow-up wants an object, risk
    wants an array), so the response is chosen from the prompt text.
    """
    llm = MagicMock()

    def respond(messages):
        prompt = messages[0].content
        response = MagicMock()
        if "risk flag classifier" in prompt:
            response.content = RISK_FLAG_JSON if detect_risk else "[]"
        else:
            response.content = json.dumps(
                {"needs_followup": False, "reason": "clear", "suggested_followup": ""}
            )
        return response

    llm.invoke.side_effect = respond
    return llm


def _make_primary():
    llm = MagicMock()
    response = MagicMock()
    response.content = "Thanks for that. What happens next in the process?"
    llm.invoke.return_value = response
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


def _stage2_llms(detect_risk: bool = False):
    stack = ExitStack()
    stack.enter_context(
        patch("agents.stage2_employee_interview.nodes._get_primary_llm",
              return_value=_make_primary())
    )
    stack.enter_context(
        patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
              return_value=_make_classifier(detect_risk))
    )
    return stack


def _start_stage2(client: AuthedTestClient, profile_id: str = "process_heavy"):
    """Create a Stage 1 session with a stored profile, then open Stage 2."""
    from api.session_manager import get_session_store

    store = get_session_store()
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, _load_profile(profile_id))
    client.adopt(stage1_id)

    response = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id})
    assert response.status_code == 200, response.text
    return response.json()["session_id"], stage1_id


def _state(session_id: str) -> dict:
    from api.routes import _registry

    instance = _registry.get(session_id)
    return instance.graph.get_state(instance.config).values


def _run_to_completion(client: AuthedTestClient, session_id: str) -> int:
    """Answer until the session completes. Returns the number of turns taken."""
    for turn in range(1, MAX_TURNS + 1):
        res = client.post(
            f"/api/sessions/{session_id}/message",
            json={"message": f"Detailed answer number {turn} about the process."},
        )
        assert res.status_code == 200, f"turn {turn} got {res.status_code}: {res.text}"
        if res.json()["session_complete"]:
            return turn
    raise AssertionError(f"Stage 2 did not complete within {MAX_TURNS} turns")


class TestStage2ProfileHandoff:
    def test_greeting_is_returned_and_profile_loads(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)

        state = _state(session_id)
        assert state["profile"] is not None, "Role Intelligence Profile did not load"
        assert state["profile"].job_title == "Senior Operations Coordinator"

    def test_block_order_reflects_the_managers_ranked_priorities(self, client):
        """Regression guard for H2 â€” priorities must survive into the interview plan."""
        with _stage2_llms():
            session_id, _ = _start_stage2(client)

        state = _state(session_id)
        order, depths = state["block_order"], state["block_depths"]

        assert order[:3] == [
            "internal_processes_workflows",
            "technical_systems_tools",
            "undocumented_workarounds",
        ], f"top three priorities not honoured: {order}"

        for block in order[:3]:
            assert depths[block] == "full"
        # Supporting categories run light.
        assert depths["supplier_vendor_relationships"] == "light"
        assert depths["decision_making_logic"] == "light"

    def test_stage2_requires_a_generated_profile(self, client):
        from api.session_manager import get_session_store

        orphan = get_session_store().create_session(stage=1)
        client.adopt(orphan)
        response = client.post("/api/sessions/stage2", json={"stage1_session_id": orphan})

        assert response.status_code == 400
        assert "profile" in response.json()["detail"].lower()


class TestStage2InterviewLoop:
    def test_interview_runs_every_phase_and_completes(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            turns = _run_to_completion(client, session_id)

        state = _state(session_id)
        expected = _expected_question_count(state["block_order"], state["block_depths"])

        assert turns == expected, (
            f"expected {expected} questions for this plan, interview took {turns}"
        )
        assert state["session_complete"] is True

    def test_first_answer_is_filed_at_index_zero(self, client):
        """Regression guard for M2 in Stage 2."""
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "I run the daily dispatch schedule."},
            )

        answers = _state(session_id)["answers"]
        assert "role_orientation.0" in answers, (
            f"first answer misfiled; keys were {sorted(answers)}"
        )

    def test_every_planned_block_is_covered_with_contiguous_indices(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            _run_to_completion(client, session_id)

        state = _state(session_id)
        answers = state["answers"]

        for block in state["block_order"]:
            indices = sorted(
                int(k.split(".")[1]) for k in answers if k.startswith(f"{block}.")
            )
            full = STAGE2_BLOCK_QUESTION_COUNTS[KnowledgeBlock(block)]
            expected = (
                min(LIGHT_TOUCH_MAX_QUESTIONS, full)
                if state["block_depths"][block] == "light"
                else full
            )
            assert indices == list(range(expected)), (
                f"block {block} ({state['block_depths'][block]}) expected indices "
                f"0..{expected - 1}, got {indices}"
            )

    def test_light_touch_blocks_ask_fewer_questions_than_full(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            _run_to_completion(client, session_id)

        answers = _state(session_id)["answers"]
        light = len([k for k in answers if k.startswith("supplier_vendor_relationships.")])
        full = len([k for k in answers if k.startswith("internal_processes_workflows.")])

        assert light == LIGHT_TOUCH_MAX_QUESTIONS
        assert full > light, "light-touch block should be shorter than a full-depth block"

    def test_closing_sequence_runs_last(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            _run_to_completion(client, session_id)

        answers = _state(session_id)["answers"]
        closing = [k for k in answers if k.startswith("closing_sequence.")]
        assert len(closing) == STAGE2_CLOSING_QUESTION_COUNT

    def test_completed_session_rejects_further_messages(self, client):
        with _stage2_llms():
            session_id, _ = _start_stage2(client)
            _run_to_completion(client, session_id)

            extra = client.post(
                f"/api/sessions/{session_id}/message", json={"message": "One more thing."}
            )

        assert extra.status_code == 400


class TestStage2RiskFlags:
    def test_risk_flags_accumulate_across_the_interview(self, client):
        with _stage2_llms(detect_risk=True):
            session_id, _ = _start_stage2(client)
            turns = _run_to_completion(client, session_id)

        flags = _state(session_id)["risk_flags"]
        assert len(flags) == turns, (
            f"expected one flag per answered question, got {len(flags)} over {turns} turns"
        )
        assert flags[0].flag_type.value == "single_point_of_failure"
        assert flags[0].severity.value == "critical"
        assert flags[0].source_block == "role_orientation"

    def test_no_flags_when_classifier_reports_none(self, client):
        with _stage2_llms(detect_risk=False):
            session_id, _ = _start_stage2(client)
            _run_to_completion(client, session_id)

        assert _state(session_id)["risk_flags"] == []

    def test_status_endpoint_reports_risk_flag_count(self, client):
        with _stage2_llms(detect_risk=True):
            session_id, _ = _start_stage2(client)
            client.post(
                f"/api/sessions/{session_id}/message", json={"message": "Only I can do it."}
            )

            status = client.get(f"/api/sessions/{session_id}/status").json()

        assert status["risk_flag_count"] >= 1
        assert status["stage"] == 2
