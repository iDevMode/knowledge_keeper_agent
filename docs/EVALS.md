# Interview eval harness

`evals/` measures how effective the Stage 2 interview agent actually is —
not whether the code runs, but whether an interview **extracts the knowledge
that was there to extract**.

## How it works

1. **Personas** (`evals/personas.py`) — synthetic departing employees built
   on the fixture Role Intelligence Profiles. Each carries **planted facts**
   (the ground truth) with a *reveal rule* describing how hard the fact is to
   reach:
   - `direct` — volunteered as soon as the topic comes up
   - `followup` — hidden behind a vague first answer; only surfaces if the
     agent follows up on that question
   - `entity_probe` — the employee name-drops an entity (a person, a system)
     in passing; the payload only surfaces if the agent later asks about that
     entity **by name** (entity memory + sweep)

   Facts can also carry a `risk_type`, and personas can refuse whole blocks
   (the agent must respect refusals, not chase them).

2. **Simulator** (`evals/simulator.py`) — runs the *real compiled LangGraph*
   (the same graph the API serves) headlessly against the persona, recording
   a full transcript with per-turn metadata.

3. **Scorecard** (`evals/scorecard.py`) — computed from the transcript and
   final state, identically for scripted and live runs:

   | Metric | What it proves |
   |---|---|
   | Planted-knowledge recall (overall + per reveal type) | the headline: did the interview get what was there? |
   | Risk recall | risky answers produced flags of the right type |
   | Follow-up precision | follow-ups surfaced hidden facts, not filler |
   | Questions vs budget | fatigue management holds |
   | Coverage | every planned block was actually visited |
   | Single-question compliance | the one-question rule held every turn |
   | Entity sweep ran | name-dropped entities were circled back to |

## Two modes

**Scripted (default)** — deterministic fakes replace both models via
`config.llm_provider`. Zero API calls, ~1s for all personas, runs in CI.
The fakes are deliberately *oracles* (they recognise the persona's planted
entities/risks/hedges exactly), so scripted mode verifies the interview
**machinery** — entity tracking, sweep routing, follow-up gating, budget
enforcement, coverage — under ideal classification.

**Live (`--live`)** — real models on both sides: the primary model asks the
questions, Haiku classifies, and Haiku roleplays the employee from the
persona sheet. Same scorecard; measures actual model behaviour. Needs
`ANTHROPIC_API_KEY` and costs credits.

## Running

```bash
# CI-safe scripted run, human-readable report
python -m evals.run_eval

# subset, JSON output, dump transcripts, enforce a floor
python -m evals.run_eval --personas veteran_ops --out report.json \
    --transcripts /tmp/transcripts --min-recall 0.8

# real models
python -m evals.run_eval --live
```

`tests/test_evals.py` runs the scripted harness in pytest and is the
regression gate: it asserts 100% recall/precision/coverage for every persona,
**and** it breaks capabilities on purpose (disables the entity sweep,
lobotomises the follow-up classifier, squeezes the budget) and asserts the
scorecard goes red — so the harness itself is proven able to catch
regressions.

## Document knowledge survival (`--document`)

`python -m evals.run_eval --document` extends the eval end to end: after each
interview it builds the handover document and measures whether the knowledge
the interview *captured* survives into the *document* a successor receives —
**captured→document recall**, per reveal type, plus how many risks reach the
Risk Summary.

- **Scripted** routes each interview block's captured answers into its handover
  section (`evals/document.py::BLOCK_TO_SECTION`) and parses the result through
  the real Stage 3 pipeline (`parse_llm_output` + confidentiality filter). This
  measures the assembly plumbing — a block→section routing bug that dropped a
  block would show as recall < 100%. `tests/test_evals.py` proves this by
  deleting a mapping and asserting the dropped facts go missing.
- **Live** calls the real `generate_document` (extract-then-compose + QA gate)
  and scores the model's actual synthesis fidelity.

## Adding a persona

Add a `Persona` to `evals/personas.py` with facts spread across reveal types
and at least one `risk_type` and one refusal block. Keep `keywords` short and
distinctive — recall credit requires **all** keywords to appear in the
captured answers. Register it in `PERSONAS`; pytest picks it up automatically.
