from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "valueverse"
    app_env: str = "development"
    database_url: str = "sqlite:///./valueverse.db"
    redis_url: str | None = None
    storage_dir: Path = Path("storage")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cognee_enabled: bool = True
    llm_extraction_enabled: bool = True
    recall_default_top_k: int = 5
    auth_secret: str = "dev-insecure-change-me"
    auth_cookie_name: str = "valueverse_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_session_max_age_seconds: int = 60 * 60 * 24 * 14
    api_docs_enabled: bool | None = None
    max_upload_bytes: int = 200 * 1024 * 1024
    allowed_llm_hosts: str = ""
    allowed_web_search_commands: str = "uvx"
    allowed_web_search_packages: str = "minimax-coding-plan-mcp"
    api_key_encryption_secret: str = "dev-insecure-api-key-encryption-secret"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "valueverse"
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False
    smtp_timeout_seconds: int = 20
    email_change_code_ttl_seconds: int = 600
    email_change_code_max_attempts: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("storage_dir", mode="before")
    @classmethod
    def normalize_storage_dir(cls, value: str | Path) -> Path:
        return Path(value)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"prod", "production"}

    @property
    def effective_api_docs_enabled(self) -> bool:
        if self.api_docs_enabled is not None:
            return self.api_docs_enabled
        return not self.is_production

    @property
    def effective_auth_cookie_secure(self) -> bool:
        return self.auth_cookie_secure or self.is_production

    @property
    def allowed_llm_hosts_set(self) -> set[str]:
        return _split_csv(self.allowed_llm_hosts)

    @property
    def allowed_web_search_commands_set(self) -> set[str]:
        return _split_csv(self.allowed_web_search_commands)

    @property
    def allowed_web_search_packages_set(self) -> set[str]:
        return _split_csv(self.allowed_web_search_packages)

    @property
    def smtp_configured(self) -> bool:
        return bool(
            self.smtp_host.strip()
            and self.smtp_username.strip()
            and self.smtp_password.strip()
            and (self.smtp_from_email.strip() or self.smtp_username.strip())
        )

    def validate_runtime_settings(self) -> None:
        if self.is_production and self.auth_secret == "dev-insecure-change-me":
            raise RuntimeError("AUTH_SECRET must be set to a strong random value in production")
        if self.is_production and self.api_key_encryption_secret == "dev-insecure-api-key-encryption-secret":
            raise RuntimeError("API_KEY_ENCRYPTION_SECRET must be set to a strong random value in production")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _split_csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}
