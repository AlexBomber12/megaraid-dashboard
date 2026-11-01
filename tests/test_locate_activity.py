"""One-property operator workflow with real temporary SQL audit and fake hardware."""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from megaraid_dashboard.db import AuditLog, Base, Event, get_engine, get_sessionmaker
from megaraid_dashboard.db.dao import set_maintenance_state
from megaraid_dashboard.storcli import StorcliCommandFailed, policy
from scripts import locate_activity as led
from scripts.deploy_release import DeployError, atomic_write, digest, root_path
from tests.safety.harness import FakeStorcli
from tests.test_storcli.test_policy import policy_data

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux operator tool")


class FakeDeployment:
    def __init__(self) -> None:
        self.profile = {
            "User": "megaraid",
            "Group": "megaraid",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "123",
            "WorkingDirectory": "/opt/megaraid-dashboard-ui-27af3da",
        }
        self.environment_values = {
            "CONTROLLER_READ_ONLY": "false",
            "CONTROLLER_ACTION_CAPABILITY": "full-supported-actions",
            "GIT_SHA": led.BASELINE,
            "DATABASE_URL": led.DATABASE,
        }

    def properties(self) -> dict[str, str]:
        return dict(self.profile)

    def environment(self, _pid: str) -> dict[str, str]:
        return dict(self.environment_values)


class FakeHost(led.Host):
    def __init__(self, root: Path) -> None:
        self.boundary = root
        self.root = root / "operation"
        self.approval = self.root / "private/approval.json"
        self.receipt = self.root / "private/receipt.json"
        self.deployment = FakeDeployment()  # type: ignore[assignment]
        self.profile = self.deployment.properties()
        self.actor = "test-operator"
        self.engine = get_engine("sqlite:///" + str(root / "audit.db"))
        Base.metadata.create_all(self.engine)
        self.sessions = get_sessionmaker(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('0009_event_deliveries')"))
        self.fake = FakeStorcli.create(root / "hardware")
        self.failure = ""
        self.window(True)

    def window(self, active: bool) -> None:
        with self.sessions() as session, session.begin():
            set_maintenance_state(
                session,
                active=active,
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                started_by="test-operator",
            )

    @contextlib.contextmanager
    def database_identity(self) -> Iterator[None]:
        yield

    def protect(self, path: Path) -> None:
        root_path(path, owner=os.getuid(), boundary=self.boundary)

    def write(self, path: Path, payload: dict[str, Any], *, public: bool = False) -> None:
        self.protect(path.parent)
        atomic_write(
            path,
            json.dumps(payload).encode(),
            mode=0o644 if public else 0o600,
            uid=os.getuid(),
            gid=os.getgid(),
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
        if (self.failure == "audit-before" and audit_id is None) or (
            self.failure == "audit-after" and audit_id is not None
        ):
            raise SQLAlchemyError("isolated audit failure")
        return super().audit(argv, audit_id, outcome, success=success, duration=duration)

    async def execute(self, mode: str, before_write: Callable[[], None]) -> dict[str, Any]:
        if self.failure == "expired":
            self.window(False)
        if self.failure == "preflight":
            self.configure_mode("Unknown")
        result = await super().execute(mode, before_write)
        if self.failure == "readback":
            self.fake.configure(read_fault="nonzero")
        if self.failure in {"topology", "settings"}:
            state = json.loads((self.fake.path.parent / "state.json").read_text())
            data = state["controller"]
            if self.failure == "topology":
                data["PD LIST"][0]["DG"] = 3
            else:
                data["Defaults"]["LED Show Drive Activity"] = "Other"
            self.fake.configure(controller=data)
        return result

    def configure_mode(self, mode: str) -> None:
        state = json.loads((self.fake.path.parent / "state.json").read_text())
        state["controller"]["Policies"]["Use drive activity for locate"] = mode
        self.fake.configure(controller=state["controller"])


@pytest.fixture
def operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHost]:
    host = FakeHost(tmp_path)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(policy_data()))
    help_file = tmp_path / "pinned-help.txt"
    help_file.write_text("storcli /cx set activityforlocate=<on|off>\n")
    monkeypatch.setattr(led, "POLICY", baseline)
    monkeypatch.setattr(led, "HELP", help_file)
    monkeypatch.setattr(led, "HELP_HASH", digest(help_file.read_bytes()))
    monkeypatch.setattr(led, "PINNED_BINARY", str(host.fake.path))
    monkeypatch.setattr(led, "verify_pinned_binary", lambda _: None)
    monkeypatch.setattr(policy, "verify_pinned_binary", lambda _: None)
    monkeypatch.setattr(policy, "protected_path", host.protect)
    host.configure_mode("Off")
    host.prepare()
    yield host
    host.engine.dispose()


