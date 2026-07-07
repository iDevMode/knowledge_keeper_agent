# Phase 1 — Going durable

## Deployment model (read first)

The FastAPI backend is designed to run as a **long-lived process in a
persistent container — deploy it on Railway/Render using the provided
`Dockerfile`.** It relies on an in-process background thread for document
generation and on a shared, warm checkpointer/registry — neither of which
survives a serverless function that is frozen or recycled between requests.

**Vercel serves the frontend only.** The `vercel.json` in this repo points
`/api/*` at the Python app for a quick preview, but a serverless deployment is
**not** a supported backend host: with `STORAGE_BACKEND=memory` a session won't
even persist between two messages, and background document generation is lost
when the invocation returns. For anything beyond a static frontend preview,
run the API on Railway with `STORAGE_BACKEND=persistent` and point the frontend
at that origin.

---


Out of the box the app runs on `STORAGE_BACKEND=memory`: everything (sessions,
conversation checkpoints, profiles, documents) lives in process memory and is
**lost on restart**. That's fine for local dev and tests. For any real
deployment, provision the services below and flip the environment variables —
no code change required.

## What to provision

| Service | Purpose | Env vars |
|---|---|---|
| **Postgres** | Durable Role Intelligence Profiles + LangGraph conversation checkpoints | `DATABASE_URL` |
| **Redis** | Session state, stage links, invite/manager tokens, document status (all TTL'd) | `REDIS_URL` |
| **Object storage** (S3 / Cloudflare R2) | Generated handover documents | `DOCUMENT_STORAGE=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL` (R2), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` |
| **SMTP provider** (Postmark, SES, …) | Emailing the handover to the manager | `EMAIL_BACKEND=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` |

## Minimum to survive a restart

Set these two and interviews + profiles become durable and the API goes
stateless (any worker can resume any session):

```
STORAGE_BACKEND=persistent
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
```

Tables are created automatically on startup (`role_profiles`, plus LangGraph's
checkpoint tables via `PostgresSaver.setup()`).

## To also survive on multiple workers / serverless

Add object storage so document downloads don't depend on the worker that
generated them, and set the public base URL for email links:

```
DOCUMENT_STORAGE=s3
S3_BUCKET=knowledgekeeper-documents
S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com   # omit for AWS S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
PUBLIC_BASE_URL=https://your-app.example.com
```

## To deliver the handover by email

```
EMAIL_BACKEND=smtp
SMTP_HOST=smtp.postmarkapp.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
EMAIL_FROM=KnowledgeKeeper <no-reply@yourdomain.com>
```

With `EMAIL_BACKEND=console` (the default) the fully composed email and digest
are written to the logs instead of sent — useful for verifying content before
wiring up a provider.

## Dependencies

The persistence dependencies (`redis`, `psycopg`, `psycopg-pool`,
`langgraph-checkpoint-postgres`, `boto3`) are declared in `requirements.txt`
and imported lazily — they're only loaded when the corresponding backend is
selected, so the memory/console defaults need none of them at runtime.

## Health check

`GET /api/health` returns `{"status": "ok", "api_key_set": <bool>}` and is the
configured Railway health-check path.

## Data protection (Phase 1b)

- **Consent.** The employee must explicitly consent on a screen before the
  Stage 2 interview starts; the server rejects session creation without it and
  records `consent_given_at`.
- **Right to erasure.** `DELETE /api/manager/{stage1_session_id}?token=...`
  (manager-token gated) permanently deletes both sessions, the profile, the
  document (blob + metadata), the conversation checkpoints, and the tokens. The
  manager dashboard exposes this as "Delete all data for this handover".
- **Retention.** Redis session/token state expires via `SESSION_TTL_HOURS` /
  `STAGE1_TO_STAGE2_LINK_TTL_HOURS`. Durable Postgres profiles are purged after
  `DATA_RETENTION_DAYS` (default 90) by a maintenance job:

  ```
  python -m scripts.purge_expired
  ```

  Schedule this daily (e.g. a Railway cron / scheduled job) in a persistent
  deployment.
