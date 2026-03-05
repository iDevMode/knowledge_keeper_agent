"""
Run Stage 3 document generation end-to-end with fixture data.

Usage:
    python -m scripts.run_mock_generation [profile_id]

Where profile_id is one of: process_heavy, decision_heavy, relationship_heavy
Defaults to process_heavy.
"""
import json
import sys
from pathlib import Path

import anthropic
from langchain_core.messages import AIMessage, HumanMessage

from agents.stage3_document_generation.prompts import STAGE3_SYSTEM_PROMPT, build_context_block
from config.constants import RiskFlagType, Severity
from config.settings import settings
from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile


def load_fixtures():
    base = Path(__file__).resolve().parent.parent
    profiles_path = base / "tests" / "fixtures" / "sample_role_profiles.json"
    results_path = base / "tests" / "fixtures" / "sample_stage2_results.json"

    with open(profiles_path) as f:
        profiles = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    return profiles, results


def build_conversation_history(raw_history):
    messages = []
    for msg in raw_history:
        if msg["type"] == "ai":
            messages.append(AIMessage(content=msg["content"]))
        else:
            messages.append(HumanMessage(content=msg["content"]))
    return messages


def build_risk_flags(raw_flags):
    flags = []
    for rf in raw_flags:
        flags.append(RiskFlag(
            flag_type=RiskFlagType(rf["flag_type"]),
            severity=Severity(rf["severity"]),
            description=rf["description"],
            recommended_action=rf["recommended_action"],
            source_block=rf["source_block"],
            source_question_index=rf["source_question_index"],
        ))
    return flags


def main():
    profile_id = sys.argv[1] if len(sys.argv) > 1 else "process_heavy"
    profiles, results = load_fixtures()

    if profile_id not in profiles:
        print(f"Unknown profile: {profile_id}")
        print(f"Available: {', '.join(profiles.keys())}")
        sys.exit(1)

    print(f"=== Running Stage 3 Document Generation ===")
    print(f"Profile: {profile_id}")
    print(f"Loading fixtures...", flush=True)

    # Build profile
    profile = RoleIntelligenceProfile.model_validate(profiles[profile_id])

    # Build Stage 2 data
    stage2 = results[profile_id]
    conversation_history = build_conversation_history(stage2["conversation_history"])
    risk_flags = build_risk_flags(stage2["risk_flags"])

    print(f"Answers: {len(stage2['answers'])}")
    print(f"Risk flags: {len(risk_flags)}")
    print(f"Blocks: {', '.join(stage2['block_order'])}")

    # Build context using existing prompt builder
    context = build_context_block(
        profile=profile,
        conversation_history=conversation_history,
        risk_flags=risk_flags,
        answers=stage2["answers"],
        block_order=stage2["block_order"],
        block_depths=stage2["block_depths"],
    )

    print(f"\nContext length: {len(context)} chars")
    print(f"Calling Claude ({settings.primary_model}) via streaming...", flush=True)
    print()

    # Use streaming to keep the connection alive
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=600.0,
        max_retries=3,
    )

    chunks = []
    print("=" * 80)
    print("GENERATED DOCUMENT")
    print("=" * 80)
    print(flush=True)

    with client.messages.stream(
        model=settings.primary_model,
        max_tokens=8192,
        system=STAGE3_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context}],
    ) as stream:
        for text in stream.text_stream:
            chunks.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()

    raw_markdown = "".join(chunks)

    print()
    print("=" * 80)
    usage = stream.get_final_message().usage
    print(f"Tokens used: input={usage.input_tokens}, output={usage.output_tokens}")

    # Save to file
    output_dir = Path(__file__).resolve().parent.parent / "output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"mock_handover_{profile_id}.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(raw_markdown)
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
