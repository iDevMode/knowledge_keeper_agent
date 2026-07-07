import logging
import math
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents.stage2_employee_interview.prompts import (
    ANSWER_ANALYSIS_PROMPT_TEMPLATE,
    BLOCK_QUESTIONS,
    CLOSING_MESSAGE,
    CLOSING_QUESTIONS,
    FOLLOWUP_CLASSIFIER_PROMPT_TEMPLATE,
    PROGRESS_INSTRUCTION_TEMPLATE,
    ROLE_ORIENTATION_QUESTIONS,
    SINGLE_QUESTION_REPROMPT,
    build_captured_context,
    build_entity_sweep_instruction,
    build_greeting_message,
    build_system_prompt,
)
from agents.stage2_employee_interview.state import Stage2State
from api.session_manager import get_session_store
from config import llm_provider
from config.constants import (
    ENTITY_SWEEP_MAX_QUESTIONS,
    LIGHT_TOUCH_MAX_QUESTIONS,
    MAX_FOLLOWUPS_PER_QUESTION,
    OVER_BUDGET_LIGHT_BLOCK_QUESTIONS,
    QUESTION_BUDGET_HARD_CAP_FACTOR,
    QUESTION_BUDGET_HEADROOM,
    STAGE2_BLOCK_QUESTION_COUNTS,
    STAGE2_CLOSING_QUESTION_COUNT,
    STAGE2_ROLE_ORIENTATION_QUESTION_COUNT,
    EntityImportance,
    InformationGain,
    KnowledgeBlock,
)
from models.classifier_outputs import AnswerAnalysis, FollowupDecision
from models.entity_memory import (
    entity_context_lines,
    mark_probed,
    merge_entities,
    unprobed_entities,
)
from models.knowledge_blocks import determine_block_order_and_depth
from models.risk_flags import RiskFlag

logger = logging.getLogger(__name__)


def _get_primary_llm():
    return llm_provider.get_primary_llm(max_tokens=2048)


def _get_classifier_llm():
    return llm_provider.get_classifier_llm(max_tokens=512)


# ---- Turn budget & progress ----

def _block_question_count(block: str, depth: str) -> int:
    try:
        count = STAGE2_BLOCK_QUESTION_COUNTS.get(KnowledgeBlock(block), 5)
    except ValueError:
        count = 5
    if depth == "light":
        count = min(LIGHT_TOUCH_MAX_QUESTIONS, count)
    return count


def planned_question_total(block_order: List[str], block_depths: Dict[str, str]) -> int:
    """Main-path question count: orientation + blocks at their depth + closing."""
    total = STAGE2_ROLE_ORIENTATION_QUESTION_COUNT + STAGE2_CLOSING_QUESTION_COUNT
    for block in block_order:
        total += _block_question_count(block, block_depths.get(block, "full"))
    return total


def compute_question_budget(block_order: List[str], block_depths: Dict[str, str]) -> int:
    """Total agent-question budget: planned questions plus headroom that
    absorbs follow-ups. Beyond it the interview tightens; at the hard cap it
    jumps to the closing sequence."""
    return math.ceil(planned_question_total(block_order, block_depths) * QUESTION_BUDGET_HEADROOM)


def _hard_cap(question_budget: int) -> int:
    return math.ceil(question_budget * QUESTION_BUDGET_HARD_CAP_FACTOR)


def _over_budget(state: Stage2State) -> bool:
    budget = state.get("question_budget", 0)
    return budget > 0 and state.get("question_count", 0) >= budget


def _at_hard_cap(state: Stage2State) -> bool:
    budget = state.get("question_budget", 0)
    return budget > 0 and state.get("question_count", 0) >= _hard_cap(budget)


def compute_progress(state: Stage2State) -> Dict[str, Any]:
    """Deterministic interview progress — drives the honest progress UI and
    the micro-summaries spoken at block transitions."""
    block_order = state.get("block_order", [])
    block_depths = state.get("block_depths", {})
    phase = state.get("current_phase", "role_orientation")
    index = state.get("current_question_index", 0)

    # Sections: orientation + each knowledge block + closing (sweep folds into closing)
    section_total = 1 + len(block_order) + 1
    planned_total = planned_question_total(block_order, block_depths)

    if phase == "role_orientation":
        section_number = 1
        questions_done = min(index, STAGE2_ROLE_ORIENTATION_QUESTION_COUNT)
    elif phase == "knowledge_blocks":
        block_index = state.get("current_block_index", 0)
        section_number = 2 + block_index
        questions_done = STAGE2_ROLE_ORIENTATION_QUESTION_COUNT
        for block in block_order[:block_index]:
            questions_done += _block_question_count(block, block_depths.get(block, "full"))
        questions_done += index
    else:  # entity_sweep or closing_sequence
        section_number = section_total
        questions_done = planned_total - STAGE2_CLOSING_QUESTION_COUNT
        if phase == "closing_sequence":
            questions_done += min(index, STAGE2_CLOSING_QUESTION_COUNT)

    percent = int(100 * questions_done / planned_total) if planned_total else 0
    if state.get("session_complete"):
        percent = 100
    else:
        percent = min(percent, 99)

    return {
        "phase": phase,
        "section_number": section_number,
        "section_total": section_total,
        "percent": percent,
        "questions_asked": state.get("question_count", 0),
        "question_budget": state.get("question_budget", 0),
    }


