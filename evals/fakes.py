"""Deterministic stand-ins for the interviewer and analyst models.

Scripted runs install these through `config.llm_provider`, so a whole interview
runs with no API calls. They are deliberately oracles: the analyst fake is
built with the persona and recognises its planted risks, hedges and refusals
exactly. Scripted evals therefore measure the interview *machinery* (what gets
asked, in what order, what is followed up, what is stored) under ideal
classification, not the models themselves. Live runs measure the real models
against the same scorecard.

They speak the v1 harness's protocol: prompts as text, JSON answers as text.
"""

import json
import re
from typing import Any, List

from langchain_core.messages import AIMessage

from config.constants import RiskFlagType

# Phrases the scripted employee uses when a fact sits behind a vague answer.
HEDGE_MARKERS = (
    "bits and pieces",
    "mostly just works",
    "not much to it",
)
REFUSAL_MARKERS = (
    "i'd rather not",
    "rather not get into",
    "i'll pass on that",
)

_VALID_RISK_TYPES = {t.value for t in RiskFlagType}
_ANSWER_RE = re.compile(r"<employee_answer>\n(.*?)\n</employee_answer>", re.DOTALL)
_SUGGESTED_RE = re.compile(r'suggested follow-up question: "(.*?)"', re.DOTALL)


def _one_question(text: str) -> str:
    text = " ".join(text.split()).replace("?", "").rstrip(" .")
    return f"{text}?"


class FakeInterviewerLLM:
    """Turns the node's instruction into a single question.

    Phrasing quality is a live-mode concern. Scripted mode needs a question
    with exactly one '?' that keeps any name the instruction contains.
    """

    def invoke(self, messages: List[Any]) -> AIMessage:
        system = messages[0].content if messages else ""
        if "## CURRENT INSTRUCTION\n\n" in system:
            instruction = system.split("## CURRENT INSTRUCTION\n\n", 1)[1]
            instruction = instruction.split("\n\nRemember", 1)[0]
            return AIMessage(content=_one_question(instruction))
        suggested = _SUGGESTED_RE.search(system)
        if suggested:
            return AIMessage(content=_one_question(suggested.group(1)))
        return AIMessage(content="Could you tell me more about that?")


class FakeAnalystLLM:
    """Persona-aware follow-up and risk classifier (an oracle by design)."""

    def __init__(self, persona):
        self._persona = persona

    def invoke(self, messages: List[Any]) -> AIMessage:
        prompt = messages[0].content if messages else ""
        match = _ANSWER_RE.search(prompt)
        answer = (match.group(1) if match else "").lower()

        if "risk flag classifier" in prompt:
            flags = []
            for fact in self._persona.facts:
                if (
                    fact.risk_type in _VALID_RISK_TYPES
                    and all(k.lower() in answer for k in fact.keywords)
                ):
                    flags.append({
                        "flag_type": fact.risk_type,
                        "severity": "high",
                        "description": fact.fact[:160],
                        "recommended_action": "Capture in the handover before the last day",
                    })
            return AIMessage(content=json.dumps(flags))

        if any(marker in answer for marker in REFUSAL_MARKERS):
            decision = {"needs_followup": False, "reason": "declined", "suggested_followup": ""}
        elif any(marker in answer for marker in HEDGE_MARKERS):
            decision = {
                "needs_followup": True,
                "reason": "vague answer hints at undocumented detail",
                "suggested_followup": "Could you walk me through that in a bit more detail",
            }
        else:
            decision = {"needs_followup": False, "reason": "substantive", "suggested_followup": ""}
        return AIMessage(content=json.dumps(decision))
