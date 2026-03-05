import json
import logging
from typing import Any, Dict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.stage2_employee_interview.prompts import (
    BLOCK_QUESTIONS,
    CLOSING_MESSAGE,
    CLOSING_QUESTIONS,
    RISK_FLAG_CLASSIFIER_PROMPT_TEMPLATE,
    ROLE_ORIENTATION_QUESTIONS,
    SINGLE_QUESTION_REPROMPT,
    build_greeting_message,
    build_system_prompt,
)
from agents.stage2_employee_interview.state import Stage2State
from api.session_manager import get_session_store
from config.constants import (
    LIGHT_TOUCH_MAX_QUESTIONS,
    MAX_FOLLOWUPS_PER_QUESTION,
    STAGE2_BLOCK_QUESTION_COUNTS,
    STAGE2_CLOSING_QUESTION_COUNT,
    STAGE2_ROLE_ORIENTATION_QUESTION_COUNT,
    KnowledgeBlock,
)
from config.settings import settings
from models.knowledge_blocks import determine_block_order_and_depth
from models.risk_flags import RiskFlag

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
    """Check that the text contains at most one question mark."""
    return text.count("?") <= 1


def load_profile_node(state: Stage2State) -> Dict[str, Any]:
    """Load the Role Intelligence Profile and compute block order/depth."""
    stage1_session_id = state["stage1_session_id"]
    session_id = state.get("session_id", "")

    logger.info("session=%s stage=2 node=load_profile stage1_session=%s", session_id, stage1_session_id)

    store = get_session_store()
    profile = store.get_profile(stage1_session_id)

    if profile is None:
        raise ValueError(f"No profile found for stage1 session {stage1_session_id}")

    ordered_blocks, depth_map = determine_block_order_and_depth(
        profile.priority_1,
        profile.priority_2,
        profile.priority_3,
        profile.supporting_categories,
    )

    block_order = [block.value for block in ordered_blocks]
    block_depths = {block.value: depth.value for block, depth in depth_map.items()}

    logger.info("session=%s stage=2 block_order=%s", session_id, block_order)

    return {
        "profile": profile,
        "block_order": block_order,
        "block_depths": block_depths,
    }


def greeting_node(state: Stage2State) -> Dict[str, Any]:
    """Output the greeting message with first role orientation question."""
    session_id = state.get("session_id", "")
    profile = state["profile"]

    logger.info("session=%s stage=2 node=greeting", session_id)

    greeting = build_greeting_message(profile)

    return {
        "current_phase": "role_orientation",
        "current_block": "role_orientation",
        "current_question_index": 1,  # greeting covers q0
        "current_block_index": 0,
        "conversation_history": [AIMessage(content=greeting)],
        "last_agent_message": greeting,
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "risk_flags": [],
        "session_complete": False,
    }


def ask_question_node(state: Stage2State) -> Dict[str, Any]:
    """Ask the next question based on current phase/block/index."""
    phase = state["current_phase"]
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    logger.info(
        "session=%s stage=2 phase=%s block=%s question=%d node=ask_question",
        session_id, phase, block, index,
    )

    # Look up the right question instruction
    if phase == "role_orientation":
        questions = ROLE_ORIENTATION_QUESTIONS
    elif phase == "closing_sequence":
        questions = CLOSING_QUESTIONS
    else:
        questions = BLOCK_QUESTIONS.get(block, [])

    if index >= len(questions):
        return {}

    instruction = questions[index]

    system_prompt = build_system_prompt(state["profile"])
    combined_system = (
        f"{system_prompt}\n\n"
        f"## CURRENT INSTRUCTION\n\n{instruction}\n\nRemember: ask exactly ONE question."
    )
    messages = [
        SystemMessage(content=combined_system),
        *state["conversation_history"],
    ]

    llm = _get_primary_llm()
    response = llm.invoke(messages)
    response_text = response.content

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


