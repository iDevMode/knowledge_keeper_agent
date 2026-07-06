# KnowledgeKeeper — Product Review & Improvement Plan

**Scope:** Full review of the three-stage agent system (Stage 1 business interview → Stage 2 employee interview → Stage 3 handover generation), covering the business↔agent interaction, the employee data-collection experience, the quality of the final handover, and platform reliability.

**Verdict in one paragraph:** The bones of this product are genuinely good — the Role Intelligence Profile as the spine, separated sessions, priority-driven block routing, a dedicated follow-up classifier, and parallel risk flagging are the right architecture. What holds it back today is (1) a handful of implementation bugs that silently degrade the data the interviews collect, (2) a trust/privacy model that contradicts the product's own promises, (3) fragile in-memory state that will lose a 60-minute employee interview on any redeploy, and (4) an interview and document pipeline that is *scripted* rather than *intelligent* — it asks good questions but doesn't yet listen, adapt, verify, or measure itself. This plan is organised as four workstreams plus a phased roadmap.

---

## Part 1 — Critical fixes (these undermine the product as shipped)

### 1.1 Follow-up answers overwrite the original answer (data loss)
`process_answer_node` in both stages stores answers as `answers[f"{block}.{index}"] = answer_text`. When the follow-up loop fires, the graph routes `followup_question → process_answer` **with the same block and index**, so the follow-up answer *replaces* the original answer. Up to 3 rich exchanges per question are silently discarded from the `answers` dict — the very follow-ups the product's design says produce its depth. Stage 3 then builds the transcript from `answers`, so the final document is generated from the *last* fragment of each exchange only.

**Fix:** change `answers: Dict[str, str]` to `Dict[str, List[str]]` (this is actually what CLAUDE.md already specifies) and append. Include follow-up Q/A pairs, labelled, in the Stage 3 transcript.

### 1.2 The employee link exposes the manager's session — and the employee downloads the risk report
Two related breaches of the product's own confidentiality promises:

- The Stage 2 share link is `/stage2/{stage1_session_id}` — the **manager's session ID is the link**. With it, the employee can call `GET /api/sessions/{stage1_id}/status`, and because sessions are unauthenticated, `POST /api/sessions/{stage1_id}/message` — the very session the greeting promises is "entirely confidential."
- On Stage 2 completion, the **employee** clicks "Generate Document" and downloads the full handover — including the Risk Summary written *about them* ("relationship_at_risk," severity, recommended actions) and any sections the manager flagged as confidential. Stage 1's closing message explicitly tells the manager the opposite flow ("you won't see their raw responses, only the final document").

