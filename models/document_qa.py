"""LLM-judge QA report for a generated handover document (plan 3.3)."""

from typing import List

from pydantic import BaseModel, Field


class DocumentQAReport(BaseModel):
    """Rubric scores for a generated handover, each in [0, 1]."""

    completeness: float = Field(
        ge=0.0, le=1.0,
        description="Are all required sections present and substantively filled?",
    )
    risk_coverage: float = Field(
        ge=0.0, le=1.0,
        description="Does every identified risk flag appear in the Risk Summary with an action?",
    )
    actionability: float = Field(
        ge=0.0, le=1.0,
        description="Could a competent successor actually act on this document?",
    )
    gap_honesty: float = Field(
        ge=0.0, le=1.0,
        description="Are missing/uncertain areas flagged as [GAP] rather than glossed over?",
    )
    issues: List[str] = Field(
        default_factory=list,
        description="Specific, actionable problems to fix on regeneration",
    )

    @property
    def overall(self) -> float:
        return round(
            (self.completeness + self.risk_coverage + self.actionability + self.gap_honesty) / 4,
            3,
        )
