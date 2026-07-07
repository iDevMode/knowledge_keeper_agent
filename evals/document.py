"""Document-stage scoring for the eval harness.

Extends the interview eval end to end: after an interview run, build the
handover document and measure whether the knowledge the interview *captured*
actually survives into the *document* a successor receives.

Two modes, mirroring the interview harness:

- scripted (default): a deterministic composer routes each interview block's
  captured answers into its handover section (via BLOCK_TO_SECTION) and renders
  a valid sentinel-sectioned document — then it is parsed through the REAL
  Stage 3 pipeline (parse_llm_output + confidentiality filter). This measures
  the assembly plumbing: block→section routing, risk-summary rendering,
  redaction — a routing bug that dropped a block would show as recall < 100%.
- live: calls the real generate_document (extract-then-compose + QA gate) and
  scores the model's actual synthesis fidelity.
"""

from typing import Dict, List

from pydantic import BaseModel

from evals.personas import Persona
from evals.simulator import InterviewRun
from models.risk_flags import RiskFlag, merge_risk_flags
from output.formatters.document_formatter import parse_llm_output

# Which handover section each interview block's knowledge lands in. Every block
# a persona plants facts in MUST map to a section, or those facts are dropped.
BLOCK_TO_SECTION = {
    "role_orientation": "Role Overview",
    "client_stakeholder_relationships": "Key Relationships",
    "supplier_vendor_relationships": "Key Relationships",
    "internal_processes_workflows": "Knowledge Transfer — Processes",
    "technical_systems_tools": "Systems and Access",
    "decision_making_logic": "Decision-Making Logic and Judgment",
    "team_dynamics_management": "Knowledge Transfer — Team",
    "regulatory_compliance": "Knowledge Transfer — Compliance",
    "undocumented_workarounds": "Undocumented Knowledge",
    "strategic_context": "In-Flight Items",
    "entity_sweep": "Undocumented Knowledge",
    "closing_sequence": "Advice to Your Replacement",
}

# The sentinel sections a valid document must contain (subset the composer must
# always emit; matches generator._REQUIRED_SENTINELS structurally).
_ALL_SECTIONS = [
    "Document Header",
    "Risk Summary",
    "Role Overview",
    "Knowledge Transfer — Processes",
    "Knowledge Transfer — Team",
    "Knowledge Transfer — Compliance",
    "Key Relationships",
    "Systems and Access",
    "In-Flight Items",
    "Decision-Making Logic and Judgment",
    "Undocumented Knowledge",
    "Advice to Your Replacement",
    "Recommended Onboarding Plan",
    "Knowledge Gaps and Recommended Follow-Up",
]


class DocumentScorecard(BaseModel):
    persona_id: str
    facts_total: int
    facts_in_document: int
    document_recall: float
    document_recall_by_reveal: Dict[str, float]
    risks_expected: int
    risks_in_summary: int
    risk_summary_recall: float
    missing_fact_ids: List[str]


def _scripted_document_markdown(run: InterviewRun, persona: Persona) -> str:
    """Deterministically compose a sentinel-sectioned document by routing each
    block's captured answers into its handover section."""
    answers: Dict[str, List[str]] = run.final_state.get("answers", {})
    section_text: Dict[str, List[str]] = {name: [] for name in _ALL_SECTIONS}

    for key, entries in answers.items():
        block = key.rsplit(".", 1)[0]
        section = BLOCK_TO_SECTION.get(block)
        if section and section in section_text:
            for entry in entries:
                section_text[section].append(str(entry))

    merged = merge_risk_flags(run.final_state.get("risk_flags", []))
    risk_lines = [
        f"- [{f.flag_type.value if hasattr(f.flag_type, 'value') else f.flag_type}] "
        f"{f.description} — {f.recommended_action}"
        for f in merged
    ]

    parts = []
    for name in _ALL_SECTIONS:
        parts.append(f"### SECTION: {name}")
        if name == "Risk Summary":
            parts.append("\n".join(risk_lines) if risk_lines else "No risks identified.")
        else:
            body = "\n\n".join(section_text.get(name, []))
            parts.append(body if body else f"No {name.lower()} captured.")
        parts.append("")
    return "\n\n".join(parts)


def _document_text(run: InterviewRun, persona: Persona, live: bool) -> str:
    if live:
        from agents.stage3_document_generation.generator import (
            GenerationRequest,
            generate_document,
        )

        state = run.final_state
        req = GenerationRequest(
            session_id=f"eval-doc-{persona.id}",
            profile=state["profile"],
            conversation_history=state.get("conversation_history", []),
            risk_flags=state.get("risk_flags", []),
            answers=state.get("answers", {}),
            block_order=state.get("block_order", []),
            block_depths=state.get("block_depths", {}),
        )
        raw = generate_document(req).raw_markdown
    else:
        raw = _scripted_document_markdown(run, persona)

    doc = parse_llm_output(raw, run.final_state["profile"], f"eval-doc-{persona.id}")
    return " ".join(s.content_markdown for s in doc.sections).lower()


def score_document(run: InterviewRun, persona: Persona, live: bool = False) -> DocumentScorecard:
    doc_text = _document_text(run, persona, live)

    outcomes = []
    by_reveal_total: Dict[str, int] = {}
    by_reveal_hit: Dict[str, int] = {}
    missing = []
    for fact in persona.facts:
        in_doc = all(k.lower() in doc_text for k in fact.keywords)
        outcomes.append(in_doc)
        by_reveal_total[fact.reveal] = by_reveal_total.get(fact.reveal, 0) + 1
        by_reveal_hit[fact.reveal] = by_reveal_hit.get(fact.reveal, 0) + (1 if in_doc else 0)
        if not in_doc:
            missing.append(fact.id)

    facts_total = len(persona.facts)
    facts_in_doc = sum(1 for o in outcomes if o)

    risky = [f for f in persona.facts if f.risk_type]
    risks_in_summary = sum(1 for f in risky if all(k.lower() in doc_text for k in f.keywords))

    return DocumentScorecard(
        persona_id=persona.id,
        facts_total=facts_total,
        facts_in_document=facts_in_doc,
        document_recall=facts_in_doc / facts_total if facts_total else 1.0,
        document_recall_by_reveal={
            reveal: by_reveal_hit.get(reveal, 0) / total
            for reveal, total in by_reveal_total.items()
        },
        risks_expected=len(risky),
        risks_in_summary=risks_in_summary,
        risk_summary_recall=risks_in_summary / len(risky) if risky else 1.0,
        missing_fact_ids=missing,
    )
