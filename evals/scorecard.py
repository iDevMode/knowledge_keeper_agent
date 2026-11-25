"""Scoring an eval interview.

Computed from the transcript, the final graph state, and the exact text Stage 3
would be given — the artefacts a real session produces — so the scorecard is
the same for scripted and live runs. Metrics that cannot be attributed in live
mode (which answer revealed which fact) are None there.

Metric definitions (targets are in docs/interview-agent-v2/02-agent-design.md §11):

recall                 planted facts whose keywords all appear in the stored answers
recall_by_reveal       the same, split by direct / followup / entity_probe
risk_recall            risky planted facts with a flag of the right type from the right block
risk_duplicate_rate    flags matching a planted risk that was already flagged, over all flags
probe_precision        follow-ups that revealed a new fact, over follow-ups asked (scripted)
redundant_question_rate  fresh questions about an objective the employee had already covered
                       (by facts revealed earlier), over fresh questions with a known objective
facts_per_employee_turn  facts captured, over employee messages
decline_respected      refusals NOT followed by a follow-up on the same question (None if none)
non_answers_filed      meta questions and pause requests stored as if they were answers
                       (None if the persona sends none)
secrets_in_pack        secret markers present in the Stage 3 input (after redaction)
concerns_in_pack       grievance markers present in the Stage 3 input
sensitive_in_pack      third-party personal detail present in the Stage 3 input
core_reached_at_half   core objectives (in-flight, deadlines, access, promises, first-break)
                       asked about within the first half of the agent's turns
core_reached           the same over the whole interview
single_question_compliance  agent turns with at most one '?'
coverage               planned blocks with at least one stored answer
est_active_minutes     reading at 250 wpm + typing at 35 wpm + 15 s per answer — an estimate,
                       for comparing runs, not a measurement
"""

from typing import Dict, List, Optional

from pydantic import BaseModel

from evals.employee import REFUSAL_ANSWER
from evals.objectives import CORE_OBJECTIVES, with_credits
from evals.personas import Persona
from evals.simulator import InterviewRun

READ_WPM = 250
TYPE_WPM = 35
THINK_SECONDS = 15


class FactOutcome(BaseModel):
    fact_id: str
    reveal: str
    captured: bool


class Scorecard(BaseModel):
    persona_id: str
    live: bool
    completed: bool

    facts_total: int
    facts_captured: int
    recall: float
    recall_by_reveal: Dict[str, float]
    fact_outcomes: List[FactOutcome]

    risks_expected: int
    risks_detected: int
    risk_recall: float
    risk_flags_total: int
    risk_duplicate_rate: float

    followups_asked: int
    followups_productive: Optional[int]
    probe_precision: Optional[float]

    questions_asked: int
    fresh_questions: int
    redundant_questions: Optional[int]
    redundant_question_rate: Optional[float]
    facts_per_employee_turn: float

    refusals: int
    decline_respected: Optional[float]
    non_answers: int
    non_answers_filed: Optional[float]
    secrets_in_pack: int
    concerns_in_pack: int
    sensitive_in_pack: int

    core_reached_at_half: float
    core_reached: float
    core_missing: List[str]

    single_question_compliance: float
    blocks_planned: int
    blocks_visited: int
    coverage: float
    turns_total: int
    est_active_minutes: float


def _stored_answers(state: dict) -> List[str]:
    chunks: List[str] = []
    for entries in (state.get("answers") or {}).values():
        chunks.extend(str(e) for e in entries) if isinstance(entries, list) else chunks.append(str(entries))
    return chunks


def _words(text: str) -> int:
    return len(text.split())


def _core_touched(turns) -> set:
    touched = set()
    for turn in turns:
        touched |= with_credits(turn.objective_id) & set(CORE_OBJECTIVES)
    return touched