def process_answer_node(state: Stage2State) -> Dict[str, Any]:
    """Store the user's answer keyed by block.index."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    history = state["conversation_history"]
    last_msg = history[-1] if history else None
    answer_text = last_msg.content if last_msg and isinstance(last_msg, HumanMessage) else ""

    logger.info("session=%s stage=2 block=%s question=%d node=process_answer", session_id, block, index)

    answers = dict(state.get("answers", {}))
    key = f"{block}.{index}"
    answers[key] = answer_text

    return {
        "answers": answers,
        "followup_count": 0,
    }


def risk_flag_classifier_node(state: Stage2State) -> Dict[str, Any]:
    """Classify the latest answer for risk flags using Haiku."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    logger.info("session=%s stage=2 block=%s question=%d node=risk_flag_classifier", session_id, block, index)

    history = state["conversation_history"]
    last_human = None
    last_ai = None
    for msg in reversed(history):
        if isinstance(msg, HumanMessage) and last_human is None:
            last_human = msg.content
        elif isinstance(msg, AIMessage) and last_ai is None:
            last_ai = msg.content
        if last_human and last_ai:
            break

    if not last_human:
        return {}

    prompt = RISK_FLAG_CLASSIFIER_PROMPT_TEMPLATE.format(
        block=block,
        question_index=index,
        question=last_ai or "",
        answer=last_human,
    )

    try:
        llm = _get_classifier_llm()
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip()

        flags_data = json.loads(raw)
        if not isinstance(flags_data, list):
            flags_data = []

        new_flags = []
        for flag_data in flags_data:
            flag = RiskFlag(
                flag_type=flag_data["flag_type"],
                severity=flag_data["severity"],
                description=flag_data["description"],
                recommended_action=flag_data["recommended_action"],
                source_block=block,
                source_question_index=index,
            )
            new_flags.append(flag)
            logger.info(
                "session=%s stage=2 risk_flag detected: %s severity=%s block=%s",
                session_id, flag.flag_type, flag.severity, block,
            )

        existing_flags = list(state.get("risk_flags", []))
        return {"risk_flags": existing_flags + new_flags}

    except Exception as e:
        logger.warning("session=%s Risk flag classifier failed: %s — returning unchanged", session_id, e)
        return {}


def followup_classifier_node(state: Stage2State) -> Dict[str, Any]:
    """Use the classifier LLM to decide if a follow-up question is needed."""
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")
    followup_count = state.get("followup_count", 0)

    logger.info(
        "session=%s stage=2 block=%s question=%d followup_count=%d node=followup_classifier",
        session_id, block, index, followup_count,
    )

    if followup_count >= MAX_FOLLOWUPS_PER_QUESTION:
        return {"pending_followup": None}

    history = state["conversation_history"]
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
You are a follow-up classifier. Given a question and answer from an employee knowledge extraction \
interview, decide whether a follow-up question is needed.

A follow-up is needed when:
- The answer is vague or generic
- The answer hints at something deeper that wasn't fully explained
- Key details are missing that would be important for the handover

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
        logger.warning("session=%s Followup classifier failed: %s — defaulting to no followup", session_id, e)
        return {"pending_followup": None}


def followup_question_node(state: Stage2State) -> Dict[str, Any]:
    """Ask a follow-up question based on the classifier's suggestion."""
    session_id = state.get("session_id", "")
    suggested = state.get("pending_followup", "")
    followup_count = state.get("followup_count", 0)

    logger.info("session=%s stage=2 node=followup_question followup_count=%d", session_id, followup_count + 1)

    instruction = (
        f"The previous answer needs a follow-up. Here is a suggested follow-up question: "
        f'"{suggested}"\n\n'
        "Use this as a guide but phrase the follow-up naturally in your own words, "
        "acknowledging what the person just said. Ask exactly ONE follow-up question."
    )

    system_prompt = build_system_prompt(state["profile"])
    combined_system = f"{system_prompt}\n\n{instruction}"
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


