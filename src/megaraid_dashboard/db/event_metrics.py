from __future__ import annotations

from typing import cast

from sqlalchemy import event
from sqlalchemy.orm import Session

from megaraid_dashboard.web.metrics import EVENTS_TOTAL

_PENDING_EVENT_METRICS_KEY = "megaraid_dashboard_pending_event_metrics"
type EventMetricLabels = tuple[str, str]


def stage_event_metric(session: Session, *, severity: str, category: str) -> None:
    pending = cast(
        list[EventMetricLabels],
        session.info.setdefault(_PENDING_EVENT_METRICS_KEY, []),
    )
    pending.append((severity, category))


def _pop_pending_event_metrics(session: Session) -> list[EventMetricLabels]:
    return cast(
        list[EventMetricLabels],
        session.info.pop(_PENDING_EVENT_METRICS_KEY, []),
    )


def _increment_staged_event_metrics(session: Session) -> None:
    for severity, category in _pop_pending_event_metrics(session):
        EVENTS_TOTAL.labels(severity=severity, category=category).inc()


def _discard_staged_event_metrics(session: Session) -> None:
    _pop_pending_event_metrics(session)


event.listen(Session, "after_commit", _increment_staged_event_metrics)
event.listen(Session, "after_rollback", _discard_staged_event_metrics)
