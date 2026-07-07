import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import anthropic
from langchain_core.messages import BaseMessage, HumanMessage

from agents.stage3_document_generation.prompts import (
    COMPOSE_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    STAGE3_SYSTEM_PROMPT,
    build_compose_context,
    build_context_block,
    build_extraction_context,
)
from config import llm_provider
from config.settings import settings
from models.document_qa import DocumentQAReport
from models.knowledge_item import ExtractedKnowledge
from models.risk_flags import RiskFlag, merge_risk_flags
from models.role_intelligence_profile import RoleIntelligenceProfile

logger = logging.getLogger(__name__)


@dataclass
class GenerationRequest:
    session_id: str
    profile: RoleIntelligenceProfile
    conversation_history: List[BaseMessage]
    risk_flags: List[RiskFlag]
    answers: Dict[str, Any]
    block_order: List[str]
    block_depths: Dict[str, str]


@dataclass
class GenerationResult:
    session_id: str
    raw_markdown: str
    generation_metadata: Dict[str, Any] = field(default_factory=dict)
    extracted: Optional[ExtractedKnowledge] = None


# Sentinel markers that must appear in valid output
_REQUIRED_SENTINELS = [
    "Document Header",
    "Risk Summary",
    "Role Overview",
    "Key Relationships",
    "Systems and Access",
    "In-Flight Items",
    "Decision-Making Logic and Judgment",
    "Undocumented Knowledge",
    "Advice to Your Replacement",
    "Recommended Onboarding Plan",
    "Knowledge Gaps and Recommended Follow-Up",
]

# Knowledge Transfer appears multiple times (once per priority)
_KNOWLEDGE_TRANSFER_MIN_COUNT = 3

_MAX_TOKENS = 12000


def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=600.0,
        max_retries=3,
    )


