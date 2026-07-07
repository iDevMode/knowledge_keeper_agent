import json
import logging
from typing import Any, Dict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.stage1_business_interview.prompts import (
    BLOCK_QUESTIONS,
    GREETING_MESSAGE,
    PROFILE_GENERATION_INSTRUCTION,
    PROFILE_REVIEW_MESSAGE_TEMPLATE,
    SESSION_CLOSE_MESSAGE,
    SINGLE_QUESTION_REPROMPT,
    STAGE1_SYSTEM_PROMPT,
)
from agents.stage1_business_interview.state import Stage1State
from api.session_manager import get_session_store
from config.constants import MAX_FOLLOWUPS_PER_QUESTION, STAGE1_BLOCKS, STAGE1_BLOCK_QUESTION_COUNTS
from config.settings import settings
from models.classifier_outputs import ConfirmationIntent, FollowupDecision
from models.role_intelligence_profile import RoleIntelligenceProfile

logger = logging.getLogger(__name__)


def _get_primary_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.primary_model,
        api_key=settings.anthropic_api_key,
        max_tokens=2048,
    )


def _get_classifier_llm() -> ChatAnthropic:
    return ChatAnthropic(
        model=settings.classifier_model,
        api_key=settings.anthropic_api_key,
        max_tokens=512,
    )


def validate_single_question(text: str) -> bool:
    """Check that the text contains at most one question mark.

    Returns True if valid (0 or 1 question marks).
    """
    return text.count("?") <= 1


def invoke_with_single_question_retry(llm, messages: list, session_id: str) -> str:
    """Invoke the LLM, re-prompting once if it asks multiple questions.

    The retry is re-validated too: if it is still invalid, the attempt with
    fewer question marks is used and a violation is logged for monitoring.
    """
    response = llm.invoke(messages)
    first_text = response.content
    if validate_single_question(first_text):
        return first_text

    logger.warning("session=%s Multiple questions detected, re-prompting", session_id)
    retry_messages = [
        *messages,
        AIMessage(content=first_text),
        HumanMessage(content=SINGLE_QUESTION_REPROMPT),
    ]
    retry_text = llm.invoke(retry_messages).content
    if validate_single_question(retry_text):
        return retry_text

    logger.error(
        "session=%s event=single_question_violation both attempts contained multiple questions",
        session_id,
    )
    return min(first_text, retry_text, key=lambda t: t.count("?"))


def greeting_node(state: Stage1State) -> Dict[str, Any]:
    """Output the greeting message and set initial block/index."""
    logger.info("session=%s stage=1 node=greeting", state.get("session_id", ""))
    return {
        "current_block": STAGE1_BLOCKS[0],
        "current_question_index": 1,  # greeting covers q0 of business_context
        "conversation_history": [AIMessage(content=GREETING_MESSAGE)],
        "last_agent_message": GREETING_MESSAGE,
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "profile_confirmed": False,
        "session_complete": False,
    }


