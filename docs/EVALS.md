# Interview eval harness

`evals/` measures how well the Stage 2 interview works: not whether the code runs, but whether an
interview **gets out the knowledge that was there to get**, without wasting the employee's time or
putting things in the pack that should never be there.

Current scores and what they mean: `docs/interview-agent-v2/baseline.md`.

## How it works

1. **Personas** (`evals/personas.py`): nine synthetic departing employees, each built on a fixture
   Role Intelligence Profile. Each carries **planted facts** (the ground truth) with a reveal rule:
   - `direct`: volunteered when the topic comes up
   - `followup`: hidden behind a vague first answer; surfaces only if the agent follows up
   - `entity_probe`: the employee names something in passing; the detail surfaces only if the
     agent later asks about it by name

   Each fact lists the **objectives** it answers (ids from
   `docs/interview-agent-v2/04-objective-bank.yaml`), which is what lets the scorecard tell when a
   question asks for something already said. Personas can also refuse blocks, and carry
   **interjections**: questions about the process, a pause request, a grievance, a typed password,
   a colleague's health.

2. **Simulator** (`evals/simulator.py`): runs the real compiled Stage 2 graph — the one the API
   serves — against the persona, turn by turn. Each employee message goes through the same secret
   redaction the API applies. It records the transcript and builds the exact text Stage 3 would be
   given, so leaks into the pack are checked on the real input.

3. **Scorecard** (`evals/scorecard.py`): the same metrics for scripted and live runs. Definitions
   are in the module docstring. The headline ones:

   | Metric | Question it answers |
   |---|---|
   | Recall (overall and per reveal type) | Did the interview get what was there? |
   | Redundant-question rate | How often did it ask for something already said? |
   | Core objectives at half-way | If the employee stops half-way, do we already have in-flight work, deadlines, access, promises and what breaks first? |
   | Non-answers filed | Were "who reads this?" or "can I stop?" stored as answers? |
   | Secrets / concerns / sensitive in pack | Did a password, a grievance or a colleague's health reach Stage 3? |
   | Declines respected | Was a refusal followed by pressure? |
   | Risk recall, duplicate rate | Were risks flagged, once each? |
   | Probe precision | Did follow-ups find something new? |

4. **Gate** (`evals/gate.py`, `evals/gate.json`): the scripted scores the current code achieves.
   `tests/test_evals.py` fails if any gets worse.

The v1 interview walks fixed questions, so `evals/objectives.py` maps each v1 question to the
objective it pursues. v2 turns will carry their objective directly; the scorecard is the same.

## Two modes

**Scripted (default)**: deterministic fakes replace the interviewer and analyst models through
`config/llm_provider.py`. No API calls; all nine personas run in a few seconds, in CI. The analyst
fake is an **oracle** (it recognises every planted hedge, refusal and risk), so scripted mode
measures the interview's machinery under perfect classification.

**Live (`--live`)**: real models on both sides. The interviewer and analysts are the configured
models; a model roleplays the employee from the persona sheet. Same scorecard. Needs
`ANTHROPIC_API_KEY` and costs credits (roughly nine full interviews).

## Running

```bash
python -m evals.run_eval                          # scripted, all personas
python -m evals.run_eval --personas rambler asker
python -m evals.run_eval --out report.json --transcripts /tmp/transcripts
python -m evals.run_eval --check-gate             # exit 1 if anything got worse
python -m evals.run_eval --update-gate            # after an improvement; commit evals/gate.json
ANTHROPIC_API_KEY=… python -m evals.run_eval --live --out live.json
```

## Proving the harness can fail

`tests/test_evals.py::TestHarnessGoesRed` breaks capabilities on purpose and checks the matching
metric moves: no follow-ups loses the hidden facts and trips the gate; no redaction puts the
password into the pack; an interviewer that asks about dropped names earns the entity-probe facts;
an employee who walks away is recorded as incomplete.

## Not yet covered

- **Document survival** (does captured knowledge reach the finished pack?). The July harness had
  this against a Stage 3 design that was never merged; it returns with phase 7, when Stage 3 is
  rebuilt on the ledger.
- **Real timing.** Minutes are estimated from a reading and typing model.
