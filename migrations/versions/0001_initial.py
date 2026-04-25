"""Initial database schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-25 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "controller_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("firmware_version", sa.String(), nullable=False),
        sa.Column("bios_version", sa.String(), nullable=False),
        sa.Column("driver_version", sa.String(), nullable=False),
        sa.Column("alarm_state", sa.String(), nullable=False),
        sa.Column("cv_present", sa.Boolean(), nullable=False),
        sa.Column("bbu_present", sa.Boolean(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_controller_snapshots")),
    )
    op.create_index(
        op.f("ix_controller_snapshots_captured_at"),
        "controller_snapshots",
        ["captured_at"],
    )

    op.create_table(
        "vd_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("vd_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("raid_level", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("access", sa.String(), nullable=False),
        sa.Column("cache", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["controller_snapshots.id"],
            name=op.f("fk_vd_snapshots_snapshot_id_controller_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vd_snapshots")),
    )
    op.create_index(op.f("ix_vd_snapshots_snapshot_id"), "vd_snapshots", ["snapshot_id"])
    op.create_index("ix_vd_snapshots_snapshot_id_vd_id", "vd_snapshots", ["snapshot_id", "vd_id"])

    op.create_table(
        "pd_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("enclosure_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("firmware_version", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("interface", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("temperature_celsius", sa.Integer(), nullable=True),
        sa.Column("media_errors", sa.Integer(), nullable=False),
        sa.Column("other_errors", sa.Integer(), nullable=False),
        sa.Column("predictive_failures", sa.Integer(), nullable=False),
        sa.Column("smart_alert", sa.Boolean(), nullable=False),
        sa.Column("sas_address", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["controller_snapshots.id"],
            name=op.f("fk_pd_snapshots_snapshot_id_controller_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pd_snapshots")),
    )
    op.create_index(op.f("ix_pd_snapshots_snapshot_id"), "pd_snapshots", ["snapshot_id"])
    op.create_index(
        "ix_pd_snapshots_snapshot_id_enclosure_id_slot_id",
        "pd_snapshots",
        ["snapshot_id", "enclosure_id", "slot_id"],
    )

    op.create_table(
        "cv_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("temperature_celsius", sa.Integer(), nullable=True),
        sa.Column("pack_energy", sa.String(), nullable=True),
        sa.Column("capacitance_percent", sa.Integer(), nullable=True),
        sa.Column("replacement_required", sa.Boolean(), nullable=False),
        sa.Column("next_learn_cycle", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["controller_snapshots.id"],
            name=op.f("fk_cv_snapshots_snapshot_id_controller_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cv_snapshots")),
        sa.UniqueConstraint("snapshot_id", name=op.f("uq_cv_snapshots_snapshot_id")),
    )

    op.create_table(
        "pd_metrics_hourly",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enclosure_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("temperature_celsius_min", sa.Integer(), nullable=True),
        sa.Column("temperature_celsius_max", sa.Integer(), nullable=True),
        sa.Column("temperature_celsius_avg", sa.Float(), nullable=True),
        sa.Column("temperature_sample_count", sa.Integer(), nullable=False),
        sa.Column("media_errors_max", sa.Integer(), nullable=False),
        sa.Column("other_errors_max", sa.Integer(), nullable=False),
        sa.Column("predictive_failures_max", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pd_metrics_hourly")),
        sa.UniqueConstraint(
            "bucket_start",
            "enclosure_id",
            "slot_id",
            "serial_number",
            name=op.f("uq_pd_metrics_hourly_bucket_start"),
        ),
    )
    op.create_index(
        op.f("ix_pd_metrics_hourly_bucket_start"),
        "pd_metrics_hourly",
        ["bucket_start"],
    )

    op.create_table(
        "pd_metrics_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enclosure_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("temperature_celsius_min", sa.Integer(), nullable=True),
        sa.Column("temperature_celsius_max", sa.Integer(), nullable=True),
        sa.Column("temperature_celsius_avg", sa.Float(), nullable=True),
        sa.Column("temperature_sample_count", sa.Integer(), nullable=False),
        sa.Column("media_errors_max", sa.Integer(), nullable=False),
        sa.Column("other_errors_max", sa.Integer(), nullable=False),
        sa.Column("predictive_failures_max", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pd_metrics_daily")),
        sa.UniqueConstraint(
            "bucket_start",
            "enclosure_id",
            "slot_id",
            "serial_number",
            name=op.f("uq_pd_metrics_daily_bucket_start"),
        ),
    )
    op.create_index(op.f("ix_pd_metrics_daily_bucket_start"), "pd_metrics_daily", ["bucket_start"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_occurred_at"), "events", ["occurred_at"])
    op.create_index("ix_events_severity_occurred_at", "events", ["severity", "occurred_at"])
    op.create_index("ix_events_category_occurred_at", "events", ["category", "occurred_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("command_argv", sa.JSON(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.String(), nullable=True),
        sa.Column("stderr_tail", sa.String(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_occurred_at"), "audit_logs", ["occurred_at"])

    op.create_table(
        "alerts_sent",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("smtp_message_id", sa.String(), nullable=True),
        sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts_sent")),
    )
    op.create_index(op.f("ix_alerts_sent_sent_at"), "alerts_sent", ["sent_at"])
    op.create_index(op.f("ix_alerts_sent_suppressed_until"), "alerts_sent", ["suppressed_until"])
    op.create_index(
        "ix_alerts_sent_fingerprint_unique",
        "alerts_sent",
        ["fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_sent_fingerprint_unique", table_name="alerts_sent")
    op.drop_index(op.f("ix_alerts_sent_suppressed_until"), table_name="alerts_sent")
    op.drop_index(op.f("ix_alerts_sent_sent_at"), table_name="alerts_sent")
    op.drop_table("alerts_sent")

    op.drop_index(op.f("ix_audit_logs_occurred_at"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_events_category_occurred_at", table_name="events")
    op.drop_index("ix_events_severity_occurred_at", table_name="events")
    op.drop_index(op.f("ix_events_occurred_at"), table_name="events")
    op.drop_table("events")

    op.drop_index(op.f("ix_pd_metrics_daily_bucket_start"), table_name="pd_metrics_daily")
    op.drop_table("pd_metrics_daily")

    op.drop_index(op.f("ix_pd_metrics_hourly_bucket_start"), table_name="pd_metrics_hourly")
    op.drop_table("pd_metrics_hourly")

    op.drop_table("cv_snapshots")

    op.drop_index("ix_pd_snapshots_snapshot_id_enclosure_id_slot_id", table_name="pd_snapshots")
    op.drop_index(op.f("ix_pd_snapshots_snapshot_id"), table_name="pd_snapshots")
    op.drop_table("pd_snapshots")

    op.drop_index("ix_vd_snapshots_snapshot_id_vd_id", table_name="vd_snapshots")
    op.drop_index(op.f("ix_vd_snapshots_snapshot_id"), table_name="vd_snapshots")
    op.drop_table("vd_snapshots")

    op.drop_index(op.f("ix_controller_snapshots_captured_at"), table_name="controller_snapshots")
    op.drop_table("controller_snapshots")
