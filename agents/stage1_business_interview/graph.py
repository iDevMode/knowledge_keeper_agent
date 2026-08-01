from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.stage1_business_interview.nodes import (
    advance_question_node,
    ask_question_node,
    corrections_node,
    finalise_node,
    followup_classifier_node,
    followup_question_node,
    greeting_node,
    process_answer_node,
    profile_clarification_node,
    profile_generation_node,
    profile_review_node,
    route_after_advance,
    route_after_clarification,
    route_after_followup_classifier,
    route_after_profile_generation,
    route_after_profile_review,
    session_close_node,
)
from agents.stage1_business_interview.state import Stage1State


def build_stage1_graph(checkpointer=None):
    """Build and compile the Stage 1 Business Interview graph."""
    builder = StateGraph(Stage1State)

    # Add nodes
    builder.add_node("greeting", greeting_node)
    builder.add_node("ask_question", ask_question_node)
    builder.add_node("process_answer", process_answer_node)
    builder.add_node("followup_classifier", followup_classifier_node)
    builder.add_node("followup_question", followup_question_node)
    builder.add_node("advance_question", advance_question_node)
    builder.add_node("profile_generation", profile_generation_node)
    builder.add_node("profile_clarification", profile_clarification_node)
    builder.add_node("profile_review", profile_review_node)
    builder.add_node("corrections", corrections_node)
    builder.add_node("finalise", finalise_node)
    builder.add_node("session_close", session_close_node)

    # Edges
    builder.set_entry_point("greeting")

    # greeting -> process_answer (waiting for first user input since greeting contains q0)
    builder.add_edge("greeting", "process_answer")

    # process_answer -> followup_classifier
    builder.add_edge("process_answer", "followup_classifier")

    # followup_classifier -> followup_question OR advance_question
    builder.add_conditional_edges(
        "followup_classifier",
        route_after_followup_classifier,
        {"followup_question": "followup_question", "advance_question": "advance_question"},
    )

    # followup_question -> process_answer (user answers follow-up, then re-evaluate)
    builder.add_edge("followup_question", "process_answer")

    # advance_question -> ask_question OR profile_generation
    builder.add_conditional_edges(
        "advance_question",
        route_after_advance,
        {"ask_question": "ask_question", "profile_generation": "profile_generation"},
    )

    # ask_question -> process_answer (waiting for user input)
    builder.add_edge("ask_question", "process_answer")

    # profile_generation -> profile_review OR profile_clarification
    # Validation failure is not fatal: the manager is asked for the missing
    # details rather than the node raising and surfacing a 500 mid-conversation.
    builder.add_conditional_edges(
        "profile_generation",
        route_after_profile_generation,
        {
            "profile_review": "profile_review",
            "profile_clarification": "profile_clarification",
        },
    )

    # profile_clarification -> profile_generation (retry) OR session_close (gave up)
    builder.add_conditional_edges(
        "profile_clarification",
        route_after_clarification,
        {
            "profile_generation": "profile_generation",
            "session_close": "session_close",
        },
    )

    # profile_review -> corrections OR finalise (after user input)
    builder.add_conditional_edges(
        "profile_review",
        route_after_profile_review,
        {"corrections": "corrections", "finalise": "finalise"},
    )

    # corrections -> profile_review (show updated profile)
    builder.add_edge("corrections", "profile_review")

    # finalise -> session_close
    builder.add_edge("finalise", "session_close")

    # session_close -> END
    builder.add_edge("session_close", END)

    # Compile with interrupt points for human-in-the-loop
    if checkpointer is None:
        checkpointer = MemorySaver()

    # Two human-in-the-loop pause points:
    #   process_answer  — wait for the manager's answer to the current question
    #   profile_review  — wait for the manager to confirm or correct the generated
    #                     profile. Without this the review message is emitted and
    #                     immediately re-evaluated against the manager's *previous*
    #                     interview answer, which routes straight back into
    #                     corrections and loops until the process dies.
    #   profile_clarification — wait for the manager to supply the fields that
    #                     failed validation before regenerating the profile.
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["process_answer"],
        interrupt_after=["profile_review", "profile_clarification"],
    )


# Module-level graph instance
graph = build_stage1_graph()
