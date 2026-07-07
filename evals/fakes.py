"""Deterministic stand-ins for the primary and classifier LLMs.

Scripted mode swaps these in via config.llm_provider so an entire interview
runs with zero API calls. They are deliberately "oracle" fakes — the
classifier is built with the persona and recognises its planted entities,
risks, and hedged answers exactly. That means scripted evals measure the
interview MACHINERY (entity tracking, sweep, follow-up routing, budget,
coverage) under ideal classification, not classifier quality itself — live
mode measures the real models against the same scorecard.
"""

import re
from typing import Any, List

from langchain_core.messages import AIMessage

from models.classifier_outputs import (
    AnswerAnalysis,
    DetectedEntity,
    DetectedRiskFlag,
    FollowupDecision,
)

# Hedge phrases the scripted employee uses when a fact is hidden behind a
# vague answer — the fake follow-up classifier keys off these.
HEDGE_MARKERS = [
    "bits and pieces",
    "mostly just works",
    "hard to put into words",
]

REFUSAL_MARKERS = [
    "i'd rather not",
    "rather not get into",
    "don't want to talk about",
]

_ANSWER_RE = re.compile(
    r"Answer received: (.*?)(?:\n\n## Risk flag types|\n\nAlready captured|\Z)",
    re.DOTALL,
)


def _extract_answer(prompt_text: str) -> str:
    match = _ANSWER_RE.search(prompt_text)
    return match.group(1).strip() if match else prompt_text


class FakePrimaryLLM:
    """Echoes the current node instruction back as a single question.

    Real phrasing quality is a live-mode concern; scripted mode only needs a
    question that (a) contains exactly one '?' and (b) preserves any entity
    name embedded in the instruction (so entity-sweep matching works).
    """

    def invoke(self, messages: List[Any]) -> AIMessage:
        system_text = messages[0].content if messages else ""
        marker = "## CURRENT INSTRUCTION\n\n"
        if marker in system_text:
            instruction = system_text.split(marker, 1)[1]
            instruction = instruction.split("\n\nRemember", 1)[0]
        else:
            instruction = "Please tell me more about your role"
        question = instruction.strip().replace("?", "").rstrip(".")
        return AIMessage(content=f"{question}?")


class _StructuredFake:
    def __init__(self, persona, schema):
        self._persona = persona
        self._schema = schema

    def invoke(self, messages: List[Any]):
        prompt = messages[0].content if messages else ""
        answer = _extract_answer(prompt)
        lowered = answer.lower()

        if self._schema is AnswerAnalysis:
            entities = []
            flags = []
            for fact in self._persona.facts:
                if fact.entity and fact.entity.lower() in lowered:
                    entities.append(
                        DetectedEntity(
                            name=fact.entity,
                            entity_type="other",
                            importance="high",
                            context=f"mentioned around {fact.block}",
                        )
                    )
                if fact.risk_type and all(k.lower() in lowered for k in fact.keywords):
                    flags.append(
                        DetectedRiskFlag(
                            flag_type=fact.risk_type,
                            severity="high",
                            description=fact.fact[:120],
                            recommended_action="Capture in handover",
                        )
                    )
            return AnswerAnalysis(flags=flags, entities=entities)

        if self._schema is FollowupDecision:
            if any(marker in lowered for marker in REFUSAL_MARKERS):
                return FollowupDecision(
                    needs_followup=False, is_refusal=True, reason="refusal"
                )
            if any(marker in lowered for marker in HEDGE_MARKERS):
                return FollowupDecision(
                    needs_followup=True,
                    information_gain="high",
                    reason="vague answer hints at undocumented detail",
                    suggested_followup="Could you walk me through that in a bit more detail?",
                )
            return FollowupDecision(
                needs_followup=False, information_gain="none", reason="substantive answer"
            )

        raise ValueError(f"FakeClassifier has no behaviour for schema {self._schema}")


class FakeClassifierLLM:
    """Persona-aware classifier fake — see module docstring for why it is an
    oracle by design."""

    def __init__(self, persona):
        self._persona = persona

    def with_structured_output(self, schema):
        return _StructuredFake(self._persona, schema)