def score_run(run: InterviewRun, persona: Persona) -> Scorecard:
    state = run.final_state
    stored = _stored_answers(state)
    captured_text = " ".join(stored).lower()
    context = run.stage3_context.lower()
    agent_turns, employee_turns = run.agent_turns, run.employee_turns

    # ---- recall
    outcomes, totals, hits = [], {}, {}
    for fact in persona.facts:
        ok = all(k.lower() in captured_text for k in fact.keywords)
        outcomes.append(FactOutcome(fact_id=fact.id, reveal=fact.reveal, captured=ok))
        totals[fact.reveal] = totals.get(fact.reveal, 0) + 1
        hits[fact.reveal] = hits.get(fact.reveal, 0) + int(ok)
    facts_captured = sum(o.captured for o in outcomes)

    # ---- risk
    flags = state.get("risk_flags") or []
    risky = [f for f in persona.facts if f.risk_type]
    # Each flag is matched to the planted risk it is about: same type, raised
    # in that fact's block (or the closing questions, where leftovers come
    # out), preferring a fact whose keywords the description mentions, then
    # one not yet matched. A flag for an already-matched fact is a duplicate.
    matched_counts: Dict[str, int] = {}
    for flag in flags:
        flag_type = getattr(flag.flag_type, "value", flag.flag_type)
        description = f"{flag.description} {flag.recommended_action}".lower()
        candidates = [
            f for f in risky
            if f.risk_type == flag_type and flag.source_block in (f.block, "closing_sequence")
        ]
        if not candidates:
            continue
        by_keyword = [f for f in candidates if any(k.lower() in description for k in f.keywords)]
        unmatched = [f for f in candidates if f.id not in matched_counts]
        fact = (by_keyword or unmatched or candidates)[0]
        matched_counts[fact.id] = matched_counts.get(fact.id, 0) + 1
    duplicates = sum(n - 1 for n in matched_counts.values())

    # ---- follow-ups and redundancy
    followup_answers = [t for t in employee_turns if t.is_followup]
    scripted = not run.live
    productive = sum(1 for t in followup_answers if t.revealed_fact_ids) if scripted else None

    fresh = redundant = 0
    revealed_so_far: set = set()
    fact_covers = {f.id: set(f.covers) for f in persona.facts}
    for turn in run.turns:
        if turn.role == "employee":
            revealed_so_far |= set(turn.revealed_fact_ids)
            continue
        if turn.is_followup or not turn.objective_id or turn.objective_id.startswith("close."):
            continue
        fresh += 1
        covered = set().union(*(fact_covers[f] for f in revealed_so_far)) if revealed_so_far else set()
        if turn.objective_id in covered:
            redundant += 1

    # ---- conduct
    refusal_indexes = [i for i, t in enumerate(run.turns) if t.role == "employee" and t.text == REFUSAL_ANSWER]
    violations = sum(
        1 for i in refusal_indexes
        if i + 1 < len(run.turns) and run.turns[i + 1].role == "agent" and run.turns[i + 1].is_followup
    )
    non_answer_markers = [i.marker.lower() for i in persona.interjections if i.kind in ("meta", "pause")]
    filed = sum(1 for m in non_answer_markers if m in captured_text)

    def leaks(kind: str) -> int:
        return sum(1 for i in persona.interjections if i.kind == kind and i.marker.lower() in context)

    # ---- coverage and time
    half = agent_turns[: max(1, len(agent_turns) // 2)]
    reached_half = _core_touched(half)
    reached_all = _core_touched(agent_turns)
    block_order = state.get("block_order") or []
    visited = {k.rsplit(".", 1)[0] for k in (state.get("answers") or {})}
    seconds = sum(_words(t.text) / READ_WPM * 60 for t in agent_turns) + sum(
        _words(t.text) / TYPE_WPM * 60 + THINK_SECONDS for t in employee_turns
    )

    return Scorecard(
        persona_id=run.persona_id,
        live=run.live,
        completed=run.completed,
        facts_total=len(persona.facts),
        facts_captured=facts_captured,
        recall=facts_captured / len(persona.facts) if persona.facts else 1.0,
        recall_by_reveal={r: hits[r] / totals[r] for r in totals},
        fact_outcomes=outcomes,
        risks_expected=len(risky),
        risks_detected=len(matched_counts),
        risk_recall=len(matched_counts) / len(risky) if risky else 1.0,
        risk_flags_total=len(flags),
        risk_duplicate_rate=duplicates / len(flags) if flags else 0.0,
        followups_asked=len(followup_answers),
        followups_productive=productive,
        probe_precision=(
            (productive / len(followup_answers) if followup_answers else 1.0) if scripted else None
        ),
        questions_asked=len(agent_turns),
        fresh_questions=fresh,
        redundant_questions=redundant if scripted else None,
        redundant_question_rate=(redundant / fresh if fresh else 0.0) if scripted else None,
        facts_per_employee_turn=facts_captured / len(employee_turns) if employee_turns else 0.0,
        refusals=len(refusal_indexes),
        decline_respected=1 - violations / len(refusal_indexes) if refusal_indexes else None,
        non_answers=len(non_answer_markers),
        non_answers_filed=filed / len(non_answer_markers) if non_answer_markers else None,
        secrets_in_pack=leaks("secret"),
        concerns_in_pack=leaks("concern"),
        sensitive_in_pack=leaks("sensitive"),
        core_reached_at_half=len(reached_half) / len(CORE_OBJECTIVES),
        core_reached=len(reached_all) / len(CORE_OBJECTIVES),
        core_missing=[c for c in CORE_OBJECTIVES if c not in reached_all],
        single_question_compliance=(
            sum(1 for t in agent_turns if t.text.count("?") <= 1) / len(agent_turns) if agent_turns else 1.0
        ),
        blocks_planned=len(block_order),
        blocks_visited=sum(1 for b in block_order if b in visited),
        coverage=sum(1 for b in block_order if b in visited) / len(block_order) if block_order else 0.0,
        turns_total=len(run.turns),
        est_active_minutes=round(seconds / 60, 1),
    )


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{round(100 * value)}%"


def render_markdown(cards: List[Scorecard]) -> str:
    lines = [
        "### Capture",
        "",
        "| Persona | Done | Recall | Direct | Follow-up | Entity | Risks | Dup flags | Probe precision | Facts/turn |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cards:
        lines.append(
            f"| {c.persona_id} | {'yes' if c.completed else 'NO'} "
            f"| {c.facts_captured}/{c.facts_total} ({_pct(c.recall)}) "
            f"| {_pct(c.recall_by_reveal.get('direct'))} | {_pct(c.recall_by_reveal.get('followup'))} "
            f"| {_pct(c.recall_by_reveal.get('entity_probe'))} "
            f"| {c.risks_detected}/{c.risks_expected} | {_pct(c.risk_duplicate_rate)} "
            f"| {_pct(c.probe_precision)} | {c.facts_per_employee_turn:.2f} |"
        )
    lines += [
        "",
        "### Conduct and coverage",
        "",
        "| Persona | Questions | Redundant | 1-Q | Declines respected | Non-answers filed | Secrets / concerns / sensitive in pack | Core at half | Core overall | Coverage | Est. minutes |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cards:
        lines.append(
            f"| {c.persona_id} | {c.questions_asked} | {_pct(c.redundant_question_rate)} "
            f"| {_pct(c.single_question_compliance)} | {_pct(c.decline_respected)} "
            f"| {_pct(c.non_answers_filed)} "
            f"| {c.secrets_in_pack} / {c.concerns_in_pack} / {c.sensitive_in_pack} "
            f"| {_pct(c.core_reached_at_half)} | {_pct(c.core_reached)} "
            f"| {_pct(c.coverage)} | {c.est_active_minutes} |"
        )

    if cards:
        total = sum(c.facts_total for c in cards)
        overall = sum(c.facts_captured for c in cards) / total if total else 1.0
        fresh = sum(c.fresh_questions for c in cards)
        redundant = sum(c.redundant_questions or 0 for c in cards)
        never = sorted(set.intersection(*(set(c.core_missing) for c in cards)))
        lines += [
            "",
            f"**Overall recall: {_pct(overall)}** · redundant questions: "
            f"{_pct(redundant / fresh if fresh else 0)} · "
            f"core objectives asked in no interview: {never or 'none'}",
            "",
            "Uncaptured facts:",
        ]
        misses = [f"- {c.persona_id}: {o.fact_id} ({o.reveal})" for c in cards for o in c.fact_outcomes if not o.captured]
        lines += misses or ["- none"]
    return "\n".join(lines)
