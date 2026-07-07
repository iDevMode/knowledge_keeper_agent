import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.stage2_employee_interview.nodes import (
    advance_question_node,
    greeting_node,
    load_profile_node,
    process_answer_node,
    route_after_advance,
    route_after_followup_classifier,
    validate_single_question,
)
from config.constants import (
    KnowledgeBlock,
    LIGHT_TOUCH_MAX_QUESTIONS,
    MAX_FOLLOWUPS_PER_QUESTION,
    STAGE2_BLOCK_QUESTION_COUNTS,
    STAGE2_CLOSING_QUESTION_COUNT,
    STAGE2_ROLE_ORIENTATION_QUESTION_COUNT,
)
from models.classifier_outputs import (
    AnswerAnalysis,
    DetectedEntity,
    DetectedRiskFlag,
    FollowupDecision,
)
from models.knowledge_blocks import determine_block_order_and_depth
from models.role_intelligence_profile import RoleIntelligenceProfile


def _make_structured_classifier(result):
    """Mock classifier LLM whose with_structured_output(...).invoke returns `result`."""
    mock_llm = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = result
    mock_llm.with_structured_output.return_value = structured
    return mock_llm


# ---- Helpers ----

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "sample_role_profiles.json"


def _load_profile(profile_id: str) -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        fixtures = json.load(f)
    return RoleIntelligenceProfile.model_validate(fixtures[profile_id])


def _make_state(**overrides):
    """Create a minimal Stage2State dict with sensible defaults."""
    profile = overrides.pop("profile", _load_profile("process_heavy"))
    base = {
        "session_id": "test-session",
        "stage1_session_id": "test-stage1-session",
        "profile": profile,
        "current_phase": "role_orientation",
        "current_block": "role_orientation",
        "current_question_index": 0,
        "current_block_index": 0,
        "block_order": [
            "internal_processes_workflows",
            "technical_systems_tools",
            "undocumented_workarounds",
            "supplier_vendor_relationships",
            "decision_making_logic",
        ],
        "block_depths": {
            "internal_processes_workflows": "full",
            "technical_systems_tools": "full",
            "undocumented_workarounds": "full",
            "supplier_vendor_relationships": "light",
            "decision_making_logic": "light",
        },
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "conversation_history": [],
        "risk_flags": [],
        "last_agent_message": "",
        "session_complete": False,
    }
    base.update(overrides)
    return base


# ---- TestAdvanceQuestionNode ----

class TestAdvanceQuestionNode:
    def test_within_role_orientation(self):
        """Advancing within role orientation increments question index."""
        state = _make_state(current_phase="role_orientation", current_question_index=1)
        result = advance_question_node(state)
        assert result["current_question_index"] == 2
        assert "current_phase" not in result

    def test_role_orientation_to_first_block(self):
        """Reaching end of role orientation transitions to first knowledge block."""
        state = _make_state(
            current_phase="role_orientation",
            current_question_index=STAGE2_ROLE_ORIENTATION_QUESTION_COUNT - 1,
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "knowledge_blocks"
        assert result["current_block"] == "internal_processes_workflows"
        assert result["current_question_index"] == 0
        assert result["current_block_index"] == 0

    def test_within_knowledge_block(self):
        """Advancing within a knowledge block increments question index."""
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="internal_processes_workflows",
            current_question_index=2,
            current_block_index=0,
        )
        result = advance_question_node(state)
        assert result["current_question_index"] == 3
        assert "current_block" not in result

    def test_full_depth_enforcement(self):
        """Full depth block uses all questions before advancing."""
        total = STAGE2_BLOCK_QUESTION_COUNTS[KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS]
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="internal_processes_workflows",
            current_question_index=total - 1,
            current_block_index=0,
        )
        result = advance_question_node(state)
        # Should move to next block
        assert result["current_block"] == "technical_systems_tools"
        assert result["current_question_index"] == 0

    def test_light_depth_enforcement(self):
        """Light depth block only runs LIGHT_TOUCH_MAX_QUESTIONS before advancing."""
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="supplier_vendor_relationships",
            current_question_index=LIGHT_TOUCH_MAX_QUESTIONS - 1,
            current_block_index=3,
        )
        result = advance_question_node(state)
        # Should move to next block since light touch max reached
        assert result["current_block"] == "decision_making_logic"
        assert result["current_question_index"] == 0

    def test_block_to_next_block(self):
        """Moving from one block to the next updates block index."""
        total = STAGE2_BLOCK_QUESTION_COUNTS[KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS]
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="technical_systems_tools",
            current_question_index=total - 1,
            current_block_index=1,
        )
        result = advance_question_node(state)
        assert result["current_block"] == "undocumented_workarounds"
        assert result["current_block_index"] == 2
        assert result["current_question_index"] == 0

    def test_last_block_to_closing(self):
        """Completing last knowledge block transitions to closing sequence."""
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="decision_making_logic",
            current_question_index=LIGHT_TOUCH_MAX_QUESTIONS - 1,
            current_block_index=4,  # last block
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "closing_sequence"
        assert result["current_block"] == "closing_sequence"
        assert result["current_question_index"] == 0

    def test_within_closing(self):
        """Advancing within closing increments question index."""
        state = _make_state(
            current_phase="closing_sequence",
            current_block="closing_sequence",
            current_question_index=1,
        )
        result = advance_question_node(state)
        assert result["current_question_index"] == 2
        assert "current_phase" not in result

    def test_closing_to_complete(self):
        """Reaching end of closing sets __complete__."""
        state = _make_state(
            current_phase="closing_sequence",
            current_block="closing_sequence",
            current_question_index=STAGE2_CLOSING_QUESTION_COUNT - 1,
        )
        result = advance_question_node(state)
        assert result["current_block"] == "__complete__"


