"""Eval harness CLI.

Scripted (default, no API key needed — runs in CI):
    python -m evals.run_eval

Live (real models on both sides, needs ANTHROPIC_API_KEY):
    python -m evals.run_eval --live

Options:
    --personas veteran_ops guarded_analyst   run a subset
    --out report.json                        also write machine-readable results
    --transcripts dir/                       dump full transcripts per persona
    --min-recall 0.8                         exit non-zero below this overall recall
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from evals.personas import PERSONAS
from evals.scorecard import render_markdown, score_run
from evals.simulator import run_interview


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the KnowledgeKeeper interview eval")
    parser.add_argument("--live", action="store_true", help="use real models (costs API credits)")
    parser.add_argument("--personas", nargs="*", default=None, help="persona ids to run")
    parser.add_argument("--out", default=None, help="write JSON results to this path")
    parser.add_argument("--transcripts", default=None, help="directory to dump transcripts")
    parser.add_argument("--min-recall", type=float, default=None,
                        help="fail (exit 1) if overall recall is below this")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    ids = args.personas or list(PERSONAS)
    unknown = [i for i in ids if i not in PERSONAS]
    if unknown:
        print(f"Unknown personas: {unknown}. Available: {list(PERSONAS)}", file=sys.stderr)
        return 2

    cards = []
    for pid in ids:
        persona = PERSONAS[pid]
        print(f"Running {'live' if args.live else 'scripted'} interview: {pid} ...", file=sys.stderr)
        run = run_interview(persona, live=args.live)
        cards.append(score_run(run, persona))

        if args.transcripts:
            out_dir = Path(args.transcripts)
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"{pid}.json", "w") as f:
                json.dump(
                    [
                        {
                            "role": t.role, "text": t.text, "phase": t.phase,
                            "block": t.block, "question_index": t.question_index,
                            "is_followup": t.is_followup,
                            "revealed_fact_ids": t.revealed_fact_ids,
                        }
                        for t in run.turns
                    ],
                    f, indent=2,
                )

    print(render_markdown(cards))

    if args.out:
        with open(args.out, "w") as f:
            json.dump([c.model_dump() for c in cards], f, indent=2)

    total_facts = sum(c.facts_total for c in cards)
    overall = sum(c.facts_captured for c in cards) / total_facts if total_facts else 1.0
    if args.min_recall is not None and overall < args.min_recall:
        print(
            f"\nFAIL: overall recall {overall:.0%} < required {args.min_recall:.0%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
