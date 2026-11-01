"""Real temporary files/SQLite with fake OS boundaries; never a real controller or service."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tarfile
import zipfile
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from scripts import deploy_release as deploy

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux-only deployment profile")

OLD = "a" * 40
NEW = "b" * 40
RAW = {
    "patrol_read": {
        "mode": "auto",
        "state": "active",
        "interval_hours": 168,
        "next_start_timestamp": "10/31/2026, 09:00:00",
    },
    "foreign_config_state": {"present": False},
}


def source_archive(content: dict[str, bytes] | None = None) -> bytes:
    members = content or {
        "src/megaraid_dashboard/__init__.py": b"# application\n",
        "alembic.ini": b"# unchanged\n",
        "migrations/env.py": b"# unchanged\n",
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def wheel_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as wheel:
        wheel.writestr("megaraid_dashboard/__init__.py", b"# application\n")
        wheel.writestr("megaraid_dashboard/alembic.ini", b"# unchanged\n")
        wheel.writestr("megaraid_dashboard/migrations/env.py", b"# unchanged\n")
        wheel.writestr("megaraid_dashboard-0.1.0.dist-info/METADATA", b"metadata")
        wheel.writestr("megaraid_dashboard-0.1.0.dist-info/licenses/LICENSE", b"MIT")
    return output.getvalue()


def inputs(tmp_path: Path) -> tuple[Path, str]:
    directory = tmp_path / "bundle"
    (directory / "wheels").mkdir(parents=True)
    data = {
        "source.tar": source_archive(),
        "runtime-lock.txt": b"dependency==1.2.3\n",
        "wheels/megaraid_dashboard-0.1.0-py3-none-any.whl": wheel_archive(),
    }
    for name, value in data.items():
        (directory / name).write_bytes(value)
    manifest = {
        "version": 1,
        "commit": NEW,
        "baseline": OLD,
        "source_sha256": deploy.digest(data["source.tar"]),
        "lock_sha256": deploy.digest(data["runtime-lock.txt"]),
        "wheels": {
            Path(name).name: deploy.digest(value)
            for name, value in data.items()
            if name.startswith("wheels/")
        },
    }
    content = json.dumps(manifest).encode()
    (directory / "release.json").write_bytes(content)
    return directory, deploy.digest(content)


class FakeHost(deploy.Host):
    def __init__(self, paths: deploy.Paths, boundary: Path) -> None:
        super().__init__(paths)
        self.boundary = boundary
        self.current = OLD
        self.pid = "123"
        self.elapsed = 0.0
        self.commands: list[list[str]] = []
        self.failure = ""
        self.collected = False
        self.job_running = False

    def protect(self, path: Path) -> None:
        deploy.root_path(path, owner=os.getuid(), boundary=self.boundary)

    def write(self, path: Path, data: bytes, mode: int = 0o644, gid: int = 0) -> None:
        self.protect(path.parent)
        if self.failure == "override_write" and path.name == "zzzz-release.conf":
            raise OSError("simulated failed atomic write")
        deploy.atomic_write(path, data, mode=mode, uid=os.getuid(), gid=os.getgid())

    def properties(self) -> dict[str, str]:
        return {
            "WorkingDirectory": str(self.paths.release(self.current)),
            "User": "megaraid",
            "Group": "megaraid",
            "MainPID": self.pid,
            "ActiveState": "active",
            "SubState": "running",
            "NoNewPrivileges": "no",
            "DropInPaths": " ".join(
                str(path) for path in sorted(self.paths.dropins.glob("*.conf"))
            ),
        }

    def environment(self, pid: str) -> dict[str, str]:
        assert pid == self.pid
        return {
            "GIT_SHA": self.current,
            "CONTROLLER_READ_ONLY": "false",
            "CONTROLLER_ACTION_CAPABILITY": "full-supported-actions",
            "MAINTENANCE_MODE": "false",
            "DESTRUCTIVE_MODE": "false",
            "METRICS_INTERVAL_SECONDS": "300",
            "PRIVATE_TEST_TOKEN": "never-print-this",
        }

    def account(self) -> tuple[int, int]:
        return os.getuid(), os.getgid()

    def run(self, args: Any, **kwargs: Any) -> bytes:
        del kwargs
        self.commands.append(list(args))
        if args[0] == "/usr/bin/systemd-run":
            self.job_running = True
            return b"Running as unit"
        if "ActiveState" in " ".join(args):
            return b"active\n" if self.job_running else b"inactive\n"
        if args[1:3] == ["restart", deploy.SERVICE]:
            override = self.paths.dropins / "zzzz-release.conf"
            self.current = (
                NEW
                if override.exists() and str(self.paths.release(NEW)) in override.read_text()
                else OLD
            )
            self.pid = "456" if self.current == NEW else "789"
            self.collected = False
        return b""

    def health(self) -> bool:
        return not (self.failure == "health" and self.current == NEW)

    def journal(self, pid: str) -> bytes:
        assert pid == self.pid
        if self.failure == "journal":
            return b""
        if self.failure == "learn_warning":
            return b"collector_run_completed cachevault_next_learn_time_invalid"
        return b"collector_run_completed" if self.collected else b""

    def now(self) -> float:
        return self.elapsed

    def sleep(self, seconds: int) -> None:
        self.elapsed += seconds
        if self.current == NEW and self.failure != "collector" and not self.collected:
            with sqlite3.connect(self.paths.database) as connection:
                connection.execute(
                    "INSERT INTO controller_snapshots (id,raw_json) VALUES (2, ?)",
                    (json.dumps(RAW),),
                )
                connection.execute(
                    "INSERT INTO cv_snapshots VALUES (2, '2026-11-07T17:50:43+00:00')"
                )
                connection.execute(
                    "INSERT INTO audit VALUES ('new history; retain after rollback')"
                )
            self.collected = True
            if self.failure == "policy_drift":
                self.paths.sudoers.write_bytes(b"changed by a separate actor")
            if self.failure == "override_drift":
                (self.paths.dropins / "zzzz-release.conf").write_bytes(b"separate actor")

    def settings_digest(self, release: Path, env: Any) -> str:
        del env
        return (
            "2" * 64
            if self.failure == "settings" and release == self.paths.release(NEW)
            else "1" * 64
        )

    def install(self, bundle: deploy.Bundle, target: Path) -> None:
        if self.failure == "install":
            raise deploy.DeployError("simulated offline pip failure")
        target.mkdir(mode=0o755)
        (target / "migrations").mkdir()
        (target / "migrations/env.py").write_bytes(
            b"changed" if self.failure == "migration" else b"# unchanged\n"
        )
        (target / "alembic.ini").write_bytes(b"# unchanged\n")
        (target / "RELEASE.json").write_text(
            json.dumps({"commit": bundle.commit, "source_sha256": bundle.source_hash})
        )

    def https_check(self) -> None:
        if self.failure == "tls":
            raise deploy.DeployError("simulated TLS failure")


@pytest.fixture
def rollout(tmp_path: Path) -> tuple[deploy.Deployment, FakeHost, Path, str]:
    root = tmp_path / "host"
    root.mkdir(mode=0o755)
    path_values = {}
    for field in fields(deploy.Paths):
        original = getattr(deploy.Paths(), field.name)
        path_values[field.name] = root / original.relative_to("/")
    paths = deploy.Paths(**path_values)
    directories = {
        paths.releases,
        paths.config,
        paths.legacy,
        paths.dropins,
        paths.database.parent,
        paths.status.parent,
        paths.backups,
        paths.lock.parent,
    }
    for path in directories:
        path.mkdir(parents=True, exist_ok=True)
    paths.legacy.chmod(0o750)
    (paths.legacy / "env").write_bytes(b"TOKEN=private-test-only\n")
    (paths.legacy / "env").chmod(0o600)
    for path in (
        paths.unit,
        paths.sudoers,
        paths.binary,
        paths.config / "policy.json",
        paths.dropins / "95-full-actions.conf",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"protected baseline")
    old = paths.release(OLD)
    (old / "migrations").mkdir(parents=True)
    (old / "migrations/env.py").write_bytes(b"# unchanged\n")
    (old / "alembic.ini").write_bytes(b"# unchanged\n")
    (old / "RELEASE.json").write_text(json.dumps({"commit": OLD}))
    with sqlite3.connect(paths.database) as connection:
        connection.executescript("""
        CREATE TABLE alembic_version (version_num TEXT);
        INSERT INTO alembic_version VALUES ('0009_event_deliveries');
        CREATE TABLE system_state (key TEXT, value TEXT);
        CREATE TABLE controller_snapshots (id INTEGER, raw_json TEXT,
                                           captured_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE cv_snapshots (snapshot_id INTEGER, next_learn_cycle TEXT);
        CREATE TABLE audit (value TEXT);
        INSERT INTO audit VALUES ('original history');
        """)
        connection.execute(
            "INSERT INTO controller_snapshots (id,raw_json) VALUES (1, ?)", (json.dumps(RAW),)
        )
    directory, manifest_hash = inputs(tmp_path)
    bundle = deploy.Bundle.load(directory, manifest_hash)
    host = FakeHost(paths, root)
    return deploy.Deployment(host, bundle), host, directory, manifest_hash


def test_release_survives_parent_exit_and_repeat_is_a_noop(rollout: Any) -> None:
    deployment, host, directory, manifest_hash = rollout
    tool = host.paths.releases / "deploy_release.py"
    tool.write_bytes(b"root-owned in production")
    queued = deploy.start(host, deployment.bundle, directory, manifest_hash, tool)
    assert queued["state"] == "queued"
    command = host.commands[-1]
    assert command[:4] == [
        "/usr/bin/systemd-run",
        "--unit=" + deploy.unit_name(NEW)[:-8],
        "--collect",
        "--property=Type=exec",
    ]
    assert ["/usr/bin/python3", "-I", str(tool), "apply"] == command[4:8]
    # A separate worker owns the operation; the launcher does not install/restart anything.
    assert not deployment.new.exists()
    result = deploy.Deployment(host, deployment.bundle).apply()
    host.job_running = False
    assert result["state"] == "verified"
    assert result["snapshot_id"] == 2
    assert deployment.receipt.parent == host.paths.status
    assert "run" not in deployment.receipt.relative_to(host.boundary).parts
    before = list(host.commands)
    receipt = deployment.receipt.read_bytes()
    again = deploy.start(host, deployment.bundle, directory, manifest_hash, tool)
    assert again["state"] == "already_installed"
    assert host.commands == before
    assert deployment.receipt.read_bytes() == receipt
    assert "never-print-this" not in receipt.decode()
    backup = Path(result["backup"])
    with sqlite3.connect(backup / "database.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 1


@pytest.mark.parametrize(
    "failure,state",
    [
        ("install", "not_activated"),
        ("settings", "not_activated"),
        ("migration", "not_activated"),
        ("override_write", "not_activated"),
        ("health", "rolled_back"),
        ("collector", "rolled_back"),
        ("journal", "rolled_back"),
        ("tls", "rolled_back"),
        ("learn_warning", "rolled_back"),
        ("policy_drift", "rollback_needs_operator"),
        ("override_drift", "rollback_needs_operator"),
    ],
)
def test_failure_paths_preserve_data_and_refuse_drift(
    rollout: Any, failure: str, state: str
) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    host.failure = failure
    with pytest.raises(deploy.DeployError, match="Deployment failed"):
        deployment.apply()
    receipt = json.loads(deployment.receipt.read_text())
    assert receipt["state"] == state
    assert receipt["controller_writes_executed"] is False
    with sqlite3.connect(host.paths.database) as connection:
        assert (
            connection.execute("SELECT value FROM audit LIMIT 1").fetchone()[0]
            == "original history"
        )
        if failure in {"journal", "learn_warning", "policy_drift", "override_drift"}:
            assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 2
    if state != "rollback_needs_operator":
        assert host.current == OLD
        assert not deployment.env.exists()
        assert not deployment.override.exists()
    if failure == "policy_drift":
        assert host.paths.sudoers.read_bytes() == b"changed by a separate actor"


def test_explicit_recovery_uses_durable_context_without_bundle_files(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    deployment.apply()
    value = json.loads(deployment.receipt.read_text())
    value["state"] = "active_waiting_for_collector"  # Simulated worker kill after restart.
    deployment.receipt.write_text(json.dumps(value))
    assert deploy.status(host, NEW)["state"] == "interrupted_needs_operator"
    recovered = deploy.Deployment(host, deploy.Bundle(NEW, OLD, {}, deployment.bundle.source_hash))
    assert recovered.rollback()["state"] == "rolled_back"
    assert host.current == OLD
    with sqlite3.connect(host.paths.database) as connection:
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 2
    before = list(host.commands)
    assert recovered.rollback()["state"] == "already_rolled_back"
    assert host.commands == before


def test_update_and_rollback_restore_previous_release_override(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    previous = deployment._dropin_for(
        deployment.old, host.paths.config / ("ui-" + OLD[:7] + ".env")
    )
    deployment.override.write_bytes(previous)
    deployment.apply()
    deployment.rollback()
    assert deployment.override.read_bytes() == previous
    assert host.current == OLD


@pytest.mark.parametrize(
    "problem", ["baseline", "maintenance", "schema", "later_dropin", "existing_target"]
)
def test_preflight_refusals_do_not_restart_service(rollout: Any, problem: str) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    if problem == "baseline":
        host.current = "c" * 40
    elif problem == "maintenance":
        with sqlite3.connect(host.paths.database) as connection:
            connection.execute(
                "INSERT INTO system_state VALUES ('maintenance_mode', ?)",
                (json.dumps({"active": True}),),
            )
    elif problem == "schema":
        with sqlite3.connect(host.paths.database) as connection:
            connection.execute("UPDATE alembic_version SET version_num='unsupported'")
    elif problem == "later_dropin":
        (host.paths.dropins / "zzzzz-unknown.conf").write_bytes(b"override")
    else:
        deployment.new.mkdir()
    with pytest.raises(deploy.DeployError):
        deployment.apply()
    assert host.commands == []
    assert not deployment.env.exists()


def test_concurrent_worker_cannot_take_lock(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    with deploy.deployment_lock(host), pytest.raises(BlockingIOError):
        deployment.apply()
    assert not deployment.new.exists()


def test_in_progress_request_is_not_started_again(rollout: Any) -> None:
    deployment, host, directory, manifest_hash = rollout
    host.paths.status.mkdir()
    deployment.receipt.write_text(json.dumps({"state": "installing_offline", "commit": NEW}))
    host.job_running = True
    result = deploy.start(host, deployment.bundle, directory, manifest_hash, host.paths.binary)
    assert result["state"] == "running"
    assert all(command[0] != "/usr/bin/systemd-run" for command in host.commands)


def test_tampering_and_missing_inputs_fail_before_plan(tmp_path: Path) -> None:
    directory, manifest_hash = inputs(tmp_path)
    (directory / "runtime-lock.txt").write_bytes(b"changed")
    with pytest.raises(deploy.DeployError, match="hash mismatch"):
        deploy.Bundle.load(directory, manifest_hash)
    with pytest.raises(deploy.DeployError, match="hash mismatch"):
        deploy.Bundle.load(directory, "0" * 64)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "_wheels/evil", ".venv/evil"])
def test_unsafe_source_archive_is_rejected(tmp_path: Path, name: str) -> None:
    directory, _manifest_hash = inputs(tmp_path)
    source = source_archive({name: b"unsafe"})
    (directory / "source.tar").write_bytes(source)
    manifest = json.loads((directory / "release.json").read_bytes())
    manifest["source_sha256"] = deploy.digest(source)
    content = json.dumps(manifest).encode()
    (directory / "release.json").write_bytes(content)
    with pytest.raises(deploy.DeployError, match="Unsafe"):
        deploy.Bundle.load(directory, deploy.digest(content))


def test_source_wheel_difference_is_rejected(tmp_path: Path) -> None:
    directory, _manifest_hash = inputs(tmp_path)
    source = source_archive({"src/megaraid_dashboard/__init__.py": b"different code"})
    (directory / "source.tar").write_bytes(source)
    manifest = json.loads((directory / "release.json").read_bytes())
    manifest["source_sha256"] = deploy.digest(source)
    content = json.dumps(manifest).encode()
    (directory / "release.json").write_bytes(content)
    with pytest.raises(deploy.DeployError, match="differs"):
        deploy.Bundle.load(directory, deploy.digest(content))


def test_symlink_and_hardlink_input_are_refused(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.write_bytes(b"value")
    linked = tmp_path / "linked"
    linked.symlink_to(target)
    with pytest.raises(OSError):
        deploy.read_file(linked)
    linked.unlink()
    os.link(target, linked)
    with pytest.raises(deploy.DeployError):
        deploy.read_file(target)


def test_bundle_command_and_plan_are_unprivileged_and_nonmutating(
    tmp_path: Path, capsys: Any
) -> None:
    directory, _manifest_hash = inputs(tmp_path)
    output = tmp_path / "prepared"
    result = deploy.make_bundle(
        directory / "source.tar",
        directory / "runtime-lock.txt",
        directory / "wheels",
        output,
        NEW,
        OLD,
    )
    assert (
        deploy.main(
            ["plan", "--bundle", str(output), "--manifest-sha256", result["manifest_sha256"]]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["service_modified"] is False
    assert len(list(output.rglob("*.whl"))) == 1


def test_root_path_checks_every_component(tmp_path: Path) -> None:
    root = tmp_path / "protected"
    root.mkdir(mode=0o755)
    value = root / "file"
    value.write_bytes(b"data")
    deploy.root_path(value, owner=os.getuid(), boundary=root)
    root.chmod(0o777)
    with pytest.raises(deploy.DeployError, match="root-controlled"):
        deploy.root_path(value, owner=os.getuid(), boundary=root)
    root.chmod(0o755)
    linked = root / "link"
    linked.symlink_to(value)
    with pytest.raises(deploy.DeployError, match="root-controlled"):
        deploy.root_path(linked, owner=os.getuid(), boundary=root)


def test_legacy_config_refuses_unsafe_modes_and_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "legacy"
    path.mkdir(mode=0o750)
    env = path / "env"
    env.write_bytes(b"private")
    env.chmod(0o600)
    deploy.legacy_config(path, os.getuid(), os.getgid())
    env.chmod(0o644)
    with pytest.raises(deploy.DeployError):
        deploy.legacy_config(path, os.getuid(), os.getgid())
    env.unlink()
    env.symlink_to(tmp_path / "outside")
    with pytest.raises(OSError):
        deploy.legacy_config(path, os.getuid(), os.getgid())


def legacy_install(deployment: deploy.Deployment, host: FakeHost) -> None:
    host.install(deployment.bundle, deployment.new)
    deployment.env.write_text("GIT_SHA=" + NEW + "\n")
    (host.paths.dropins / "zz-ui-legacy.conf").write_bytes(deployment._dropin())
    host.current = NEW
    host.pid = "456"
    host.sleep(5)


def test_adoption_attests_current_legacy_release_without_restart_or_database_write(
    rollout: Any,
) -> None:
    deployment, host, directory, manifest_hash = rollout
    legacy_install(deployment, host)
    before_db = host.paths.database.read_bytes()
    result = deployment.adopt()
    assert result["state"] == "verified_external"
    assert result["managed_rollback"] is False
    assert result["service_modified"] is False
    assert host.paths.database.read_bytes() == before_db
    assert host.commands == []
    tool = host.paths.releases / "tool.py"
    tool.write_bytes(b"owned tool")
    assert (
        deploy.start(host, deployment.bundle, directory, manifest_hash, tool)["state"]
        == "already_installed"
    )
    assert host.commands == []
    assert deploy.status(host, NEW)["state"] == "verified_external"
    with pytest.raises(deploy.DeployError, match="legacy attestation"):
        deployment.rollback()


@pytest.mark.parametrize(
    "problem", ["old_sample", "no_process_collection", "source", "unhealthy", "config_drift"]
)
def test_adoption_refuses_unproven_or_changed_installation(rollout: Any, problem: str) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    legacy_install(deployment, host)
    if problem == "old_sample":
        with sqlite3.connect(host.paths.database) as connection:
            connection.execute("UPDATE controller_snapshots SET captured_at='2000-01-01T00:00:00'")
    elif problem == "no_process_collection":
        host.collected = False
    elif problem == "source":
        (deployment.new / "RELEASE.json").write_text(
            json.dumps({"commit": NEW, "source_sha256": "0" * 64})
        )
    elif problem == "unhealthy":
        host.failure = "health"
    else:
        deployment.adopt()
        host.paths.sudoers.write_bytes(b"outside change")
    with pytest.raises(deploy.DeployError):
        deployment.adopt()
    assert host.commands == []


def test_unrecorded_active_candidate_needs_explicit_adoption(rollout: Any) -> None:
    deployment, host, directory, manifest_hash = rollout
    legacy_install(deployment, host)
    with pytest.raises(deploy.DeployError, match="explicit adopt"):
        deploy.start(host, deployment.bundle, directory, manifest_hash, host.paths.binary)
    assert host.commands == []


def test_foreign_release_override_is_never_overwritten(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    custom = b"[Service]\nProtectSystem=strict\n"
    deployment.override.write_bytes(custom)
    with pytest.raises(deploy.DeployError, match="recognized"):
        deployment.apply()
    assert deployment.override.read_bytes() == custom
    assert host.commands == []


def test_partial_replacement_failure_retains_the_previous_override(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    previous = deployment._dropin_for(
        deployment.old, host.paths.config / ("ui-" + OLD[:7] + ".env")
    )
    deployment.override.write_bytes(previous)
    host.failure = "override_write"
    with pytest.raises(deploy.DeployError):
        deployment.apply()
    # The writer fault also affects restoring the existing override: no unsafe deletion.
    assert deployment.override.read_bytes() == previous
    assert host.current == OLD


def test_status_does_not_claim_a_verified_receipt_is_the_live_release(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    deployment.apply()
    host.current = OLD
    result = deploy.status(host, NEW)
    assert result["state"] == "not_current_or_unhealthy"
    assert result["receipt"]["state"] == "verified"


def test_cli_guards_and_all_operator_paths(rollout: Any, monkeypatch: Any, capsys: Any) -> None:
    _deployment, host, directory, manifest_hash = rollout
    tool = host.paths.releases / "tool.py"
    tool.write_bytes(b"owned tool")
    monkeypatch.setattr(deploy, "Host", lambda _paths: host)
    monkeypatch.setattr(deploy, "__file__", str(tool))
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    base = ["--bundle", str(directory), "--manifest-sha256", manifest_hash]
    assert deploy.main(["start", *base]) == 1
    assert json.loads(capsys.readouterr().err)["state"] == "refused_or_failed"
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert deploy.main(["start", *base]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "queued"
    assert deploy.main(["apply", *base]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "verified"
    host.job_running = False
    assert deploy.main(["status", "--commit", NEW]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "verified"
    assert deploy.main(["rollback", "--commit", NEW]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "rolled_back"


def test_actual_install_method_uses_only_offline_pinned_inputs(
    rollout: Any, monkeypatch: Any
) -> None:
    deployment, host, _directory, _manifest_hash = rollout

    class Builder:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def create(self, path: Path) -> None:
            path.mkdir()

    monkeypatch.setattr(deploy.venv, "EnvBuilder", Builder)
    deploy.Host.install(host, deployment.bundle, deployment.new)
    install = next(command for command in host.commands if "install" in command)
    assert "--no-index" in install and "--find-links" in install
    assert json.loads((deployment.new / "RELEASE.json").read_text())["commit"] == NEW
    assert (deployment.new / "migrations/env.py").read_bytes() == b"# unchanged\n"


@pytest.mark.parametrize(
    "lock",
    [
        b"package @ https://example.invalid/a.whl",
        b"-r other.txt",
        b"--index-url=https://example.invalid",
    ],
)
def test_lock_cannot_enable_network_or_extra_files(tmp_path: Path, lock: bytes) -> None:
    directory, _manifest_hash = inputs(tmp_path)
    manifest = json.loads((directory / "release.json").read_bytes())
    (directory / "runtime-lock.txt").write_bytes(lock)
    manifest["lock_sha256"] = deploy.digest(lock)
    content = json.dumps(manifest).encode()
    (directory / "release.json").write_bytes(content)
    with pytest.raises(deploy.DeployError, match="pinned versions"):
        deploy.Bundle.load(directory, deploy.digest(content))


def test_protected_receipt_directory_symlink_is_not_followed(rollout: Any) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    elsewhere = host.boundary / "unrelated"
    elsewhere.mkdir(mode=0o700)
    host.paths.status.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(deploy.DeployError, match="root-controlled"):
        deployment.apply()
    assert (elsewhere.stat().st_mode & 0o777) == 0o700
    assert not list(elsewhere.iterdir())


def test_fifo_and_oversized_inputs_fail_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(deploy.DeployError, match="regular"):
        deploy.read_file(fifo)
    large = tmp_path / "large"
    large.write_bytes(b"12345")
    with pytest.raises(deploy.DeployError, match="size limit"):
        deploy.read_file(large, limit=4)


@pytest.mark.parametrize("change", ["version", "commit", "baseline", "extra", "wheel_name"])
def test_manifest_contract_does_not_accept_paths_or_unreviewed_options(
    tmp_path: Path, change: str
) -> None:
    directory, _manifest_hash = inputs(tmp_path)
    manifest = json.loads((directory / "release.json").read_bytes())
    if change == "version":
        manifest["version"] = True
    elif change == "commit":
        manifest["commit"] = "../escape"
    elif change == "baseline":
        manifest["baseline"] = NEW
    elif change == "extra":
        manifest["service"] = "unrelated.service"
    else:
        manifest["wheels"]["../outside.whl"] = "0" * 64
    data = json.dumps(manifest).encode()
    (directory / "release.json").write_bytes(data)
    with pytest.raises(deploy.DeployError):
        deploy.Bundle.load(directory, deploy.digest(data))


def test_preflight_refusal_is_durable_and_can_be_rechecked_without_relaxing_guards(
    rollout: Any,
) -> None:
    deployment, host, _directory, _manifest_hash = rollout
    with sqlite3.connect(host.paths.database) as connection:
        connection.execute(
            "INSERT INTO system_state VALUES ('maintenance_mode', ?)",
            (json.dumps({"active": True}),),
        )
    with pytest.raises(deploy.DeployError, match="Maintenance"):
        deployment.apply()
    receipt = json.loads(deployment.receipt.read_text())
    assert receipt["state"] == "refused" and receipt["no_changes"] is True
    assert host.commands == []
    with sqlite3.connect(host.paths.database) as connection:
        connection.execute("DELETE FROM system_state WHERE key='maintenance_mode'")
    assert deployment.apply()["state"] == "verified"
