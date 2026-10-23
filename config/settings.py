import os
import sys

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    primary_model: str = "claude-sonnet-4-6"
    classifier_model: str = "claude-haiku-4-5-20251001"

    # Persistence (review finding H3)
    # When unset, all state is held in-process: sessions, LangGraph checkpoints
    # and generated documents. That works for a single worker but does not
    # survive a restart, so every redeploy destroys in-flight interviews.
    # Required in production — see validate_for_production.
    database_url: str = ""

    # Connection budget per worker:
    #   db_pool_size        short queries — session store and document store
    #   db_lock_pool_size   advisory locks, each held for a whole interview turn
    #   db_pool_size        the LangGraph checkpointer's own pool
    # Total per worker is roughly 2 * db_pool_size + db_lock_pool_size, and it
    # multiplies by WEB_CONCURRENCY. Managed Postgres often caps around 100, so
    # raising worker count without lowering these is how you exhaust it.
    db_pool_size: int = 5
    db_lock_pool_size: int = 10

    # Session
    session_ttl_hours: int = 72
    # Must not exceed session_ttl_hours — the employee link cannot outlive the
    # session behind it. Was 168 against a 72h session TTL, which meant the
    # link stopped working four days before it claimed to expire. To give the
    # employee longer, raise BOTH values.
    stage1_to_stage2_link_ttl_hours: int = 72

    # Output
    default_output_format: str = "docx"

    # Email (SendGrid)
    # Without an API key, emails are logged (recipient count and subject only)
    # instead of sent — fine for development, a warning in production.
    sendgrid_api_key: str = ""
    # Must be an address on a domain verified in SendGrid (Sender Authentication).
    email_from_address: str = ""
    email_from_name: str = "KnowledgeKeeper"
    # Public origin of the app, used to build links inside emails,
    # e.g. https://knowledgekeeper.nukode.co.uk
    public_base_url: str = "http://localhost:3000"

    # API
    api_secret_key: str = ""
    allowed_origins: str = "http://localhost:3000"

    # Environment
    environment: str = "development"
    log_level: str = "INFO"

    # Port (for deployment platforms like Railway)
    port: int = 8321

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Fallback: read directly from os.environ if pydantic-settings missed them
        # This handles deployment platforms where env var injection timing varies
        for field_name in type(self).model_fields:
            env_name = field_name.upper()
            env_val = os.environ.get(env_name)
            if env_val and not getattr(self, field_name, None):
                object.__setattr__(self, field_name, env_val)

    def validate_for_production(self) -> None:
        """Check critical settings are configured. Call at startup."""
        print(
            f"[STARTUP] environment={self.environment} "
            f"api_key={'set' if self.anthropic_api_key else 'MISSING'}",
            file=sys.stderr,
        )
        errors = []
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set")
        workers = os.environ.get("WEB_CONCURRENCY", "1")
        try:
            worker_count = int(workers)
        except ValueError:
            worker_count = 1
        if worker_count > 1 and not self.database_url:
            # Each worker would hold its own sessions, checkpoints and
            # documents, so a session created by one is invisible to the rest
            # and the next message 404s — intermittently, depending on which
            # worker the request lands on.
            errors.append(
                f"WEB_CONCURRENCY is {worker_count} but DATABASE_URL is not set — "
                f"workers cannot share session state"
            )
        if not self.database_url and self.environment != "development":
            # Without it every restart destroys in-flight interviews: sessions,
            # LangGraph checkpoints and generated documents all live in process
            # memory.
            errors.append(
                "DATABASE_URL is not set — sessions would not survive a restart"
            )
        if not self.api_secret_key and self.environment != "development":
            # Session tokens are signed with this. Without it the app falls back
            # to a per-process key, so every restart silently invalidates every
            # live interview link.
            errors.append(
                "API_SECRET_KEY is not set — session tokens would not survive a restart"
            )
        if self.allowed_origins == "http://localhost:3000" and self.environment != "development":
            errors.append("ALLOWED_ORIGINS is still set to localhost — set to your production domain")
        if self.stage1_to_stage2_link_ttl_hours > self.session_ttl_hours:
            # The employee link cannot outlive the session it points at. With
            # the defaults (168 vs 72) an employee was told they had a week and
            # got "Session not found" on day four, and any interview finished
            # after 72h produced no document because the Role Intelligence
            # Profile had already been swept.
            errors.append(
                f"STAGE1_TO_STAGE2_LINK_TTL_HOURS ({self.stage1_to_stage2_link_ttl_hours}) "
                f"exceeds SESSION_TTL_HOURS ({self.session_ttl_hours}) — the employee "
                f"link would outlive the session behind it"
            )
        if self.environment != "development":
            if not self.sendgrid_api_key:
                print(
                    "[WARN] SENDGRID_API_KEY is not set — handover packs will not be emailed",
                    file=sys.stderr,
                )
            elif not self.email_from_address:
                errors.append(
                    "SENDGRID_API_KEY is set but EMAIL_FROM_ADDRESS is not — SendGrid "
                    "rejects mail without a verified sender"
                )
        if errors:
            for err in errors:
                print(f"[FATAL] {err}", file=sys.stderr)
            if self.environment != "development":
                raise SystemExit(1)


settings = Settings()