def ask_question_node(state: Stage1State) -> Dict[str, Any]:
    """Ask the next question in the current block using the primary LLM."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    logger.info("session=%s stage=1 block=%s question=%d node=ask_question", session_id, block, index)

    questions = BLOCK_QUESTIONS.get(block, [])
    if index >= len(questions):
        # This shouldn't happen — advance_question_node should handle block transitions
        return {}

    instruction = questions[index]

    combined_system = (
        f"{STAGE1_SYSTEM_PROMPT}\n\n"
        f"## CURRENT INSTRUCTION\n\n{instruction}\n\nRemember: ask exactly ONE question."
    )
    messages = [
        SystemMessage(content=combined_system),
        *state["conversation_history"],
    ]

    llm = _get_primary_llm()
    response_text = invoke_with_single_question_retry(llm, messages, session_id)

    return {
        "conversation_history": [AIMessage(content=response_text)],
        "last_agent_message": response_text,
        "followup_count": 0,
        "pending_followup": None,
    }


def process_answer_node(state: Stage1State) -> Dict[str, Any]:
    """Append the user's answer to the answer list keyed by block.index.

    Answers are lists because follow-up answers land on the same question key —
    they must accumulate, not overwrite the original answer.
    """
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    # The last message in history should be the user's answer
    history = state["conversation_history"]
    last_msg = history[-1] if history else None
    answer_text = last_msg.content if last_msg and isinstance(last_msg, HumanMessage) else ""

    logger.info("session=%s stage=1 block=%s question=%d node=process_answer", session_id, block, index)

    answers = dict(state.get("answers", {}))
    key = f"{block}.{index}"
    answers[key] = [*answers.get(key, []), answer_text]

    # NOTE: followup_count is deliberately NOT reset here — this node also
    # processes answers to follow-up questions, and resetting on every answer
    # would defeat the MAX_FOLLOWUPS_PER_QUESTION limit. The counter resets
    # when the interview advances (ask_question / advance_question).
    return {
        "answers": answers,
    }


def followup_classifier_node(state: Stage1State) -> Dict[str, Any]:
    """Use the classifier LLM to decide if a follow-up question is needed."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")
    followup_count = state.get("followup_count", 0)

    logger.info(
        "session=%s stage=1 block=%s question=%d followup_count=%d node=followup_classifier",
        session_id, block, index, followup_count,
    )

    # If already at max follow-ups, skip
    if followup_count >= MAX_FOLLOWUPS_PER_QUESTION:
        return {"pending_followup": None}

    history = state["conversation_history"]
    # Get last question (AI) and answer (Human)
    last_human = None
    last_ai = None
    for msg in reversed(history):
        if isinstance(msg, HumanMessage) and last_human is None:
            last_human = msg.content
        elif isinstance(msg, AIMessage) and last_ai is None:
            last_ai = msg.content
        if last_human and last_ai:
            break

    if not last_human or not last_ai:
        return {"pending_followup": None}

    classifier_prompt = f"""\
You are a follow-up classifier. Given a question and answer from a business interview, decide whether
a follow-up question is needed.

A follow-up is needed when:
- The answer is vague or generic
- The answer hints at something deeper that wasn't fully explained
- Key details are missing that would be important for the profile

A follow-up is NOT needed when:
- The answer is clear and substantive
- The person said "I don't know" (respect that and move on)
- The answer is a straightforward factual response

Question asked: {last_ai}

Answer received: {last_human}
"""

    try:
        llm = _get_classifier_llm().with_structured_output(FollowupDecision)
        result = llm.invoke([HumanMessage(content=classifier_prompt)])

        if result.needs_followup and result.suggested_followup:
            return {"pending_followup": result.suggested_followup}
        return {"pending_followup": None}
    except Exception as e:
        # Default to no follow-up on classifier failure
        logger.error(
            "session=%s event=classifier_failure classifier=followup error=%s — defaulting to no followup",
            session_id, e,
        )
        return {"pending_followup": None}


def followup_question_node(state: Stage1State) -> Dict[str, Any]:
    """Ask a follow-up question based on the classifier's suggestion."""
    session_id = state.get("session_id", "")
    suggested = state.get("pending_followup", "")
    followup_count = state.get("followup_count", 0)

    logger.info("session=%s stage=1 node=followup_question followup_count=%d", session_id, followup_count + 1)

    instruction = (
        f"The previous answer needs a follow-up. Here is a suggested follow-up question: "
        f'"{suggested}"\n\n'
        "Use this as a guide but phrase the follow-up naturally in your own words, "
        "acknowledging what the person just said. Ask exactly ONE follow-up question."
    )

    combined_system = f"{STAGE1_SYSTEM_PROMPT}\n\n{instruction}"
    messages = [
        SystemMessage(content=combined_system),
        *state["conversation_history"],
    ]

    llm = _get_primary_llm()
    response_text = invoke_with_single_question_retry(llm, messages, session_id)

    return {
        "conversation_history": [AIMessage(content=response_text)],
        "last_agent_message": response_text,
        "followup_count": followup_count + 1,
        "pending_followup": None,
    }


def advance_question_node(state: Stage1State) -> Dict[str, Any]:
    """Advance to the next question or next block."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    max_for_block = STAGE1_BLOCK_QUESTION_COUNTS.get(block, 0)
    next_index = index + 1

    if next_index < max_for_block:
        # Stay in same block, next question
        logger.info("session=%s stage=1 advancing to %s.%d", session_id, block, next_index)
        return {
            "current_question_index": next_index,
            "followup_count": 0,
            "pending_followup": None,
        }

    # Move to next block
    try:
        current_block_idx = STAGE1_BLOCKS.index(block)
    except ValueError:
        current_block_idx = len(STAGE1_BLOCKS)

    next_block_idx = current_block_idx + 1

    if next_block_idx < len(STAGE1_BLOCKS):
        next_block = STAGE1_BLOCKS[next_block_idx]
        logger.info("session=%s stage=1 advancing to block %s", session_id, next_block)
        return {
            "current_block": next_block,
            "current_question_index": 0,
            "followup_count": 0,
            "pending_followup": None,
        }

    # All blocks complete
    logger.info("session=%s stage=1 all blocks complete", session_id)
    return {
        "current_block": "__complete__",
        "current_question_index": 0,
        "followup_count": 0,
        "pending_followup": None,
    }


def profile_generation_node(state: Stage1State) -> Dict[str, Any]:
    """Generate the Role Intelligence Profile from all collected answers."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=1 node=profile_generation", session_id)

    llm = _get_primary_llm()

    messages = [
        SystemMessage(content=STAGE1_SYSTEM_PROMPT),
        *state["conversation_history"],
        SystemMessage(content=PROFILE_GENERATION_INSTRUCTION),
    ]

    # Use with_structured_output for Pydantic parsing
    structured_llm = llm.with_structured_output(RoleIntelligenceProfile)

    try:
        profile = structured_llm.invoke(messages)
    except Exception as first_error:
        logger.warning("session=%s Profile generation failed: %s — retrying with explicit errors", session_id, first_error)
        # Retry once with field-level error feedback
        retry_instruction = (
            f"{PROFILE_GENERATION_INSTRUCTION}\n\n"
            f"The previous attempt failed with: {first_error}\n"
            "Please ensure all required fields are present and correctly typed."
        )
        messages[-1] = SystemMessage(content=retry_instruction)
        try:
            profile = structured_llm.invoke(messages)
        except Exception as second_error:
            logger.error("session=%s Profile generation failed on retry: %s", session_id, second_error)
            raise

    return {
        "role_intelligence_profile": profile,
    }


