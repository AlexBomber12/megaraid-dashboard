from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
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
    storcli_use_sudo: bool = False
    metrics_interval_seconds: int = Field(...)
    metrics_raw_retention_days: int = 30
    metrics_hourly_retention_days: int = 365
    store_raw_snapshot_payload: bool = False
    collector_enabled: bool = True
    temp_warning_celsius: int = 55
    temp_critical_celsius: int = 60
    temp_hysteresis_celsius: int = 5
    cv_capacitance_warning_percent: int = 70
    database_url: str = "sqlite:///./megaraid.db"
    log_level: str = Field(...)

    @model_validator(mode="after")
    def validate_runtime_values(self) -> Settings:
        if self.metrics_interval_seconds <= 0:
            msg = "metrics_interval_seconds must be positive"
            raise ValueError(msg)
        if self.temp_critical_celsius <= self.temp_warning_celsius:
            msg = "temp_critical_celsius must be greater than temp_warning_celsius"
            raise ValueError(msg)
        if self.temp_hysteresis_celsius < 1:
            msg = "temp_hysteresis_celsius must be at least 1"
            raise ValueError(msg)
        return self


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./megaraid.db"


@lru_cache
def get_settings() -> Settings:
    # BaseSettings reads required fields from environment sources at runtime.
    return Settings()  # type: ignore[call-arg]


def get_database_url() -> str:
    return DatabaseSettings().database_url
