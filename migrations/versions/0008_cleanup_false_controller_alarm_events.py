"""Cleanup historical false-positive controller alarm events.

Revision ID: 0008_cleanup_false_controller_alarm_events
Revises: 0007_system_state
Create Date: 2026-05-14 00:00:00.000000

The previous event detector emitted a baseline ``controller`` event with
summary ``Alarm state is <X>`` whenever ``HwCfg.Alarm`` was not ``Off``.
``HwCfg.Alarm`` is the buzzer-enabled configuration flag (default ``On``),
not an active alarm. The new code no longer emits this baseline event; this
migration removes the historical false-positive rows so that the events
table and recent-activity feed are not permanently polluted.

Only the baseline emissions (``summary LIKE 'Alarm state is %'``) are
deleted. Legitimate alarm-state transition events are renamed by the new
code under a separate category (``controller_alarm_setting_changed``) and
are not touched here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_cleanup_false_controller_alarm_events"
down_revision: str | None = "0007_system_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM events WHERE category = 'controller' AND summary LIKE 'Alarm state is %'"
        )
    )


def downgrade() -> None:
    # One-way cleanup of false-positive rows: the new code does not emit
    # these events, so re-creating them on downgrade would be incorrect.
    pass