async def test_enable_repeat_and_restore_have_exact_audited_writes(operation: FakeHost) -> None:
    host = operation
    baseline = led.POLICY.read_bytes()
    assert (await led.operate(host, "plan"))["current_mode"] == "off"
    assert host.fake.mutations == []
    assert (await led.operate(host, "apply"))["state"] == "verified"
    assert (await led.operate(host, "apply"))["state"] == "already_on"
    assert (await led.operate(host, "status"))["current_mode"] == "on"
    assert (await led.operate(host, "restore"))["state"] == "restored"
    assert (await led.operate(host, "restore"))["state"] == "already_off"
    assert host.fake.mutations == [
        ["/c0", "set", "activityforlocate=on", "J"],
        ["/c0", "set", "activityforlocate=off", "J"],
    ]
    assert led.POLICY.read_bytes() == baseline
    with host.sessions() as session:
        audits = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
        events = list(session.scalars(select(Event)))
    assert len(audits) == 2 and all(row.success and row.exit_code == 0 for row in audits)
    assert [row.command_argv for row in audits] == host.fake.mutations
    assert len(events) == 4 and all(
        row.category == "operator_action" and row.severity == "info" for row in events
    )
    assert (host.approval.stat().st_mode & 0o777) == 0o600
    assert (host.root / "private").stat().st_mode & 0o777 == 0o700
    assert (
        json.loads((host.root / "status.json").read_text())["software_cannot_verify_physical_led"]
        is True
    )
    assert len(list(host.receipt.parent.glob("audit-*.json"))) == 2


