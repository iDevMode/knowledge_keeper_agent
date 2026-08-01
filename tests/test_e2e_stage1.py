"""End-to-end Stage 1 tests driving the real graph through the API.

These exercise the full interrupt/resume cycle rather than calling routing
functions in isolation, which is how the manager-review checkpoint regression
(review finding C1) went undetected.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.api_client import AuthedTestClient

from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"

# Upper bound on interview turns before the profile review must appear. Kept
# deliberately loose so these tests do not need updating when question counts or
# indexing change; the assertion is that review IS reached, not exactly when.
MAX_INTERVIEW_TURNS = 40

REVIEW_MARKER = "ROLE INTELLIGENCE PROFILE"


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        fixtures = json.load(f)
    return RoleIntelligenceProfile.model_validate(fixtures[profile_id])


def _make_primary_llm() -> MagicMock:
    """Primary LLM: plain text for questions, a real profile for structured output."""
    llm = MagicMock()
    response = MagicMock()
    response.content = "Thanks for that. What comes next?"
    llm.invoke.return_value = response

    structured = MagicMock()
    structured.invoke.return_value = _load_profile()
    llm.with_structured_output.return_value = structured
    return llm


def _make_classifier_llm() -> MagicMock:
    """Classifier LLM: never requests a follow-up."""
    llm = MagicMock()
    response = MagicMock()
    response.content = json.dumps(
        {"needs_followup": False, "reason": "clear", "suggested_followup": ""}
    )
    llm.invoke.return_value = response
    return llm


@pytest.fixture(autouse=True)
def reset_singletons():
    import api.routes as routes_mod
    import api.session_manager as sm_mod

    sm_mod._store = None
    routes_mod._registry = routes_mod.GraphRegistry()
    routes_mod._document_store = {}
    yield


@pytest.fixture
def stage1_llms():
    primary = _make_primary_llm()
    classifier = _make_classifier_llm()
    patches = [
        patch("agents.stage1_business_interview.nodes._get_primary_llm", return_value=primary),
        patch("agents.stage1_business_interview.nodes._get_classifier_llm", return_value=classifier),
    ]
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield primary, classifier


@pytest.fixture
def client():
    from api.routes import app
    return AuthedTestClient(app)


def _answer(client: AuthedTestClient, session_id: str, text: str) -> dict:
    res = client.post(f"/api/sessions/{session_id}/message", json={"message": text})
    assert res.status_code == 200, f"unexpected {res.status_code}: {res.text}"
    return res.json()


def _run_interview_until_review(client: AuthedTestClient, session_id: str) -> dict:
    """Answer questions until the profile review appears. Returns that response."""
    for i in range(MAX_INTERVIEW_TURNS):
        # Neutral replies: neither a confirmation nor a correction instruction.
        res = _answer(client, session_id, f"Answer number {i} with some detail.")
        if REVIEW_MARKER in res["message"]:
            return res
    raise AssertionError(
        f"profile review never presented after {MAX_INTERVIEW_TURNS} turns"
    )


class TestStage1ManagerReviewCheckpoint:
    """The manager MUST see the profile and confirm it before Stage 1 completes."""

    def test_profile_review_is_presented_and_session_pauses(self, client, stage1_llms):
        session_id = client.post("/api/sessions/stage1").json()["session_id"]

        review = _run_interview_until_review(client, session_id)

        assert review["session_complete"] is False, (
            "Stage 1 completed without waiting for the manager to confirm the profile"
        )

    def test_confirmation_after_review_completes_session(self, client, stage1_llms):
        session_id = client.post("/api/sessions/stage1").json()["session_id"]
        _run_interview_until_review(client, session_id)

        final = _answer(client, session_id, "Looks good, nothing to change.")

        assert final["session_complete"] is True
        assert final["profile"] is not None, "confirmed profile was not returned"

    def test_profile_is_persisted_on_confirmation(self, client, stage1_llms):
        session_id = client.post("/api/sessions/stage1").json()["session_id"]
        _run_interview_until_review(client, session_id)
        _answer(client, session_id, "Looks good, nothing to change.")

        from api.session_manager import get_session_store

        stored = get_session_store().get_profile(session_id)
        assert stored is not None, "profile was not stored for Stage 2 handoff"
        assert stored.job_title == _load_profile().job_title


class TestStage1AnswerIndexing:
    """Every question asked must be recorded under its own index (finding M2)."""

    def _state(self, session_id):
        from api.routes import _registry

        instance = _registry.get(session_id)
        return instance.graph.get_state(instance.config).values

    def test_first_answer_is_filed_at_index_zero(self, client, stage1_llms):
        session_id = client.post("/api/sessions/stage1").json()["session_id"]

        # The greeting contains business_context question 0, so this answer
        # belongs at index 0. Filing it at 1 also skipped question 1 entirely.
        _answer(client, session_id, "We are a logistics firm in the Midlands.")

        answers = self._state(session_id)["answers"]
        assert "business_context.0" in answers, (
            f"first answer misfiled; keys were {sorted(answers)}"
        )

    def test_no_question_index_is_skipped_in_the_first_block(self, client, stage1_llms):
        from config.constants import STAGE1_BLOCK_QUESTION_COUNTS

        session_id = client.post("/api/sessions/stage1").json()["session_id"]

        expected = STAGE1_BLOCK_QUESTION_COUNTS["business_context"]
        for i in range(expected):
            _answer(client, session_id, f"Business context answer {i}.")

        answers = self._state(session_id)["answers"]
        recorded = sorted(
            int(k.split(".")[1]) for k in answers if k.startswith("business_context.")
        )
        assert recorded == list(range(expected)), (
            f"expected contiguous indices 0..{expected - 1}, got {recorded}"
        )