def profile_review_node(state: Stage1State) -> Dict[str, Any]:
    """Present the generated profile for manager review."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=1 node=profile_review", session_id)

    profile = state.get("role_intelligence_profile")
    if profile is None:
        return {
            "conversation_history": [AIMessage(content="I encountered an issue generating the profile. Let me try again.")],
            "last_agent_message": "I encountered an issue generating the profile. Let me try again.",
        }

    # Format the profile for display
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    # Format agent flags
    flags = data.get("agent_flags", [])
    flags_formatted = "\n".join(f"- {f}" for f in flags) if flags else "- None"

    # Format list fields
    tools = ", ".join(data.get("key_tools", [])) if data.get("key_tools") else "None listed"
    recipients = ", ".join(data.get("recipients", [])) if data.get("recipients") else "None listed"
    supporting = ", ".join(data.get("supporting_categories", [])) if data.get("supporting_categories") else "None"

    review_msg = PROFILE_REVIEW_MESSAGE_TEMPLATE.format(
        company_name=data.get("company_name") or "Not specified",
        industry=data.get("industry", ""),
        team_structure=data.get("team_structure", ""),
        key_tools=tools,
        culture_type=data.get("culture_type", ""),
        previous_knowledge_loss=data.get("previous_knowledge_loss") or "None reported",
        job_title=data.get("job_title", ""),
        department=data.get("department", ""),
        tenure=data.get("tenure", ""),
        reports_to=data.get("reports_to", ""),
        direct_reports=data.get("direct_reports") or "None",
        role_type=data.get("role_type", ""),
        role_type_weighting=data.get("role_type_weighting") or "Not specified",
        immediate_risk=data.get("immediate_risk", ""),
        undocumented_areas=data.get("undocumented_areas") or "None identified",
        key_external_relationships=data.get("key_external_relationships") or "None",
        system_access_gaps=data.get("system_access_gaps") or "None",
        hire_type=data.get("hire_type", ""),
        replacement_experience_level=data.get("replacement_experience_level", ""),
        most_important_context=data.get("most_important_context", ""),
        success_definition_90_days=data.get("success_definition_90_days", ""),
        overlap_period=data.get("overlap_period") or "No overlap planned",
        priority_1=data.get("priority_1", ""),
        priority_2=data.get("priority_2", ""),
        priority_3=data.get("priority_3", ""),
        supporting_categories=supporting,
        document_destination=data.get("document_destination", ""),
        recipients=recipients,
        confidential_sections=data.get("confidential_sections") or "None specified",
        existing_template=data.get("existing_template") or "None — will generate best-practice structure",
        departure_type=data.get("departure_type", ""),
        leaving_on_good_terms=data.get("leaving_on_good_terms", ""),
        employee_aware=data.get("employee_aware", ""),
        sensitivity_flags=data.get("sensitivity_flags") or "None",
        notice_period=data.get("notice_period", ""),
        agent_flags_formatted=flags_formatted,
    )

    return {
        "conversation_history": [AIMessage(content=review_msg)],
        "last_agent_message": review_msg,
    }


def await_review_response_node(state: Stage1State) -> Dict[str, Any]:
    """Interrupt gate: pauses the graph so the manager can respond to the review.

    Without this gate the corrections/finalise routing would evaluate against
    the manager's last *interview* answer instead of their review response.
    """
    return {}


def corrections_node(state: Stage1State) -> Dict[str, Any]:
    """Apply corrections to the profile based on manager feedback."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=1 node=corrections", session_id)

    profile = state["role_intelligence_profile"]
    profile_json = profile.model_dump_json() if hasattr(profile, "model_dump_json") else json.dumps(profile)

    # Get the corrections from the last human message
    history = state["conversation_history"]
    corrections = ""
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            corrections = msg.content
            break

    instruction = (
        f"The manager has reviewed the Role Intelligence Profile and provided corrections:\n\n"
        f'"{corrections}"\n\n'
        f"Here is the current profile:\n{profile_json}\n\n"
        "Apply the corrections and return the updated profile as valid JSON."
    )

    messages = [
        SystemMessage(content=STAGE1_SYSTEM_PROMPT),
        SystemMessage(content=instruction),
    ]

    llm = _get_primary_llm()
    structured_llm = llm.with_structured_output(RoleIntelligenceProfile)

    try:
        updated_profile = structured_llm.invoke(messages)
    except Exception as e:
        logger.error("session=%s Corrections failed: %s", session_id, e)
        # Return original profile if corrections fail
        return {
            "conversation_history": [
                AIMessage(content="I had trouble applying those corrections. Could you try rephrasing what needs to change?")
            ],
            "last_agent_message": "I had trouble applying those corrections. Could you try rephrasing what needs to change?",
        }

    return {
        "role_intelligence_profile": updated_profile,
    }


