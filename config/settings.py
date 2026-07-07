import os
import sys

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    primary_model: str = "claude-sonnet-4-6"
    classifier_model: str = "claude-haiku-4-5-20251001"

    # Session
    session_ttl_hours: int = 72
    stage1_to_stage2_link_ttl_hours: int = 168

    # Storage
    # "memory"     — in-process (dev/tests; lost on restart)
    # "persistent" — Redis for session state/tokens + Postgres for profiles
    storage_backend: str = "memory"
    database_url: str = ""
    redis_url: str = ""

    # Output
    default_output_format: str = "docx"

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
        for field_name in self.model_fields:
            env_name = field_name.upper()
            env_val = os.environ.get(env_name)
            if env_val and not getattr(self, field_name, None):
                object.__setattr__(self, field_name, env_val)

    def validate_for_production(self) -> None:
        """Check critical settings are configured. Call at startup."""
        key_preview = f"{self.anthropic_api_key[:8]}..." if self.anthropic_api_key else "(empty)"
        print(f"[STARTUP] environment={self.environment} api_key={key_preview} origins={self.allowed_origins}", file=sys.stderr)
        errors = []
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set")
        if self.allowed_origins == "http://localhost:3000" and self.environment != "development":
            errors.append("ALLOWED_ORIGINS is still set to localhost — set to your production domain")
        if self.storage_backend == "persistent":
            if not self.redis_url:
                errors.append("STORAGE_BACKEND=persistent requires REDIS_URL")
            if not self.database_url:
                errors.append("STORAGE_BACKEND=persistent requires DATABASE_URL")
        elif self.environment == "production":
            # Warn, don't fail — don't take down an existing deploy that hasn't
            # provisioned Redis/Postgres yet. Flip to persistent when ready.
            print(
                "[WARN] STORAGE_BACKEND=memory in production — sessions and profiles "
                "are lost on restart. Set STORAGE_BACKEND=persistent once Redis and "
                "Postgres are provisioned.",
                file=sys.stderr,
            )
        if errors:
            for err in errors:
                print(f"[FATAL] {err}", file=sys.stderr)
            if self.environment != "development":
                raise SystemExit(1)


settings = Settings()
