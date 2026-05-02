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
    alert_smtp_use_starttls: bool = True
    alert_severity_threshold: str = "critical"
    alert_suppress_window_minutes: int = 60
    alert_throttle_per_hour: int = 20
    admin_username: str = Field(...)
    admin_password_hash: str = Field(...)
    storcli_path: str = Field(...)
    storcli_use_sudo: bool = False
    metrics_interval_seconds: int = Field(...)
    metrics_raw_retention_days: int = 30
    metrics_hourly_retention_days: int = 365
    store_raw_snapshot_payload: bool = False
    collector_enabled: bool = True
    collector_lock_path: str = "/tmp/megaraid-dashboard-collector.lock"
    temp_warning_celsius: int = 55
    temp_critical_celsius: int = 60
    temp_hysteresis_celsius: int = 5
    roc_temp_warning_celsius: int = 95
    roc_temp_critical_celsius: int = 105
    roc_temp_hysteresis_celsius: int = 5
    cv_capacitance_warning_percent: int = 70
    database_url: str = "sqlite:///./megaraid.db"
    log_level: str = Field(...)

    @model_validator(mode="after")
    def validate_runtime_values(self) -> Settings:
        if self.metrics_interval_seconds <= 0:
            msg = "metrics_interval_seconds must be positive"
            raise ValueError(msg)
        if self.metrics_raw_retention_days <= 0:
            msg = "metrics_raw_retention_days must be positive"
            raise ValueError(msg)
        if self.metrics_hourly_retention_days <= 0:
            msg = "metrics_hourly_retention_days must be positive"
            raise ValueError(msg)
        if not 1 <= self.cv_capacitance_warning_percent <= 100:
            msg = "cv_capacitance_warning_percent must be between 1 and 100"
            raise ValueError(msg)
        if not self.collector_lock_path.strip():
            msg = "collector_lock_path must not be empty"
            raise ValueError(msg)
        if self.temp_critical_celsius <= self.temp_warning_celsius:
            msg = "temp_critical_celsius must be greater than temp_warning_celsius"
            raise ValueError(msg)
        if self.temp_hysteresis_celsius < 1:
            msg = "temp_hysteresis_celsius must be at least 1"
            raise ValueError(msg)
        if self.temp_hysteresis_celsius >= self.temp_warning_celsius:
            msg = "temp_hysteresis_celsius must be less than temp_warning_celsius"
            raise ValueError(msg)
        if not 40 <= self.roc_temp_warning_celsius <= 130:
            msg = "roc_temp_warning_celsius must be between 40 and 130"
            raise ValueError(msg)
        if not 40 <= self.roc_temp_critical_celsius <= 130:
            msg = "roc_temp_critical_celsius must be between 40 and 130"
            raise ValueError(msg)
        if self.roc_temp_critical_celsius <= self.roc_temp_warning_celsius:
            msg = "roc_temp_critical_celsius must be greater than roc_temp_warning_celsius"
            raise ValueError(msg)
        if self.roc_temp_hysteresis_celsius < 1:
            msg = "roc_temp_hysteresis_celsius must be at least 1"
            raise ValueError(msg)
        if self.roc_temp_hysteresis_celsius >= self.roc_temp_warning_celsius:
            msg = "roc_temp_hysteresis_celsius must be less than roc_temp_warning_celsius"
            raise ValueError(msg)
        if self.alert_smtp_port <= 0:
            msg = "alert_smtp_port must be positive"
            raise ValueError(msg)
        if self.alert_severity_threshold not in {"info", "warning", "critical"}:
            msg = "alert_severity_threshold must be one of info, warning, critical"
            raise ValueError(msg)
        if self.alert_suppress_window_minutes <= 0:
            msg = "alert_suppress_window_minutes must be positive"
            raise ValueError(msg)
        if self.alert_throttle_per_hour <= 0:
            msg = "alert_throttle_per_hour must be positive"
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