def advance_question_node(state: Stage2State) -> Dict[str, Any]:
    """Advance to the next question, block, or phase."""
    phase = state["current_phase"]
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    next_index = index + 1

    if phase == "role_orientation":
        if next_index < STAGE2_ROLE_ORIENTATION_QUESTION_COUNT:
            logger.info("session=%s stage=2 advancing to role_orientation.%d", session_id, next_index)
            return {
                "current_question_index": next_index,
                "followup_count": 0,
                "pending_followup": None,
            }
        # Transition to knowledge_blocks phase
        block_order = state.get("block_order", [])
        if not block_order:
            # No knowledge blocks — skip to closing
            logger.info("session=%s stage=2 no knowledge blocks, advancing to closing", session_id)
            return {
                "current_phase": "closing_sequence",
                "current_block": "closing_sequence",
                "current_question_index": 0,
                "current_block_index": 0,
                "followup_count": 0,
                "pending_followup": None,
            }
        first_block = block_order[0]
        logger.info("session=%s stage=2 advancing to knowledge_blocks, first block=%s", session_id, first_block)
        return {
            "current_phase": "knowledge_blocks",
            "current_block": first_block,
            "current_question_index": 0,
            "current_block_index": 0,
            "followup_count": 0,
            "pending_followup": None,
        }

    elif phase == "knowledge_blocks":
        block_depths = state.get("block_depths", {})
        depth = block_depths.get(block, "full")

        # Determine max questions for this block
        try:
            block_enum = KnowledgeBlock(block)
            total_for_block = STAGE2_BLOCK_QUESTION_COUNTS.get(block_enum, 5)
        except ValueError:
            total_for_block = 5

        if depth == "light":
            max_questions = min(LIGHT_TOUCH_MAX_QUESTIONS, total_for_block)
        else:
            max_questions = total_for_block

        if next_index < max_questions:
            logger.info("session=%s stage=2 advancing to %s.%d", session_id, block, next_index)
            return {
                "current_question_index": next_index,
                "followup_count": 0,
                "pending_followup": None,
            }

        # Move to next block
        block_order = state.get("block_order", [])
        current_block_index = state.get("current_block_index", 0)
        next_block_index = current_block_index + 1

        if next_block_index < len(block_order):
            next_block = block_order[next_block_index]
            logger.info("session=%s stage=2 advancing to block %s", session_id, next_block)
            return {
                "current_block": next_block,
                "current_question_index": 0,
                "current_block_index": next_block_index,
                "followup_count": 0,
                "pending_followup": None,
            }

        # All blocks done — transition to closing
        logger.info("session=%s stage=2 all blocks complete, advancing to closing", session_id)
        return {
            "current_phase": "closing_sequence",
            "current_block": "closing_sequence",
            "current_question_index": 0,
            "current_block_index": 0,
            "followup_count": 0,
            "pending_followup": None,
        }

    elif phase == "closing_sequence":
        if next_index < STAGE2_CLOSING_QUESTION_COUNT:
            logger.info("session=%s stage=2 advancing to closing.%d", session_id, next_index)
            return {
                "current_question_index": next_index,
                "followup_count": 0,
                "pending_followup": None,
            }

        # All done
        logger.info("session=%s stage=2 interview complete", session_id)
        return {
            "current_block": "__complete__",
            "current_question_index": 0,
            "followup_count": 0,
            "pending_followup": None,
        }

    # Fallback
    return {
        "current_block": "__complete__",
        "current_question_index": 0,
        "followup_count": 0,
        "pending_followup": None,
    }


def session_complete_node(state: Stage2State) -> Dict[str, Any]:
    """Output the closing message and mark session complete."""
    session_id = state.get("session_id", "")
    logger.info("session=%s stage=2 node=session_complete", session_id)

    return {
        "conversation_history": [AIMessage(content=CLOSING_MESSAGE)],
        "last_agent_message": CLOSING_MESSAGE,
        "session_complete": True,
    }


# Routing functions

def route_after_followup_classifier(state: Stage2State) -> str:
    """Route to followup_question or advance based on classifier result."""
    pending = state.get("pending_followup")
    followup_count = state.get("followup_count", 0)

    if pending and followup_count < MAX_FOLLOWUPS_PER_QUESTION:
        return "followup_question"
    return "advance_question"


def route_after_advance(state: Stage2State) -> str:
    """Route to ask_question (more questions) or session_complete (all done)."""
    if state.get("current_block") == "__complete__":
        return "session_complete"
    return "ask_question"
