"""Scoring for eval interview runs.

Everything is computed from the transcript and final graph state — the same
artefacts a real session produces — so the scorecard works identically for
scripted and live runs.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from evals.personas import Persona
from evals.simulator import InterviewRun


class FactOutcome(BaseModel):
    fact_id: str
    reveal: str
    captured: bool


class Scorecard(BaseModel):
    persona_id: str
    completed: bool

    # Planted-knowledge recall — the headline metric
    facts_total: int
    facts_captured: int
    recall: float
    recall_by_reveal: Dict[str, float]  # direct / followup / entity_probe
    fact_outcomes: List[FactOutcome]

    # Risk detection
    risks_expected: int
    risks_detected: int
    risk_recall: float

    # Follow-up quality: of the follow-ups asked, how many surfaced a hidden fact
    followups_asked: int
    followups_productive: Optional[int]  # None in live mode (reveals unknown)
    followup_precision: Optional[float]

    # Efficiency & fatigue
    questions_asked: int
    question_budget: int
    within_budget: bool
    turns_total: int

    # Discipline & coverage
    single_question_compliance: float  # fraction of agent turns with <= 1 '?'
    blocks_planned: int
    blocks_visited: int
    coverage: float
    entity_sweep_ran: bool


def _captured_text(final_state: dict) -> str:
    chunks = []
    for entries in final_state.get("answers", {}).values():
        if isinstance(entries, list):
            chunks.extend(str(e) for e in entries)
        else:
            chunks.append(str(entries))
    return " ".join(chunks).lower()


def score_run(run: InterviewRun, persona: Persona) -> Scorecard:
    state = run.final_state
    captured = _captured_text(state)

    # ---- Fact recall ----
    outcomes = []
    by_reveal_totals: Dict[str, int] = {}
    by_reveal_captured: Dict[str, int] = {}
    for fact in persona.facts:
        is_captured = all(k.lower() in captured for k in fact.keywords)
        outcomes.append(
            FactOutcome(fact_id=fact.id, reveal=fact.reveal, captured=is_captured)
        )
        by_reveal_totals[fact.reveal] = by_reveal_totals.get(fact.reveal, 0) + 1
        by_reveal_captured[fact.reveal] = (
            by_reveal_captured.get(fact.reveal, 0) + (1 if is_captured else 0)
        )
    facts_total = len(persona.facts)
    facts_captured = sum(1 for o in outcomes if o.captured)
    recall_by_reveal = {
        reveal: by_reveal_captured.get(reveal, 0) / total
        for reveal, total in by_reveal_totals.items()
    }

    # ---- Risk recall: every risky planted fact that was captured should have
    # produced a flag of the matching type ----
    detected_types = {
        getattr(f.flag_type, "value", f.flag_type) for f in state.get("risk_flags", [])
    }
    risky_facts = [f for f in persona.facts if f.risk_type]
    risks_detected = sum(1 for f in risky_facts if f.risk_type in detected_types)

    # ---- Follow-up precision ----
    followup_answers = [
        t for t in run.turns if t.role == "employee" and t.is_followup
    ]
    followups_asked = len(followup_answers)
    scripted = any(t.revealed_fact_ids for t in run.turns) or followups_asked == 0
    if scripted:
        followups_productive = sum(1 for t in followup_answers if t.revealed_fact_ids)
        followup_precision = (
            followups_productive / followups_asked if followups_asked else 1.0
        )
    else:  # live mode can't attribute reveals to turns deterministically
        followups_productive = None
        followup_precision = None

    # ---- Efficiency ----
    questions_asked = state.get("question_count", 0)
    question_budget = state.get("question_budget", 0)

    # ---- Discipline & coverage ----
    agent_turns = run.agent_turns
    compliant = sum(1 for t in agent_turns if t.text.count("?") <= 1)
    compliance = compliant / len(agent_turns) if agent_turns else 1.0

    block_order = state.get("block_order", [])
    visited = {
        key.rsplit(".", 1)[0] for key in state.get("answers", {})
    }
    blocks_visited = sum(1 for b in block_order if b in visited)
    coverage = blocks_visited / len(block_order) if block_order else 0.0

    return Scorecard(
        persona_id=run.persona_id,
        completed=run.completed,
        facts_total=facts_total,
        facts_captured=facts_captured,
        recall=facts_captured / facts_total if facts_total else 1.0,
        recall_by_reveal=recall_by_reveal,
        fact_outcomes=outcomes,
        risks_expected=len(risky_facts),
        risks_detected=risks_detected,
        risk_recall=risks_detected / len(risky_facts) if risky_facts else 1.0,
        followups_asked=followups_asked,
        followups_productive=followups_productive,
        followup_precision=followup_precision,
        questions_asked=questions_asked,
        question_budget=question_budget,
        within_budget=question_budget == 0 or questions_asked <= question_budget,
        turns_total=len(run.turns),
        single_question_compliance=compliance,
        blocks_planned=len(block_order),
        blocks_visited=blocks_visited,
        coverage=coverage,
        entity_sweep_ran=any(t.block == "entity_sweep" for t in run.turns),
    )


def render_markdown(cards: List[Scorecard]) -> str:
    """Aggregate report across personas."""
    lines = [
        "# KnowledgeKeeper interview eval",
        "",
        "| Persona | Done | Recall | Direct | Follow-up | Entity | Risk | F/up precision | Questions (budget) | Coverage | 1-Q |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def pct(value: Optional[float]) -> str:
        return "—" if value is None else f"{round(100 * value)}%"

    for c in cards:
        lines.append(
            f"| {c.persona_id} | {'yes' if c.completed else 'NO'} "
            f"| {c.facts_captured}/{c.facts_total} ({pct(c.recall)}) "
            f"| {pct(c.recall_by_reveal.get('direct'))} "
            f"| {pct(c.recall_by_reveal.get('followup'))} "
            f"| {pct(c.recall_by_reveal.get('entity_probe'))} "
            f"| {c.risks_detected}/{c.risks_expected} "
            f"| {pct(c.followup_precision)} "
            f"| {c.questions_asked} ({c.question_budget}) "
            f"| {pct(c.coverage)} "
            f"| {pct(c.single_question_compliance)} |"
        )

    if cards:
        overall = sum(c.facts_captured for c in cards) / max(
            sum(c.facts_total for c in cards), 1
        )
        lines += ["", f"**Overall planted-knowledge recall: {round(100 * overall)}%**"]

    lines += [
        "",
        "Uncaptured facts:",
    ]
    misses = [
        f"- {c.persona_id}: {o.fact_id} ({o.reveal})"
        for c in cards
        for o in c.fact_outcomes
        if not o.captured
    ]
    lines += misses if misses else ["- none"]
    return "\n".join(lines)
