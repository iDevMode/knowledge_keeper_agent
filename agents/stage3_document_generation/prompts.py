from typing import Any, Dict, List, Optional, Union

from langchain_core.messages import BaseMessage, HumanMessage

from agents.stage2_employee_interview.prompts import (
    BLOCK_QUESTIONS as STAGE2_BLOCK_QUESTIONS,
    CLOSING_QUESTIONS,
    ROLE_ORIENTATION_QUESTIONS,
)
from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile


EXTRACTION_SYSTEM_PROMPT = """\
You are KnowledgeKeeper's knowledge-extraction pass. You are given a Role Intelligence Profile \
and the full transcript of an employee handover interview.

Your job is to extract EVERY discrete piece of handover knowledge in the transcript as a separate \
structured item. This is an inventory pass, not a writing pass — be exhaustive. A successor will \
rely on this inventory, so a detail you drop here is a detail lost forever.

For each item:
- Choose the category it belongs in (role_overview, knowledge_transfer, key_relationships, \
systems_and_access, in_flight_items, decision_making, undocumented_knowledge, advice, onboarding, \
knowledge_gap).
- Write the `detail` as clear, synthesised prose the successor can act on — what it is, why it \
matters, how to do it — never a verbatim quote.
- List any named entities involved (people, clients, systems, documents).
- Mark `is_gap: true` for anything the employee could NOT answer, answered only partially, or \
that was flagged as missing — capture the gap explicitly rather than omitting it.
- Set importance honestly (critical/high/medium/low).

Extract many small, specific items rather than a few broad ones. Do not merge unrelated facts. \
Do not invent anything not supported by the transcript.\
"""


COMPOSE_SYSTEM_PROMPT = """\
You are KnowledgeKeeper's composition pass. You are given the Role Intelligence Profile, a \
pre-extracted INVENTORY of knowledge items (already grouped by section), and a de-duplicated \
risk summary.

Your task is to compose the final Handover Intelligence Document from this inventory. Every item \
in the inventory MUST appear in the document, placed in its section and woven into readable, \
professional prose — as though written by a senior consultant who understands the role. You are \
composing a complete inventory into a document; you are not summarising and you must not drop items.

## DOCUMENT GENERATION RULES

- Write in clear, professional prose. Use headers and sub-sections for navigation. Use bullet \
points only for lists of items (contacts, tools, tasks) — never for narrative content.
- Write for the incoming replacement — assume they are competent but have no prior context about \
this specific business, role, or environment.
- Items marked as gaps must be surfaced with [GAP: brief description of what is missing] — never \
gloss over them.
- The de-duplicated risk summary appears prominently in the Risk Summary section at the top.
- Calibrate the depth of each section to the knowledge priority ranking from Stage 1.
- Each section MUST begin with the exact sentinel marker format: ### SECTION: [Section Name]
- Do not use any other heading format for section delimiters.\
"""


STAGE3_SYSTEM_PROMPT = """\
You are KnowledgeKeeper. You have now completed both the business interview (Stage 1) and the \
employee interview (Stage 2). You have a complete Role Intelligence Profile and a full interview \
transcript.

Your task is to synthesise everything into a structured, professional Handover Intelligence Document. \
This document should read as though it was written by a senior consultant who deeply understands \
the role — not as a transcript summary.

## DOCUMENT GENERATION RULES

- Write in clear, professional prose. Use headers and sub-sections for navigation. Use bullet \
points only for lists of items (contacts, tools, tasks) — never for narrative content.
- Never quote the employee verbatim unless the exact phrasing is critical. Paraphrase and synthesise.
- Write for the incoming replacement — assume they are competent but have no prior context about \
this specific business, role, or environment.
- Where the employee's answers revealed gaps, uncertainty, or partial information, flag it \
explicitly with [GAP: brief description of what is missing] rather than glossing over it.
- Any Risk Flags tracked during Stage 2 appear prominently in the Risk Summary at the top of \
the document.
- Calibrate the depth of each section to the knowledge priority ranking from Stage 1.
- Each section MUST begin with the exact sentinel marker format: ### SECTION: [Section Name]
- Do not use any other heading format for section delimiters.\
"""


