from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    alert_smtp_host: str = "smtp.protonmail.ch"
    alert_smtp_port: int = 587
    alert_smtp_user: str = "alert@alexbomber.com"
    alert_smtp_password: str = "changeme-proton-smtp-token"
    alert_from: str = "alert@alexbomber.com"
    alert_to: str = "changeme@example.com"
    admin_username: str = "admin"
    admin_password_hash: str = "changeme-bcrypt-hash"
    storcli_path: str = "/usr/local/sbin/storcli64"
    metrics_interval_seconds: int = 300
    database_url: str = "sqlite:///./megaraid.db"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
