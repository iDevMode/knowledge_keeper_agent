from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.stage2_employee_interview.nodes import (
    advance_question_node,
    ask_question_node,
    followup_classifier_node,
    followup_question_node,
    greeting_node,
    load_profile_node,
    process_answer_node,
    risk_flag_classifier_node,
    route_after_advance,
    route_after_followup_classifier,
    session_complete_node,
)
from agents.stage2_employee_interview.state import Stage2State


def build_stage2_graph(checkpointer=None):
    """Build and compile the Stage 2 Employee Interview graph."""
    builder = StateGraph(Stage2State)

    # Add nodes
    builder.add_node("load_profile", load_profile_node)
    builder.add_node("greeting", greeting_node)
    builder.add_node("ask_question", ask_question_node)
    builder.add_node("process_answer", process_answer_node)
    builder.add_node("risk_flag_classifier", risk_flag_classifier_node)
    builder.add_node("followup_classifier", followup_classifier_node)
    builder.add_node("followup_question", followup_question_node)
    builder.add_node("advance_question", advance_question_node)
    builder.add_node("session_complete", session_complete_node)

    # Edges
    builder.set_entry_point("load_profile")

    # load_profile -> greeting
    builder.add_edge("load_profile", "greeting")

    # greeting -> process_answer (waiting for first user input since greeting contains q0)
    builder.add_edge("greeting", "process_answer")

    # process_answer -> risk_flag_classifier
    builder.add_edge("process_answer", "risk_flag_classifier")

    # risk_flag_classifier -> followup_classifier
    builder.add_edge("risk_flag_classifier", "followup_classifier")

    # followup_classifier -> followup_question OR advance_question
    builder.add_conditional_edges(
        "followup_classifier",
        route_after_followup_classifier,
        {"followup_question": "followup_question", "advance_question": "advance_question"},
    )

    # followup_question -> process_answer (user answers follow-up, then re-evaluate)
    builder.add_edge("followup_question", "process_answer")

    # advance_question -> ask_question OR session_complete
    builder.add_conditional_edges(
        "advance_question",
        route_after_advance,
        {"ask_question": "ask_question", "session_complete": "session_complete"},
    )

    # ask_question -> process_answer (waiting for user input)
    builder.add_edge("ask_question", "process_answer")

    # session_complete -> END
    builder.add_edge("session_complete", END)

    # Compile with interrupt points for human-in-the-loop
    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["process_answer"],
    )


# Module-level graph instance
graph = build_stage2_graph()
