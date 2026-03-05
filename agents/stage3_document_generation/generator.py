import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from agents.stage3_document_generation.prompts import STAGE3_SYSTEM_PROMPT, build_context_block
from config.settings import settings
from models.risk_flags import RiskFlag
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


def _get_generation_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.primary_model,
        api_key=settings.anthropic_api_key,
        max_tokens=8192,
    )


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


def generate_document(request: GenerationRequest) -> GenerationResult:
    """Generate the complete handover document from Stage 2 data.

    Makes a single LLM call (retries once on structural validation failure).

    Raises:
        ValueError: If request has invalid preconditions.
        RuntimeError: If both LLM attempts fail structural validation.
    """
    if not request.session_id:
        raise ValueError("session_id is required")
    if request.profile is None:
        raise ValueError("profile is required")

    session_id = request.session_id

    logger.info(
        "session=%s stage=3 node=generate_document model=%s answers=%d flags=%d",
        session_id, settings.primary_model, len(request.answers), len(request.risk_flags),
    )

    # Build full context
    context = build_context_block(
        profile=request.profile,
        conversation_history=request.conversation_history,
        risk_flags=request.risk_flags,
        answers=request.answers,
        block_order=request.block_order,
        block_depths=request.block_depths,
    )

    llm = _get_generation_llm()
    messages = [
        SystemMessage(content=STAGE3_SYSTEM_PROMPT),
        HumanMessage(content=context),
    ]

    # First attempt
    response = llm.invoke(messages)
    raw_markdown = response.content

    # Validate structure
    missing = _validate_output(raw_markdown)

    if missing:
        logger.warning(
            "session=%s stage=3 missing sections on first attempt: %s — retrying",
            session_id, missing,
        )
        # Retry with explicit instruction about missing sections
        retry_msg = (
            "The document you generated is missing the following required sections. "
            "Please regenerate the COMPLETE document including ALL sections:\n\n"
            + "\n".join(f"- ### SECTION: {name}" for name in missing)
        )
        messages.append(SystemMessage(content=retry_msg))
        response = llm.invoke(messages)
        raw_markdown = response.content

        # Validate again
        still_missing = _validate_output(raw_markdown)
        if still_missing:
            logger.error(
                "session=%s stage=3 still missing sections after retry: %s",
                session_id, still_missing,
            )
            raise RuntimeError(
                f"Document generation failed: missing sections after retry: {still_missing}"
            )

    metadata = {
        "question_count": len(request.answers),
        "risk_flag_count": len(request.risk_flags),
        "model": settings.primary_model,
    }

    logger.info("session=%s stage=3 document generated successfully", session_id)

    return GenerationResult(
        session_id=session_id,
        raw_markdown=raw_markdown,
        generation_metadata=metadata,
    )
