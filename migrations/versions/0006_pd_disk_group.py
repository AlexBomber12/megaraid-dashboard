"""Add disk_group_id to physical drive snapshots.

Revision ID: 0006_pd_disk_group
Revises: 0005_operator_action_username
Create Date: 2026-05-03 22:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_pd_disk_group"
down_revision: str | None = "0005_operator_action_username"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("pd_snapshots") as batch:
        batch.add_column(sa.Column("disk_group_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pd_snapshots") as batch:
        batch.drop_column("disk_group_id")
