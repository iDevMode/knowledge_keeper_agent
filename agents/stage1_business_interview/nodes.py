import json
import logging
import re
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


def greeting_node(state: Stage1State) -> Dict[str, Any]:
    """Output the greeting message and set initial block/index."""
    logger.info("session=%s stage=1 node=greeting", state.get("session_id", ""))
    return {
        "current_block": STAGE1_BLOCKS[0],
        # The greeting text *is* question 0, and process_answer runs before
        # ask_question. Index must therefore stay at 0 so the answer to the
        # greeting is filed under business_context.0; advance_question then
        # moves to 1. Setting 1 here filed that answer as .1 and skipped q1.
        "current_question_index": 0,
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
    response = llm.invoke(messages)
    response_text = response.content

    # Validate single question — retry once if needed
    if not validate_single_question(response_text):
        logger.warning("session=%s Multiple questions detected, re-prompting", session_id)
        messages.append(AIMessage(content=response_text))
        messages.append(HumanMessage(content=SINGLE_QUESTION_REPROMPT))
        response = llm.invoke(messages)
        response_text = response.content

    return {
        "conversation_history": [AIMessage(content=response_text)],
        "last_agent_message": response_text,
        "followup_count": 0,
        "pending_followup": None,
    }


def process_answer_node(state: Stage1State) -> Dict[str, Any]:
    """Store the user's answer keyed by block.index."""
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
    answers[key] = answer_text

    return {
        "answers": answers,
        "followup_count": 0,
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

Respond with ONLY a JSON object (no markdown, no explanation):
{{"needs_followup": true/false, "reason": "brief reason", "suggested_followup": "the follow-up question to ask if needed"}}
"""

    try:
        llm = _get_classifier_llm()
        response = llm.invoke([HumanMessage(content=classifier_prompt)])
        result = json.loads(response.content.strip())

        if result.get("needs_followup", False):
            return {"pending_followup": result.get("suggested_followup", "")}
        return {"pending_followup": None}
    except Exception as e:
        # Default to no follow-up on classifier failure
        logger.warning("session=%s Followup classifier failed: %s — defaulting to no followup", session_id, e)
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
    response = llm.invoke(messages)
    response_text = response.content

    if not validate_single_question(response_text):
        messages.append(AIMessage(content=response_text))
        messages.append(HumanMessage(content=SINGLE_QUESTION_REPROMPT))
        response = llm.invoke(messages)
        response_text = response.content

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


def _normalise_reply(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text.strip().lower()).split())


# Phrases that unambiguously confirm the profile. Several contain words that are
# also correction cues ("change", "corrections"), so these are removed from the
# message before cue scanning — otherwise "nothing to change" reads as a change.
# Run through _normalise_reply so punctuation variants ("that's" vs "thats")
# cannot drift out of sync with how replies are normalised.
_CONFIRMATION_PHRASES = tuple(
    _normalise_reply(p)
    for p in (
        "nothing to change",
        "no changes",
        "no corrections",
        "nothing to add",
        "nothing missing",
        "all correct",
        "all good",
        "looks good",
        "looks correct",
        "looks right",
        "that's correct",
        "that is correct",
        "that's right",
        "that is right",
        "sounds right",
        "sounds good",
        "spot on",
        "lgtm",
    )
)

# Single words that, on their own, read as agreement.
_AFFIRMATIVE_WORDS = frozenset(
    {"yes", "yep", "yeah", "correct", "confirmed", "confirm", "approved",
     "perfect", "great", "fine", "good", "ok", "okay"}
)

# Cues that the manager is asking for something to be different. Presence of any
# of these means we route to corrections even alongside an affirmation, because
# "yes, but change X" is a correction, not a confirmation.
_CORRECTION_CUES = frozenset(
    {"but", "however", "although", "though", "except", "actually", "instead",
     "change", "changed", "correct_that", "amend", "update", "add", "remove",
     "delete", "replace", "swap", "fix", "wrong", "incorrect", "missing",
     "should", "shouldn't", "isn't", "not", "rather"}
)


def route_after_profile_review(state: Stage1State) -> str:
    """Route to corrections or finalise based on the manager's reply.

    Deliberately biased towards `corrections`: re-presenting the profile is
    recoverable, whereas finalising discards the manager's edit and completes the
    session irreversibly. A bare substring scan previously routed
    "yes, but change the job title" to finalise and dropped the correction.
    """
    history = state["conversation_history"]
    last_human = None
    for msg in reversed(history):
        if isinstance(msg, HumanMessage):
            last_human = msg.content
            break

    if last_human is None:
        # No reply to evaluate — do not finalise on the manager's behalf.
        return "corrections"

    normalised = _normalise_reply(last_human)

    # Strip whole confirmation phrases first so their internal words ("change",
    # "corrections") are not mistaken for correction cues.
    residue = normalised
    has_confirmation_phrase = False
    for phrase in _CONFIRMATION_PHRASES:
        if phrase in residue:
            has_confirmation_phrase = True
            residue = residue.replace(phrase, " ")

    residue_words = set(residue.split())
    if residue_words & _CORRECTION_CUES:
        return "corrections"

    if has_confirmation_phrase:
        return "finalise"

    # Otherwise only a short, purely affirmative reply counts as confirmation.
    words = normalised.split()
    if words and len(words) <= 4 and (set(words) & _AFFIRMATIVE_WORDS):
        return "finalise"

    return "corrections"
