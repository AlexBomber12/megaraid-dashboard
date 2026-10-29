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
import shlex
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
    StorcliResponseFailed,
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
from megaraid_dashboard.storcli.policy import load_policy

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


def secure_components(path: Path, *, reject_service_writes: bool = False) -> None:
    """Require a root-controlled, symlink-free path, including every ancestor."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Absolute canonical path required: {path}")
    for component in (*reversed(path.parents), path):
        info = component.lstat()  # Deliberately does not follow symlinks.
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"Unsafe ownership, permissions or symlink: {component}")
        if reject_service_writes and os.access(component, os.W_OK, effective_ids=True):
            raise ValueError(f"Service-writable inventory path: {component}")


def secure_path(path: Path) -> None:
    """Validate the protected executable and its ancestors."""
    secure_components(path)
    if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.X_OK):
        raise ValueError(f"Not a regular executable: {path}")


def verify_binary(path: Path, expected: str) -> None:
    secure_path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")


def load_inventory(path: Path) -> dict[str, Any]:
    """Read only an independently root-controlled baseline as the service identity."""
    secure_components(path, reject_service_writes=True)
    if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.R_OK, effective_ids=True):
        raise ValueError(f"Inventory must be a readable regular file: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Inventory JSON must be an object")
    return value


def stage() -> None:
    """Create a separate copy exclusively; preserve both original files and metadata."""
    if os.geteuid() != 0:
        raise ValueError("Staging requires an operator root shell")
    verify_binary(SOURCE, LEGACY_HASH)
    verify_binary(ORIGINAL, ORIGINAL_HASH)
    # Check existing ancestors before creating missing directories. Never follow a link.
    for directory in (*reversed(STAGED.parent.parents), STAGED.parent):
        created = False
        if not directory.exists() and not directory.is_symlink():
            directory.mkdir(mode=0o755)
            created = True
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"Unsafe staging directory: {directory}")
        if created:
            # mkdir's mode is masked by the operator's umask. Only adjust our new directories.
            os.chown(directory, 0, 0, follow_symlinks=False)
            directory.chmod(0o755)
            info = directory.lstat()
            if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
                raise ValueError(f"New staging directory must be root:root 0755: {directory}")
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


def full_policy(inventory_path: Path) -> str:
    """Generate only the commands independently approved in the protected contract."""
    return load_policy(str(inventory_path)).sudoers(READS)


def authorization_checks(inventory_path: Path) -> str:
    """Emit authorization-only tests. sudo -l lists permission; it never runs the command."""
    approved = load_policy(str(inventory_path))
    lines = ["#!/bin/sh", "set -eu", f"# Run as {approved.service_user}; never as root."]
    for command in (*READS, *(item.command for item in approved.commands)):
        lines.append(f"sudo -n -l -- {shlex.quote(str(STAGED))} {command} >/dev/null")
    denied = (
        f"{ORIGINAL} /c0 set alarm=on J",
        f"{STAGED} /c1 set alarm=on J",
        f"{STAGED} /c0/e252/s8 start locate J",
        f"{STAGED} /c0/e252/s0 set bad J",
        f"{STAGED} /c0/e252/s0 set offline force J",
        f"{STAGED} /c0/v{(approved.virtual_drive + 1) % 64} start cc J",
        f"{STAGED} /c0/e252/s0 add hotsparedrive dgs={(approved.disk_group + 1) % 64} J",
        f"{STAGED} /c0/e252/s0 insert dg={approved.disk_group} array=64 row=0 J",
    )
    denied += tuple(
        f"{STAGED} /c0/e252/s{topology.slot} insert dg={topology.dg} "
        f"array={topology.array} row={(topology.row + 1) % 256} J"
        for topology in approved.replacements
    )
    for command in denied:
        lines.append(f"if sudo -n -l -- {command} >/dev/null 2>&1; then")
        lines.append(f"  echo 'Unexpected permission: {command}' >&2; exit 1")
        lines.append("fi")
    return "\n".join(lines) + "\n"


def locate_policy() -> str:
    """Render an additive policy for exactly eight slots on the pinned selection."""
    commands = ", \\\n  ".join(
        f"{STAGED} /c0/e252/s{slot} {action} locate J"
        for slot in range(8)
        for action in ("start", "stop")
    )
    return (
        f"Cmnd_Alias MEGARAID_LOCATE = {commands}\nmegaraid ALL=(root) NOPASSWD: MEGARAID_LOCATE\n"
    )


async def probe(binary: Path, inventory_path: Path) -> dict[str, Any]:
    """Execute only wrapper-allowlisted reads as the service account, checking every JSON status."""
    if pwd.getpwuid(os.geteuid()).pw_name != "megaraid":
        raise ValueError(
            "Run probe as megaraid so sudo authorization is tested for the service account"
        )
    expected = load_inventory(inventory_path)
    verify_binary(binary, LEGACY_HASH if binary == STAGED else ORIGINAL_HASH)
    responses: dict[str, Any] = {}
    for command in READS:
        process_failure = None
        try:
            payload = await run_storcli(command.split(), binary_path=str(binary), use_sudo=True)
        except StorcliResponseFailed as exc:
            process_failure = exc
            payload = exc.payload
        controllers = payload.get("Controllers")
        if not isinstance(controllers, list) or len(controllers) != 1:
            raise ValueError(f"Expected exactly one controller: {command}")
        if not isinstance(controllers[0], dict):
            raise ValueError(f"Malformed controller: {command}")
        status = controllers[0].get("Command Status", {})
        if not isinstance(status, dict) or type(status.get("Controller")) is not int:
            raise ValueError(f"Missing controller identity: {command}")
        if status["Controller"] != 0:
            raise ValueError(f"Wrong controller: {command}")
        # Inspect exact reasons directly: generic failure messages/logs must not
        # contain the private JSON. Multiple or contradictory details are fatal.
        if status.get("Status") == "Failure":
            details = status.get("Detailed Status")
            if details is None:
                reason = status.get("Description")
            elif (
                isinstance(details, list)
                and len(details) == 1
                and isinstance(details[0], dict)
                and details[0].get("Status", "Failure") in ("Failure", "Failed")
                and details[0].get("Controller", 0) == 0
                and details[0].get("Ctrl", 0) == 0
            ):
                reason = details[0].get("ErrMsg")
            else:
                reason = None
            if command == "/c0/fall show all J" and reason == "No foreign configuration":
                parse_foreign_config(payload)
                responses[command] = payload
                continue
            unsupported = reason in ("Un-supported command", "Unsupported command")
            bbu_redirect = command == "/c0/bbu show all J" and reason == "use /cx/cv"
            if command not in OPTIONAL or not (unsupported or bbu_redirect):
                raise process_failure or StorcliCommandFailed("Preflight read failed unexpectedly")
            responses[command] = {"unsupported": True, "payload": payload}
            continue
        if process_failure is not None:
            raise process_failure
        ensure_command_succeeded(payload)
        responses[command] = payload
    c, v, p = (responses[command] for command in READS[:3])
    command_status = c["Controllers"][0]["Command Status"]
    version = command_status.get("CLI Version")
    version_pattern = (
        r"(?:1\.23\.02|001\.2302(?:\.\d+)+)(?:\s|$)"
        if binary == STAGED
        else r"007\.37\d*(?:\.\d+)+(?:\s|$)"
    )
    if "CLI Version" in command_status and (
        not isinstance(version, str) or not re.match(version_pattern, version)
    ):
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
    responses["_binary_identity"] = {
        "sha256": LEGACY_HASH if binary == STAGED else ORIGINAL_HASH,
        "pinned_version": "1.23.02" if binary == STAGED else "7.37",
        "json_cli_version": version,
        "version_metadata": "reported" if "CLI Version" in command_status else "unavailable",
    }
    return responses


def main() -> None:
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "stage",
            "policy",
            "locate-policy",
            "full-policy",
            "authorization-checks",
            "probe",
        ],
    )
    parser.add_argument("--binary", type=Path, choices=[STAGED, ORIGINAL], default=STAGED)
    parser.add_argument(
        "--inventory",
        type=Path,
        help="Identity baseline for probe; reviewed command contract for full policy/checks",
    )
    args = parser.parse_args()
    if args.action == "stage":
        stage()
    elif args.action == "policy":
        print(policy(args.binary), end="")
    elif args.action in {"full-policy", "authorization-checks"}:
        if args.inventory is None or args.binary != STAGED:
            parser.error("Full policy/checks require --inventory and the pinned binary")
        render = full_policy if args.action == "full-policy" else authorization_checks
        print(render(args.inventory), end="")
    elif args.action == "locate-policy":
        if args.binary != STAGED or args.inventory is not None:
            parser.error("locate-policy accepts only the pinned binary and no inventory")
        print(locate_policy(), end="")
    else:
        if args.inventory is None:
            parser.error("probe requires --inventory")
        print(
            json.dumps(asyncio.run(probe(args.binary, args.inventory)), indent=2),
        )


if __name__ == "__main__":
    main()
