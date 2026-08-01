"""Tolerant classifier JSON parsing (review finding M3).

Every classifier failure previously degraded silently: a fenced or prefaced
response raised JSONDecodeError, and the caller treated that identically to
"no follow-up needed" / "no risk flags found".
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.parsing import ClassifierParseError, extract_json


class TestExtractJson:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"needs_followup": true, "suggested_followup": "Which system?"}',
            '```json\n{"needs_followup": true, "suggested_followup": "Which system?"}\n```',
            '```\n{"needs_followup": true, "suggested_followup": "Which system?"}\n```',
            'Here is my assessment:\n{"needs_followup": true, "suggested_followup": "Which system?"}',
            '```JSON\n{"needs_followup": true, "suggested_followup": "Which system?"}\n```  ',
        ],
    )
    def test_object_recovered_from_every_common_wrapping(self, raw):
        result = extract_json(raw)
        assert result["needs_followup"] is True
        assert result["suggested_followup"] == "Which system?"

    @pytest.mark.parametrize(
        "raw",
        [
            '[{"flag_type": "single_point_of_failure"}]',
            '```json\n[{"flag_type": "single_point_of_failure"}]\n```',
            'Findings:\n[{"flag_type": "single_point_of_failure"}]',
        ],
    )
    def test_array_recovered(self, raw):
        result = extract_json(raw)
        assert isinstance(result, list)
        assert result[0]["flag_type"] == "single_point_of_failure"

    def test_empty_array_is_a_valid_negative_result(self):
        assert extract_json("[]") == []
        assert extract_json("```json\n[]\n```") == []

    def test_braces_inside_strings_do_not_break_extraction(self):
        raw = 'Note:\n{"description": "use the {placeholder} syntax", "severity": "high"}'
        assert extract_json(raw)["description"] == "use the {placeholder} syntax"

    def test_escaped_quote_inside_string_is_handled(self):
        raw = '{"description": "they call it \\"the beast\\"", "severity": "medium"}'
        assert extract_json(raw)["severity"] == "medium"

    @pytest.mark.parametrize("raw", ["", "   ", None, "no json here at all", "{unclosed"])
    def test_unrecoverable_input_raises_parse_error(self, raw):
        with pytest.raises(ClassifierParseError):
            extract_json(raw)


def _classifier_returning(text):
    llm = MagicMock()
    response = MagicMock()
    response.content = text
    llm.invoke.return_value = response
    return llm


def _history():
    return [
        AIMessage(content="Which systems do you own?"),
        HumanMessage(content="A few, mainly the billing platform."),
    ]


class TestFollowupClassifierUsesTolerantParsing:
    def test_fenced_response_now_produces_a_followup(self):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        fenced = '```json\n{"needs_followup": true, "reason": "vague", "suggested_followup": "Which billing platform?"}\n```'
        state = {
            "session_id": "s", "current_block": "technical_systems_tools",
            "current_question_index": 1, "followup_count": 0,
            "conversation_history": _history(),
        }

        with patch(
            "agents.stage2_employee_interview.nodes._get_classifier_llm",
            return_value=_classifier_returning(fenced),
        ):
            result = followup_classifier_node(state)

        assert result["pending_followup"] == "Which billing platform?", (
            "a fenced classifier response silently disabled follow-ups"
        )

    def test_unparseable_response_logs_at_error_level(self, caplog):
        from agents.stage2_employee_interview.nodes import followup_classifier_node

        state = {
            "session_id": "s", "current_block": "technical_systems_tools",
            "current_question_index": 1, "followup_count": 0,
            "conversation_history": _history(),
        }

        with patch(
            "agents.stage2_employee_interview.nodes._get_classifier_llm",
            return_value=_classifier_returning("I could not decide."),
        ):
            with caplog.at_level(logging.ERROR):
                result = followup_classifier_node(state)

        assert result["pending_followup"] is None
        assert any("UNPARSEABLE" in r.message for r in caplog.records), (
            "a parse failure must be distinguishable from a genuine 'no follow-up'"
        )


class TestRiskClassifierUsesTolerantParsing:
    def test_fenced_response_now_produces_risk_flags(self):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        fenced = json.dumps([{
            "flag_type": "single_point_of_failure",
            "severity": "critical",
            "description": "Only person who can run the month-end batch",
            "recommended_action": "Document and cross-train before departure",
        }])
        state = {
            "session_id": "s", "current_block": "internal_processes_workflows",
            "current_question_index": 2, "risk_flags": [],
            "conversation_history": _history(),
        }

        with patch(
            "agents.stage2_employee_interview.nodes._get_classifier_llm",
            return_value=_classifier_returning(f"```json\n{fenced}\n```"),
        ):
            result = risk_flag_classifier_node(state)

        assert len(result["risk_flags"]) == 1, (
            "a fenced classifier response silently disabled risk detection"
        )
        assert result["risk_flags"][0].flag_type.value == "single_point_of_failure"

    def test_unparseable_response_logs_at_error_level(self, caplog):
        from agents.stage2_employee_interview.nodes import risk_flag_classifier_node

        state = {
            "session_id": "s", "current_block": "internal_processes_workflows",
            "current_question_index": 2, "risk_flags": [],
            "conversation_history": _history(),
        }

        with patch(
            "agents.stage2_employee_interview.nodes._get_classifier_llm",
            return_value=_classifier_returning("No risks that I can see."),
        ):
            with caplog.at_level(logging.ERROR):
                result = risk_flag_classifier_node(state)

        assert result == {}
        assert any("UNPARSEABLE" in r.message for r in caplog.records)
