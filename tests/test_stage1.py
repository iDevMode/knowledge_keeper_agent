import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.stage1_business_interview.nodes import (
    advance_question_node,
    greeting_node,
    process_answer_node,
    route_after_advance,
    route_after_followup_classifier,
    route_after_profile_review,
    validate_single_question,
)
from config.constants import STAGE1_BLOCKS, STAGE1_BLOCK_QUESTION_COUNTS
from models.role_intelligence_profile import RoleIntelligenceProfile


# ---- Helpers ----

def _make_state(**overrides):
    """Create a minimal Stage1State dict with sensible defaults."""
    base = {
        "session_id": "test-session",
        "business_name": "",
        "current_block": "business_context",
        "current_question_index": 0,
        "answers": {},
        "conversation_history": [],
        "role_intelligence_profile": None,
        "profile_confirmed": False,
        "session_complete": False,
        "followup_count": 0,
        "pending_followup": None,
        "last_agent_message": "",
    }
    base.update(overrides)
    return base


# ---- TestSingleQuestionValidator ----

class TestSingleQuestionValidator:
    def test_single_question_passes(self):
        assert validate_single_question("What industry does your business operate in?") is True

    def test_no_question_passes(self):
        assert validate_single_question("Thank you for sharing that.") is True

    def test_multiple_questions_detected(self):
        text = "What industry are you in? And how large is your team?"
        assert validate_single_question(text) is False

    def test_three_questions_detected(self):
        text = "What do you do? How big is the team? Who leads it?"
        assert validate_single_question(text) is False


# ---- TestQuestionAdvancement ----

class TestQuestionAdvancement:
    def test_within_block_advance(self):
        """Advancing within a block increments question index."""
        state = _make_state(current_block="business_context", current_question_index=1)
        result = advance_question_node(state)
        assert result["current_question_index"] == 2
        assert "current_block" not in result  # block stays the same

    def test_cross_block_advance(self):
        """Reaching the end of a block advances to the next block."""
        # business_context has 5 questions (indices 0-4), so at index 4 we should advance
        last_index = STAGE1_BLOCK_QUESTION_COUNTS["business_context"] - 1
        state = _make_state(current_block="business_context", current_question_index=last_index)
        result = advance_question_node(state)
        assert result["current_block"] == "vacant_role"
        assert result["current_question_index"] == 0

    def test_all_blocks_complete(self):
        """Reaching the end of the last block sets __complete__."""
        last_block = STAGE1_BLOCKS[-1]
        last_index = STAGE1_BLOCK_QUESTION_COUNTS[last_block] - 1
        state = _make_state(current_block=last_block, current_question_index=last_index)
        result = advance_question_node(state)
        assert result["current_block"] == "__complete__"

    def test_block_order_is_correct(self):
        """Blocks advance in the correct order as defined in constants."""
        expected_order = [
            "business_context",
            "vacant_role",
            "replacement_profile",
            "knowledge_priorities",
            "output_preferences",
            "departure_sensitivity",
        ]
        assert STAGE1_BLOCKS == expected_order


# ---- TestGreetingNode ----

class TestGreetingNode:
    def test_greeting_sets_initial_state(self):
        state = _make_state()
        result = greeting_node(state)
        assert result["current_block"] == "business_context"
        assert result["current_question_index"] == 1  # greeting covers q0
        assert len(result["conversation_history"]) == 1
        assert isinstance(result["conversation_history"][0], AIMessage)
        assert "KnowledgeKeeper" in result["conversation_history"][0].content


# ---- TestProcessAnswerNode ----

class TestProcessAnswerNode:
    def test_stores_answer(self):
        state = _make_state(
            current_block="business_context",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="How is the team structured?"),
                HumanMessage(content="We have 3 teams of 5 people each."),
            ],
        )
        result = process_answer_node(state)
        assert result["answers"]["business_context.1"] == "We have 3 teams of 5 people each."

    def test_resets_followup_count(self):
        state = _make_state(
            current_block="vacant_role",
            current_question_index=0,
            followup_count=2,
            conversation_history=[HumanMessage(content="Software Engineer in Product team")],
        )
        result = process_answer_node(state)
        assert result["followup_count"] == 0


# ---- TestRouting ----

class TestRouting:
    def test_route_to_followup_when_pending(self):
        state = _make_state(pending_followup="Can you elaborate?", followup_count=0)
        assert route_after_followup_classifier(state) == "followup_question"

    def test_route_to_advance_when_no_followup(self):
        state = _make_state(pending_followup=None, followup_count=0)
        assert route_after_followup_classifier(state) == "advance_question"

    def test_route_to_advance_when_max_followups(self):
        state = _make_state(pending_followup="Can you elaborate?", followup_count=3)
        assert route_after_followup_classifier(state) == "advance_question"

    def test_route_to_profile_generation_when_complete(self):
        state = _make_state(current_block="__complete__")
        assert route_after_advance(state) == "profile_generation"

    def test_route_to_ask_question_when_not_complete(self):
        state = _make_state(current_block="vacant_role")
        assert route_after_advance(state) == "ask_question"

    def test_route_to_finalise_on_confirmation(self):
        state = _make_state(
            conversation_history=[HumanMessage(content="Looks good, no changes needed.")]
        )
        assert route_after_profile_review(state) == "finalise"

    def test_route_to_corrections_on_feedback(self):
        state = _make_state(
            conversation_history=[HumanMessage(content="The job title should be Senior Manager, not Manager.")]
        )
        assert route_after_profile_review(state) == "corrections"


