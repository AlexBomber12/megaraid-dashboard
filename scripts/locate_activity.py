#!/usr/bin/env python3
"""Root operator only: audited Locate activity setup, never app startup or a web route."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import pwd
import re
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from time import monotonic
from typing import Any

import structlog
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import make_url

from megaraid_dashboard.db import AuditLog, get_engine, get_sessionmaker
from megaraid_dashboard.db.dao import get_maintenance_state, record_audit
from megaraid_dashboard.services.audit import record_operator_action
from megaraid_dashboard.storcli import (
    ensure_command_succeeded,
    parse_controller_show_all,
    run_storcli,
)
from megaraid_dashboard.storcli.policy import (
    PINNED_BINARY,
    PINNED_SHA256,
    CommandPolicy,
    load_policy,
    verify_pinned_binary,
)
from scripts.deploy_release import (
    Host as DeploymentHost,
)
from scripts.deploy_release import (
    Paths,
    atomic_write,
    deployment_lock,
    digest,
    read_file,
    root_path,
)

BASELINE = "27af3da95318ce3f902eaa9bf6a86dad89710da2"
ROOT = Path("/var/lib/megaraid-dashboard-led-maintenance")
POLICY = Path("/etc/megaraid-dashboard-rollout/actions.json")
HELP = Path("/etc/megaraid-dashboard-rollout/evidence-25c2b74/storcli-1.23.02-global-help.txt")
HELP_HASH = "c4b8eb58c45c0a6eed3baddd3ca6891af0c09541cf3ee135277bdd506cdeae08"
DATABASE = "sqlite:////var/lib/megaraid-dashboard/megaraid.db"
COMMANDS = {f"/c0 set activityforlocate={mode} J" for mode in ("on", "off")}


class RefusedError(RuntimeError):
    """A bounded operator-owned explanation, never raw configuration or controller output."""


def observation(payload: dict[str, Any], approval: CommandPolicy) -> tuple[str, str]:
    ensure_command_succeeded(payload)
    controller = parse_controller_show_all(payload)
    if (
        controller.serial_number != approval.controller_serial
        or controller.model_name != "LSI MegaRAID SAS 9270CV-8i"
    ):
        raise RefusedError("Controller identity changed")
    data = payload["Controllers"][0]["Response Data"]
    policies = data.get("Policies")
    value = policies.get("Use drive activity for locate") if isinstance(policies, dict) else None
    if not isinstance(value, str) or value.strip().casefold() not in {"on", "off"}:
        raise RefusedError("Locate activity setting is unknown")
    inventory: dict[str, Any] = {}
    for name, keys in (
        ("PD LIST", ("EID:Slt", "DID", "State", "DG")),
        ("VD LIST", ("DG/VD", "TYPE", "State", "Size")),
        ("TOPOLOGY", ("DG", "Arr", "Row", "EID:Slot", "DID", "Type", "State")),
    ):
        rows = data.get(name)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise RefusedError("Array inventory is incomplete")
        inventory[name] = [{key: row.get(key) for key in keys} for row in rows]
    if (
        data.get("Physical Drives") != 8
        or len(inventory["PD LIST"]) != 8
        or data.get("Virtual Drives") != 1
        or len(inventory["VD LIST"]) != 1
    ):
        raise RefusedError("Array inventory differs from the approved eight-drive profile")
    if {row["EID:Slt"] for row in inventory["PD LIST"]} != {
        f"252:{slot}" for slot in range(8)
    } or any(row["State"] != "Onln" for row in inventory["PD LIST"]):
        raise RefusedError("Physical drive membership or state changed")
    if (
        inventory["VD LIST"][0]["DG/VD"] != f"{approval.disk_group}/{approval.virtual_drive}"
        or inventory["VD LIST"][0]["State"] != "Optl"
    ):
        raise RefusedError("Virtual drive identity or state changed")
    settings = {}
    for section in ("Policies", "Defaults"):
        fields = data.get(section)
        if not isinstance(fields, dict):
            raise RefusedError("Controller settings are incomplete")
        settings[section] = {
            key: item for key, item in fields.items() if key != "Use drive activity for locate"
        }
    inventory["controller_settings"] = settings
    return value.strip().casefold(), digest(json.dumps(inventory, sort_keys=True).encode())


class Host:
    """All controller access uses the dedicated wrapper; tests replace this OS boundary."""

    def __init__(self) -> None:
        self.root = ROOT
        self.approval = self.root / "private/approval.json"
        self.receipt = self.root / "private/receipt.json"
        self.deployment = DeploymentHost(Paths())
        self.profile = self.deployment.properties()
        self.actor = os.environ.get("SUDO_USER", "root")
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", self.actor):
            raise RefusedError("Invalid operator identity")
        self.engine = get_engine(DATABASE)
        self.sessions = get_sessionmaker(self.engine)

    def protect(self, path: Path) -> None:
        root_path(path)

    def write(self, path: Path, payload: dict[str, Any], *, public: bool = False) -> None:
        self.protect(path.parent)
        atomic_write(
            path,
            json.dumps(payload, indent=2).encode(),
            mode=0o644 if public else 0o600,
            uid=os.geteuid(),
            gid=os.getegid(),
        )

    @contextlib.contextmanager
    def database_identity(self) -> Iterator[None]:
        uid, gid = os.geteuid(), os.getegid()
        try:
            if uid == 0:
                account = pwd.getpwnam("megaraid")
                os.setegid(account.pw_gid)
                os.seteuid(account.pw_uid)
            yield
        finally:
            if uid == 0:
                os.seteuid(uid)
                os.setegid(gid)

    def guard(self, *, maintenance: bool) -> None:
        expected = {
            "User": "megaraid",
            "Group": "megaraid",
            "ActiveState": "active",
            "SubState": "running",
            "WorkingDirectory": "/opt/megaraid-dashboard-ui-27af3da",
        }
        current = self.deployment.properties()
        if current != self.profile or any(
            current.get(key) != value for key, value in expected.items()
        ):
            raise RefusedError("The running application baseline changed")
        environment = self.deployment.environment(current["MainPID"])
        url = make_url(environment.get("DATABASE_URL", "sqlite://"))
        if (
            TypeAdapter(bool).validate_python(environment.get("CONTROLLER_READ_ONLY", "true"))
            or environment.get("CONTROLLER_ACTION_CAPABILITY") != "full-supported-actions"
            or environment.get("GIT_SHA") != BASELINE
            or url.get_backend_name() != "sqlite"
            or url.database != "/var/lib/megaraid-dashboard/megaraid.db"
        ):
            raise RefusedError("The reviewed runtime profile changed")
        verify_pinned_binary(PINNED_BINARY)
        with self.database_identity(), self.sessions() as session:
            if session.execute(text("SELECT version_num FROM alembic_version")).all() != [
                ("0009_event_deliveries",)
            ]:
                raise RefusedError("Database schema differs")
            window = get_maintenance_state(session, now=dt.datetime.now(dt.UTC))
            if maintenance and (not window.active or window.expires_at is None):
                raise RefusedError("Open an explicit maintenance window in the UI first")

    def prepare(self) -> None:
        self.guard(maintenance=False)
        self.protect(self.root.parent)
        for path, mode in ((self.root, 0o755), (self.root / "private", 0o700)):
            if path.exists() or path.is_symlink():
                self.protect(path)
                if (
                    not stat.S_ISDIR(path.lstat().st_mode)
                    or stat.S_IMODE(path.stat().st_mode) != mode
                ):
                    raise RefusedError("Existing maintenance directory permissions differ")
            else:
                path.mkdir(mode=mode)
                path.chmod(mode)
        self.protect(HELP)
        help_text = read_file(HELP, HELP_HASH).decode()
        if "storcli /cx set activityforlocate=<on|off>" not in help_text:
            raise RefusedError("Pinned help does not approve the exact property syntax")
        baseline = load_policy(str(POLICY))
        data = baseline.model_dump()
        data["commands"] = [
            {
                "command": command,
                "evidence": "Operator-approved Locate activity setup; pinned help SHA256 "
                + HELP_HASH,
            }
            for command in sorted(COMMANDS)
        ]
        approved = CommandPolicy.model_validate(data)
        if self.approval.exists() or self.approval.is_symlink():
            if load_policy(str(self.approval)) != approved:
                raise RefusedError("Existing private approval differs")
        else:
            self.write(self.approval, approved.model_dump())

    async def read(self) -> dict[str, Any]:
        return await run_storcli(
            ["/c0", "show", "all", "J"], use_sudo=False, binary_path=PINNED_BINARY
        )

    async def execute(self, mode: str, before_write: Callable[[], None]) -> dict[str, Any]:
        return await run_storcli(
            ["/c0", "set", "activityforlocate=" + mode, "J"],
            use_sudo=False,
            binary_path=PINNED_BINARY,
            authorization_path=str(self.approval),
            before_write=before_write,
        )

    def audit(
        self,
        argv: list[str],
        audit_id: int | None,
        outcome: str,
        *,
        success: bool = False,
        duration: float | None = None,
    ) -> int:
        with self.database_identity(), self.sessions() as session, session.begin():
            entry: AuditLog | None
            if audit_id is None:
                entry = record_audit(
                    session,
                    actor=self.actor,
                    action="locate_activity_configuration",
                    target="controller 0",
                    command_argv=argv,
                    exit_code=None,
                    stdout_tail=None,
                    stderr_tail=outcome,
                    duration_seconds=None,
                    success=False,
                )
            else:
                entry = session.get(AuditLog, audit_id)
                if entry is None or entry.actor != self.actor or entry.command_argv != argv:
                    raise RefusedError("Audit intent identity differs")
                entry.success = success
                entry.exit_code = 0 if success else None
                entry.stderr_tail = outcome
                entry.duration_seconds = duration
            record_operator_action(
                session, username=self.actor, message="Locate activity configuration: " + outcome
            )
            return entry.id

    def phase(self, receipt: dict[str, Any], state: str, **extra: Any) -> dict[str, Any]:
        receipt.update(state=state, updated_at=dt.datetime.now(dt.UTC).isoformat(), **extra)
        self.write(self.receipt, receipt)
        self.write(self.root / "private" / f"audit-{receipt['audit_id']}.json", receipt)
        public = {
            key: value
            for key, value in receipt.items()
            if key
            in {
                "state",
                "updated_at",
                "before",
                "target",
                "observed",
                "command_confirmed",
                "array_state_preserved",
                "other_settings_preserved",
                "write_dispatched",
                "audit_id",
            }
        }
        public["software_cannot_verify_physical_led"] = True
        self.write(self.root / "status.json", public, public=True)
        return public


async def operate(host: Host, action: str) -> dict[str, Any]:
    if action not in {"plan", "apply", "status", "restore"}:
        raise RefusedError("Unknown maintenance action")
    host.guard(maintenance=False)
    approval = load_policy(str(host.approval))
    if {
        item.command for item in approval.commands
    } != COMMANDS or approval.controller_serial != load_policy(str(POLICY)).controller_serial:
        raise RefusedError("Private approval differs from the approved single property")
    mode, topology = observation(await host.read(), approval)
    receipt = None
    if host.receipt.exists() or host.receipt.is_symlink():
        host.protect(host.receipt)
        receipt = json.loads(read_file(host.receipt))
        if (
            receipt.get("controller_sha256") != digest(approval.controller_serial.encode())
            or receipt.get("binary_sha256") != PINNED_SHA256
        ):
            raise RefusedError("Receipt identity differs")
    if action == "status":
        return {
            "state": "observed",
            "current_mode": mode,
            "receipt_state": None if receipt is None else receipt["state"],
            "controller_writes_executed": False,
        }
    if action == "plan":
        return {
            "state": "plan_verified",
            "current_mode": mode,
            "target": "on",
            "controller_writes_executed": False,
        }
    target = "on" if action == "apply" else "off"
    if mode == target:
        return {
            "state": "already_" + target,
            "current_mode": mode,
            "controller_writes_executed": False,
        }
    if (
        receipt is not None
        and receipt["state"] not in {"verified", "restored"}
        and not (
            action in {"apply", "restore"}
            and receipt["state"] == "not_sent"
            and receipt.get("write_dispatched") is False
        )
    ):
        raise RefusedError("Earlier attempt needs inspection; no automatic retry")
    original = receipt
    if action == "restore":
        if receipt is not None and receipt["state"] == "not_sent":
            original = receipt.get("restore_of")
        if (
            not isinstance(original, dict)
            or original.get("state") != "verified"
            or original.get("before") != "off"
            or original.get("target") != "on"
            or original.get("command_confirmed") is not True
            or original.get("controller_sha256") != digest(approval.controller_serial.encode())
            or original.get("binary_sha256") != PINNED_SHA256
        ):
            raise RefusedError("No matching verified change to restore")
    host.guard(maintenance=True)
    argv = ["/c0", "set", "activityforlocate=" + target, "J"]
    intent = {
        "before": mode,
        "target": target,
        "topology_sha256": topology,
        "controller_sha256": digest(approval.controller_serial.encode()),
        "actor": host.actor,
        "binary_sha256": PINNED_SHA256,
        "write_dispatched": False,
        "command_confirmed": False,
    }
    if action == "restore":
        intent["restore_of"] = original
    audit_id = host.audit(argv, None, "prepared; command not sent")
    intent["audit_id"] = audit_id
    host.phase(intent, "prepared")
    started = monotonic()
    confirmed = False

    def guard() -> None:
        host.guard(maintenance=True)
        host.phase(intent, "dispatching", write_dispatched=True)

    try:
        result = await host.execute(target, guard)
        ensure_command_succeeded(result)
        confirmed = True
        observed, after_topology = observation(await host.read(), approval)
        if observed != target or after_topology != topology:
            raise RefusedError("Readback or array preservation check failed")
    except Exception as error:
        state = "uncertain" if intent["write_dispatched"] else "not_sent"
        host.phase(intent, state, command_confirmed=confirmed, error_type=type(error).__name__)
        host.audit(
            argv, audit_id, state + ": " + type(error).__name__, duration=monotonic() - started
        )
        raise RefusedError("Inspect the durable receipt and audit; no automatic retry") from error
    host.phase(
        intent,
        "verified_pending_audit",
        command_confirmed=True,
        observed=observed,
        array_state_preserved=True,
        other_settings_preserved=True,
    )
    host.audit(
        argv,
        audit_id,
        "verified " + mode + " -> " + target,
        success=True,
        duration=monotonic() - started,
    )
    return host.phase(intent, "verified" if action == "apply" else "restored")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "plan", "apply", "status", "restore"))
    args = parser.parse_args(argv)
    host = None
    previous_mask = None
    try:
        if os.geteuid() != 0:
            raise RefusedError("An explicit operator sudo session is required")
        root_path(Path(__file__).absolute())
        previous_mask = os.umask(0o077)
        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
        host = Host()
        with deployment_lock(host.deployment):
            if args.action == "prepare":
                host.prepare()
                result = {"state": "approval_prepared", "controller_writes_executed": False}
            else:
                result = asyncio.run(operate(host, args.action))
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0
    except Exception as error:
        sys.stderr.write(
            json.dumps(
                {
                    "state": "refused_or_failed",
                    "error": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, RefusedError)
                    else "Inspect protected receipt and audit",
                }
            )
            + "\n"
        )
        return 1
    finally:
        try:
            if host is not None:
                host.engine.dispose()
        finally:
            if previous_mask is not None:
                os.umask(previous_mask)


if __name__ == "__main__":
    raise SystemExit(main())
