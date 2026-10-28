"""Fail-closed UI/API rollout policy; sudoers remains an independent boundary."""

from megaraid_dashboard.config import Settings


def action_disabled_reason(settings: Settings, family: str) -> str | None:
    """Return the same operator-facing refusal for templates and POST routes."""
    if settings.controller_read_only:
        return "Controller actions disabled: read-only rollout."
    if settings.controller_action_capability == "locate" and family in {"locate", "maintenance"}:
        return None
    return (
        f"Controller action '{family}' unavailable: CONTROLLER_ACTION_CAPABILITY="
        f"{settings.controller_action_capability}. Only Locate Start/Stop is supported by this "
        "rollout. An operator may select locate after reviewing exact sudoers grants; "
        "other writes require a separate reviewed rollout."
    )
