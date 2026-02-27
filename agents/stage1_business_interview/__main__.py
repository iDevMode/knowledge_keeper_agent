import argparse
import json
import sys
import uuid

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.stage1_business_interview.graph import build_stage1_graph


def run_cli():
    """Run Stage 1 Business Interview in CLI mode."""
    checkpointer = MemorySaver()
    graph = build_stage1_graph(checkpointer=checkpointer)

    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "session_id": session_id,
        "business_name": "",
        "current_block": "",
        "current_question_index": 0,
        "answers": {},
        "conversation_history": [],
        "role_intelligence_profile": None,
        "profile_confirmed": False,
        "session_complete": False,
        "followup_count": 0,
        "pending_followup": None,
        "last_agent_message": "",
    }

    print("\n" + "=" * 60)
    print("  KnowledgeKeeper — Stage 1 Business Interview")
    print("  Type 'quit' or 'exit' to end the session")
    print("=" * 60 + "\n")

    # First invocation — runs until interrupt (before process_answer)
    result = graph.invoke(initial_state, config)

    # Print the greeting / last agent message
    last_msg = result.get("last_agent_message", "")
    if last_msg:
        print(f"\nKnowledgeKeeper: {last_msg}\n")

    while True:
        # Check if session is complete
        snapshot = graph.get_state(config)
        if snapshot.values.get("session_complete"):
            break

        # Get user input
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nSession ended by user.")
            break

        if user_input.lower() in ("quit", "exit"):
            print("\nSession ended.")
            break

        if not user_input:
            continue

        # Resume graph with the user's message
        graph.update_state(
            config,
            {"conversation_history": [HumanMessage(content=user_input)]},
        )

        # Continue execution until next interrupt or completion
        result = None
        for event in graph.stream(None, config, stream_mode="values"):
            result = event

        if result:
            last_msg = result.get("last_agent_message", "")
            if last_msg:
                print(f"\nKnowledgeKeeper: {last_msg}\n")

            # Check completion
            if result.get("session_complete"):
                profile = result.get("role_intelligence_profile")
                if profile:
                    print("\n" + "=" * 60)
                    print("  GENERATED ROLE INTELLIGENCE PROFILE (JSON)")
                    print("=" * 60)
                    profile_data = profile.model_dump() if hasattr(profile, "model_dump") else profile
                    print(json.dumps(profile_data, indent=2, default=str))
                break


def main():
    parser = argparse.ArgumentParser(description="KnowledgeKeeper Stage 1 Business Interview")
    parser.add_argument("--mode", choices=["cli"], default="cli", help="Run mode")
    args = parser.parse_args()

    if args.mode == "cli":
        run_cli()


if __name__ == "__main__":
    main()