def _stream_generation(client: anthropic.Anthropic, system: str, user_content: str) -> str:
    """Generate document using streaming to keep connection alive."""
    chunks = []
    with client.messages.stream(
        model=settings.primary_model,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
    return "".join(chunks)


def _validate_output(raw_text: str) -> List[str]:
    """Check all required sentinel markers are present.

    Returns list of missing section names (empty = valid).
    """
    missing = []
    for sentinel in _REQUIRED_SENTINELS:
        marker = f"### SECTION: {sentinel}"
        if marker not in raw_text:
            missing.append(sentinel)

    # Check Knowledge Transfer appears at least 3 times
    kt_count = raw_text.count("### SECTION: Knowledge Transfer")
    if kt_count < _KNOWLEDGE_TRANSFER_MIN_COUNT:
        missing.append(f"Knowledge Transfer (found {kt_count}, need {_KNOWLEDGE_TRANSFER_MIN_COUNT})")

    return missing


def _extract_knowledge(request: GenerationRequest) -> ExtractedKnowledge:
    """Extraction pass: turn the transcript into a structured inventory.

    Routed through llm_provider with structured output so it is injectable in
    tests and the eval harness. On failure, returns an empty inventory — the
    composer then falls back to composing from the raw profile/risks rather
    than blocking delivery entirely.
    """
    # Without a key (tests, local dev) and no injected fake, skip the network
    # call and compose from the raw profile/risks instead of hanging on retries.
    if not settings.anthropic_api_key and llm_provider._primary_factory is None:
        logger.info("session=%s stage=3 extraction skipped (no api key)", request.session_id)
        return ExtractedKnowledge(items=[])

    context = build_extraction_context(
        profile=request.profile,
        conversation_history=request.conversation_history,
        answers=request.answers,
        block_order=request.block_order,
        block_depths=request.block_depths,
    )
    try:
        llm = llm_provider.get_primary_llm(max_tokens=8000).with_structured_output(
            ExtractedKnowledge
        )
        messages = [
            HumanMessage(content=f"{EXTRACTION_SYSTEM_PROMPT}\n\n{context}"),
        ]
        result = llm.invoke(messages)
        logger.info(
            "session=%s stage=3 extraction items=%d gaps=%d",
            request.session_id, len(result.items), len(result.gaps()),
        )
        return result
    except Exception as e:
        logger.error(
            "session=%s stage=3 event=extraction_failure error=%s — composing without inventory",
            request.session_id, e,
        )
        return ExtractedKnowledge(items=[])


def _compose_with_retry(session_id: str, system: str, context: str) -> str:
    """Stream a document, validate structure, retry once on missing sections."""
    client = _get_client()
    raw_markdown = _stream_generation(client, system, context)

    missing = _validate_output(raw_markdown)
    if missing:
        logger.warning(
            "session=%s stage=3 missing sections on first attempt: %s — retrying",
            session_id, missing,
        )
        retry_context = (
            context
            + "\n\n## CRITICAL REMINDER\n"
            + "Your previous attempt was missing these required sections. "
            + "You MUST include ALL of them:\n"
            + "\n".join(f"- ### SECTION: {name}" for name in missing)
        )
        raw_markdown = _stream_generation(client, system, retry_context)
        still_missing = _validate_output(raw_markdown)
        if still_missing:
            logger.error(
                "session=%s stage=3 still missing sections after retry: %s",
                session_id, still_missing,
            )
            raise RuntimeError(
                f"Document generation failed: missing sections after retry: {still_missing}"
            )
    return raw_markdown


_QA_JUDGE_PROMPT = """\
You are a quality reviewer for employee handover documents. Score the document below against \
the rubric. Be strict — this document is the only record a successor will have.

## Identified risk flags (every one must appear in the Risk Summary with a recommended action)
{risk_summary}

## Rubric (score each 0.0–1.0)
- completeness: all required sections present and substantively filled (not placeholder text)
- risk_coverage: every identified risk flag above appears in the Risk Summary with an action
- actionability: a competent but new successor could actually act on this
- gap_honesty: missing/uncertain areas are flagged as [GAP: ...], not glossed over

List specific, actionable issues to fix. If the document is strong, return an empty issues list.

## DOCUMENT
{document}
"""


def _review_document(
    session_id: str, raw_markdown: str, risk_flags: List[RiskFlag]
) -> Optional[DocumentQAReport]:
    """LLM-judge QA pass. Returns None if unavailable (never blocks delivery)."""
    if not settings.anthropic_api_key and llm_provider._primary_factory is None:
        return None

    risk_summary = "\n".join(
        f"- [{f.flag_type.value if hasattr(f.flag_type, 'value') else f.flag_type}] {f.description}"
        for f in risk_flags
    ) or "(none identified)"
    prompt = _QA_JUDGE_PROMPT.format(risk_summary=risk_summary, document=raw_markdown)

    try:
        llm = llm_provider.get_primary_llm(max_tokens=1500).with_structured_output(
            DocumentQAReport
        )
        report = llm.invoke([HumanMessage(content=prompt)])
        logger.info(
            "session=%s stage=3 qa_review overall=%.2f completeness=%.2f risk=%.2f action=%.2f gaps=%.2f",
            session_id, report.overall, report.completeness, report.risk_coverage,
            report.actionability, report.gap_honesty,
        )
        return report
    except Exception as e:
        logger.error(
            "session=%s stage=3 event=qa_review_failure error=%s — accepting document as-is",
            session_id, e,
        )
        return None


def generate_document(request: GenerationRequest) -> GenerationResult:
    """Generate the complete handover document from Stage 2 data.

    Default (`synthesis_mode="compose"`): extract a structured knowledge
    inventory first, then compose the document from it, so nothing buried in a
    long transcript is silently dropped. `synthesis_mode="single"` keeps the
    legacy one-pass path. Risk flags are de-duplicated before either path.

    Raises:
        ValueError: If request has invalid preconditions.
        RuntimeError: If composition fails structural validation twice.
    """
    if not request.session_id:
        raise ValueError("session_id is required")
    if request.profile is None:
        raise ValueError("profile is required")

    session_id = request.session_id
    merged_flags = merge_risk_flags(request.risk_flags)

    logger.info(
        "session=%s stage=3 node=generate_document mode=%s model=%s answers=%d flags=%d→%d",
        session_id, settings.synthesis_mode, settings.primary_model,
        len(request.answers), len(request.risk_flags), len(merged_flags),
    )

    extracted: Optional[ExtractedKnowledge] = None

    if settings.synthesis_mode == "single":
        context = build_context_block(
            profile=request.profile,
            conversation_history=request.conversation_history,
            risk_flags=merged_flags,
            answers=request.answers,
            block_order=request.block_order,
            block_depths=request.block_depths,
        )
        compose_system = STAGE3_SYSTEM_PROMPT
        raw_markdown = _compose_with_retry(session_id, compose_system, context)
    else:
        extracted = _extract_knowledge(request)
        context = build_compose_context(
            profile=request.profile,
            items=extracted.items,
            risk_flags=merged_flags,
            answers=request.answers,
        )
        compose_system = COMPOSE_SYSTEM_PROMPT
        raw_markdown = _compose_with_retry(session_id, compose_system, context)

    # QA gate: score the document; below threshold, regenerate once with the
    # judge's concrete issues appended. We accept the second attempt regardless
    # — the gate raises quality on average without risking an infinite loop or
    # blocking delivery.
    qa_report = None
    if settings.enable_qa_gate:
        qa_report = _review_document(session_id, raw_markdown, merged_flags)
        if qa_report and qa_report.overall < settings.qa_gate_min_score:
            logger.warning(
                "session=%s stage=3 qa_gate below threshold (%.2f < %.2f) — regenerating. issues=%s",
                session_id, qa_report.overall, settings.qa_gate_min_score, qa_report.issues,
            )
            feedback = (
                context
                + "\n\n## QA FEEDBACK — FIX THESE ISSUES\n"
                + "A reviewer scored a previous draft below the quality bar. "
                + "Address every issue while keeping all required sections:\n"
                + "\n".join(f"- {issue}" for issue in qa_report.issues)
            )
            raw_markdown = _compose_with_retry(session_id, compose_system, feedback)
            qa_report = _review_document(session_id, raw_markdown, merged_flags) or qa_report

    metadata = {
        "question_count": len(request.answers),
        "risk_flag_count": len(merged_flags),
        "risk_flag_count_raw": len(request.risk_flags),
        "synthesis_mode": settings.synthesis_mode,
        "extracted_item_count": len(extracted.items) if extracted else None,
        "qa_score": qa_report.overall if qa_report else None,
        "qa_issues": qa_report.issues if qa_report else None,
        "model": settings.primary_model,
    }

    logger.info("session=%s stage=3 document generated successfully", session_id)

    return GenerationResult(
        session_id=session_id,
        raw_markdown=raw_markdown,
        generation_metadata=metadata,
        extracted=extracted,
    )
