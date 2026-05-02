"""Add RoC temperature column to controller snapshots.

Revision ID: 0004_controller_roc_temperature
Revises: 0003_events_notified_at
Create Date: 2026-05-02 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_controller_roc_temperature"
down_revision: str | None = "0003_events_notified_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("controller_snapshots") as batch:
        batch.add_column(sa.Column("roc_temperature_celsius", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("controller_snapshots") as batch:
        batch.drop_column("roc_temperature_celsius")