**Fix:**
- Mint an opaque, signed, single-use **invite token** for the Stage 2 link (`/stage2/i/{token}`), mapped server-side to the Stage 1 session. The Stage 1 session ID never leaves the manager's browser.
- Split endpoints by role: employee-facing routes can only message their own Stage 2 session. Document generation and download become **manager-facing** (delivered by email link with its own token, per the MVP scope's "email delivery to recipients").
- Give the employee an appropriate end-state instead: "Your interview is complete — the handover will be compiled and shared with {recipients}."

### 1.3 All state is in-memory — a redeploy destroys a live interview
`InMemorySessionStore`, `MemorySaver` checkpointers, the `GraphRegistry`, generation jobs, and generated files (in `tempfile.mkdtemp`) all live in process memory / ephemeral disk. On Railway/Render, any restart, deploy, or scale-to-multiple-workers means: employee loses an hour of candid answers, the manager's profile vanishes, download links 404. The frontend already betrays this — on refresh it shows "Welcome back… please continue" with an empty transcript because there is no history endpoint.

**Fix (highest-leverage platform work):**
- Swap `MemorySaver` for LangGraph's Postgres or Redis checkpointer; rebuild graph instances from the checkpoint on demand instead of requiring a live registry entry (makes the API stateless and multi-worker safe).
- Implement the `RedisSessionStore`/Postgres persistence CLAUDE.md already promises; store profiles and transcripts in Postgres.
- Store generated documents in object storage (S3/R2) keyed by document ID.
- Add `GET /api/sessions/{id}/history` so a page refresh restores the conversation.

### 1.4 Profile confirmation heuristic mis-finalises
`route_after_profile_review` does substring matching: *"No changes needed except the job title — it's actually Head of Ops"* contains `"no changes"` → **finalises with the wrong title**. Similarly `"yes"` inside any longer sentence finalises. Replace the keyword list with a Haiku classification call (`{intent: confirm | correct | unclear}`) — the pattern already exists for follow-ups.

### 1.5 Classifier JSON parsing is fragile, so follow-ups silently stop firing
Both Haiku classifiers do `json.loads(response.content.strip())`. Haiku frequently wraps JSON in ```` ```json ```` fences or adds a preamble; every parse failure is swallowed as "no follow-up" / "no risk flags." The product's depth mechanism degrades silently with zero observability.

**Fix:** use tool-use / `with_structured_output` for both classifiers (same as profile generation already does), and emit a metric on classifier failure rather than only a log line.

### 1.6 Stage 3 never sees the questions
`_format_answers_by_block` renders the transcript as `Q3: <answer text>` — the *question is never included*, only its index. The synthesis model must guess what "Q3" asked. Since the question instructions are static and known, render `Q: <question instruction>` / `A: <answer(s)>` pairs. This is a one-file change with a direct document-quality payoff.

### 1.7 Smaller correctness items
- `validate_single_question` retry does not re-validate the second attempt — an invalid retry is shipped to the user. Also, counting `?` flags legitimate text (URLs, quoted client questions) and misses question-shaped imperatives; move validation into the Haiku classifier or accept ≤1 *sentence-final* question mark.
- `/api/health` leaks API-key previews and enumerates env var names — remove before any real customer traffic; replace with a boolean-only health check behind auth.
- `api_secret_key` exists in settings but is never enforced — there is no authentication anywhere. At minimum, protect generation/download/status routes.
- Risk-flag "parallel branch" is actually **serial**: every turn runs risk classifier → follow-up classifier → main model in sequence, so the employee waits on three LLM round-trips per answer. Run the risk classifier concurrently with the follow-up classifier (they're independent), or move risk flagging to an async per-block batch. Combined with SSE streaming of the agent's reply (see 4.4), this cuts perceived latency roughly in half.

---

## Part 2 — Making the interviews *intelligent*, not just scripted

The current agents execute a fixed question list with a binary "follow up or not" gate. The biggest product upgrade is making the agent visibly *listen*.

### 2.1 Entity memory: track what the employee mentions and chase it
Add a lightweight extraction step (Haiku, async per answer — it can share the risk-classifier call) that maintains a structured inventory in Stage 2 state:

```python
class EntityInventory(TypedDict):
    people: List[Entity]        # "Sarah at Acme — main client contact, difficult"
    processes: List[Entity]     # "month-end reconciliation — undocumented"
    systems: List[Entity]       # "the SAP batch scheduler workaround"
    in_flight: List[Entity]     # "the Q3 vendor renegotiation"
```

Each entity carries `mentioned_in`, `depth_captured: none|light|full`, and `needs_probe: bool`. Then:

- **Block questions become entity-aware.** "For each key relationship mentioned…" currently relies on the model re-reading history. Instead, inject the inventory into the question instruction: *"They mentioned Sarah (Acme), Tom (supplier), and 'the auditors' — probe Sarah first, she's marked difficult and at-risk."*
- **A coverage gate at the end of each block** checks the inventory: entities mentioned but never probed get one targeted sweep-up question before the block closes. This converts "we asked all the questions" into "we captured all the things."
- The inventory feeds Stage 3 directly as a structured appendix (contact list, system list, in-flight register) — sections that today depend on the synthesis model re-mining prose.

### 2.2 Upgrade the follow-up classifier from "vague?" to "information gain"
Today the classifier sees only the last Q/A pair with no notion of what the question was *for*. Give it: the block's objective (one line per block in constants), the entity inventory, the questions remaining in the block, and remaining follow-up budget. Ask it for `{needs_followup, expected_information_gain: high|medium|low, suggested_followup}` and only follow up on medium+. This stops the two current failure modes: interrogating a complete answer because it was short, and accepting a vague answer because it was long.

### 2.3 Fatigue management — the interview is currently 40–100+ turns
Role orientation (5) + three full blocks (~6 each) + light blocks + undocumented workarounds (5) + closing (4), times up to 3 follow-ups each. Departing employees — especially involuntary ones — will not give 90 candid minutes. Changes:

- **Time budget in state.** Give the session a turn budget derived from the profile (notice period, seniority, sensitivity). When the budget tightens, the block router trims light-touch blocks first, then converts full blocks to their 3 highest-value questions. Better to capture the top priorities well than everything badly.
- **Multi-sitting support.** "You've done about half — want to continue or pick this up later? Your link will bring you right back." (Requires 1.3 persistence.) Send a resume email/link.
- **Honest progress.** The frontend has a ProgressBar; drive it from `current_block_index / len(block_order)` plus block position, with a "~15 min left" estimate. Unknown-length conversations feel longer than they are.
- **Per-block micro-summaries.** At each block transition, the agent plays back 2–3 bullets: *"So: month-end recs are undocumented and only you run them; the fix for the SAP failure is X. Anything wrong or missing before we move on?"* This is simultaneously a fatigue break, an accuracy check, and a candour prompt — people correct and *add* when they see their words summarised.

### 2.4 Artefact capture
Employees constantly reference things during handovers: "there's a spreadsheet for that," "I have a checklist in my drafts." Add a per-answer affordance to paste links or upload files, stored against the current block, listed in the document's per-section "Referenced materials." Zero LLM work, large real-world handover value. (File upload can be MVP-simple: link capture only first.)

### 2.5 Stage 1 improvements for the business side
- **Context ingestion up front:** let the manager paste a job description / org notes before the interview. Pre-fill profile fields from it and *skip questions already answered* — Stage 1 drops from ~28 questions to ~15. This is the single biggest "wow" moment available for the manager experience.
- **Editable profile review UI** instead of chat-based corrections: render the profile as a form with editable fields alongside the chat. Chat-mediated correction of 30 fields is the worst tool for the job, and it's where the 1.4 bug lives.
- **Infer supporting blocks.** If the manager only picks a top three, the other six blocks never run except undocumented workarounds. Auto-suggest supporting blocks from profile signals (has `direct_reports` → team dynamics; regulated `industry` → compliance; `key_external_relationships` → suppliers/clients) and confirm in the review step.
- **Employee-aware gate.** If `employee_aware` is false, block Stage 2 link creation with clear guidance to the manager. Interviewing someone about their departure when they don't know they're leaving is a serious failure mode the schema records but nothing enforces.

### 2.6 Consent and tone for the employee
Add a first-turn consent screen (not chat): what this is, who sees what (raw answers: nobody; synthesised doc: {recipients}), retention period, and an explicit accept. This is both a UK GDPR requirement and — stated plainly — the strongest candour lever the product has. The privacy fixes in 1.2 make the promise true; the consent screen makes it *believed*.

---

## Part 3 — Handover document quality

### 3.1 Two-phase synthesis: extract, then compose (keeping the single final call)
The single synthesis call is the right design for coherence, but it currently receives raw prose and must do extraction *and* composition at once — and long transcripts will crowd it. Insert a cheap structured pass first: per block, extract `KnowledgeItem`s (`claim, detail, entities, confidence, source_qs, is_gap`). Then the final single call composes the document from Profile + KnowledgeItems + risk flags + entity inventory + best transcript excerpts. Benefits: cross-referencing is preserved, gaps become deterministic ([GAP] markers come from data, not model vigilance), and the extraction output is reusable (dashboard, Notion export, search) later.

### 3.2 Deterministic confidentiality redaction
Today confidentiality is a prompt instruction ("do NOT include…"), which will eventually fail. Add a post-generation redaction pass: a dedicated Haiku call over the draft with only the confidential-sections instruction, returning either violations to strip or a clean bill. Prompt-only redaction is not a defensible answer to the first customer who asks "how do you guarantee salary context stays out?"

### 3.3 Document QA gate
Before export, run an LLM-judge scoring pass against a rubric: every priority block has substantive content; every critical/high risk flag appears in the Risk Summary with an action; the onboarding plan references `success_definition_90_days` and the overlap period; no unresolved placeholders; entity appendix matches inventory. Below threshold → one regeneration with the judge's notes appended (mirrors the existing sentinel-validation retry, but for substance instead of structure).

### 3.4 Risk flag hygiene
Flags are appended per answer with no dedup — the same single point of failure gets flagged five times across five questions, which reads as noise in the Risk Summary. Add a merge step (post-interview, pre-Stage 3): cluster by type + entity, keep highest severity, merge descriptions, count corroborating mentions (corroboration should *raise* prominence, not duplicate rows).

### 3.5 Delivery
Wire the webhook stubs: Stage 2 completion auto-triggers generation (as CLAUDE.md specifies — today it's a manual employee-side button, see 1.2); document completion emails the recipients a tokenised download link. Manager also gets a short "what happened" digest: duration, blocks covered, N risk flags (M critical), top 3 gaps. Without delivery, the product's output depends on someone remembering to come back.

---

## Part 4 — Platform, trust, and knowing if it works

### 4.1 Persistence & statelessness
Covered in 1.3 — Postgres + Redis checkpointing, stateless graph rehydration, object storage for documents, history endpoint. This is the prerequisite for multi-sitting interviews, delivery emails, and any dashboard.

### 4.2 Security baseline
- Invite tokens for both stages (manager link and employee link), single-use, TTL from settings.
- Role-scoped endpoints (employee can only message; manager can review/generate/download).
- Enforce `api_secret_key` (or proper session auth) on all non-chat routes; strip the leaky health endpoint.
- Redact PII from logs (the logging convention is right — metadata only — but the health endpoint and classifier warnings currently violate it).

### 4.3 GDPR / data lifecycle (UK product, employee personal data — this is not optional)
Consent capture (2.6), a documented retention policy enforced by TTLs in Postgres (not just Redis), a deletion endpoint (`DELETE /api/sessions/{id}` cascading transcript + profile + documents), and a data-processing note for the sales conversation. Enterprise buyers will ask; having the answer *is* a feature.

### 4.4 Latency & streaming
Per-turn latency is currently 2 serial Haiku calls + 1 Sonnet call before the employee sees anything. Fixes, in order of impact: SSE-stream the agent's reply tokens to the frontend; run risk + follow-up classifiers concurrently; enable Anthropic prompt caching on the Stage 2 system prompt (it's rebuilt identically every turn — `build_system_prompt(profile)` output is cacheable per session and the transcript prefix is cache-friendly); memoise `build_system_prompt` per session. Target: first token < 2s.

### 4.5 Evaluation harness — the product cannot currently measure itself
This is the most important *long-term* investment. Build:

- **Synthetic interview personas:** 6–10 scripted employee personas (candid ops manager, terse engineer, rambling account exec, guarded involuntary leaver, "everything's documented" denier…) driven by an LLM, each paired with a fixture profile. Run full Stage 2 sessions headlessly in CI.
- **Interview scorecards (LLM judge):** per session — % of persona's planted "hidden knowledge" items surfaced, follow-up precision (follow-ups that yielded new info / total), risk-flag recall against the persona's planted risks, turn count, refusal handling.
- **Document scorecards:** the 3.3 rubric applied to fixture runs, tracked over time.
- **Production telemetry:** funnel (stage 1 started → profile confirmed → stage 2 started → completed → doc delivered → doc downloaded), per-block drop-off, median session duration, classifier failure rate, follow-up rate. Drop-off *per block* tells you exactly which questions exhaust people.

Without this, every prompt tweak is a guess; with it, the interview measurably improves week over week — and "surfaced 84% of planted tribal knowledge in benchmark interviews" becomes a sales asset.

### 4.6 Model configuration
Move primary generation to `claude-sonnet-5` (better long-context synthesis for Stage 3 and better instruction-following on the one-question constraint); keep `claude-haiku-4-5` for classifiers. Consider a larger model for the single Stage 3 call only — it runs once per engagement, so cost is negligible relative to its importance.

---

## Roadmap

### Phase 0 — Trust & correctness (Week 1) — *do before any real customer session*
| # | Item | Ref |
|---|------|-----|
| 1 | Append follow-up answers instead of overwriting | 1.1 |
| 2 | Invite tokens; stop exposing stage1 session ID; move doc generation/download to manager side | 1.2 |
| 3 | Include question text in Stage 3 transcript | 1.6 |
| 4 | Structured output for both classifiers + failure metric | 1.5 |
| 5 | Haiku intent classification for profile confirmation | 1.4 |
| 6 | Remove health-endpoint leaks; enforce API auth | 1.7, 4.2 |
| 7 | Re-validate single-question retry | 1.7 |

### Phase 1 — Durability & delivery (Weeks 2–3)
Postgres/Redis persistence + checkpointing + stateless rehydration (1.3) · history endpoint & frontend restore · object storage for documents · auto-trigger Stage 3 on Stage 2 completion + email delivery with digest (3.5) · consent screen (2.6) · deletion endpoint & retention TTLs (4.3).

### Phase 2 — Interview intelligence (Weeks 3–5)
Entity inventory + entity-aware questions + block coverage gate (2.1) · information-gain follow-up classifier (2.2) · per-block micro-summaries (2.3) · turn budget + multi-sitting resume (2.3) · progress UI (2.3) · Stage 1 context ingestion & question skipping (2.5) · editable profile review form (2.5) · employee-aware gate + inferred supporting blocks (2.5) · SSE streaming + concurrent classifiers + prompt caching (4.4).

### Phase 3 — Document quality & measurement (Weeks 5–8)
Extract-then-compose synthesis (3.1) · deterministic redaction (3.2) · document QA gate (3.3) · risk-flag dedup/merge (3.4) · link/artefact capture (2.4) · synthetic-persona eval harness in CI + telemetry funnel (4.5).

### Phase 4 — Expansion (Post-MVP, aligns with existing Phase 2 scope)
Manager dashboard (live block progress, risk flags as they surface, gap list) · Notion/Confluence/SharePoint export reusing 3.1's structured KnowledgeItems · multi-role/bulk sessions · voice-based interview option · knowledge-base mode (query past handovers).

---

## Success metrics to hold this to

| Metric | Today | Target |
|---|---|---|
| Stage 2 completion rate | unmeasured | > 85% |
| Median employee session time | unmeasured (est. 60–90 min) | ≤ 45 min or split across sittings |
| Planted-knowledge recall (eval harness) | unmeasured | > 80% |
| Follow-up precision (new info / follow-up) | unmeasured | > 70% |
| Interview data surviving a redeploy | 0% | 100% |
| Documents delivered to recipients without manual steps | 0% | 100% |
| Time-to-first-token per turn | est. 6–15 s | < 2 s |
