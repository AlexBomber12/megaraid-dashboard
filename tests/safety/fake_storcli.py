"""Executable StorCLI substitute. Only reads/writes files beside this script.

The parent copies this file into a temporary directory and invokes it through the
real wrapper. Unknown reads fail closed. The trace records argv before any result.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


def main() -> None:
    root = Path(__file__).parent
    config = json.loads((root / "state.json").read_text())
    args = sys.argv[1:]
    with (root / "calls.jsonl").open("a") as trace:
        trace.write(json.dumps(args) + "\n")
    assert args[-1] == "J", "JSON output required"
    read = "show" in args
    fault = config.get("read_fault" if read else "fault", "success")
    if fault == "timeout":
        time.sleep(60)
    if fault == "nonzero":
        sys.exit(7)
    if not read:
        time.sleep(config.get("delay_seconds", 0))
    controller: dict[str, Any] = {"Command Status": {"Controller": 0, "Status": "Success"}}
    if fault == "failure":
        controller["Command Status"] = {"Status": "Failure", "Description": "TEST refusal"}
    elif fault == "missing":
        controller.pop("Command Status")
    elif fault == "unknown":
        controller["Command Status"] = {"Status": "Mystery"}
    data: dict[str, Any] = {}
    if read:
        if args[0] == "/c0/fall":
            data = config["foreign"]
        elif args[1:3] == ["show", "patrolread"]:
            data = {"PR Mode": config["mode"], "PR Current State": config["operation_state"]}
        elif args[1:3] == ["show", "cc"]:
            data = {"CC Mode": config["mode"], "CC Current State": config["operation_state"]}
        elif args[1:3] == ["show", "rebuild"]:
            data = {"Rebuild Status": "In progress", "Progress%": 10}
        elif args[0].startswith("/c0/e") and args[1:3] == ["show", "all"]:
            slot = args[0].split("/s")[-1]
            enc = args[0].split("/e")[-1].split("/")[0]
            data = {
                f"Drive {args[0]}": [{"EID:Slt": f"{enc}:{slot}", "State": config["drive_state"]}],
                f"Drive {args[0]} - Detailed Information": {
                    f"Drive {args[0]} Device attributes": {"SN": config["serial"]}
                },
            }
        else:
            raise AssertionError(f"Unconfigured read: {args}")
    elif fault == "success":
        if args[1:3] == ["set", "offline"]:
            config["drive_state"] = "Offln"
        if args[1:3] == ["set", "missing"]:
            config["drive_state"] = "Msng"
        if args[1] in {"start", "stop"} and args[2] in {"patrolread", "cc"}:
            config["operation_state"] = "Active" if args[1] == "start" else "Stopped"
        (root / "state.json").write_text(json.dumps(config))
    controller["Response Data"] = data
    sys.stdout.write(json.dumps({"Controllers": [controller]}))


if __name__ == "__main__":
    main()