def finalise_node(state: Stage1State) -> Dict[str, Any]:
    """Store the confirmed profile via session manager."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=1 node=finalise", session_id)

    profile = state["role_intelligence_profile"]
    store = get_session_store()
    store.store_profile(session_id, profile)

    return {
        "profile_confirmed": True,
    }


def session_close_node(state: Stage1State) -> Dict[str, Any]:
    """Output the closing message and mark session complete."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=1 node=session_close", session_id)

    return {
        "conversation_history": [AIMessage(content=SESSION_CLOSE_MESSAGE)],
        "last_agent_message": SESSION_CLOSE_MESSAGE,
        "session_complete": True,
    }


# Routing functions for conditional edges

def route_after_followup_classifier(state: Stage1State) -> str:
    """Route to followup_question or advance based on classifier result."""
    pending = state.get("pending_followup")
    followup_count = state.get("followup_count", 0)

    if pending and followup_count < MAX_FOLLOWUPS_PER_QUESTION:
        return "followup_question"
    return "advance_question"


def route_after_advance(state: Stage1State) -> str:
    """Route to ask_question (more questions) or profile_generation (all done)."""
    if state.get("current_block") == "__complete__":
        return "profile_generation"
    return "ask_question"


CONFIRMATION_INTENT_PROMPT = """\
A manager has just reviewed a generated Role Intelligence Profile and responded.
Classify their intent:

- "confirm": they approve the profile as-is, with NO changes requested
- "correct": they request ANY change, addition, or fix — even if they also say it
  mostly looks good (e.g. "looks good but the title should be Head of Ops" is "correct")
- "unclear": the intent cannot be determined

Manager's response: {response}
"""


def _confirmation_heuristic(text: str) -> str:
    """Conservative fallback when the intent classifier is unavailable.

    Only finalises on short, unambiguous confirmations — anything that hints at
    a change routes to corrections, where the manager can clarify.
    """
    normalised = text.strip().lower().rstrip(".!,")
    confirmation_signals = [
        "looks good", "looks correct", "that's correct", "that's right",
        "all good", "perfect", "yes", "confirm", "no changes", "nothing to change",
        "all correct", "spot on", "no corrections", "approved", "lgtm",
    ]
    # NB: avoid substrings of confirmation phrases ("change" is inside "no changes")
    change_signals = [
        "but ", "except", "however", "although", "should be",
        "actually", "instead", "wrong", "incorrect", "missing", "add ",
    ]

    has_confirmation = any(signal in normalised for signal in confirmation_signals)
    has_change = any(signal in normalised for signal in change_signals)

    if has_confirmation and not has_change and len(normalised) <= 60:
        return "finalise"
    return "corrections"


def route_after_profile_review(state: Stage1State) -> str:
    """Route to corrections or finalise based on the manager's review response.

    Uses a Haiku intent classifier — keyword matching mis-finalises on responses
    like "no changes needed except the job title". Falls back to a conservative
    heuristic if the classifier call fails.
    """
    session_id = state.get("session_id", "")
    history = state["conversation_history"]
    last_human = None
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            last_human = msg.content
            break

    if last_human is None:
        return "finalise"

    try:
        llm = _get_classifier_llm().with_structured_output(ConfirmationIntent)
        result = llm.invoke([HumanMessage(content=CONFIRMATION_INTENT_PROMPT.format(response=last_human))])
        if result.intent == "confirm":
            return "finalise"
        # "correct" and "unclear" both go to corrections — the corrections node
        # asks the manager to clarify if it can't apply the change
        return "corrections"
    except Exception as e:
        logger.error(
            "session=%s event=classifier_failure classifier=confirmation_intent error=%s — using heuristic",
            session_id, e,
        )
        return _confirmation_heuristic(last_human)