GENERATION_INSTRUCTION_TEMPLATE = """\
## GENERATION INSTRUCTION

Produce the complete Handover Intelligence Document with ALL of the following sections, each \
beginning with the exact sentinel marker shown. Do not skip any section — if information is \
not available for a section, write a brief note explaining what was not covered and why.

Required sections (in this exact order):
1. ### SECTION: Document Header
2. ### SECTION: Risk Summary
3. ### SECTION: Role Overview
4. ### SECTION: Knowledge Transfer — {priority_1_label}
5. ### SECTION: Knowledge Transfer — {priority_2_label}
6. ### SECTION: Knowledge Transfer — {priority_3_label}
7. ### SECTION: Key Relationships
8. ### SECTION: Systems and Access
9. ### SECTION: In-Flight Items
10. ### SECTION: Decision-Making Logic and Judgment
11. ### SECTION: Undocumented Knowledge
12. ### SECTION: Advice to Your Replacement
13. ### SECTION: Recommended Onboarding Plan
14. ### SECTION: Knowledge Gaps and Recommended Follow-Up

{confidentiality_instruction}

Tone guidance: The company culture is described as "{culture_type}". Match the document's formality \
to this culture — use professional but accessible language for informal cultures, and more structured \
formal language for formal cultures.

After the final section, add this footer:
"This document has been generated by KnowledgeKeeper. It is based on {question_count} questions \
across two interview sessions. {risk_flag_count} risk flags were identified and are summarised \
in Section 2. This document is ready to share with {recipients}."
"""


def build_context_block(
    profile: RoleIntelligenceProfile,
    conversation_history: List[BaseMessage],
    risk_flags: List[RiskFlag],
    answers: Dict[str, Any],
    block_order: List[str],
    block_depths: Dict[str, str],
) -> str:
    """Build the full context string passed to the LLM for document generation."""
    parts = []

    # 1. Role Intelligence Profile
    parts.append("## ROLE INTELLIGENCE PROFILE\n")
    parts.append(_format_profile_for_context(profile))

    # 2. Risk Flags
    if risk_flags:
        parts.append("\n## RISK FLAGS IDENTIFIED\n")
        parts.append(_format_risk_flags(risk_flags))

    # 3. Interview transcript by block
    parts.append("\n## INTERVIEW TRANSCRIPT BY BLOCK\n")
    parts.append(_format_answers_by_block(answers, block_order, block_depths))

    # 4. Key conversation exchanges
    excerpts = _format_conversation_excerpts(conversation_history)
    if excerpts:
        parts.append("\n## KEY CONVERSATION EXCHANGES\n")
        parts.append(excerpts)

    # 5. Generation instruction
    parts.append("\n")
    parts.append(_build_generation_instruction(profile, answers, risk_flags))

    return "\n".join(parts)


def build_extraction_context(
    profile: RoleIntelligenceProfile,
    conversation_history: List[BaseMessage],
    answers: Dict[str, Any],
    block_order: List[str],
    block_depths: Dict[str, str],
) -> str:
    """Context for the extraction pass: profile + full transcript, no writing
    instruction — the extractor's job is defined entirely by its system prompt
    and the structured-output schema."""
    parts = [
        "## ROLE INTELLIGENCE PROFILE\n",
        _format_profile_for_context(profile),
        "\n## INTERVIEW TRANSCRIPT BY BLOCK\n",
        _format_answers_by_block(answers, block_order, block_depths),
    ]
    excerpts = _format_conversation_excerpts(conversation_history)
    if excerpts:
        parts.append("\n## KEY CONVERSATION EXCHANGES\n")
        parts.append(excerpts)
    parts.append(
        "\n## INSTRUCTION\n\nExtract every discrete knowledge item from the "
        "transcript above as structured items. Be exhaustive."
    )
    return "\n".join(parts)