def validate_single_question(text: str) -> bool:
    """Check that the text contains at most one question mark."""
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

    question_budget = compute_question_budget(block_order, block_depths)
    logger.info(
        "session=%s stage=2 block_order=%s question_budget=%d",
        session_id, block_order, question_budget,
    )

    return {
        "profile": profile,
        "block_order": block_order,
        "block_depths": block_depths,
        "question_budget": question_budget,
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
        "entities": {},
        "sweep_questions": [],
        "question_count": 1,  # the greeting contains question 0
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
    elif phase == "entity_sweep":
        questions = state.get("sweep_questions", [])
    else:
        questions = BLOCK_QUESTIONS.get(block, [])

    if index >= len(questions):
        return {}

    instruction = questions[index]

    extras = []
    # Micro-summary at each knowledge-block transition: acknowledge the topic
    # just finished and say honestly how far through we are.
    if phase == "knowledge_blocks" and index == 0:
        progress = compute_progress(state)
        extras.append(
            PROGRESS_INSTRUCTION_TEMPLATE.format(
                section_number=progress["section_number"],
                section_total=progress["section_total"],
                percent=progress["percent"],
            )
        )
    # Entity memory: things mentioned earlier that no question has explored yet.
    entities = state.get("entities", {})
    if phase in ("knowledge_blocks", "entity_sweep"):
        entity_lines = entity_context_lines(entities)
        if entity_lines:
            extras.append(
                "## ENTITY MEMORY\n\n"
                "The employee mentioned these earlier, but no question has explored them yet:\n"
                + "\n".join(entity_lines)
                + "\nIf the current question naturally relates to one of these, refer to it "
                "by name rather than asking generically."
            )

    extras_text = ("\n\n" + "\n\n".join(extras)) if extras else ""
    system_prompt = build_system_prompt(state["profile"])
    combined_system = (
        f"{system_prompt}{extras_text}\n\n"
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
        "entities": mark_probed(entities, response_text),
        "question_count": state.get("question_count", 0) + 1,
    }


def process_answer_node(state: Stage2State) -> Dict[str, Any]:
    """Append the user's answer to the answer list keyed by block.index.

    Answers are lists because follow-up answers land on the same question key —
    they must accumulate, not overwrite the original answer.
    """
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    history = state["conversation_history"]
    last_msg = history[-1] if history else None
    answer_text = last_msg.content if last_msg and isinstance(last_msg, HumanMessage) else ""

    logger.info("session=%s stage=2 block=%s question=%d node=process_answer", session_id, block, index)

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


def risk_flag_classifier_node(state: Stage2State) -> Dict[str, Any]:
    """Analyse the latest answer with one Haiku call: risk flags + entities.

    Entity extraction rides on the risk-flag call rather than being a second
    classifier — one structured call per answer keeps latency and cost flat.
    """
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

    prompt = ANSWER_ANALYSIS_PROMPT_TEMPLATE.format(
        block=block,
        question_index=index,
        question=last_ai or "",
        answer=last_human,
    )

    try:
        llm = _get_classifier_llm().with_structured_output(AnswerAnalysis)
        result = llm.invoke([HumanMessage(content=prompt)])

        new_flags = []
        for detected in result.flags:
            flag = RiskFlag(
                flag_type=detected.flag_type,
                severity=detected.severity,
                description=detected.description,
                recommended_action=detected.recommended_action,
                source_block=block,
                source_question_index=index,
            )
            new_flags.append(flag)
            logger.info(
                "session=%s stage=2 risk_flag detected: %s severity=%s block=%s",
                session_id, flag.flag_type, flag.severity, block,
            )

        updates: Dict[str, Any] = {
            "risk_flags": list(state.get("risk_flags", [])) + new_flags,
        }
        if result.entities:
            updates["entities"] = merge_entities(
                state.get("entities", {}), result.entities, block
            )
            logger.info(
                "session=%s stage=2 entities extracted=%d tracked=%d",
                session_id, len(result.entities), len(updates["entities"]),
            )
        return updates

    except Exception as e:
        logger.error(
            "session=%s event=classifier_failure classifier=answer_analysis error=%s — returning unchanged",
            session_id, e,
        )
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

    classifier_prompt = FOLLOWUP_CLASSIFIER_PROMPT_TEMPLATE.format(
        block=block,
        question=last_ai,
        answer=last_human,
        captured_context=build_captured_context(state.get("answers", {}), block),
    )

    try:
        llm = _get_classifier_llm().with_structured_output(FollowupDecision)
        result = llm.invoke([HumanMessage(content=classifier_prompt)])

        if result.is_refusal or not result.needs_followup or not result.suggested_followup:
            return {"pending_followup": None}

        gain = result.information_gain
        if gain in (InformationGain.NONE, InformationGain.LOW):
            logger.info(
                "session=%s stage=2 followup suppressed: information_gain=%s",
                session_id, gain.value,
            )
            return {"pending_followup": None}

        # Over the turn budget, only chase clearly-important gaps.
        if _over_budget(state) and gain != InformationGain.HIGH:
            logger.info(
                "session=%s stage=2 followup suppressed: over budget (count=%d budget=%d gain=%s)",
                session_id, state.get("question_count", 0),
                state.get("question_budget", 0), gain.value,
            )
            return {"pending_followup": None}

        return {"pending_followup": result.suggested_followup}
    except Exception as e:
        logger.error(
            "session=%s event=classifier_failure classifier=followup error=%s — defaulting to no followup",
            session_id, e,
        )
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
    response_text = invoke_with_single_question_retry(llm, messages, session_id)

    return {
        "conversation_history": [AIMessage(content=response_text)],
        "last_agent_message": response_text,
        "followup_count": followup_count + 1,
        "pending_followup": None,
        "entities": mark_probed(state.get("entities", {}), response_text),
        "question_count": state.get("question_count", 0) + 1,
    }


def _transition_to_sweep_or_closing(state: Stage2State) -> Dict[str, Any]:
    """After the knowledge blocks: probe the most important entities that were
    mentioned but never explored (if turn budget allows), then close."""
    session_id = state.get("session_id", "")

    if not _at_hard_cap(state):
        high_unprobed = [
            e for e in unprobed_entities(state.get("entities", {}))
            if e.importance == EntityImportance.HIGH
        ]
        sweep = [
            build_entity_sweep_instruction(e.model_dump())
            for e in high_unprobed[:ENTITY_SWEEP_MAX_QUESTIONS]
        ]
        if sweep:
            logger.info(
                "session=%s stage=2 entity_sweep targets=%s",
                session_id, [e.name for e in high_unprobed[:ENTITY_SWEEP_MAX_QUESTIONS]],
            )
            return {
                "current_phase": "entity_sweep",
                "current_block": "entity_sweep",
                "current_question_index": 0,
                "current_block_index": 0,
                "sweep_questions": sweep,
                "followup_count": 0,
                "pending_followup": None,
            }

    logger.info("session=%s stage=2 advancing to closing", session_id)
    return {
        "current_phase": "closing_sequence",
        "current_block": "closing_sequence",
        "current_question_index": 0,
        "current_block_index": 0,
        "followup_count": 0,
        "pending_followup": None,
    }


def advance_question_node(state: Stage2State) -> Dict[str, Any]:
    """Advance to the next question, block, or phase."""
    phase = state["current_phase"]
    block = state["current_block"]
    index = state["current_question_index"]
    session_id = state.get("session_id", "")

    next_index = index + 1

    # Hard turn cap: the employee's attention is spent — skip whatever main-path
    # questions remain and wrap up (entity sweep is skipped too, closing still runs).
    if phase in ("role_orientation", "knowledge_blocks") and _at_hard_cap(state):
        logger.warning(
            "session=%s stage=2 hard question cap reached (count=%d budget=%d) — jumping to closing",
            session_id, state.get("question_count", 0), state.get("question_budget", 0),
        )
        return _transition_to_sweep_or_closing(state)

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
        max_questions = _block_question_count(block, depth)

        # Over the soft budget, light-touch blocks shrink to their single most
        # valuable question — priority blocks keep their full depth.
        if depth == "light" and _over_budget(state):
            max_questions = min(max_questions, OVER_BUDGET_LIGHT_BLOCK_QUESTIONS)

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

        # All blocks done — entity sweep (if warranted), then closing
        logger.info("session=%s stage=2 all blocks complete", session_id)
        return _transition_to_sweep_or_closing(state)

    elif phase == "entity_sweep":
        if next_index < len(state.get("sweep_questions", [])):
            logger.info("session=%s stage=2 advancing to entity_sweep.%d", session_id, next_index)
            return {
                "current_question_index": next_index,
                "followup_count": 0,
                "pending_followup": None,
            }
        logger.info("session=%s stage=2 entity sweep complete, advancing to closing", session_id)
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
