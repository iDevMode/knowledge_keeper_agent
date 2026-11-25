# Interview agent — v1 baseline

The scores every later phase is measured against. Produced by the eval harness added in phase 1
(`python -m evals.run_eval`, see `docs/EVALS.md`).

- **Agent behaviour measured:** the v1 Stage 2 interview as of commit `7f752c4` (PR #16, phase 0).
  Phase 1 changes no interview behaviour; it only routes model creation through
  `config/llm_provider.py`.
- **Mode:** scripted only. Deterministic fakes play the interviewer and analyst models, and a
  scripted employee answers by each persona's rules. The analyst fake is an oracle (it recognises
  every planted risk and hedge), so these numbers measure the interview's *machinery* — what is
  asked, in what order, what is followed up, what is stored — under perfect classification.
- **Live baseline: not yet recorded.** No API key was available when this was written. Run
  `ANTHROPIC_API_KEY=… python -m evals.run_eval --live --out live-v1.json` on this commit and add
  the table below. Expect lower numbers: real classifiers miss things the oracle does not.
- **Date:** 25 Nov 2026.

## Results (scripted)

### Capture

| Persona | Done | Recall | Direct | Follow-up | Entity | Risks | Dup flags | Probe precision | Facts/turn |
|---|---|---|---|---|---|---|---|---|---|
| veteran_ops | yes | 5/6 (83%) | 100% | 100% | 0% | 2/3 | 0% | 100% | 0.15 |
| guarded_analyst | yes | 3/4 (75%) | 100% | 100% | 0% | 1/3 | 0% | 100% | 0.08 |
| relationship_mgr | yes | 4/5 (80%) | 100% | 100% | 0% | 3/4 | 0% | 100% | 0.12 |
| terse_engineer | yes | 4/4 (100%) | 100% | 100% | — | 3/3 | 0% | 100% | 0.11 |
| front_loader | yes | 6/6 (100%) | 100% | — | — | 3/3 | 0% | 100% | 0.19 |
| asker | yes | 3/3 (100%) | 100% | 100% | — | 1/2 | 0% | 100% | 0.08 |
| careful_exit | yes | 3/3 (100%) | 100% | 100% | — | 2/3 | 0% | 100% | 0.09 |
| oversharer | yes | 3/3 (100%) | 100% | 100% | — | 1/1 | 0% | 100% | 0.09 |
| rambler | yes | 1/5 (20%) | 100% | — | 0% | 0/4 | 0% | 100% | 0.03 |

### Conduct and coverage

| Persona | Questions | Redundant | 1-Q | Declines respected | Non-answers filed | Secrets / concerns / sensitive in pack | Core at half | Core overall | Coverage | Est. minutes |
|---|---|---|---|---|---|---|---|---|---|---|
| veteran_ops | 34 | 21% | 100% | — | — | 0 / 0 / 0 | 0% | 40% | 100% | 26.6 |
| guarded_analyst | 37 | 12% | 100% | 100% | — | 0 / 0 / 0 | 20% | 60% | 100% | 26.6 |
| relationship_mgr | 34 | 14% | 100% | — | — | 0 / 0 / 0 | 20% | 40% | 100% | 26.0 |
| terse_engineer | 35 | 14% | 100% | — | — | 0 / 0 / 0 | 0% | 40% | 100% | 16.8 |
| front_loader | 32 | 21% | 100% | — | — | 0 / 0 / 0 | 0% | 40% | 100% | 23.5 |
| asker | 37 | 9% | 100% | — | 100% | 0 / 0 / 0 | 20% | 60% | 100% | 26.0 |
| careful_exit | 33 | 14% | 100% | 100% | — | 0 / 1 / 0 | 20% | 40% | 100% | 24.1 |
| oversharer | 33 | 10% | 100% | — | — | 0 / 0 / 1 | 0% | 40% | 100% | 23.7 |
| rambler | 32 | 0% | 100% | — | — | 0 / 0 / 0 | 20% | 40% | 100% | 28.5 |

**Overall recall: 82%** · redundant questions: 13% · core objective asked in no interview:
`core.first_break`.

All seven missed facts are entity-probe facts: things the employee named in passing that are only
explained if the interviewer asks about them by name.

## What the baseline says

Against the targets in `02-agent-design.md` §11:

| Target | v1 (scripted) | Gap |
|---|---|---|
| Recall ≥ 85% live, 100% scripted | **82%** scripted; **0%** of entity-probe facts | v1 never returns to a name the employee dropped. The rambler persona loses 4 of 5 facts. Fixed by entity memory and the sweep (P2–P3). |
| Redundant questions ≤ 3% | **13%** overall; 21% for the veteran and the front-loader | The fixed script asks for things already answered. Fixed by the ledger and cross-objective credit (P2). |
| Core objectives reached by 50% | **0–20%** at half-way; **40–60%** even by the end | No question ever asks what breaks first; in-flight work, deadlines and access are asked only if the manager happened to rank the matching block, and late. Fixed by the always-on core group, asked early (P3). |
| Non-answers handled | **100% filed as answers** | "Who reads this?" and "can I stop here?" are stored in the pack as answers. Fixed in P5. |
| Grievance and third-party health detail kept out of the pack | **1 grievance and 1 health disclosure reach the pack** | Fixed by concern handling and the sensitive flag (P5). |
| Secrets in the pack = 0 | **0** | Already met by phase 0's redaction. |
| Declines respected = 100% | **100%** | Already met (scripted). |
| One question per turn = 100% | **100%** | Met; the fake always asks one. A live run is the real test. |
| Risk recall ≥ 85% | Misses come from uncaptured entity facts, plus two risk types v1 cannot express (`dated_commitment`, `unwritten_agreement`) | P6 adds the types. |

### Reading the numbers

- **Probe precision is 100% in scripted mode by construction**: the oracle classifier only asks a
  follow-up when the scripted employee hedged, and the scripted employee always has something
  behind a hedge. A live run is what tests it.
- **Facts per turn** depends on how many facts a persona carries; compare it across versions for
  the same persona, not across personas.
- **Estimated minutes** comes from a reading/typing model on short scripted answers. It is useful
  for comparing versions, not as a prediction of a real interview, which will be longer.

## The gate

`evals/gate.json` holds these scores; `tests/test_evals.py` fails CI if any gets worse. When a
phase improves them, regenerate with `python -m evals.run_eval --update-gate` and commit the file
with the PR, quoting the before/after in the PR description.
