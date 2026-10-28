"""Fail-closed UI/API rollout policy; sudoers remains an independent boundary."""

from megaraid_dashboard.config import Settings
from megaraid_dashboard.storcli import policy


def action_disabled_reason(settings: Settings, family: str) -> str | None:
    """Return the same operator-facing refusal for templates and POST routes."""
    if settings.controller_read_only:
        return "Controller actions disabled: read-only rollout."
    if settings.controller_action_capability == "full-supported-actions":
        try:
            policy.verify_pinned_binary(settings.storcli_path)
            policy.load_policy(settings.controller_command_policy)
        except (OSError, ValueError) as exc:
            return (
                f"Actions unavailable: {exc}. Ask the administrator to verify the approved policy."
            )
        return None
    if settings.controller_action_capability == "locate" and family in {"locate", "maintenance"}:
        return None
    return (
        f"Controller action '{family}' unavailable: CONTROLLER_ACTION_CAPABILITY="
        f"{settings.controller_action_capability}. Only Locate Start/Stop is supported by this "
        "rollout. An operator may select locate after reviewing exact sudoers grants; "
        "other writes require the reviewed full-supported-actions policy."
    )