# ---- TestRoleIntelligenceProfile ----

class TestRoleIntelligenceProfile:
    def test_valid_profile_parses(self):
        fixtures_path = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"
        with open(fixtures_path) as f:
            fixtures = json.load(f)

        for profile_id, data in fixtures.items():
            profile = RoleIntelligenceProfile.model_validate(data)
            assert profile.job_title
            assert profile.industry
            assert profile.priority_1

    def test_missing_required_field_raises(self):
        with pytest.raises(Exception):  # ValidationError
            RoleIntelligenceProfile.model_validate({
                "industry": "Tech",
                # Missing many required fields
            })

    def test_optional_fields_default_to_none(self):
        """Minimal valid profile with all required fields."""
        data = {
            "industry": "Technology",
            "team_structure": "Flat",
            "key_tools": ["Slack"],
            "culture_type": "informal",
            "job_title": "Engineer",
            "department": "Engineering",
            "tenure": "3 years",
            "reports_to": "CTO",
            "role_type": "mixed",
            "immediate_risk": "API knowledge loss",
            "hire_type": "external",
            "replacement_experience_level": "mid",
            "most_important_context": "API architecture",
            "success_definition_90_days": "Ship one feature independently",
            "priority_1": "technical_systems_tools",
            "priority_2": "internal_processes_workflows",
            "priority_3": "decision_making_logic",
            "document_destination": "Google Drive",
            "recipients": ["CTO"],
            "departure_type": "voluntary",
            "leaving_on_good_terms": "yes",
            "employee_aware": True,
            "notice_period": "4 weeks",
        }
        profile = RoleIntelligenceProfile.model_validate(data)
        assert profile.company_name is None
        assert profile.direct_reports is None
        assert profile.sensitivity_flags is None
        assert profile.agent_flags == []


# ---- TestAskQuestionNode (mocked LLM) ----

class TestAskQuestionNode:
    @patch("agents.stage1_business_interview.nodes._get_primary_llm")
    def test_ask_question_calls_llm(self, mock_get_llm):
        from agents.stage1_business_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="How is your team structured?")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_block="business_context",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="Welcome..."),
                HumanMessage(content="We're a manufacturing company."),
            ],
        )

        result = ask_question_node(state)
        assert result["last_agent_message"] == "How is your team structured?"
        assert mock_llm.invoke.called

    @patch("agents.stage1_business_interview.nodes._get_primary_llm")
    def test_ask_question_retries_on_multiple_questions(self, mock_get_llm):
        from agents.stage1_business_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        # First call returns multiple questions, second returns single
        mock_llm.invoke.side_effect = [
            MagicMock(content="How is the team structured? And who leads it?"),
            MagicMock(content="How is the team structured?"),
        ]
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_block="business_context",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="Welcome..."),
                HumanMessage(content="We're in fintech."),
            ],
        )

        result = ask_question_node(state)
        assert result["last_agent_message"] == "How is the team structured?"
        assert mock_llm.invoke.call_count == 2


# ---- TestFollowupClassifierNode (mocked LLM) ----

class TestFollowupClassifierNode:
    @patch("agents.stage1_business_interview.nodes._get_classifier_llm")
    def test_followup_needed(self, mock_get_llm):
        from agents.stage1_business_interview.nodes import followup_classifier_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='{"needs_followup": true, "reason": "vague", "suggested_followup": "Can you elaborate?"}'
        )
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            conversation_history=[
                AIMessage(content="What tools does the team use?"),
                HumanMessage(content="Various things."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] == "Can you elaborate?"

    @patch("agents.stage1_business_interview.nodes._get_classifier_llm")
    def test_no_followup_needed(self, mock_get_llm):
        from agents.stage1_business_interview.nodes import followup_classifier_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='{"needs_followup": false, "reason": "clear answer", "suggested_followup": ""}'
        )
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            conversation_history=[
                AIMessage(content="What industry are you in?"),
                HumanMessage(content="We are a B2B SaaS company in the HR tech space."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] is None

    @patch("agents.stage1_business_interview.nodes._get_classifier_llm")
    def test_classifier_failure_defaults_to_no_followup(self, mock_get_llm):
        from agents.stage1_business_interview.nodes import followup_classifier_node

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("API error")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            conversation_history=[
                AIMessage(content="What industry?"),
                HumanMessage(content="Tech."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] is None
