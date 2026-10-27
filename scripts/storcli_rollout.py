#!/usr/bin/env python3
"""Operator-only staging/policy/preflight; never invoked by install or service startup."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path
from typing import Any

import structlog

from megaraid_dashboard.services.drive_actions import (
    parse_consistency_check_status,
    parse_patrol_read_status,
    parse_rebuild_status,
)
from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    ensure_command_succeeded,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
    parse_foreign_config,
    parse_physical_drives,
    parse_virtual_drives,
    run_storcli,
)
from megaraid_dashboard.storcli.parser import validate_complete_inventory

SOURCE = Path("/usr/local/MegaRAID Storage Manager/StorCLI/storcli64")
ORIGINAL = Path("/usr/local/sbin/storcli64")
STAGED = Path("/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64")
LEGACY_HASH = "642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547"
ORIGINAL_HASH = "dd2a9409ed37362bcfe0d9940f25537c61f564c95062cf280ef419b9bd5bb119"
READS = (
    "/c0 show all J",
    "/c0/vall show all J",
    "/c0/eall/sall show all J",
    "/c0/cv show all J",
    "/c0/bbu show all J",
    "/c0/fall show all J",
    "/c0 show patrolread J",
    "/c0 show cc J",
    "/c0/vall show cc J",
    *(f"/c0/e252/s{slot} show {probe} J" for slot in range(8) for probe in ("all", "rebuild")),
)
OPTIONAL = {"/c0/bbu show all J", "/c0/fall show all J"}


def secure_path(path: Path) -> None:
    """Require every component, including /, to be root-owned and non-writable by others."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Absolute canonical path required: {path}")
    for component in (*reversed(path.parents), path):
        info = component.lstat()  # Deliberately does not follow symlinks.
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"Unsafe ownership, permissions or symlink: {component}")
    if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.X_OK):
        raise ValueError(f"Not a regular executable: {path}")


def verify_binary(path: Path, expected: str) -> None:
    secure_path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")