# ---- TestRouting ----

class TestRouting:
    def test_route_to_followup_when_pending(self):
        state = _make_state(pending_followup="Can you elaborate?", followup_count=0)
        assert route_after_followup_classifier(state) == "followup_question"

    def test_route_to_advance_when_no_followup(self):
        state = _make_state(pending_followup=None, followup_count=0)
        assert route_after_followup_classifier(state) == "advance_question"

    def test_route_to_advance_when_max_followups(self):
        state = _make_state(pending_followup="Can you elaborate?", followup_count=MAX_FOLLOWUPS_PER_QUESTION)
        assert route_after_followup_classifier(state) == "advance_question"

    def test_route_to_session_complete(self):
        state = _make_state(current_block="__complete__")
        assert route_after_advance(state) == "session_complete"

    def test_route_to_ask_question(self):
        state = _make_state(current_block="internal_processes_workflows")
        assert route_after_advance(state) == "ask_question"


# ---- TestLoadProfileNode ----

class TestLoadProfileNode:
    @patch("agents.stage2_employee_interview.nodes.get_session_store")
    def test_loads_profile_and_computes_block_order(self, mock_get_store):
        profile = _load_profile("process_heavy")
        mock_store = MagicMock()
        mock_store.get_profile.return_value = profile
        mock_get_store.return_value = mock_store

        state = _make_state(stage1_session_id="s1-123", profile=None)
        result = load_profile_node(state)

        assert result["profile"] == profile
        assert isinstance(result["block_order"], list)
        assert len(result["block_order"]) > 0
        assert isinstance(result["block_depths"], dict)
        mock_store.get_profile.assert_called_once_with("s1-123")

    @patch("agents.stage2_employee_interview.nodes.get_session_store")
    def test_raises_on_missing_profile(self, mock_get_store):
        mock_store = MagicMock()
        mock_store.get_profile.return_value = None
        mock_get_store.return_value = mock_store

        state = _make_state(stage1_session_id="nonexistent", profile=None)
        with pytest.raises(ValueError, match="No profile found"):
            load_profile_node(state)


# ---- TestRiskFlagClassifierNode ----

