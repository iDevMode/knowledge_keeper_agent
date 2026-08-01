# KnowledgeKeeper — Review Fix Status

**Working file for the review-fix branch. Delete when the branch merges — the git history is the permanent record.**

Branch: `fix/review-findings` (off `chore/repo-hygiene-and-debug-cleanup`, itself off `main`)
Tests: **271 passing** (was 121 before this work; 150 added). Fresh clone without
`.env` or `frontend/dist`: 267 passed, 4 skipped (the skips are mounted-route SPA
tests that genuinely require a built frontend).
Working tree: clean, nothing pushed
Last updated: 2026-08-01

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

## ⚠️ Deployment constraint — read before deploying

**Pin the deployment to a single uvicorn worker.** All session state is in-process
(session store, graph registry, LangGraph `MemorySaver` checkpointers, document
store). With two or more workers, a session created on worker A is invisible to
worker B and the next message returns 404. See H3 below. `Dockerfile` currently
runs a single process, so this holds today — but any change to worker count or
autoscaling breaks live interviews silently.

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

## Remaining work

### Needs YOUR decision — do not let an agent guess these

**H5 — Authentication posture.**
Every endpoint is unauthenticated. Access control rests entirely on possession of
an unguessable session UUID (capability-URL model). `API_SECRET_KEY` is declared
in `config/settings.py:21` and referenced nowhere. There is no separation between
the manager surface (Stage 1, review, generate) and the employee surface (Stage 2)
— anyone holding a Stage 2 session ID can call `POST /sessions/{id}/generate`.

The product handles sensitive HR content, and the Stage 2 link is *designed to be
forwarded to the employee*, so capability URLs leak through history and referrers.

Pick one:
- **(a)** Issue signed, expiring, stage-scoped tokens (HMAC with `API_SECRET_KEY`
  or JWT), verified in a FastAPI dependency, gating generate/review/download; or
- **(b)** Accept capability-UUIDs as a documented MVP tradeoff, and either wire in
  or delete the dead `api_secret_key`.

Also fix while in there: `api/routes.py:141-147` — `allowed_origins.split(",")`
does not strip whitespace, so `"a, b"` yields a broken `" b"` origin.

**M6 — Spec drift on Stage 3 auto-trigger.**
CLAUDE.md says Stage 3 is "triggered automatically on Stage 2 completion".
In code, `api/webhooks.py:14-19` only logs; generation is client-driven via
`POST /sessions/{id}/generate`. Consequence: if the employee closes the tab at
completion, no document is ever produced.
Either implement server-side auto-trigger, or amend CLAUDE.md. They just need to agree.

### Mechanical — safe to hand to agents

**H3 — Durable persistence (its own workstream, do last).**
Implement the existing `SessionStore` Protocol (`api/session_manager.py:9-17`)
against Redis with TTL from `settings.session_ttl_hours`; replace `MemorySaver`
with a persistent LangGraph checkpointer; persist the document/job stores. The
`GraphRegistry` should reconstruct graphs bound to the shared checkpointer rather
than caching live instances in a dict. Until this lands, see the deployment
constraint at the top.

---

## Suggested next order

1. **H5 and M6** — blocked on your decision, nothing else depends on them
2. **H3** — separate scoped workstream; lifts the single-worker constraint

Every mechanical finding from the original review is now fixed. What remains is
two decisions and one infrastructure workstream.
