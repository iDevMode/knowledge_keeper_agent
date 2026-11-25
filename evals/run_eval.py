"""Interview eval CLI.

Scripted (default — no API key, runs in CI, a few seconds):
    python -m evals.run_eval

Live (real models on both sides; needs ANTHROPIC_API_KEY and costs credits):
    python -m evals.run_eval --live

Options:
    --personas veteran_ops asker     run a subset
    --out report.json                also write the full scorecards as JSON
    --transcripts dir/               dump each transcript as JSON
    --min-recall 0.8                 exit 1 if overall recall is below this
    --check-gate                     exit 1 if any scripted score is worse than evals/gate.json
    --update-gate                    rewrite evals/gate.json from this (scripted) run
"""

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from evals import gate
from evals.personas import PERSONAS
from evals.scorecard import render_markdown, score_run
from evals.simulator import run_interview


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the KnowledgeKeeper interview eval")
    parser.add_argument("--live", action="store_true", help="real models (costs API credits)")
    parser.add_argument("--personas", nargs="*", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--transcripts", default=None)
    parser.add_argument("--min-recall", type=float, default=None)
    parser.add_argument("--check-gate", action="store_true")
    parser.add_argument("--update-gate", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    if not args.verbose:
        # LangGraph warns about checkpoint types on every deserialisation.
        logging.getLogger("langgraph").setLevel(logging.ERROR)

    if args.live and (args.update_gate or args.check_gate):
        print("The gate holds scripted scores only; drop --live.", file=sys.stderr)
        return 2
    if args.update_gate and args.personas:
        print("--update-gate needs every persona; drop --personas.", file=sys.stderr)
        return 2

    ids = args.personas or list(PERSONAS)
    unknown = [i for i in ids if i not in PERSONAS]
    if unknown:
        print(f"Unknown personas: {unknown}. Available: {list(PERSONAS)}", file=sys.stderr)
        return 2

    cards = []
    for pid in ids:
        persona = PERSONAS[pid]
        print(f"{'live' if args.live else 'scripted'}: {pid} ...", file=sys.stderr)
        run = run_interview(persona, live=args.live)
        cards.append(score_run(run, persona))
        if args.transcripts:
            out = Path(args.transcripts)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{pid}.json").write_text(json.dumps([asdict(t) for t in run.turns], indent=2))

    print(render_markdown(cards))

    if args.out:
        Path(args.out).write_text(json.dumps([c.model_dump() for c in cards], indent=2))

    status = 0
    if args.update_gate:
        gate.write_gate(cards)
        print(f"\nWrote {gate.GATE_PATH}", file=sys.stderr)
    elif args.check_gate:
        problems = gate.regressions(cards)
        if problems:
            print("\nGATE FAILED:\n" + "\n".join(f"- {p}" for p in problems), file=sys.stderr)
            status = 1

    total = sum(c.facts_total for c in cards)
    overall = sum(c.facts_captured for c in cards) / total if total else 1.0
    if args.min_recall is not None and overall < args.min_recall:
        print(f"\nFAIL: overall recall {overall:.0%} < {args.min_recall:.0%}", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
