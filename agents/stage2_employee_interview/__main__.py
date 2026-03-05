import argparse
import json
import sys
import uuid

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.stage2_employee_interview.graph import build_stage2_graph
from api.session_manager import get_session_store
from models.role_intelligence_profile import RoleIntelligenceProfile


def run_cli(profile_path: str, profile_id: str):
    """Run Stage 2 Employee Interview in CLI mode with a fixture profile."""
    # Load the profile from fixture JSON
    with open(profile_path) as f:
        fixtures = json.load(f)

    if profile_id not in fixtures:
        print(f"Error: profile '{profile_id}' not found. Available: {list(fixtures.keys())}")
        sys.exit(1)

    profile_data = fixtures[profile_id]
    profile = RoleIntelligenceProfile.model_validate(profile_data)

    # Store the profile in session store under a fake stage1 session ID
    stage1_session_id = str(uuid.uuid4())
    store = get_session_store()
    store.store_profile(stage1_session_id, profile)

    # Build graph
    checkpointer = MemorySaver()
    graph = build_stage2_graph(checkpointer=checkpointer)

    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "session_id": session_id,
        "stage1_session_id": stage1_session_id,
        "profile": None,
        "current_phase": "",
        "current_block": "",
        "current_question_index": 0,
        "current_block_index": 0,
        "block_order": [],
        "block_depths": {},
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "conversation_history": [],
        "risk_flags": [],
        "last_agent_message": "",
        "session_complete": False,
    }

    print("\n" + "=" * 60)
    print("  KnowledgeKeeper — Stage 2 Employee Interview")
    print(f"  Profile: {profile_id} ({profile.job_title} at {profile.company_name or 'Unknown'})")
    print("  Type 'quit' or 'exit' to end the session")
    print("=" * 60 + "\n")

    # First invocation — runs until interrupt (before process_answer)
    result = graph.invoke(initial_state, config)

    last_msg = result.get("last_agent_message", "")
    if last_msg:
        print(f"\nKnowledgeKeeper: {last_msg}\n")

    while True:
        snapshot = graph.get_state(config)
        if snapshot.values.get("session_complete"):
            break

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

        graph.update_state(
            config,
            {"conversation_history": [HumanMessage(content=user_input)]},
        )

        result = None
        for event in graph.stream(None, config, stream_mode="values"):
            result = event

        if result:
            last_msg = result.get("last_agent_message", "")
            if last_msg:
                print(f"\nKnowledgeKeeper: {last_msg}\n")

            if result.get("session_complete"):
                # Print risk flags summary
                risk_flags = result.get("risk_flags", [])
                if risk_flags:
                    print("\n" + "=" * 60)
                    print("  RISK FLAGS DETECTED")
                    print("=" * 60)
                    for flag in risk_flags:
                        flag_data = flag.model_dump() if hasattr(flag, "model_dump") else flag
                        severity = flag_data.get("severity", "unknown")
                        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(severity, "⚪")
                        print(f"\n{icon} {flag_data.get('flag_type', 'unknown')} [{severity}]")
                        print(f"   {flag_data.get('description', '')}")
                        print(f"   Action: {flag_data.get('recommended_action', '')}")
                        print(f"   Source: {flag_data.get('source_block', '')}.{flag_data.get('source_question_index', '')}")
                else:
                    print("\nNo risk flags detected.")
                break


def main():
    parser = argparse.ArgumentParser(description="KnowledgeKeeper Stage 2 Employee Interview")
    parser.add_argument("--mode", choices=["cli"], default="cli", help="Run mode")
    parser.add_argument("--profile", required=True, help="Path to fixture JSON file")
    parser.add_argument("--profile-id", required=True, help="Profile key within the JSON file")
    args = parser.parse_args()

    if args.mode == "cli":
        run_cli(args.profile, args.profile_id)


if __name__ == "__main__":
    main()
