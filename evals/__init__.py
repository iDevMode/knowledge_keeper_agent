"""KnowledgeKeeper eval harness.

Measures how effective the Stage 2 interview agent actually is, using
synthetic employee personas with planted knowledge:

- scripted mode (default): deterministic fake LLMs — zero API calls, runs in
  CI, verifies the interview *machinery* (does entity memory probe what was
  mentioned, do follow-ups dig where answers were vague, does the turn budget
  hold, is coverage complete)
- live mode: real models play both sides — measures actual model behaviour
  against the same scorecard

Run: python -m evals.run_eval [--live] [--personas id ...] [--out report.json]
"""
