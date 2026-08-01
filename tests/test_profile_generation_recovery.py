"""Profile generation failure handling (review finding H4).

RoleIntelligenceProfile has 22 required fields. If the manager skips or answers
"I don't know" to enough questions, with_structured_output raises and the node
previously re-raised, surfacing an unhandled 500 mid-conversation.

CLAUDE.md requires the opposite: return the specific validation errors and ask
the manager to clarify, and never silently fill defaults.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config.constants import MAX_PROFILE_GENERATION_ATTEMPTS
from models.role_intelligence_profile import RoleIntelligenceProfile

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"
MAX_INTERVIEW_TURNS = 40


def _load_profile(profile_id: str = "process_heavy") -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _validation_error() -> ValidationError:
    """A genuine ValidationError with several required fields missing."""
    try:
        RoleIntelligenceProfile.model_validate({"industry": "Logistics"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected the partial profile to fail validation")


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


def _make_llms(structured_side_effect):
    primary = MagicMock()
    text = MagicMock()
    text.content = "Thanks. And the next detail?"
    primary.invoke.return_value = text

    structured = MagicMock()
    structured.invoke.side_effect = structured_side_effect
    primary.with_structured_output.return_value = structured

    classifier = MagicMock()
    cls_response = MagicMock()
    cls_response.content = json.dumps(
        {"needs_followup": False, "reason": "clear", "suggested_followup": ""}
    )
    classifier.invoke.return_value = cls_response
    return primary, classifier


def _patched(primary, classifier):
    stack = ExitStack()
    stack.enter_context(
        patch("agents.stage1_business_interview.nodes._get_primary_llm", return_value=primary)
    )
    stack.enter_context(
        patch("agents.stage1_business_interview.nodes._get_classifier_llm", return_value=classifier)
    )
    return stack


def _drive_to_generation(client: TestClient, session_id: str) -> dict:
    """Answer questions until the agent stops asking interview questions."""
    last = None
    for i in range(MAX_INTERVIEW_TURNS):
        res = client.post(
            f"/api/sessions/{session_id}/message", json={"message": f"Answer {i}."}
        )
        assert res.status_code == 200, f"got {res.status_code}: {res.text}"
        last = res.json()
        if "ROLE INTELLIGENCE PROFILE" in last["message"] or "could not pin down" in last["message"]:
            return last
    raise AssertionError("never reached profile generation")


class TestProfileGenerationFailureAsksInsteadOfCrashing:
    def test_validation_failure_returns_a_clarifying_question(self, client):
        # Always fails: both the first attempt and the internal retry.
        primary, classifier = _make_llms(_validation_error())

        with _patched(primary, classifier):
            session_id = client.post("/api/sessions/stage1").json()["session_id"]
            result = _drive_to_generation(client, session_id)

        assert "could not pin down" in result["message"], (
            f"expected a clarifying question, got: {result['message'][:200]}"
        )
        assert result["session_complete"] is False

    def test_clarifying_question_names_the_missing_fields(self, client):
        primary, classifier = _make_llms(_validation_error())

        with _patched(primary, classifier):
            session_id = client.post("/api/sessions/stage1").json()["session_id"]
            result = _drive_to_generation(client, session_id)

        # Manager-readable labels, not raw field names or a stack trace.
        assert "The job title of the departing role" in result["message"]
        assert "The notice period you are working within" in result["message"]
        assert "Traceback" not in result["message"]

    def test_manager_reply_triggers_regeneration_and_succeeds(self, client):
        # Fail the first generation (2 calls: attempt + retry), then succeed.
        profile = _load_profile()
        primary, classifier = _make_llms(
            [_validation_error(), _validation_error(), profile]
        )

        with _patched(primary, classifier):
            session_id = client.post("/api/sessions/stage1").json()["session_id"]
            _drive_to_generation(client, session_id)

            recovered = client.post(
                f"/api/sessions/{session_id}/message",
                json={"message": "Job title is Ops Manager, notice period is 3 months."},
            )

        assert recovered.status_code == 200
        assert "ROLE INTELLIGENCE PROFILE" in recovered.json()["message"], (
            "supplying the missing details should produce the profile review"
        )

    def test_repeated_failure_ends_gracefully_rather_than_looping(self, client):
        primary, classifier = _make_llms(_validation_error())

        with _patched(primary, classifier):
            session_id = client.post("/api/sessions/stage1").json()["session_id"]
            _drive_to_generation(client, session_id)

            last = None
            for _ in range(MAX_PROFILE_GENERATION_ATTEMPTS + 2):
                res = client.post(
                    f"/api/sessions/{session_id}/message",
                    json={"message": "I genuinely do not know."},
                )
                if res.status_code == 400:
                    break  # session closed; further messages rejected
                assert res.status_code == 200, f"got {res.status_code}: {res.text}"
                last = res.json()
                if last["session_complete"]:
                    break

        assert last is not None
        assert "administrator" in last["message"], (
            f"expected a graceful hand-off message, got: {last['message'][:200]}"
        )
