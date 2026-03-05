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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def validate_for_production(self) -> None:
        """Check critical settings are configured. Call at startup."""
        errors = []
        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY is not set")
        if self.allowed_origins == "http://localhost:3000" and self.environment != "development":
            errors.append("ALLOWED_ORIGINS is still set to localhost — set to your production domain")
        if errors:
            for err in errors:
                print(f"[FATAL] {err}", file=sys.stderr)
            if self.environment != "development":
                raise SystemExit(1)


settings = Settings()