class TestRiskFlagClassifierNode:
    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_detects_flag(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(AnswerAnalysis(flags=[
            DetectedRiskFlag(
                flag_type="single_point_of_failure",
                severity="critical",
                description="Only person who knows SAP workflow",
                recommended_action="Document before departure",
            ),
        ]))

        state = _make_state(
            current_block="internal_processes_workflows",
            current_question_index=0,
            conversation_history=[
                AIMessage(content="What processes do you own?"),
                HumanMessage(content="I'm the only one who knows how to run the SAP batch scheduling."),
            ],
        )
        result = risk_flag_classifier_node(state)
        assert len(result["risk_flags"]) == 1
        assert result["risk_flags"][0].flag_type.value == "single_point_of_failure"
        assert result["risk_flags"][0].severity.value == "critical"

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_no_flags_returns_empty(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(AnswerAnalysis(flags=[]))

        state = _make_state(
            current_block="role_orientation",
            current_question_index=0,
            conversation_history=[
                AIMessage(content="Tell me about your role."),
                HumanMessage(content="I'm a project manager. I coordinate work across 3 teams."),
            ],
        )
        result = risk_flag_classifier_node(state)
        assert result["risk_flags"] == []

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_failure_returns_unchanged(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.invoke.side_effect = Exception("API error")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_block="role_orientation",
            current_question_index=0,
            conversation_history=[
                AIMessage(content="Tell me about your role."),
                HumanMessage(content="I do a lot of things."),
            ],
        )
        result = risk_flag_classifier_node(state)
        # Should return empty dict (no changes to risk_flags)
        assert "risk_flags" not in result

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_multiple_flags(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(AnswerAnalysis(flags=[
            DetectedRiskFlag(
                flag_type="single_point_of_failure",
                severity="critical",
                description="Only admin for SAP",
                recommended_action="Transfer admin access",
            ),
            DetectedRiskFlag(
                flag_type="access_credential_gap",
                severity="high",
                description="Holds sole Power BI credentials",
                recommended_action="Share credentials",
            ),
        ]))

        state = _make_state(
            current_block="technical_systems_tools",
            current_question_index=4,
            conversation_history=[
                AIMessage(content="Do you have any access that isn't shared?"),
                HumanMessage(content="I'm the only admin for SAP and I have the only Power BI login."),
            ],
        )
        result = risk_flag_classifier_node(state)
        assert len(result["risk_flags"]) == 2


# ---- TestAskQuestionNode ----

class TestAskQuestionNode:
    @patch("agents.stage2_employee_interview.nodes._get_primary_llm")
    def test_role_orientation_question(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="What does a typical week look like for you?")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_phase="role_orientation",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="Hello, tell me about your role..."),
                HumanMessage(content="I coordinate production schedules."),
            ],
        )
        result = ask_question_node(state)
        assert result["last_agent_message"] == "What does a typical week look like for you?"

    @patch("agents.stage2_employee_interview.nodes._get_primary_llm")
    def test_knowledge_block_question(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="Can you walk me through the key processes you own?")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="internal_processes_workflows",
            current_question_index=0,
            conversation_history=[
                AIMessage(content="Let's talk about your processes."),
                HumanMessage(content="Sure, happy to."),
            ],
        )
        result = ask_question_node(state)
        assert result["last_agent_message"] == "Can you walk me through the key processes you own?"

    @patch("agents.stage2_employee_interview.nodes._get_primary_llm")
    def test_closing_question(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="Is there anything important about this role that I haven't covered?"
        )
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_phase="closing_sequence",
            current_block="closing_sequence",
            current_question_index=0,
            conversation_history=[
                AIMessage(content="We're nearing the end now."),
                HumanMessage(content="OK."),
            ],
        )
        result = ask_question_node(state)
        assert "haven't covered" in result["last_agent_message"]

    @patch("agents.stage2_employee_interview.nodes._get_primary_llm")
    def test_retries_on_multiple_questions(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import ask_question_node

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [
            MagicMock(content="What do you do? And what's your typical week?"),
            MagicMock(content="What does a typical week look like for you?"),
        ]
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            current_phase="role_orientation",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="Tell me about your role."),
                HumanMessage(content="I'm an ops coordinator."),
            ],
        )
        result = ask_question_node(state)
        assert result["last_agent_message"] == "What does a typical week look like for you?"
        assert mock_llm.invoke.call_count == 2


# ---- TestFollowupClassifierNode ----

