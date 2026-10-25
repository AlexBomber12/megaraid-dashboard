"""Add per-channel event delivery state.

Revision ID: 0009_event_deliveries
Revises: 0008_cleanup_false_controller_alarm_events
Create Date: 2026-10-25 00:00:00.000000

``events.notified_at`` stays the SMTP delivery marker and is not touched, so existing
email delivery and duplicate suppression continue unchanged. Telegram delivery and retry
state lives in ``event_deliveries`` (one row per event and channel).

Backfill: every warning/critical event that exists when the migration runs gets a
``telegram`` row with status ``skipped`` (detail ``pre_migration``). Enabling Telegram
therefore never replays historical events; only events recorded after the upgrade can
be delivered. Downgrade drops the table and leaves ``events`` exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_event_deliveries"
down_revision: str | None = "0008_cleanup_false_controller_alarm_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_deliveries_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_deliveries")),
        sa.UniqueConstraint(
            "event_id",
            "channel",
            name="uq_event_deliveries_event_id_channel",
        ),
    )
    op.create_index(
        "ix_event_deliveries_channel_status",
        "event_deliveries",
        ["channel", "status"],
        unique=False,
    )
    op.execute(
        sa.text(
            "INSERT INTO event_deliveries (event_id, channel, status, attempts, detail) "
            "SELECT id, 'telegram', 'skipped', 0, 'pre_migration' FROM events "
            "WHERE severity IN ('warning', 'critical')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_event_deliveries_channel_status", table_name="event_deliveries")
    op.drop_table("event_deliveries")
