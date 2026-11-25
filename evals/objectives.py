"""Objective ids for the eval scorecard, for both interview harnesses.

v2 interviews choose questions by objective (docs/interview-agent-v2/
04-objective-bank.yaml), so each agent turn will carry an objective id. The v1
interview walks fixed question lists, so `V1_QUESTION_OBJECTIVES` maps each v1
question (block, index) to the objective it pursues. With that, the same
metrics — redundant questions, core objectives reached — are computed for the
v1 baseline and for v2.

The mapping is a judgement about what each v1 brief asks for; it lives here,
not in the agent, because only the scorecard uses it.
"""

from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Set, Tuple

import yaml

BANK_PATH = (
    Path(__file__).resolve().parent.parent / "docs" / "interview-agent-v2" / "04-objective-bank.yaml"
)

CORE_OBJECTIVES: Tuple[str, ...] = (
    "core.inflight", "core.deadlines", "core.access", "core.promises", "core.first_break",
)

V1_QUESTION_OBJECTIVES: Dict[str, Tuple[str, ...]] = {
    "role_orientation": ("ori.shape", "ori.rhythm", "ori.shape", "ori.sole", "ori.rhythm"),
    "client_stakeholder_relationships": (
        "rel.map", "rel.each", "rel.transition", "rel.informal", "rel.careful", "core.promises",
    ),
    "internal_processes_workflows": (
        "proc.inventory", "proc.each", "proc.docs", "proc.dependence", "proc.failure",
        "proc.why", "proc.novice",
    ),
    "technical_systems_tools": (
        "sys.inventory", "sys.each", "sys.integrations", "sys.quirks", "core.access",
        "sys.automations",
    ),
    "decision_making_logic": (
        "dec.each", "dec.each", "dec.differs", "dec.highstakes", "dec.rules", "dec.rules",
    ),
    "team_dynamics_management": (
        "team.map", "team.reports", "team.context", "team.transition", "team.challenge",
    ),
    "supplier_vendor_relationships": (
        "sup.map", "sup.each", "sup.terms", "sup.performance", "sup.dates",
    ),
    "regulatory_compliance": (
        "reg.obligations", "reg.dates", "reg.watch", "reg.docs", "reg.bodies",
    ),
    "undocumented_workarounds": (
        "und.justknow", "und.shortcuts", "und.prevent", "und.ifthen", "und.justknow",
    ),
    "strategic_context": (
        "strat.priorities", "strat.role_link", "strat.initiatives", "strat.watch", "strat.impact",
    ),
    "closing_sequence": ("close.missed", "close.advice", "close.people"),
}


@lru_cache(maxsize=1)
def objective_ids() -> FrozenSet[str]:
    bank = yaml.safe_load(BANK_PATH.read_text())
    return frozenset(o["id"] for group in bank.values() for o in group)


@lru_cache(maxsize=1)
def _credits() -> Dict[str, Tuple[str, ...]]:
    bank = yaml.safe_load(BANK_PATH.read_text())
    return {o["id"]: tuple(o.get("credit") or ()) for group in bank.values() for o in group}


def v1_objective(block: str, index: int) -> Optional[str]:
    questions = V1_QUESTION_OBJECTIVES.get(block, ())
    return questions[index] if 0 <= index < len(questions) else None


def with_credits(objective_id: Optional[str]) -> Set[str]:
    """The objective plus any it also satisfies (the bank's `credit` field)."""
    if not objective_id:
        return set()
    return {objective_id, *_credits().get(objective_id, ())}