def stage() -> None:
    """Create a separate copy exclusively; preserve both original files and metadata."""
    if os.geteuid() != 0:
        raise ValueError("Staging requires an operator root shell")
    verify_binary(SOURCE, LEGACY_HASH)
    verify_binary(ORIGINAL, ORIGINAL_HASH)
    # Check existing ancestors before creating missing directories. Never follow a link.
    for directory in (*reversed(STAGED.parent.parents), STAGED.parent):
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o755)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"Unsafe staging directory: {directory}")
    fd = os.open(STAGED, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
    with os.fdopen(fd, "wb") as output:
        output.write(SOURCE.read_bytes())
        os.fchown(output.fileno(), 0, 0)
        os.fchmod(output.fileno(), 0o755)
        output.flush()
        os.fsync(output.fileno())
    verify_binary(STAGED, LEGACY_HASH)
    verify_binary(SOURCE, LEGACY_HASH)
    verify_binary(ORIGINAL, ORIGINAL_HASH)


def policy(binary: Path) -> str:
    """Render exact commands, never wildcard arguments or write permissions."""
    if binary not in {STAGED, ORIGINAL}:
        raise ValueError("Only the two recorded binary selections are supported")
    commands = ", \\\n  ".join(f"{binary} {command}" for command in READS)
    return (
        f"Cmnd_Alias MEGARAID_RO_READS = {commands}\n"
        "megaraid ALL=(root) NOPASSWD: MEGARAID_RO_READS\n"
    )


async def probe(binary: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Execute only wrapper-allowlisted reads as the service account, checking every JSON status."""
    if pwd.getpwuid(os.geteuid()).pw_name != "megaraid":
        raise ValueError(
            "Run probe as megaraid so sudo authorization is tested for the service account"
        )
    verify_binary(binary, LEGACY_HASH if binary == STAGED else ORIGINAL_HASH)
    responses: dict[str, Any] = {}
    for command in READS:
        payload = await run_storcli(command.split(), binary_path=str(binary), use_sudo=True)
        controllers = payload.get("Controllers")
        if not isinstance(controllers, list) or len(controllers) != 1:
            raise ValueError(f"Expected exactly one controller: {command}")
        status = controllers[0].get("Command Status", {})
        if status.get("Controller", 0) != 0:
            raise ValueError(f"Wrong controller: {command}")
        try:
            ensure_command_succeeded(payload)
        except StorcliCommandFailed as exc:
            reason = exc.err_msg.strip().casefold()
            if command == "/c0/fall show all J" and reason == "no foreign configuration":
                parse_foreign_config(payload)
                responses[command] = payload
                continue
            unsupported = reason in {"un-supported command", "unsupported command"}
            bbu_redirect = command == "/c0/bbu show all J" and reason == "use /cx/cv"
            if command not in OPTIONAL or not (unsupported or bbu_redirect):
                raise
            responses[command] = {"unsupported": True, "payload": payload}
            continue
        responses[command] = payload
    c, v, p = (responses[command] for command in READS[:3])
    version = c["Controllers"][0]["Command Status"].get("CLI Version", "")
    version_pattern = (
        r"(?:1\.23\.02|001\.2302(?:\.\d+)+)(?:\s|$)"
        if binary == STAGED
        else r"007\.37\d*(?:\.\d+)+(?:\s|$)"
    )
    if not re.match(version_pattern, version):
        raise ValueError(f"Unexpected CLI version: {version!r}")
    controller = parse_controller_show_all(c)
    drives = parse_physical_drives(p)
    validate_complete_inventory(c, v, p, parse_virtual_drives(v), drives)
    actual = {f"{drive.enclosure_id}:{drive.slot_id}": drive.serial_number for drive in drives}
    if (
        controller.serial_number != expected["controller_serial"]
        or controller.model_name != "LSI MegaRAID SAS 9270CV-8i"
        or actual != expected["drive_serials"]
        or set(actual) != {f"252:{slot}" for slot in range(8)}
        or len(drives) != 8
        or len(set(actual.values())) != 8
    ):
        raise ValueError("Controller/drive inventory differs from approved baseline")
    cv = parse_cachevault(responses["/c0/cv show all J"])
    if cv is None or not controller.cv_present:
        raise ValueError("Required CacheVault missing")
    for command, parser in (
        ("/c0/bbu show all J", parse_bbu),
        ("/c0/fall show all J", parse_foreign_config),
    ):
        if not responses[command].get("unsupported"):
            parser(responses[command])
    patrol = parse_patrol_read_status(responses["/c0 show patrolread J"])
    cc = parse_consistency_check_status(responses["/c0 show cc J"], responses["/c0/vall show cc J"])
    if patrol.state == "unknown" or cc.state.lower() == "unknown":
        raise ValueError("Required patrol/CC state is unknown")
    for slot in range(8):
        command = f"/c0/e252/s{slot} show all J"
        detail = parse_physical_drives(responses[command])
        if (
            len(detail) != 1
            or detail[0].enclosure_id != 252
            or detail[0].slot_id != slot
            or detail[0].serial_number != actual[f"252:{slot}"]
        ):
            raise ValueError(f"Per-drive identity mismatch: {command}")
        rebuild = parse_rebuild_status(
            responses[f"/c0/e252/s{slot} show rebuild J"], requested_drive=f"/c0/e252/s{slot}"
        )
        if rebuild.state == "Unknown":
            raise ValueError(f"Required rebuild state is unknown: slot {slot}")
    verify_binary(binary, LEGACY_HASH if binary == STAGED else ORIGINAL_HASH)
    return responses


def main() -> None:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "policy", "probe"])
    parser.add_argument("--binary", type=Path, choices=[STAGED, ORIGINAL], default=STAGED)
    parser.add_argument("--inventory", type=Path, help="Approved private baseline JSON")
    args = parser.parse_args()
    if args.action == "stage":
        stage()
    elif args.action == "policy":
        print(policy(args.binary), end="")
    else:
        if args.inventory is None:
            parser.error("probe requires --inventory")
        print(
            json.dumps(
                asyncio.run(probe(args.binary, json.loads(args.inventory.read_text()))), indent=2
            ),
        )


if __name__ == "__main__":
    main()
