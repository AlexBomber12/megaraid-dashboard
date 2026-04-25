from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    alert_smtp_host: str = Field(...)
    alert_smtp_port: int = Field(...)
    alert_smtp_user: str = Field(...)
    alert_smtp_password: str = Field(...)
    alert_from: str = Field(...)
    alert_to: str = Field(...)
    admin_username: str = Field(...)
    admin_password_hash: str = Field(...)
    storcli_path: str = Field(...)
    metrics_interval_seconds: int = Field(...)
    database_url: str = Field(...)
    log_level: str = Field(...)


@lru_cache
def get_settings() -> Settings:
    # BaseSettings reads required fields from environment sources at runtime.
    return Settings()  # type: ignore[call-arg]
