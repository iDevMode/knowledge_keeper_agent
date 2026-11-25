"""KnowledgeKeeper interview eval harness.

Measures how well the Stage 2 interview extracts what a departing employee
knows, using synthetic personas with planted facts. See docs/EVALS.md.

    python -m evals.run_eval            scripted, no API key, a few seconds
    python -m evals.run_eval --live     real models on both sides
"""
