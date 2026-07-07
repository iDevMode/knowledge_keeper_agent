"""Structured output schemas for the Haiku classifier calls.

Using with_structured_output (tool use) instead of free-text JSON parsing —
Haiku frequently wraps raw JSON in markdown fences, and every parse failure
silently degrades to "no follow-up" / "no risk flags".
"""

from typing import List, Literal

from pydantic import BaseModel, Field

from config.constants import (
    EntityImportance,
    EntityType,
    InformationGain,
    RiskFlagType,
    Severity,
)


class FollowupDecision(BaseModel):
    """Decision on whether the last answer needs a follow-up question.

    A follow-up is only worth a turn of the employee's attention if it is
    expected to surface NEW information — the classifier rates that expected
    information gain, and the graph only follows up on medium/high (high-only
    once the interview is over its turn budget).
    """

    needs_followup: bool
    information_gain: InformationGain = Field(
        default=InformationGain.MEDIUM,
        description=(
            "Expected information gain of a follow-up: 'high' — the answer "
            "clearly hints at important undocumented knowledge; 'medium' — "
            "the answer is incomplete on a relevant point; 'low' — a follow-up "
            "would mostly rephrase what is already captured; 'none' — nothing "
            "left to gain"
        ),
    )
    is_refusal: bool = Field(
        default=False,
        description=(
            "True if the person declined to answer or said they don't know — "
            "refusals are respected, never chased with follow-ups"
        ),
    )
    reason: str = Field(description="Brief reason for the decision")
    suggested_followup: str = Field(
        default="",
        description="The follow-up question to ask, empty if none needed",
    )


class DetectedRiskFlag(BaseModel):
    """A single risk flag detected in an answer (source fields added by the caller)."""

    flag_type: RiskFlagType
    severity: Severity
    description: str
    recommended_action: str


class RiskFlagBatch(BaseModel):
    """All risk flags detected in a single answer. Empty list if none."""

    flags: List[DetectedRiskFlag] = Field(default_factory=list)


class DetectedEntity(BaseModel):
    """A named entity mentioned in an answer (person, client, system, ...)."""

    name: str = Field(description="The entity's name exactly as mentioned")
    entity_type: EntityType
    importance: EntityImportance = Field(
        description=(
            "'high' if the handover would be incomplete without exploring this "
            "entity, 'medium' if worth a mention, 'low' if incidental"
        )
    )
    context: str = Field(
        default="",
        description="One short phrase summarising what was said about it",
    )


class AnswerAnalysis(BaseModel):
    """Combined per-answer analysis: risk flags plus mentioned entities.

    One structured Haiku call per answer covers both — cheaper and faster
    than running two separate classifiers.
    """

    flags: List[DetectedRiskFlag] = Field(default_factory=list)
    entities: List[DetectedEntity] = Field(default_factory=list)


class ConfirmationIntent(BaseModel):
    """Classification of the manager's response to the profile review."""

    intent: Literal["confirm", "correct", "unclear"] = Field(
        description=(
            "'confirm' if the response approves the profile as-is with no changes; "
            "'correct' if it requests any change, addition, or fix — even alongside "
            "approval language; 'unclear' if the intent cannot be determined"
        )
    )
