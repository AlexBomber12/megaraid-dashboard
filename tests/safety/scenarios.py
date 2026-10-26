"""Reviewed command expectations, independent of application command builders.

Each controller mutation has one matrix row; browser scenarios and integration
failure/gate scenarios consume the same rows. Adding a POST route fails inventory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Action:
    name: str
    path: str
    command: tuple[str, ...]
    state: str = "UGood"
    family: str = "advanced"


ACTIONS = (
    Action(
        "locate-start",
        "/drives/252:0/locate/start",
        ("/c0/e252/s0", "start", "locate", "J"),
        family="locate",
    ),
    Action(
        "locate-stop",
        "/drives/252:0/locate/stop",
        ("/c0/e252/s0", "stop", "locate", "J"),
        family="locate",
    ),
    *(
        Action(
            f"buzzer-{action}",
            f"/controller/buzzer/{action}",
            ("/c0", "set", f"alarm={value}", "J"),
            family="buzzer",
        )
        for action, value in (("silence", "silence"), ("disable", "off"), ("enable", "on"))
    ),
    *(
        Action(
            f"{family}-{action}",
            f"/controller/{family}/{action}",
            command,
            family=family,
        )
        for family, action, command in (
            ("patrol-read", "start", ("/c0", "start", "patrolread", "J")),
            ("patrol-read", "stop", ("/c0", "stop", "patrolread", "J")),
            ("patrol-read", "mode", ("/c0", "set", "patrolread=on", "mode=auto", "J")),
            ("consistency-check", "start", ("/c0/vall", "start", "cc", "J")),
            ("consistency-check", "stop", ("/c0/vall", "stop", "cc", "J")),
            ("consistency-check", "mode", ("/c0", "set", "consistencycheck=on", "mode=auto", "J")),
        )
    ),
    Action("mark-ubad", "/drives/252:0/actions/mark-ubad", ("/c0/e252/s0", "set", "bad", "J")),
    Action(
        "mark-ugood",
        "/drives/252:0/actions/mark-ugood",
        ("/c0/e252/s0", "set", "good", "J"),
        "UBad",
    ),
    Action("spindown", "/drives/252:0/actions/spindown", ("/c0/e252/s0", "spindown", "J")),
    Action(
        "hotspare",
        "/drives/252:0/actions/hotspare",
        ("/c0/e252/s0", "add", "hotsparedrive", "dgs=0", "J"),
    ),
    *(
        Action(
            f"replace-{step}",
            f"/drives/252:0/replace/{step}",
            ("/c0/e252/s0", "set", step, "J"),
            "Offln" if step == "missing" else "Onln",
            "replace",
        )
        for step in ("offline", "missing")
    ),
    Action(
        "replace-insert",
        "/drives/252:0/replace/insert",
        ("/c0/e252/s0", "insert", "dg=0", "array=0", "row=0", "J"),
        family="replace",
    ),
    *(
        Action(
            f"foreign-{action}",
            f"/controller/foreign-config/{action}",
            ("/c0/fall", verb, "J"),
            family="foreign",
        )
        for action, verb in (("import", "import"), ("clear", "delete"))
    ),
)
