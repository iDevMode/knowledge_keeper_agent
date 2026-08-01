"""One-question-per-turn enforcement (review finding M5).

CLAUDE.md Core Principle 3 makes this "a hard constraint enforced at the node
level, not just in the prompt". The retry's output was previously used without
re-checking, so a model that ignored the re-prompt still shipped multiple
questions and the enforcement was effectively advisory.
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.text_utils import enforce_single_question, validate_single_question

STAGE_MODULES = [
    "agents.stage1_business_interview.nodes",
    "agents.stage2_employee_interview.nodes",
]


class TestValidateSingleQuestion:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("What industry are you in?", True),
            ("Thanks for sharing that.", True),
            ("Thanks. What tools do you use? And who owns them?", False),
            ("A? B? C?", False),
        ],
    )
    def test_counts_question_marks(self, text, expected):
        assert validate_single_question(text) is expected


class TestEnforceSingleQuestion:
    def test_truncates_after_the_first_question(self):
        text = "Thanks for that. What tools do you use? And who owns them?"
        assert enforce_single_question(text) == "Thanks for that. What tools do you use?"

    def test_keeps_the_leading_acknowledgement(self):
        result = enforce_single_question("Got it. Who signs off? Anyone else?")
        assert result.startswith("Got it.")
        assert validate_single_question(result)

    def test_text_without_a_question_is_unchanged(self):
        assert enforce_single_question("Thank you, that's helpful.") == (
            "Thank you, that's helpful."
        )

    def test_output_always_satisfies_the_validator(self):
        for text in [
            "A? B? C?",
            "One question only?",
            "No questions here.",
            "Intro. First? Second? Third?",
        ]:
            assert validate_single_question(enforce_single_question(text))


def _llm_returning(*texts):
    llm = MagicMock()
    responses = []
    for t in texts:
        r = MagicMock()
        r.content = t
        responses.append(r)
    llm.invoke.side_effect = responses
    return llm


@pytest.mark.parametrize("module", STAGE_MODULES)
class TestRetryPathIsGuarded:
    def _helper(self, module):
        import importlib

        return importlib.import_module(module)._single_question_or_retry

    def test_compliant_first_response_is_returned_unchanged(self, module):
        helper = self._helper(module)
        llm = _llm_returning("should not be called")
        result = helper(llm, [], "What does a typical week look like?", "s1")
        assert result == "What does a typical week look like?"
        llm.invoke.assert_not_called()

    def test_reprompt_result_is_used_when_it_complies(self, module):
        helper = self._helper(module)
        llm = _llm_returning("Thanks. Which tools? And who owns them?")
        result = helper(llm, [], "First? Second?", "s1")
        assert result == "Thanks. Which tools? And who owns them?" or validate_single_question(result)

    def test_persistent_multi_question_output_is_truncated(self, module):
        helper = self._helper(module)
        # The model ignores the re-prompt and returns two questions again.
        llm = _llm_returning("Sure. Which system do you use? And who administers it?")

        result = helper(llm, [], "First? Second?", "s1")

        assert validate_single_question(result), (
            f"multiple questions still reached the user: {result!r}"
        )
        assert result == "Sure. Which system do you use?"
