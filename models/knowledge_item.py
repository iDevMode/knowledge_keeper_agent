"""Structured knowledge items — the intermediate representation for Stage 3.

Extract-then-compose synthesis (plan 3.1): rather than asking one LLM call to
read the whole transcript and write the whole document in a single pass — where
details buried mid-transcript get silently dropped — we first extract discrete
KnowledgeItems, then compose the document from them. The extraction pass is
exhaustive and structured; the composition pass turns a complete, itemised
inventory into prose, so nothing has to be "remembered" across a long context.

These are also the reusable unit for Phase 4 exports (Notion/Confluence): a
handover is, structurally, a set of KnowledgeItems grouped into sections.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

# The document sections a knowledge item can belong to. Mirrors the sentinel
# sections the composer emits, minus the meta sections (header/risk summary/
# footer) which are generated from structured data, not extracted items.
ItemCategory = Literal[
    "role_overview",
    "knowledge_transfer",
    "key_relationships",
    "systems_and_access",
    "in_flight_items",
    "decision_making",
    "undocumented_knowledge",
    "advice",
    "onboarding",
    "knowledge_gap",
]


class KnowledgeItem(BaseModel):
    """One discrete, self-contained piece of handover knowledge."""

    category: ItemCategory = Field(
        description="Which handover section this item belongs in"
    )
    title: str = Field(description="Short, specific title (e.g. 'SAP batch scheduling workaround')")
    detail: str = Field(
        description=(
            "Synthesised prose a successor can act on — what it is, why it "
            "matters, and how to do it. Written for someone competent but new."
        )
    )
    entities: List[str] = Field(
        default_factory=list,
        description="Named people, clients, systems, or documents this item involves",
    )
    importance: Literal["critical", "high", "medium", "low"] = "medium"
    is_gap: bool = Field(
        default=False,
        description="True if this records something MISSING or only partially captured",
    )
    source_blocks: List[str] = Field(
        default_factory=list,
        description="Interview block(s) this was drawn from, for traceability",
    )


class ExtractedKnowledge(BaseModel):
    """The full structured extraction from an interview transcript."""

    items: List[KnowledgeItem] = Field(default_factory=list)

    def by_category(self, category: str) -> List[KnowledgeItem]:
        return [item for item in self.items if item.category == category]

    def gaps(self) -> List[KnowledgeItem]:
        return [item for item in self.items if item.is_gap]