def _format_extracted_items(items: List[Any]) -> str:
    """Render extracted KnowledgeItems grouped by category for the composer."""
    if not items:
        return "(no items extracted)"

    by_category: Dict[str, List[Any]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    lines = []
    for category, group in by_category.items():
        lines.append(f"### {category.replace('_', ' ').title()}")
        for item in group:
            gap = " [GAP]" if item.is_gap else ""
            entities = f" (entities: {', '.join(item.entities)})" if item.entities else ""
            lines.append(f"- [{item.importance}]{gap} {item.title}: {item.detail}{entities}")
        lines.append("")
    return "\n".join(lines)


def build_compose_context(
    profile: RoleIntelligenceProfile,
    items: List[Any],
    risk_flags: List[RiskFlag],
    answers: Dict[str, Any],
) -> str:
    """Context for the composition pass: profile + extracted inventory +
    de-duplicated risks + the section-by-section generation instruction."""
    parts = [
        "## ROLE INTELLIGENCE PROFILE\n",
        _format_profile_for_context(profile),
    ]
    if risk_flags:
        parts.append("\n## DE-DUPLICATED RISK FLAGS\n")
        parts.append(_format_risk_flags(risk_flags))
    parts.append("\n## EXTRACTED KNOWLEDGE INVENTORY\n")
    parts.append(_format_extracted_items(items))
    parts.append("\n")
    parts.append(_build_generation_instruction(profile, answers, risk_flags))
    return "\n".join(parts)


def _format_profile_for_context(profile: RoleIntelligenceProfile) -> str:
    """Serialise the full profile into readable text for the LLM."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    tools = ", ".join(data.get("key_tools", [])) if data.get("key_tools") else "Not specified"
    recipients = ", ".join(data.get("recipients", [])) if data.get("recipients") else "Not specified"
    supporting = ", ".join(data.get("supporting_categories", [])) if data.get("supporting_categories") else "None"

    lines = [
        "### Business Context",
        f"- Company: {data.get('company_name') or 'Not specified'}",
        f"- Industry: {data.get('industry', 'Unknown')}",
        f"- Team Structure: {data.get('team_structure', 'Unknown')}",
        f"- Key Tools & Platforms: {tools}",
        f"- Culture Type: {data.get('culture_type', 'Unknown')}",
        f"- Previous Knowledge Loss: {data.get('previous_knowledge_loss') or 'None reported'}",
        "",
        "### Vacant Role",
        f"- Job Title: {data.get('job_title', 'Unknown')}",
        f"- Department: {data.get('department', 'Unknown')}",
        f"- Tenure: {data.get('tenure', 'Unknown')}",
        f"- Reports To: {data.get('reports_to', 'Unknown')}",
        f"- Direct Reports: {data.get('direct_reports') or 'None'}",
        f"- Role Type: {data.get('role_type', 'Unknown')}"
        f"{' (' + data['role_type_weighting'] + ')' if data.get('role_type_weighting') else ''}",
        f"- Immediate Risk if No Handover: {data.get('immediate_risk', 'Unknown')}",
        f"- Known Undocumented Areas: {data.get('undocumented_areas') or 'None identified'}",
        f"- Key External Relationships: {data.get('key_external_relationships') or 'None'}",
        f"- System Access / Credentials: {data.get('system_access_gaps') or 'None'}",
        "",
        "### Replacement Profile",
        f"- Hire Type: {data.get('hire_type', 'Unknown')}",
        f"- Experience Level: {data.get('replacement_experience_level', 'Unknown')}",
        f"- Most Important Context: {data.get('most_important_context', 'Unknown')}",
        f"- 90-Day Success Definition: {data.get('success_definition_90_days', 'Unknown')}",
        f"- Overlap Period: {data.get('overlap_period') or 'No overlap planned'}",
        "",
        "### Knowledge Priorities (ranked)",
        f"1. {data.get('priority_1', 'Unknown')}",
        f"2. {data.get('priority_2', 'Unknown')}",
        f"3. {data.get('priority_3', 'Unknown')}",
        f"Supporting: {supporting}",
        "",
        "### Output Preferences",
        f"- Document Destination: {data.get('document_destination', 'Unknown')}",
        f"- Recipients: {recipients}",
        f"- Confidential Sections: {data.get('confidential_sections') or 'None specified'}",
        "",
        "### Departure Context",
        f"- Departure Type: {data.get('departure_type', 'Unknown')}",
        f"- Leaving on Good Terms: {data.get('leaving_on_good_terms', 'Unknown')}",
        f"- Employee Aware: {data.get('employee_aware', 'Unknown')}",
        f"- Sensitivity Flags: {data.get('sensitivity_flags') or 'None'}",
        f"- Notice Period: {data.get('notice_period', 'Unknown')}",
    ]

    return "\n".join(lines)


def _format_risk_flags(risk_flags: List[RiskFlag]) -> str:
    """Group risk flags by severity and format each."""
    grouped: Dict[str, List[RiskFlag]] = {"critical": [], "high": [], "medium": []}
    for flag in risk_flags:
        severity = flag.severity.value if hasattr(flag.severity, "value") else str(flag.severity)
        grouped.setdefault(severity, []).append(flag)

    lines = []
    for severity in ["critical", "high", "medium"]:
        flags = grouped.get(severity, [])
        if not flags:
            continue
        label = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM"}.get(severity, severity.upper())
        lines.append(f"### {label}")
        for flag in flags:
            flag_type = flag.flag_type.value if hasattr(flag.flag_type, "value") else str(flag.flag_type)
            lines.append(f"- [{flag_type}] {flag.description}")
            lines.append(f"  Recommended action: {flag.recommended_action}")
            lines.append(f"  Source: {flag.source_block} (question {flag.source_question_index})")
        lines.append("")

    return "\n".join(lines)


def _question_instruction(block: str, index: int) -> Optional[str]:
    """Look up the question instruction that produced a given answer key."""
    if block == "role_orientation":
        questions = ROLE_ORIENTATION_QUESTIONS
    elif block == "closing_sequence":
        questions = CLOSING_QUESTIONS
    else:
        questions = STAGE2_BLOCK_QUESTIONS.get(block, [])
    if 0 <= index < len(questions):
        return questions[index]
    return None


def _format_answer_entries(block: str, key: str, value: Union[str, List[str]]) -> List[str]:
    """Format one question's answers, including the question that was asked.

    The question text is essential context — without it the synthesis model
    only sees 'Q3: <answer>' and has to guess what was asked. Answers are lists
    (original answer plus follow-up answers); plain strings are accepted for
    backward compatibility with older fixtures.
    """
    index = int(key.split(".")[1])
    lines = []

    question = _question_instruction(block, index)
    if question:
        lines.append(f"Q{index} (question asked): {question}")
    else:
        lines.append(f"Q{index}:")

    answer_list = value if isinstance(value, list) else [value]
    for i, answer in enumerate(answer_list):
        label = "A" if i == 0 else "A (follow-up)"
        lines.append(f"{label}: {answer}")

    return lines


def _format_answers_by_block(
    answers: Dict[str, Any],
    block_order: List[str],
    block_depths: Dict[str, str],
) -> str:
    """Group answers by block in priority order, as question/answer pairs."""
    lines = []

    def _append_block(block: str, heading: str) -> None:
        block_answers = {k: v for k, v in answers.items() if k.startswith(f"{block}.")}
        if not block_answers:
            return
        lines.append(heading)
        for key in sorted(block_answers.keys(), key=lambda k: int(k.split(".")[1])):
            lines.extend(_format_answer_entries(block, key, block_answers[key]))
        lines.append("")

    # Role orientation first
    _append_block("role_orientation", "### Role Orientation")

    # Knowledge blocks in priority order
    for block in block_order:
        depth = block_depths.get(block, "full")
        label = block.replace("_", " ").title()
        _append_block(block, f"### {label} [{depth} depth]")

    # Closing sequence
    _append_block("closing_sequence", "### Closing Questions")

    return "\n".join(lines)


def _format_conversation_excerpts(conversation_history: List[BaseMessage]) -> str:
    """Extract substantive human turns from the conversation history."""
    excerpts = []
    for msg in conversation_history:
        if isinstance(msg, HumanMessage) and len(msg.content) > 100:
            excerpts.append(msg.content)
            if len(excerpts) >= 30:
                break

    if not excerpts:
        return ""

    lines = []
    for i, excerpt in enumerate(excerpts, 1):
        lines.append(f"[{i}] {excerpt}")
    return "\n\n".join(lines)


def _build_generation_instruction(
    profile: RoleIntelligenceProfile,
    answers: Dict[str, Any],
    risk_flags: List[RiskFlag],
) -> str:
    """Build the generation instruction with profile-specific values."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    priority_1_label = str(data.get("priority_1", "Priority 1")).replace("_", " ").title()
    priority_2_label = str(data.get("priority_2", "Priority 2")).replace("_", " ").title()
    priority_3_label = str(data.get("priority_3", "Priority 3")).replace("_", " ").title()

    confidential = data.get("confidential_sections")
    if confidential:
        confidentiality_instruction = (
            f'CONFIDENTIALITY WARNING: The manager has flagged the following as confidential: '
            f'"{confidential}". Do NOT include confidential content in those sections — write '
            f'"[CONFIDENTIAL — content omitted per manager\'s instruction]" in place of any '
            f"sensitive material."
        )
    else:
        confidentiality_instruction = ""

    question_count = len(answers)
    risk_flag_count = len(risk_flags)
    recipients = ", ".join(data.get("recipients", [])) if data.get("recipients") else "the designated recipients"
    culture_type = str(data.get("culture_type", "mixed"))

    return GENERATION_INSTRUCTION_TEMPLATE.format(
        priority_1_label=priority_1_label,
        priority_2_label=priority_2_label,
        priority_3_label=priority_3_label,
        confidentiality_instruction=confidentiality_instruction,
        culture_type=culture_type,
        question_count=question_count,
        risk_flag_count=risk_flag_count,
        recipients=recipients,
    )
