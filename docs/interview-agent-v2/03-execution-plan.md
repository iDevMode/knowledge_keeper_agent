# Interview agent v2 — execution plan

For the engineer (Opus 5.5) building this. Read `01-review.md` for why, `02-agent-design.md` for
what, and `04-objective-bank.yaml` for the interview content. This file is the order of work and the
definition of done.

## Ground rules

1. **Base branch**: `fix/followup-answers-and-pooler` (PR #15), or `main` once #10 → #11 → #12 → #14 →
   #15 have merged. Check which before starting; do not build on bare `main` at `0b9399b`.
2. **One PR per phase**, stacked in order. Each PR leaves the full test suite green, with and without
   Postgres, exactly as CI runs it today.
3. **Follow the repo's `CLAUDE.md`**: prompts live in `prompts.py`; nodes end `_node`; never log
   conversation content; tests never send email. Update `CLAUDE.md` and `agents/CLAUDE.md` in the PR
   that changes what they describe (principle 4, the Stage 2 graph, the prompt-construction rules).
4. **Prompts are specified.** Copy the text in `02-agent-design.md` section 6 verbatim. If a live eval
   shows a prompt needs to change, change it in its own commit and quote the before/after scores.
5. **Verify the API surface before coding against it.** Look up the current Anthropic and
   `langchain-anthropic` documentation for: model ids, structured outputs / strict tool use, prompt
   caching breakpoints, streaming. Do not write these from memory.
6. **In-flight interviews must survive each deploy.** State gains `harness_version`. A checkpoint
   without it (or with `1`) keeps running on the v1 graph until it completes or expires; new sessions
   get v2. Remove the v1 graph one release after P4 ships (sessions live 72 hours).
7. **Salvage before writing.** Branch `claude/ai-agent-product-review-660lhy` (July, PR #1) has
   `evals/`, `config/llm_provider.py`, `models/entity_memory.py`, `models/knowledge_item.py`,
   `models/classifier_outputs.py`, `models/document_qa.py` and `tests/test_evals.py`. It diverged from
   the current stack by ~120 files, so port file by file (`git show <branch>:<path>`); do not merge it.
8. **No scope creep.** Organisations, billing, integrations and voice are out. If something outside
   this plan looks broken, note it in the PR description and leave it.

## Phases

### P0 — Trust and correctness fixes (small, ship first)

Independent of the redesign; each item is a finding in `01-review.md`.

| Task | Files | Done when |
|---|---|---|
| A1: complete the session on the last answer. Remove the "thank them" pseudo-question; after the final closing answer route straight to `session_complete`. `STAGE2_CLOSING_QUESTION_COUNT` becomes 3. | `stage2/prompts.py`, `nodes.py`, `constants.py`, tests | E2E test: after the answer to the last real question, the response has `session_complete: true` and generation has started. No further message needed. Exactly one closing message. |
| A2: remove the risk badge from the employee view; stop returning `risk_flag_count` to employee-scope tokens. | `ProgressBar.jsx`, `useChat.js`, `api/routes.py` status | API test: employee token gets no `risk_flag_count`; manager token still does. |
| A3: new opening message (design 6.2), built from profile and settings. | `stage2/prompts.py` | Unit tests for default and careful tone; snapshot contains recipients, skip, pause, time. |
| A4: secret redaction. `agents/redaction.py` with patterns (password/PIN/key/token assignments, long high-entropy strings, private-key blocks). Apply to the employee message **before** `update_state`. | `api/routes.py`, new module | Test: a typed password is absent from checkpoint state, `answers`, and the Stage 3 context. |
| A5/A6: move the follow-up prompt to `prompts.py`; wrap employee text in tags in both classifier prompts; one retry on parse failure. | `stage2/nodes.py`, `prompts.py` | Existing classifier tests pass; new test for retry-then-default; injection string in an answer does not change the parsed result in a mocked test of prompt construction. |
| B1: render the whole profile into the Stage 2 system prompt as a role brief; stop appending free-text `agent_flags`, derive settings in code (design section 5 table). | `stage2/prompts.py`, new `stage2/role_brief.py` | Unit test asserts every non-empty profile field appears; settings table covered case by case. |
| G: split model settings (`INTERVIEWER_MODEL`, `ANALYST_MODEL`, `SYNTHESIS_MODEL`; `PRIMARY_MODEL` and `CLASSIFIER_MODEL` remain as fallbacks). Defaults per design section 10. | `config/settings.py`, `.env.example`, README | Old env names still work. |
| E3: check `stop_reason` in Stage 3; raise on truncation; lift `_MAX_TOKENS`. | `stage3/generator.py` | Test with a mocked truncated stream raises a clear error. |

### P1 — Measurement before change

| Task | Done when |
|---|---|
| Port `config/llm_provider.py` as the single seam for creating models (interviewer, analyst, synthesis), replacing the per-module `_get_*_llm` factories. Keep the existing patch points working or update the tests in the same commit. | All tests green. |
| Port `evals/` and `tests/test_evals.py`; adapt the simulator to the current graph and API of `answers`. | `python -m evals.run_eval` runs scripted, in CI, under 10 seconds. |
| Add personas: **terse** (short answers, needs probing), **front-loader** (answers three objectives in one message), **asker** (meta questions, pause, "I already said that"), **careful-exit** (involuntary; declines two topics; raises a grievance once), **oversharer** (types a password; volunteers a colleague's health detail), **rambler** (drops four entities in passing). Each with planted facts and expected behaviours. | Personas load; scripted run produces a scorecard. |
| Add metrics from design section 11 that are missing: redundant-question rate, probe precision, facts per turn, decline respected, secrets in pack, core-reached-at-50%-abandonment, active minutes. | Scorecard JSON includes them. |
| Record the **v1 baseline** (scripted and one live run) in `docs/interview-agent-v2/baseline.md`. | File committed with numbers and the commit hash they came from. |

Everything after this phase quotes its scorecard delta in the PR description.

### P2 — The ledger and the turn analyst (memory)

| Task | Notes |
|---|---|
| Models: `KnowledgeItem`, `Entity`, `OpenThread`, `ObjectiveState`, `TurnAnalysis`, `RiskOp` in `models/`. | Schemas in design section 2. |
| `objectives.py`: load and validate `04-objective-bank.yaml` (move the file into the package); helper to build the objective set for a profile (always-on groups + blocks at depth). | Test: every block in `KnowledgeBlock` has objectives; every `credit` and `seeds` reference resolves; each opener is one sentence with at most one "?". |
| `turn_analyst_node`: replaces `followup_classifier_node`. Structured output, prompt from design 6.3, context per design section 5. | Still fails safe: on error, treat as `intent=answer`, no items, no gaps. |
| `ledger_update_node`: merges analysis into state (entity upsert by normalised name, item append, thread upsert, objective status including cross-credit, `excluded` on retract). Pure function, no model. | Heavily unit-tested; this is the memory. |
| Reducers: any field written by a parallel branch needs one (risk register: dict merge by id). | Test as in `test_risk_flag_parallelism.py`. |
| In this phase the v1 linear walker still chooses questions; the analyst's `gaps` replace `suggested_followup`, and objectives already `covered` are skipped. | First measurable win: redundant-question rate drops. |

Done when: scripted recall is unchanged or better; redundant-question rate on **front-loader** ≤ 3%;
unit tests cover the four cases CLAUDE.md requires per block (clear, vague, refusal, partial) expressed
as analyst outputs.

### P3 — The planner (coverage, order, time)

| Task | Notes |
|---|---|
| `planner.py`: pure function `(state, analysis) → Move` implementing design section 3. | No model calls. Table-driven tests for every rule: skip covered, probe caps, don't-know redirect, decline final, per-entity loop and catch-all, budget drop order, fatigue, non-answers not advancing. |
| New order: orientation → core → priorities → supporting (light) → undocumented → sweep → close. | Replaces `advance_question_node` and `block_order` walking for v2 sessions. |
| Time tracking: `active_seconds` from message timestamps (cap 5 min per gap); `budget_minutes` from profile (new optional Stage 1 answer, default 60). | Stage 1: add one question to `output_preferences` — how much of the employee's time is available — and `last_working_day` as a date in `departure_sensitivity`. Both optional in the model so old profiles validate. |
| `not_reached` objectives recorded for Stage 3. | |
| Graph: `process_answer → [turn_analyst ∥ risk_analyst] → ledger_update → planner → interviewer → (interrupt)`; `planner → session_complete` when the move is `close`. | Keep `interrupt_before` semantics and the API contract of `/message` unchanged. |

Done when: **core-reached-at-50%** = 100% in scripted evals; a simulated interview over budget drops
light objectives first and never a core one; pack sections Key Relationships, Systems and Access and
In-Flight Items have source objectives for every fixture profile.

### P4 — The interviewer (prompt, context, guard)

| Task | Notes |
|---|---|
| Interviewer prompt and process facts (design 6.1); role brief as system block 2. | Verbatim. |
| Context builder (design section 5): cached system blocks, `session_digest`, last 8 turns, final message with `<employee_message>` and `<turn_brief>`. | Unit test asserts the per-turn brief is never in a cached block and that two consecutive turns produce identical system blocks. |
| Structured output `{reflection, question}`; guard; one retry; deterministic fallback. Retire `validate_single_question` / `enforce_single_question` for v2 (keep for Stage 1). | Test each guard failure → retry → fallback path. |
| `session_digest` renderer (code, from the ledger). | Snapshot tests. |
| Streaming of the interviewer message to the browser (SSE on a new endpoint; keep the JSON endpoint). | Optional within this phase; if it threatens the schedule, split to P8. |

Done when (live eval, all personas): one-question compliance 100%; probe precision ≥ 70%; recall ≥ 85%;
median turn latency recorded; cached-token share of interviewer input > 60% after turn 10.

### P5 — Conversation handling

Implement every `intent` and move end to end: `meta_question → answer_meta`, `pause_request` and
30-minute gaps → `resume`, `decline → accept_decline`, `correction` (update items, acknowledge),
`retract` (exclude, confirm), `concern` (no capture; excluded from Stage 3), `fatigue → check_in`,
budget → `offer_stop`, and `close.readback` (planner builds the bullets per the note in the objective
bank). Sensitive items route to the restricted section; add the Stage 1 opt-in question for it.

Done when: **asker**, **careful-exit** and **oversharer** personas pass their expected-behaviour
checks in scripted mode and are spot-checked live; decline-respected = 100%; secrets-in-pack = 0;
nothing from a `concern` turn appears in Stage 3 input (asserted on the built context string).

### P6 — Risk analyst v2

Prompt from design 6.4; structured `add`/`update` ops against a register keyed by id; receives
existing titles and the last working day; two new types (`dated_commitment`, `unwritten_agreement`)
added to `RiskFlagType`, the Stage 3 template and the formatter. Keep `risk_flags` readable for
packs generated from v1 sessions.

Done when: risk recall ≥ 85% and duplicate rate ≤ 5% live; no risk description in the eval set
reads as a statement about the employee's conduct (checked by a rubric grader in the live eval).

### P7 — Stage 3 on the ledger

Design section 9, items 1–7. Port `models/document_qa.py` from the July branch as the starting point
for the grounding check. Include the Stage 1 transcript summary (CLAUDE.md principle 6). Partial
interviews (TTL reached) generate a pack marked partial and notify the manager.

Done when: captured→pack survival ≥ 95%; ungrounded names/dates = 0 across the eval set; a
truncated generation fails with a clear error; fixture packs for process-, decision- and
relationship-heavy roles still render to Word and PDF.

### P8 — Surfaces

| Task | Notes |
|---|---|
| Employee progress: a coarse indicator (stage of the interview + "about N minutes left"), and a Pause button that sends the pause intent. | From planner state. No question counts, no risk information. |
| Manager coverage view on the dashboard: per group — covered / partial / declined / not reached — plus whether the interview was partial. | Closes the "manager progress view" gap. Status only; no employee wording. |
| Per-turn metrics table (`kk_turn_metrics`): session id, turn, intent, move, objective id, tokens in/out/cached, latency per call. No content. Swept with the session. | Feeds a quality line in the "pack ready" email: coverage % and count of gaps. |
| Remove the v1 Stage 2 graph and question lists once no v1 sessions can exist. | Separate PR. |

## Definition of done for the whole programme

- Every target in `02-agent-design.md` section 11 met on a live eval run, recorded next to the baseline.
- Three human-read transcripts (one per fixture role type) reviewed by the product owner for tone.
- `CLAUDE.md`, `agents/CLAUDE.md`, `README.md`, `docs/data-retention.md` (new state fields, metrics
  table, redaction) and the project docs updated.
- No regression in the existing ~660 tests; CI with and without Postgres green.

## Decisions for the product owner (do not guess; ask)

1. Default interview length — 60 minutes proposed.
2. Whether the restricted "team context" section exists at all, or sensitive third-party detail is
   simply never captured.
3. Whether a partial interview should auto-generate a pack at expiry (proposed: yes, marked partial).
4. Whether the employee may receive a copy of the read-back summary.
5. Model spend ceiling per handover, which decides whether the analyst may move from Haiku to Sonnet.
