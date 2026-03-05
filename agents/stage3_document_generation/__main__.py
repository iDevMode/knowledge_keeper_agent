import argparse
import json
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage

from agents.stage3_document_generation.generator import GenerationRequest, generate_document
from config.settings import settings
from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile
from output.formatters.document_formatter import parse_llm_output
from output.exporters.word_exporter import generate_docx


def _reconstruct_conversation_history(raw: list):
    messages = []
    for entry in raw:
        if entry["type"] == "ai":
            messages.append(AIMessage(content=entry["content"]))
        else:
            messages.append(HumanMessage(content=entry["content"]))
    return messages


def run_generation(stage2_path: str, profile_path: str, profile_id: str,
                   output_dir: str, output_format: str):
    """Run Stage 3 document generation from fixture files."""
    # Load Stage 2 results
    with open(stage2_path) as f:
        stage2_fixtures = json.load(f)

    if profile_id not in stage2_fixtures:
        print(f"Error: '{profile_id}' not found in Stage 2 results. "
              f"Available: {list(stage2_fixtures.keys())}")
        sys.exit(1)

    stage2_data = stage2_fixtures[profile_id]

    # Load profile
    with open(profile_path) as f:
        profile_fixtures = json.load(f)

    actual_profile_id = stage2_data.get("profile_id", profile_id)
    if actual_profile_id not in profile_fixtures:
        print(f"Error: profile '{actual_profile_id}' not found. "
              f"Available: {list(profile_fixtures.keys())}")
        sys.exit(1)

    profile = RoleIntelligenceProfile.model_validate(profile_fixtures[actual_profile_id])

    # Reconstruct data
    history = _reconstruct_conversation_history(stage2_data["conversation_history"])
    risk_flags = [RiskFlag.model_validate(rf) for rf in stage2_data["risk_flags"]]

    request = GenerationRequest(
        session_id=stage2_data["session_id"],
        profile=profile,
        conversation_history=history,
        risk_flags=risk_flags,
        answers=stage2_data["answers"],
        block_order=stage2_data["block_order"],
        block_depths=stage2_data["block_depths"],
    )

    print("\n" + "=" * 60)
    print("  KnowledgeKeeper — Stage 3 Document Generation")
    print(f"  Profile: {profile_id} ({profile.job_title} at {profile.company_name or 'Unknown'})")
    print(f"  Format: {output_format}")
    print("=" * 60 + "\n")

    print("Generating document...")
    result = generate_document(request)

    print("Formatting document...")
    interim_doc = parse_llm_output(result.raw_markdown, profile, request.session_id)

    # Export
    os.makedirs(output_dir, exist_ok=True)
    filename = f"handover_{profile_id}.{output_format}"
    output_path = os.path.join(output_dir, filename)

    if output_format == "docx":
        generate_docx(interim_doc, output_path)
    elif output_format == "pdf":
        from output.exporters.pdf_exporter import generate_pdf
        generate_pdf(interim_doc, output_path)
    else:
        print(f"Error: unsupported format '{output_format}'")
        sys.exit(1)

    print(f"\nDocument written to: {os.path.abspath(output_path)}")

    # Print summary
    print(f"\n  Sections: {len(interim_doc.sections)}")
    print(f"  Questions captured: {result.generation_metadata.get('question_count', 0)}")
    print(f"  Risk flags: {result.generation_metadata.get('risk_flag_count', 0)}")

    if risk_flags:
        print("\n  Risk Flag Summary:")
        for flag in risk_flags:
            severity = flag.severity.value if hasattr(flag.severity, "value") else str(flag.severity)
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(severity, "⚪")
            flag_type = flag.flag_type.value if hasattr(flag.flag_type, "value") else str(flag.flag_type)
            print(f"  {icon} {flag_type} [{severity}]: {flag.description}")

    if interim_doc.confidential_sections_note:
        print(f"\n  Confidential sections redacted: {interim_doc.confidential_sections_note}")

    print("\nDone.")


def main():
    parser = argparse.ArgumentParser(description="KnowledgeKeeper Stage 3 Document Generation")
    parser.add_argument("--stage2", required=True, help="Path to Stage 2 results JSON fixture")
    parser.add_argument("--profile", required=True, help="Path to profiles JSON fixture")
    parser.add_argument("--profile-id", required=True, help="Profile key within the JSON")
    parser.add_argument("--output-dir", default=".", help="Directory for output files")
    parser.add_argument("--format", choices=["docx", "pdf"],
                       default=settings.default_output_format,
                       help="Output format")
    args = parser.parse_args()

    run_generation(args.stage2, args.profile, args.profile_id, args.output_dir, args.format)


if __name__ == "__main__":
    main()
