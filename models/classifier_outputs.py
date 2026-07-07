"""Structured output schemas for the Haiku classifier calls.

Using with_structured_output (tool use) instead of free-text JSON parsing —
Haiku frequently wraps raw JSON in markdown fences, and every parse failure
silently degrades to "no follow-up" / "no risk flags".
"""

from typing import List, Literal

from pydantic import BaseModel, Field

from config.constants import RiskFlagType, Severity


class FollowupDecision(BaseModel):
    """Decision on whether the last answer needs a follow-up question."""

    needs_followup: bool
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


class ConfirmationIntent(BaseModel):
    """Classification of the manager's response to the profile review."""

    intent: Literal["confirm", "correct", "unclear"] = Field(
        description=(
            "'confirm' if the response approves the profile as-is with no changes; "
            "'correct' if it requests any change, addition, or fix — even alongside "
            "approval language; 'unclear' if the intent cannot be determined"
        )
    )
