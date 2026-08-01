"""Routing after the Stage 1 profile review (review finding H1).

The router decides whether the manager confirmed the Role Intelligence Profile
or asked for changes. Getting this wrong in the finalise direction silently
discards the manager's edit and completes the session irreversibly, so the
router is deliberately biased towards corrections.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agents.stage1_business_interview.nodes import route_after_profile_review


def _route(reply: str | None) -> str:
    history = [AIMessage(content="**ROLE INTELLIGENCE PROFILE** ...")]
    if reply is not None:
        history.append(HumanMessage(content=reply))
    return route_after_profile_review({"conversation_history": history})


class TestConfirmations:
    @pytest.mark.parametrize(
        "reply",
        [
            "Looks good, nothing to change.",
            "Yes that is all correct",
            "Perfect",
            "Yes",
            "Yep, all good",
            "That's right",
            "That is right",
            "No corrections",
            "No changes",
            "lgtm",
            "Sounds good",
            "Spot on",
        ],
    )
    def test_clean_confirmation_finalises(self, reply):
        assert _route(reply) == "finalise"


class TestCorrections:
    @pytest.mark.parametrize(
        "reply",
        [
            # The regression case: an affirmation carrying an edit. Substring
            # matching on "yes" previously routed these to finalise and dropped
            # the correction entirely.
            "yes, but change the job title to Senior Analyst",
            "Yes - although the tenure should be 6 years not 3",
            "Correct, but you are missing the compliance piece",
            "Mostly right, but she actually reports to the COO",
            "The industry is wrong, we are fintech not retail",
            "Add that she owns the FCA relationship",
            "Can you update the notice period to 3 months",
            "Remove the bit about direct reports",
        ],
    )
    def test_reply_containing_an_edit_routes_to_corrections(self, reply):
        assert _route(reply) == "corrections"

    @pytest.mark.parametrize("reply", ["hmm", "not sure", "what does role type mean"])
    def test_ambiguous_reply_defaults_to_corrections(self, reply):
        # Never finalise on an unclear reply — corrections re-presents the
        # profile, which is recoverable; finalise is not.
        assert _route(reply) == "corrections"

    def test_missing_reply_does_not_finalise(self):
        assert _route(None) == "corrections"


class TestConfirmationPhrasesSurviveNormalisation:
    """Apostrophes are stripped during normalisation; phrases must match anyway."""

    @pytest.mark.parametrize(
        "reply", ["that's correct", "thats correct", "That's Correct!"]
    )
    def test_punctuation_variants_all_confirm(self, reply):
        assert _route(reply) == "finalise"