@pytest.mark.parametrize(
    "failure",
    [
        "audit-before",
        "audit-after",
        "expired",
        "preflight",
        "readback",
        "topology",
        "settings",
        "failure",
        "malformed",
        "timeout",
    ],
)
async def test_failure_never_claims_success_or_retries(
    operation: FakeHost, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = operation
    host.failure = failure
    if failure in {"failure", "malformed", "timeout"}:
        host.fake.configure(fault=failure)
    if failure == "timeout":
        original = led.run_storcli

        async def bounded(args: list[str], **kwargs: Any) -> dict[str, Any]:
            if "show" not in args:
                kwargs["timeout_seconds"] = 0.5
            return await original(args, **kwargs)

        monkeypatch.setattr(led, "run_storcli", bounded)
    with pytest.raises((led.RefusedError, SQLAlchemyError)):
        await led.operate(host, "apply")
    mutations = host.fake.mutations
    assert mutations == (
        []
        if failure in {"audit-before", "expired", "preflight"}
        else [["/c0", "set", "activityforlocate=on", "J"]]
    )
    if host.receipt.exists():
        receipt = json.loads(host.receipt.read_text())
        assert receipt["state"] in {"not_sent", "uncertain", "verified_pending_audit"}
        assert receipt["command_confirmed"] is (
            failure in {"readback", "topology", "settings", "audit-after"}
        )
        host.failure = ""
        host.fake.configure(read_fault="success", fault="success")
        if failure == "preflight":
            host.configure_mode("Off")
        # Inspection can observe a change, but does not rewrite an uncertain outcome.
        result = await led.operate(host, "status")
        assert result["receipt_state"] == receipt["state"]
        if receipt["state"] in {"uncertain", "verified_pending_audit"}:
            host.configure_mode("Off")
            with pytest.raises(led.RefusedError, match="Earlier attempt"):
                await led.operate(host, "apply")
    assert host.fake.mutations == mutations


@pytest.mark.parametrize(
    "problem",
    ["maintenance", "read-only", "capability", "service", "schema", "binary", "policy", "receipt"],
)
async def test_profile_drift_refuses_before_any_write(
    operation: FakeHost, problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = operation
    if problem == "maintenance":
        host.window(False)
    elif problem == "read-only":
        host.deployment.environment_values["CONTROLLER_READ_ONLY"] = "true"
    elif problem == "capability":
        host.deployment.environment_values["CONTROLLER_ACTION_CAPABILITY"] = "none"
    elif problem == "service":
        host.deployment.profile["MainPID"] = "456"
    elif problem == "schema":
        with host.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='other'"))
    elif problem == "binary":

        def bad(_: str) -> None:
            raise ValueError("unapproved binary")

        monkeypatch.setattr(led, "verify_pinned_binary", bad)
    elif problem == "policy":
        data = json.loads(host.approval.read_text())
        data["commands"].pop()
        host.approval.write_text(json.dumps(data))
    else:
        host.write(host.receipt, {"state": "verified", "controller_sha256": "wrong"})
    with pytest.raises((led.RefusedError, ValueError)):
        await led.operate(host, "apply")
    assert host.fake.mutations == []


async def test_no_restore_without_a_managed_change(operation: FakeHost) -> None:
    operation.configure_mode("On")
    assert (await led.operate(operation, "apply"))["state"] == "already_on"
    with pytest.raises(led.RefusedError, match="No matching"):
        await led.operate(operation, "restore")
    assert operation.fake.mutations == []


async def test_application_policy_does_not_gain_the_new_property(operation: FakeHost) -> None:
    from megaraid_dashboard.storcli import StorcliNotAvailable

    with pytest.raises(StorcliNotAvailable, match="lacks reviewed"):
        await led.run_storcli(
            ["/c0", "set", "activityforlocate=on"],
            use_sudo=False,
            binary_path=str(operation.fake.path),
            authorization_path=str(led.POLICY),
        )
    assert operation.fake.calls == []


@pytest.mark.parametrize(
    "problem",
    ["identity", "model", "mode", "rows", "count", "slot", "pd_state", "vd", "settings"],
)
async def test_live_inventory_must_be_complete_and_optimal_before_write(
    operation: FakeHost, problem: str
) -> None:
    state = json.loads((operation.fake.path.parent / "state.json").read_text())
    data = state["controller"]
    if problem == "identity":
        data["Basics"]["Serial Number"] = "OTHER-CONTROLLER"
    elif problem == "model":
        data["Basics"]["Model"] = "Other Controller"
    elif problem == "mode":
        data["Policies"]["Use drive activity for locate"] = None
    elif problem == "rows":
        data["TOPOLOGY"] = [None]
    elif problem == "count":
        data["Physical Drives"] = 7
    elif problem == "slot":
        data["PD LIST"][0]["EID:Slt"] = "253:0"
    elif problem == "pd_state":
        data["PD LIST"][0]["State"] = "UBad"
    elif problem == "vd":
        data["VD LIST"][0]["State"] = "Dgrd"
    else:
        data["Defaults"] = None
    operation.fake.configure(controller=data)
    with pytest.raises(led.RefusedError):
        await led.operate(operation, "apply")
    assert operation.fake.mutations == []
    with operation.sessions() as session:
        assert list(session.scalars(select(AuditLog))) == []


@pytest.mark.parametrize(
    "mode,value,allowed",
    [
        ("on", "Off", True),
        ("off", "On", True),
        ("on", "On", False),
        ("off", "Off", False),
        ("on", "Unknown", False),
        ("on", None, False),
        ("on", True, False),
        ("off", {}, False),
    ],
)
async def test_real_wrapper_uses_explicit_activity_approval(
    operation: FakeHost, mode: str, value: Any, allowed: bool
) -> None:
    host = operation
    host.configure_mode(value)
    if allowed:
        result = await host.execute(mode, lambda: None)
        assert result["Controllers"][0]["Command Status"]["Status"] == "Success"
    else:
        with pytest.raises(StorcliCommandFailed, match="Locate activity"):
            await host.execute(mode, lambda: None)
    assert host.fake.mutations == (
        [["/c0", "set", "activityforlocate=" + mode, "J"]] if allowed else []
    )


@pytest.mark.parametrize("shape", [None, [], "unknown"])
async def test_missing_policies_are_refused(operation: FakeHost, shape: Any) -> None:
    state = json.loads((operation.fake.path.parent / "state.json").read_text())
    state["controller"]["Policies"] = shape
    operation.fake.configure(controller=state["controller"])
    with pytest.raises(StorcliCommandFailed, match="Locate activity"):
        await operation.execute("on", lambda: None)
    assert operation.fake.mutations == []


async def test_proven_no_write_attempt_can_be_explicitly_retried(operation: FakeHost) -> None:
    operation.failure = "expired"
    with pytest.raises(led.RefusedError):
        await led.operate(operation, "apply")
    operation.failure = ""
    operation.window(True)
    assert (await led.operate(operation, "apply"))["state"] == "verified"
    assert len(operation.fake.mutations) == 1
    with operation.sessions() as session:
        audits = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert len(audits) == 2 and not audits[0].success and audits[1].success


async def test_restore_can_retry_only_a_proven_unsent_attempt(operation: FakeHost) -> None:
    assert (await led.operate(operation, "apply"))["state"] == "verified"
    original = json.loads(operation.receipt.read_text())
    operation.failure = "expired"
    with pytest.raises(led.RefusedError):
        await led.operate(operation, "restore")
    refused = json.loads(operation.receipt.read_text())
    assert refused["state"] == "not_sent" and refused["write_dispatched"] is False
    assert refused["restore_of"] == original
    assert len(operation.fake.mutations) == 1
    operation.failure = ""
    operation.window(True)
    assert (await led.operate(operation, "restore"))["state"] == "restored"
    assert len(operation.fake.mutations) == 2
    assert json.loads(operation.receipt.read_text())["restore_of"] == original
    with operation.sessions() as session:
        audits = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert [row.success for row in audits] == [True, False, True]


async def test_restore_cannot_retry_uncertain_dispatch(operation: FakeHost) -> None:
    await led.operate(operation, "apply")
    operation.fake.configure(fault="failure")
    with pytest.raises(led.RefusedError):
        await led.operate(operation, "restore")
    receipt = json.loads(operation.receipt.read_text())
    assert receipt["state"] == "uncertain" and receipt["write_dispatched"] is True
    operation.fake.configure(fault="success")
    with pytest.raises(led.RefusedError, match="Earlier attempt"):
        await led.operate(operation, "restore")
    assert len(operation.fake.mutations) == 2


@pytest.mark.parametrize(
    "problem", ["help_hash", "help_syntax", "approval", "directory", "symlink"]
)
def test_preparation_refuses_unproven_or_unsafe_evidence(
    operation: FakeHost, problem: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if problem == "help_hash":
        led.HELP.write_text("tampered")
    elif problem == "help_syntax":
        led.HELP.write_text("no supported property")
        monkeypatch.setattr(led, "HELP_HASH", digest(led.HELP.read_bytes()))
    elif problem == "approval":
        data = json.loads(operation.approval.read_text())
        data["commands"].pop()
        operation.approval.write_text(json.dumps(data))
    elif problem == "directory":
        operation.approval.parent.chmod(0o755)
    else:
        operation.approval.unlink()
        operation.approval.symlink_to(led.POLICY)
    with pytest.raises((led.RefusedError, DeployError, ValueError)):
        operation.prepare()
    assert operation.fake.calls == []


def test_root_audit_identity_is_restored_even_after_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    identities = {"uid": 0, "gid": 0}
    monkeypatch.setattr(led.os, "geteuid", lambda: identities["uid"])
    monkeypatch.setattr(led.os, "getegid", lambda: identities["gid"])
    monkeypatch.setattr(led.os, "seteuid", lambda value: identities.update(uid=value))
    monkeypatch.setattr(led.os, "setegid", lambda value: identities.update(gid=value))
    monkeypatch.setattr(led.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1234, pw_gid=4321))
    host = object.__new__(led.Host)
    with pytest.raises(ValueError), host.database_identity():
        assert identities == {"uid": 1234, "gid": 4321}
        raise ValueError("simulated transaction failure")
    assert identities == {"uid": 0, "gid": 0}


def test_cli_is_explicit_root_only_and_never_leaks_errors(
    operation: FakeHost, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(led.os, "geteuid", lambda: 1234)
    assert led.main(["apply"]) == 1
    assert "sudo" in capsys.readouterr().err
    monkeypatch.setattr(led.os, "geteuid", lambda: 0)
    monkeypatch.setattr(led, "root_path", lambda _: None)
    monkeypatch.setattr(led, "Host", lambda: operation)

    @contextlib.contextmanager
    def lock(_host: Any) -> Iterator[None]:
        yield

    monkeypatch.setattr(led, "deployment_lock", lock)
    assert led.main(["prepare"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "approval_prepared"
    assert operation.fake.mutations == []
    assert led.main(["plan"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "plan_verified"
