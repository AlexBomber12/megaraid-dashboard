"""Add notified_at column to events.

Revision ID: 0003_events_notified_at
Revises: 0002_pd_temp_state
Create Date: 2026-05-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_events_notified_at"
down_revision: str | None = "0002_pd_temp_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "notified_at")
