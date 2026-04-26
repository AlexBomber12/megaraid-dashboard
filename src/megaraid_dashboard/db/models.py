from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from megaraid_dashboard.db.base import Base, TimestampedMixin, UTCDateTime


class ControllerSnapshot(TimestampedMixin, Base):
    __tablename__ = "controller_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    firmware_version: Mapped[str] = mapped_column(String, nullable=False)
    bios_version: Mapped[str] = mapped_column(String, nullable=False)
    driver_version: Mapped[str] = mapped_column(String, nullable=False)
    alarm_state: Mapped[str] = mapped_column(String, nullable=False)
    cv_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bbu_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    virtual_drives: Mapped[list[VirtualDriveSnapshot]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    physical_drives: Mapped[list[PhysicalDriveSnapshot]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    cachevault: Mapped[CacheVaultSnapshot | None] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class VirtualDriveSnapshot(TimestampedMixin, Base):
    __tablename__ = "vd_snapshots"
    __table_args__ = (Index("ix_vd_snapshots_snapshot_id_vd_id", "snapshot_id", "vd_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("controller_snapshots.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    vd_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    raid_level: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    access: Mapped[str] = mapped_column(String, nullable=False)
    cache: Mapped[str] = mapped_column(String, nullable=False)

    snapshot: Mapped[ControllerSnapshot] = relationship(back_populates="virtual_drives")


class PhysicalDriveSnapshot(TimestampedMixin, Base):
    __tablename__ = "pd_snapshots"
    __table_args__ = (
        Index(
            "ix_pd_snapshots_snapshot_id_enclosure_id_slot_id",
            "snapshot_id",
            "enclosure_id",
            "slot_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("controller_snapshots.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    enclosure_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    firmware_version: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    interface: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    temperature_celsius: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_errors: Mapped[int] = mapped_column(Integer, nullable=False)
    other_errors: Mapped[int] = mapped_column(Integer, nullable=False)
    predictive_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    smart_alert: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sas_address: Mapped[str] = mapped_column(String, nullable=False)

    snapshot: Mapped[ControllerSnapshot] = relationship(back_populates="physical_drives")


class PhysicalDriveTempState(TimestampedMixin, Base):
    __tablename__ = "pd_temp_states"
    __table_args__ = (
        UniqueConstraint("enclosure_id", "slot_id", "serial_number"),
        Index("ix_pd_temp_states_enclosure_id_slot_id", "enclosure_id", "slot_id"),
        CheckConstraint("state in ('ok', 'warning', 'critical')", name="state_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enclosure_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class CacheVaultSnapshot(TimestampedMixin, Base):
    __tablename__ = "cv_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("controller_snapshots.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    temperature_celsius: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_energy: Mapped[str | None] = mapped_column(String, nullable=True)
    capacitance_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replacement_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    next_learn_cycle: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    snapshot: Mapped[ControllerSnapshot] = relationship(back_populates="cachevault")


class PhysicalDriveMetricsHourly(TimestampedMixin, Base):
    __tablename__ = "pd_metrics_hourly"
    __table_args__ = (UniqueConstraint("bucket_start", "enclosure_id", "slot_id", "serial_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    enclosure_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    temperature_celsius_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_celsius_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_celsius_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    media_errors_max: Mapped[int] = mapped_column(Integer, nullable=False)
    other_errors_max: Mapped[int] = mapped_column(Integer, nullable=False)
    predictive_failures_max: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)


class PhysicalDriveMetricsDaily(TimestampedMixin, Base):
    __tablename__ = "pd_metrics_daily"
    __table_args__ = (UniqueConstraint("bucket_start", "enclosure_id", "slot_id", "serial_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    enclosure_id: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    temperature_celsius_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_celsius_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_celsius_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    media_errors_max: Mapped[int] = mapped_column(Integer, nullable=False)
    other_errors_max: Mapped[int] = mapped_column(Integer, nullable=False)
    predictive_failures_max: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)


class Event(TimestampedMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_severity_occurred_at", "severity", "occurred_at"),
        Index("ix_events_category_occurred_at", "category", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditLog(TimestampedMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    command_argv: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdout_tail: Mapped[str | None] = mapped_column(String, nullable=True)
    stderr_tail: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AlertSent(TimestampedMixin, Base):
    __tablename__ = "alerts_sent"
    __table_args__ = (Index("ix_alerts_sent_fingerprint_unique", "fingerprint", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    smtp_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    suppressed_until: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        index=True,
        nullable=True,
    )
