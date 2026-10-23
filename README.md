# KnowledgeKeeper

Three-stage AI agent system for institutional knowledge capture during employee departures. Built by Nukode.

1. **Business interview** — the manager signs in and answers a setup interview, producing a Role Intelligence Profile they review and confirm.
2. **Employee interview** — the departing employee is interviewed through a personal link the manager shares.
3. **Handover pack** — generated automatically (Word or PDF), emailed to the recipients the manager named, and kept on the manager's dashboard.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY; set ENVIRONMENT=development
python -m uvicorn api.routes:app --port 8321

cd frontend && npm install && npm run dev
```

Without `DATABASE_URL` everything is held in memory. Without `SENDGRID_API_KEY` no email is sent: to sign in, enter your email on the home page and open the **DEVELOPMENT sign-in link** printed in the server log.

PDF output needs WeasyPrint's system libraries (Pango, HarfBuzz) — the Dockerfile installs them.

## Production (Railway)

Required: `ANTHROPIC_API_KEY`, `DATABASE_URL`, `API_SECRET_KEY`, `ALLOWED_ORIGINS`, `PUBLIC_BASE_URL`, `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS` (on a domain verified in SendGrid). The app refuses to start without them. See `.env.example`.

## Tests

```bash
pytest tests/ -v
# Postgres-backed tests too:
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/kk_test pytest tests/ -v
```

Project conventions and design decisions: `CLAUDE.md`.
