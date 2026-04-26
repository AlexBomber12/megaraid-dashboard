"""Add physical drive temperature state table.

Revision ID: 0002_pd_temp_state
Revises: 0001_initial
Create Date: 2026-04-26 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pd_temp_state"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pd_temp_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("enclosure_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state in ('ok', 'warning', 'critical')",
            name=op.f("ck_pd_temp_states_state_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pd_temp_states")),
        sa.UniqueConstraint(
            "enclosure_id",
            "slot_id",
            "serial_number",
            name=op.f("uq_pd_temp_states_enclosure_id"),
        ),
    )
    op.create_index(
        "ix_pd_temp_states_enclosure_id_slot_id",
        "pd_temp_states",
        ["enclosure_id", "slot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pd_temp_states_enclosure_id_slot_id", table_name="pd_temp_states")
    op.drop_table("pd_temp_states")
