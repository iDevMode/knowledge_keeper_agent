"""Risk classification runs as a parallel branch (review finding M1).

CLAUDE.md Principle 5: risk flag detection "runs as a parallel LangGraph branch
on every answer. It does not block the main conversation flow." It was wired in
series ahead of the follow-up classifier, adding a Haiku round-trip to the
latency of every employee turn.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURE = "tests/fixtures/sample_role_profiles.json"
CLASSIFIER_DELAY = 0.2

RISK_FLAG_JSON = json.dumps([{
    "flag_type": "single_point_of_failure",
    "severity": "critical",
    "description": "Sole owner of the reconciliation process",
    "recommended_action": "Cross-train a second person",
}])
NO_FOLLOWUP_JSON = json.dumps(
    {"needs_followup": False, "reason": "clear", "suggested_followup": ""}
)


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURE) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    yield


@pytest.fixture
def client():
    from api.routes import app
    return TestClient(app)


def _start_stage2(client: TestClient) -> str:
    from api.session_manager import get_session_store

    store = get_session_store()
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, _load_profile())
    response = client.post("/api/sessions/stage2", json={"stage1_session_id": stage1_id})
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _state(session_id: str) -> dict:
    from api.routes import _registry

    instance = _registry.get(session_id)
    return instance.graph.get_state(instance.config).values


class _ConcurrencyTracker:
    """Records how many classifier calls are in flight at once."""

    def __init__(self, delay: float = CLASSIFIER_DELAY, detect_risk: bool = True):
        self.delay = delay
        self.detect_risk = detect_risk
        self.peak = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        llm = MagicMock()
        llm.invoke.side_effect = self._respond
        return llm

    def _respond(self, messages):
        with self._lock:
            self._in_flight += 1
            self.peak = max(self.peak, self._in_flight)
        try:
            time.sleep(self.delay)
        finally:
            with self._lock:
                self._in_flight -= 1

        response = MagicMock()
        if "risk flag classifier" in messages[0].content:
            response.content = RISK_FLAG_JSON if self.detect_risk else "[]"
        else:
            response.content = NO_FOLLOWUP_JSON
        return response


def _primary() -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = "Understood. What happens after that?"
    llm.invoke.return_value = response
    return llm


class TestClassifiersRunConcurrently:
    def test_both_classifiers_are_in_flight_at_the_same_time(self, client):
        tracker = _ConcurrencyTracker()

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   return_value=_primary()), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=tracker):
            session_id = _start_stage2(client)
            client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "I am the only person who runs reconciliation."},
            )

        assert tracker.peak == 2, (
            f"expected risk and follow-up classifiers to overlap, peak in-flight "
            f"was {tracker.peak} — they are running in series"
        )

    def test_turn_latency_is_not_the_sum_of_both_classifiers(self, client):
        tracker = _ConcurrencyTracker()

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   return_value=_primary()), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=tracker):
            session_id = _start_stage2(client)
            started = time.time()
            client.post(
                f"/api/sessions/{session_id}/message", json={"message": "An answer."}
            )
            elapsed = time.time() - started

        # Parallel should land near CLASSIFIER_DELAY, serial near 2x. The
        # threshold sits well clear of both so the test is not decided by a few
        # milliseconds of scheduling noise.
        serial = CLASSIFIER_DELAY * 2
        threshold = serial * 0.75
        assert elapsed < threshold, (
            f"turn took {elapsed:.2f}s, threshold {threshold:.2f}s; serial "
            f"execution would be ~{serial:.2f}s"
        )


class TestRiskFlagAccumulation:
    def test_flags_are_appended_not_duplicated(self, client):
        """Guards the operator.add reducer against double-counting."""
        tracker = _ConcurrencyTracker(delay=0.0)

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   return_value=_primary()), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=tracker):
            session_id = _start_stage2(client)
            for i in range(3):
                client.post(
                    f"/api/sessions/{session_id}/message", json={"message": f"Answer {i}."}
                )

        flags = _state(session_id)["risk_flags"]
        assert len(flags) == 3, (
            f"expected exactly one flag per answer, got {len(flags)} — the node "
            f"returns only new flags and the reducer appends them"
        )

    def test_flags_carry_their_source_block(self, client):
        tracker = _ConcurrencyTracker(delay=0.0)

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   return_value=_primary()), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=tracker):
            session_id = _start_stage2(client)
            client.post(f"/api/sessions/{session_id}/message", json={"message": "Answer."})

        flag = _state(session_id)["risk_flags"][0]
        assert flag.source_block == "role_orientation"
        assert flag.source_question_index == 0


class TestRiskBranchNeverBlocksTheConversation:
    def test_classifier_failure_does_not_break_the_turn(self, client):
        """CLAUDE.md: a classifier failure must never block the main flow."""

        def flaky(*args, **kwargs):
            llm = MagicMock()

            def respond(messages):
                if "risk flag classifier" in messages[0].content:
                    raise RuntimeError("Haiku unavailable")
                response = MagicMock()
                response.content = NO_FOLLOWUP_JSON
                return response

            llm.invoke.side_effect = respond
            return llm

        with patch("agents.stage2_employee_interview.nodes._get_primary_llm",
                   return_value=_primary()), \
             patch("agents.stage2_employee_interview.nodes._get_classifier_llm",
                   side_effect=flaky):
            session_id = _start_stage2(client)
            response = client.post(
                f"/api/sessions/{session_id}/message", json={"message": "An answer."}
            )

        assert response.status_code == 200, "a risk classifier failure broke the turn"
        assert response.json()["message"], "no next question was produced"
        assert _state(session_id)["risk_flags"] == []
