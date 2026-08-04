# KnowledgeKeeper — Review Fix Status

**Working file for the review-fix branch. Delete when the branch merges — the git history is the permanent record.**

Branch: `feat/h3-durable-persistence` (off `main`, after PRs #2 and #3 merged)
Tests: **442 passing** with a Postgres test database (was 121 before this work).
Without one: 374 passed, 68 skipped — all 68 are the Postgres-backed halves of the
store contract, restart-persistence and multi-worker suites, and every one names
`TEST_DATABASE_URL` in its skip reason, so a silent skip cannot pass for a pass
(verified with `pytest -rs`: no skip has any other cause). The in-memory half of
each contract runs everywhere.
Working tree: clean; branch pushed, PR not yet opened
Last updated: 2026-08-04

**All three stages verified working end to end** — `tests/test_e2e_full_journey.py`
drives manager interview → confirmed profile → employee interview → risk flags →
generated `.docx` download through the real graphs and the API.

The authoritative record is the git history, not this file. Every commit message
contains the reproduction evidence and measured before/after for its finding:

```bash
git log main..HEAD                 # all commits
git log main..HEAD --format=%B     # full bodies with the evidence
```

---

## ⚠️ Read before deploying — three environment variables are startup-fatal

Outside `ENVIRONMENT=development`, the app refuses to start without these. Each
one fails *silently and intermittently* if it is merely wrong rather than absent,
which is why startup validation is loud instead:

| Variable | Set it to | What happens without it |
|---|---|---|
| `DATABASE_URL` | the Postgres addon's URL | Every restart destroys in-flight interviews — sessions, checkpoints and generated documents all live in process memory |
| `API_SECRET_KEY` | a long random string | Each process invents its own signing key, so every restart invalidates every live interview link, and with more than one worker tokens fail on whichever worker did not mint them |
| `STAGE1_TO_STAGE2_LINK_TTL_HOURS` | `72`, or raise `SESSION_TTL_HOURS` to match | A link that outlives its session: the employee is told they have a week and gets "Session not found" on day four |

**The single-worker pin is lifted.** It held while all state was in-process; H3
made state durable and cross-process locking real. `Dockerfile` now runs
`--workers ${WEB_CONCURRENCY:-1}` — the default stays 1 so raising it is
deliberate, and startup refuses `WEB_CONCURRENCY > 1` without `DATABASE_URL`.
Before raising it, read the connection budget in `config/settings.py`: each
worker costs roughly `2 * DB_POOL_SIZE + DB_LOCK_POOL_SIZE` connections, and
managed Postgres often caps at 100.

---

## Completed — 11 review findings fixed

Each was **reproduced before being changed**, per the fix brief's rule.

| ID | Finding | Commit | Evidence it was real |
|---|---|---|---|
| **C1** | Stage 1 never paused for manager review; profile→corrections looped | `2a58a8c` | Message 27 hard-crashed the interpreter (Windows access violation in msgpack checkpoint serialisation) |
| **C2** | Path traversal in SPA fallback route | `660d150` | `serve_spa('../../.env')` returned the repo `.env` including the live `ANTHROPIC_API_KEY` |
| **H2** | Manager's ranked priorities silently dropped | `5ae882d` | "Client relationships", "Decision making", "Supplier relationships", "Regulatory knowledge" all resolved to `None`; priorities 1 and 3 vanished with nothing logged |
| **M2** | Question 1 of the first block never asked, in both stages | `f83fed2` | Full run recorded 27 answers, keys `business_context.1`–`.4`, `.0` absent |
| **H1** | Manager corrections discarded when reply began with an affirmation | `6975076` | "yes, but change the job title" → `finalise`; 2 of 8 realistic replies misrouted, both toward data loss |
| **H4** | Profile generation 500'd instead of asking the manager | `f1a371f` | 22 required fields; any skipped interview answer crashed the session unrecoverably |
| **M3** | Classifier JSON parsing too brittle | `62a3b5c` | 4 of 6 realistic response shapes failed to parse → follow-ups and risk flags silently disabled |
| **M5** | One-question rule not enforced after retry | `9f6ab16` | Retry output used without re-validation |
| **M7** | Profile double-write, `corrections_node` context loss, lock TOCTOU | `a5772f9` | Three contained correctness issues |
| **M4** | Unbounded growth of registry, sessions, jobs and temp dirs | `f1932cb` | 23 orphaned `kk_*` temp dirs already on disk; `registry.remove()` called nowhere in app code |
| **M1** | Risk classifier blocked every turn (serial, not parallel) | `5018b87` | Peak in-flight classifiers was 1; turn 0.60s serial vs 0.31s parallel with 300ms stubs |

### Two corrections to the original review — carried forward deliberately

1. **C2 was over-rated as Critical/actively exploitable.** Starlette normalises
   `..` out of request paths before routing, so it was **not** reachable over HTTP
   as deployed. The handler itself is genuinely unsafe and the guard belongs there,
   but the severity claim was wrong. Verified by probing both layers.
2. **C1 was worse than reported.** The review predicted a clean
   `GraphRecursionError`; it is actually an interpreter-level crash.

Two bugs were also caught *in the fixes themselves* during verification and are
covered by tests: apostrophe stripping meant `"that's correct"` could never match
in H1, and `"management"` was too generic a keyword in H2 (tied "Vendor
management" against the team block).

### Pre-push self-review — three further corrections to the fixes

A commit-by-commit review of the branch before pushing found three issues in the
fix work itself, each fixed in its own commit:

| Commit | Issue |
|---|---|
| `91d2aa1` | The M4 cleanup replaced `tempfile.mkdtemp` (0o700) with `Path.mkdir` (default 0o755 on Linux), widening read access to generated handover documents on the production image. Directory is now created 0o700 with an explicit `chmod`. Invisible on Windows, which is why it survived local testing. |
| `5a200e7` | All four C2 traversal tests were gated on `frontend/dist` existing — a gitignored build artefact — so they silently skipped in CI and on fresh clones. The containment logic is now `safe_static_path()` at module level with 9 build-independent tests that run everywhere. |
| `11d8d28` | Dead imports left behind by M3 and M5. |

### New test files

- `tests/test_e2e_stage1.py` — drives the real graph through the API (the gap that let C1 ship)
- `tests/test_spa_route.py` — build-independent containment tests against `safe_static_path()` (run everywhere, incl. CI) plus mounted-route tests that call `serve_spa()` directly; an HTTP-only test passes against the unguarded handler and proves nothing
- `tests/test_block_resolution.py` — canonical + paraphrased priority labels
- `tests/test_profile_review_routing.py` — confirmation vs correction routing
- `tests/test_profile_generation_recovery.py` — validation-failure recovery loop
- `tests/test_classifier_parsing.py` — fenced/prefaced JSON recovery
- `tests/test_single_question_guard.py` — retry-path enforcement
- `tests/test_e2e_stage2.py` — drives the Stage 2 interview loop through the API
- `tests/test_e2e_full_journey.py` — all three stages chained, including the Stage 2 → Stage 3 handoff
- `tests/test_resource_cleanup.py` — eviction, expiry and document retention
- `tests/test_risk_flag_parallelism.py` — classifier concurrency and reducer correctness

---

## The three findings that needed a decision — all now closed

**H5 — Authentication posture.** *Decided: stage-scoped signed tokens.* PR #3.
Every endpoint had been unauthenticated, with access resting on possession of an
unguessable session UUID — and the Stage 2 link is *designed to be forwarded to
the departing employee*, so the link was the credential and it was the manager's
session id. Anyone holding it could read and write the manager's Stage 1
interview and generate and download the handover pack, including the Risk Summary
written about them.

Tokens are now HMAC-SHA256 signed with `API_SECRET_KEY`, carry `{sid, scope, exp}`
and come in two scopes: `manager` (its own session plus the linked one, plus
generate and download) and `employee` (that one session, never the document). The
manager mints the employee's session, because minting an employee token requires
proving you are the manager. See `api/auth.py` and `tests/test_auth.py`.

| Commit | What it did |
|---|---|
| `e951570` | Gate every endpoint with stage-scoped tokens; auto-trigger Stage 3 |
| `9a56f56` | Carry tokens through the frontend and rework the share link |
| `2ecbff5` | Document the token model; mark `API_SECRET_KEY` required |
| `e44361a` | Close a session-id enumeration oracle *I introduced* in `e951570` — Stage 2 creation looked up the session before authorising, so an existing id 401'd and an absent one 404'd |
| `98b670b`, `fe75479` | Five further problems found reviewing my own branch |

The CORS whitespace bug (`"a, b"` → a broken `" b"` origin) went with it:
`parse_allowed_origins()` now strips.

**M6 — Stage 3 auto-trigger.** *Decided: implement it, don't amend the spec.*
`api/webhooks.py` only logged; generation was client-driven, so an employee who
closed the tab at completion produced no document at all. Stage 3 now fires
server-side on Stage 2 completion. Covered by `tests/test_stage3_autotrigger.py`.

**H3 — Durable persistence.** *Decided: Postgres for everything, falling back to
in-memory when `DATABASE_URL` is unset.* Four commits on
`feat/h3-durable-persistence`:

| Commit | What it did |
|---|---|
| `e6405c7` | Postgres-backed session store behind the existing `SessionStore` Protocol, with one contract suite run against both backends |
| `a28ca75` | `PostgresSaver` checkpointer; `GraphRegistry` rebuilt to reconstruct graphs from the store rather than cache live instances in a dict |
| `214a55d` | Documents persisted as bytes in `kk_documents` rather than as files on a container-local disk |
| `13013a2` | Cross-process advisory locks, `--workers ${WEB_CONCURRENCY:-1}`, connection-pool consolidation |

Redis was the plan and was rejected on evidence, not preference: LangGraph's
`RedisSaver.setup()` issues `FT._LIST`, a RediSearch command, and dies with
`ResponseError: unknown command 'ft._list'` against the plain Redis addon a
managed platform provides. Rationale is recorded in CLAUDE.md.

`tests/test_restart_persistence.py` proves the point rather than asserting it: an
interview is driven partway, the process state is thrown away, and it resumes.
It also carries a false-positive guard — the in-memory path is asserted *not* to
survive a restart, so a test that stopped exercising Postgres would fail rather
than quietly pass. `tests/test_multi_worker.py` starts a genuine two-worker
uvicorn, asserts the fixture really got two workers before testing anything, and
inserts a session directly into Postgres so that neither worker created it.

---

## Remaining work

Nothing from the original review is outstanding. Two things are known, flagged,
and deliberately not fixed:

1. **The download token travels in a query string**, because `<a href>` downloads
   cannot set an `Authorization` header — so it lands in uvicorn access logs. The
   fix is a blob download on the frontend, which conflicts with emailed document
   links being in MVP scope. Revisit when email delivery is built.
2. **This file is a working document.** Delete it when the branch merges; the git
   history is the permanent record, and every commit message carries the
   reproduction evidence and measured before/after for its finding.