class TestFollowupClassifierNode:
    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_followup_needed(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(
            FollowupDecision(needs_followup=True, reason="vague", suggested_followup="Can you be more specific?")
        )

        state = _make_state(
            conversation_history=[
                AIMessage(content="What processes do you own?"),
                HumanMessage(content="Various things."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] == "Can you be more specific?"

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_no_followup(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(
            FollowupDecision(needs_followup=False, reason="clear answer", suggested_followup="")
        )

        state = _make_state(
            conversation_history=[
                AIMessage(content="What does your typical week look like?"),
                HumanMessage(content="Monday I do the production schedule, Tuesday is quality reviews..."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] is None

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_classifier_failure_defaults(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.invoke.side_effect = Exception("API error")
        mock_get_llm.return_value = mock_llm

        state = _make_state(
            conversation_history=[
                AIMessage(content="What do you do?"),
                HumanMessage(content="Stuff."),
            ],
        )
        result = followup_classifier_node(state)
        assert result["pending_followup"] is None


# ---- TestBlockOrderIntegration ----

class TestBlockOrderIntegration:
    def test_process_heavy_priorities(self):
        """Process-heavy profile prioritises processes, tech, workarounds."""
        profile = _load_profile("process_heavy")
        ordered, depths = determine_block_order_and_depth(
            profile.priority_1, profile.priority_2, profile.priority_3,
            profile.supporting_categories,
        )
        # Top 3 should be full depth
        assert ordered[0] == KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS
        assert ordered[1] == KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS
        assert ordered[2] == KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS
        assert depths[KnowledgeBlock.INTERNAL_PROCESSES_WORKFLOWS].value == "full"
        assert depths[KnowledgeBlock.TECHNICAL_SYSTEMS_TOOLS].value == "full"
        assert depths[KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS].value == "full"

    def test_decision_heavy_priorities(self):
        """Decision-heavy profile prioritises decisions, clients, strategy."""
        profile = _load_profile("decision_heavy")
        ordered, depths = determine_block_order_and_depth(
            profile.priority_1, profile.priority_2, profile.priority_3,
            profile.supporting_categories,
        )
        assert ordered[0] == KnowledgeBlock.DECISION_MAKING_LOGIC
        assert ordered[1] == KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS
        assert ordered[2] == KnowledgeBlock.STRATEGIC_CONTEXT
        assert depths[KnowledgeBlock.DECISION_MAKING_LOGIC].value == "full"
        # Supporting categories should be light
        assert depths[KnowledgeBlock.REGULATORY_COMPLIANCE].value == "light"
        assert depths[KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT].value == "light"
        # Undocumented workarounds always full
        assert depths[KnowledgeBlock.UNDOCUMENTED_WORKAROUNDS].value == "full"

    def test_relationship_heavy_priorities(self):
        """Relationship-heavy profile prioritises clients, team, strategy."""
        profile = _load_profile("relationship_heavy")
        ordered, depths = determine_block_order_and_depth(
            profile.priority_1, profile.priority_2, profile.priority_3,
            profile.supporting_categories,
        )
        assert ordered[0] == KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS
        assert ordered[1] == KnowledgeBlock.TEAM_DYNAMICS_MANAGEMENT
        assert ordered[2] == KnowledgeBlock.STRATEGIC_CONTEXT
        assert depths[KnowledgeBlock.CLIENT_STAKEHOLDER_RELATIONSHIPS].value == "full"


# ---- TestGreetingNode ----

class TestGreetingNode:
    def test_greeting_sets_initial_state(self):
        state = _make_state()
        result = greeting_node(state)
        assert result["current_phase"] == "role_orientation"
        assert result["current_block"] == "role_orientation"
        assert result["current_question_index"] == 1  # greeting covers q0
        assert len(result["conversation_history"]) == 1
        assert isinstance(result["conversation_history"][0], AIMessage)
        assert "KnowledgeKeeper" in result["conversation_history"][0].content

    def test_greeting_adapts_tone_for_involuntary(self):
        profile = _load_profile("relationship_heavy")  # involuntary departure
        state = _make_state(profile=profile)
        result = greeting_node(state)
        assert "straightforward and comfortable" in result["last_agent_message"]

    def test_greeting_adapts_tone_for_voluntary(self):
        profile = _load_profile("process_heavy")  # voluntary, no sensitivity
        state = _make_state(profile=profile)
        result = greeting_node(state)
        assert "real difference" in result["last_agent_message"]


# ---- TestProcessAnswerNode ----

class TestProcessAnswerNode:
    def test_stores_answer_as_list(self):
        state = _make_state(
            current_block="internal_processes_workflows",
            current_question_index=1,
            conversation_history=[
                AIMessage(content="What triggers this process?"),
                HumanMessage(content="An email from the production manager."),
            ],
        )
        result = process_answer_node(state)
        assert result["answers"]["internal_processes_workflows.1"] == ["An email from the production manager."]

    def test_followup_answer_appends_not_overwrites(self):
        """Follow-up answers land on the same key — they must accumulate."""
        state = _make_state(
            current_block="internal_processes_workflows",
            current_question_index=1,
            answers={"internal_processes_workflows.1": ["An email from the production manager."]},
            conversation_history=[
                AIMessage(content="What happens if that email never arrives?"),
                HumanMessage(content="I chase the manager directly — there's no formal fallback."),
            ],
        )
        result = process_answer_node(state)
        assert result["answers"]["internal_processes_workflows.1"] == [
            "An email from the production manager.",
            "I chase the manager directly — there's no formal fallback.",
        ]

    def test_does_not_reset_followup_count(self):
        """Resetting here would defeat the MAX_FOLLOWUPS limit — this node also
        processes follow-up answers."""
        state = _make_state(
            current_block="role_orientation",
            current_question_index=0,
            followup_count=2,
            conversation_history=[HumanMessage(content="I coordinate production.")],
        )
        result = process_answer_node(state)
        assert "followup_count" not in result


# ---- Phase 2: entity memory ----

class TestEntityMemory:
    def _entity(self, name="Traxis", probed=False, importance="high"):
        return {
            "name": name, "entity_type": "system", "importance": importance,
            "context": "runs the schedule", "source_block": "technical_systems_tools",
            "mention_count": 1, "probed": probed,
        }

    def test_merge_new_and_repeat_mentions(self):
        from models.entity_memory import merge_entities

        detected = [DetectedEntity(name="Traxis", entity_type="system", importance="medium")]
        merged = merge_entities({}, detected, "technical_systems_tools")
        assert merged["traxis"]["mention_count"] == 1

        again = [DetectedEntity(name="traxis", entity_type="system", importance="high", context="sole admin")]
        merged = merge_entities(merged, again, "undocumented_workarounds")
        assert merged["traxis"]["mention_count"] == 2
        assert merged["traxis"]["importance"] == "high"  # upgraded
        assert merged["traxis"]["context"] == "sole admin"

    def test_mark_probed_by_agent_question(self):
        from models.entity_memory import mark_probed

        entities = {"traxis": self._entity()}
        updated = mark_probed(entities, "Can you tell me more about Traxis and who administers it?")
        assert updated["traxis"]["probed"] is True

    def test_unprobed_sorted_by_importance(self):
        from models.entity_memory import unprobed_entities

        entities = {
            "a": self._entity(name="A", importance="low"),
            "b": self._entity(name="B", importance="high"),
            "c": self._entity(name="C", probed=True, importance="high"),
        }
        names = [e.name for e in unprobed_entities(entities)]
        assert names == ["B", "A"]  # probed C excluded, high first

    @patch("agents.stage2_employee_interview.nodes._get_classifier_llm")
    def test_classifier_merges_entities_into_state(self, mock_get_llm):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        mock_get_llm.return_value = _make_structured_classifier(AnswerAnalysis(
            flags=[],
            entities=[DetectedEntity(name="Priya", entity_type="person", importance="high", context="unblocks Northgate")],
        ))
        state = _make_state(
            current_block="client_stakeholder_relationships",
            conversation_history=[
                AIMessage(content="Who do you work with most?"),
                HumanMessage(content="Half my job is knowing when to ping Priya."),
            ],
            entities={},
        )
        result = risk_flag_classifier_node(state)
        assert result["entities"]["priya"]["name"] == "Priya"
        assert result["entities"]["priya"]["probed"] is False

    def test_advance_transitions_to_entity_sweep(self):
        """After the last block, unprobed high-importance entities get a sweep."""
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="decision_making_logic",
            current_question_index=LIGHT_TOUCH_MAX_QUESTIONS - 1,
            current_block_index=4,  # last block
            entities={"traxis": self._entity()},
            question_count=10,
            question_budget=45,
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "entity_sweep"
        assert len(result["sweep_questions"]) == 1
        assert "Traxis" in result["sweep_questions"][0]

    def test_no_sweep_when_all_probed(self):
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="decision_making_logic",
            current_question_index=LIGHT_TOUCH_MAX_QUESTIONS - 1,
            current_block_index=4,
            entities={"traxis": self._entity(probed=True)},
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "closing_sequence"

    def test_sweep_advances_to_closing(self):
        state = _make_state(
            current_phase="entity_sweep",
            current_block="entity_sweep",
            current_question_index=0,
            sweep_questions=["Ask about Traxis"],
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "closing_sequence"


# ---- Phase 2: information-gain follow-ups ----

class TestInformationGainFollowups:
    def _state_with_qa(self, **overrides):
        return _make_state(
            conversation_history=[
                AIMessage(content="What processes do you own?"),
                HumanMessage(content="A few bits and pieces, it mostly just works."),
            ],
            **overrides,
        )

    def _run(self, decision, **state_overrides):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        with patch("agents.stage2_employee_interview.nodes._get_classifier_llm") as mock_get:
            mock_get.return_value = _make_structured_classifier(decision)
            return followup_classifier_node(self._state_with_qa(**state_overrides))

    def test_low_gain_suppressed(self):
        result = self._run(FollowupDecision(
            needs_followup=True, information_gain="low", reason="rephrasing",
            suggested_followup="Anything else?",
        ))
        assert result["pending_followup"] is None

    def test_refusal_suppressed(self):
        result = self._run(FollowupDecision(
            needs_followup=True, is_refusal=True, information_gain="high",
            reason="declined", suggested_followup="Are you sure?",
        ))
        assert result["pending_followup"] is None

    def test_medium_gain_allowed_under_budget(self):
        result = self._run(
            FollowupDecision(
                needs_followup=True, information_gain="medium", reason="incomplete",
                suggested_followup="What triggers that?",
            ),
            question_count=5, question_budget=40,
        )
        assert result["pending_followup"] == "What triggers that?"

    def test_medium_gain_suppressed_over_budget(self):
        result = self._run(
            FollowupDecision(
                needs_followup=True, information_gain="medium", reason="incomplete",
                suggested_followup="What triggers that?",
            ),
            question_count=41, question_budget=40,
        )
        assert result["pending_followup"] is None

    def test_high_gain_allowed_over_budget(self):
        result = self._run(
            FollowupDecision(
                needs_followup=True, information_gain="high", reason="undocumented process",
                suggested_followup="Who else can run that?",
            ),
            question_count=41, question_budget=40,
        )
        assert result["pending_followup"] == "Who else can run that?"

    def test_captured_context_in_prompt(self):
        from agents.stage2_employee_interview.prompts import build_captured_context

        answers = {"internal_processes_workflows.0": ["I run the monthly close."]}
        ctx = build_captured_context(answers, "internal_processes_workflows")
        assert "monthly close" in ctx
        assert build_captured_context({}, "internal_processes_workflows") == "(nothing captured yet)"


# ---- Phase 2: turn budget & progress ----

class TestTurnBudget:
    def test_budget_computed_from_plan(self):
        from agents.stage2_employee_interview.nodes import (
            compute_question_budget,
            planned_question_total,
        )

        order = ["internal_processes_workflows", "supplier_vendor_relationships"]
        depths = {"internal_processes_workflows": "full", "supplier_vendor_relationships": "light"}
        planned = planned_question_total(order, depths)
        # 5 orientation + 7 full + 3 light + 4 closing
        assert planned == 19
        assert compute_question_budget(order, depths) > planned

    def test_hard_cap_jumps_to_closing(self):
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="internal_processes_workflows",
            current_question_index=1,
            current_block_index=0,
            question_count=100,
            question_budget=40,
        )
        result = advance_question_node(state)
        assert result["current_phase"] == "closing_sequence"

    def test_light_block_shrinks_over_budget(self):
        """Over the soft budget a light block stops after its first question."""
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="supplier_vendor_relationships",
            current_question_index=0,
            current_block_index=3,
            question_count=45,
            question_budget=40,
        )
        result = advance_question_node(state)
        assert result["current_block"] == "decision_making_logic"

    def test_light_block_full_length_under_budget(self):
        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="supplier_vendor_relationships",
            current_question_index=0,
            current_block_index=3,
            question_count=10,
            question_budget=40,
        )
        result = advance_question_node(state)
        assert result["current_question_index"] == 1
        assert "current_block" not in result

    def test_progress_shape(self):
        from agents.stage2_employee_interview.nodes import compute_progress

        state = _make_state(
            current_phase="knowledge_blocks",
            current_block="technical_systems_tools",
            current_question_index=2,
            current_block_index=1,
            question_count=12,
            question_budget=45,
        )
        progress = compute_progress(state)
        assert progress["section_number"] == 3  # orientation + first block done
        assert progress["section_total"] == 7   # orientation + 5 blocks + closing
        assert 0 < progress["percent"] < 100
        assert progress["question_budget"] == 45
