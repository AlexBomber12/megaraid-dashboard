#!/usr/bin/env python3
"""Offline, application-only rollout for the existing SERVER deployment.

This program never invokes StorCLI, migrates a database or changes command grants.
Fresh installs and changes to the security/schema contract need their own procedure.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import venv
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "0009_event_deliveries"
SERVICE = "megaraid-dashboard.service"
ACCOUNT = "megaraid"
SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TERMINAL = {
    "refused",
    "verified",
    "verified_external",
    "rolled_back",
    "not_activated",
    "rollback_needs_operator",
}


class DeployError(RuntimeError):
    """A refused or unsuccessful rollout; details belong in the private log."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path, expected: str | None = None, *, limit: int = 256 * 1024 * 1024) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DeployError("Expected a regular, single-link file")
        if info.st_size > limit:
            raise DeployError("Input exceeds the file size limit")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise DeployError("Input grew beyond the file size limit")
    if expected is not None and digest(data) != expected:
        raise DeployError("Input hash mismatch: " + path.name)
    return data


def root_path(path: Path, *, owner: int = 0, boundary: Path = Path("/")) -> None:
    """Check every component, including ancestors; test boundaries are never CLI options."""
    if not path.is_absolute() or boundary not in (path, *path.parents):
        raise DeployError("Invalid protected path")
    parts = [path, *path.parents]
    for part in reversed(parts[: parts.index(boundary) + 1]):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
            raise DeployError("Path is not root-controlled: " + str(part))


def atomic_write(path: Path, data: bytes, *, mode: int = 0o644, uid: int = 0, gid: int = 0) -> None:
    if path.is_symlink():
        raise DeployError("Refusing a symlink destination")
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchown(stream.fileno(), uid, gid)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def legacy_config(path: Path, uid: int, gid: int) -> dict[str, Any]:
    """Preserve the existing service-owned directory without following links or accepting races."""

    def signature(info: os.stat_result) -> list[int]:
        return [
            info.st_dev,
            info.st_ino,
            info.st_uid,
            info.st_gid,
            stat.S_IMODE(info.st_mode),
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ]

    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        if (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) != (uid, gid, 0o750):
            raise DeployError("Legacy configuration ownership or mode differs")
        descriptor = os.open("env", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid not in {0, uid}
                or before.st_gid not in {0, gid}
                or stat.S_IMODE(before.st_mode) not in {0o600, 0o640}
            ):
                raise DeployError("Legacy env is not a private regular root/service file")
            content = stream.read()
            after = os.fstat(stream.fileno())
            named = os.stat("env", dir_fd=directory, follow_symlinks=False)
            if signature(before) != signature(after) or signature(after) != signature(named):
                raise DeployError("Legacy env changed during inspection")
        current = path.lstat()
        identity = [parent.st_dev, parent.st_ino, uid, gid, 0o750]
        if (
            not stat.S_ISDIR(current.st_mode)
            or [
                current.st_dev,
                current.st_ino,
                current.st_uid,
                current.st_gid,
                stat.S_IMODE(current.st_mode),
            ]
            != identity
        ):
            raise DeployError("Legacy configuration directory changed during inspection")
        return {
            "file_signature": signature(after),
            "sha256": digest(content),
            "directory_identity": identity,
        }
    finally:
        os.close(directory)


@dataclass(frozen=True)
class Bundle:
    commit: str
    baseline: str
    files: Mapping[str, bytes]
    source_hash: str

    @classmethod
    def load(cls, directory: Path, manifest_hash: str) -> Bundle:
        if not DIGEST.fullmatch(manifest_hash):
            raise DeployError("A reviewed manifest SHA-256 is required")
        manifest = json.loads(
            read_file(directory / "release.json", manifest_hash, limit=1024 * 1024)
        )
        if not isinstance(manifest, dict) or set(manifest) != {
            "version",
            "commit",
            "baseline",
            "source_sha256",
            "lock_sha256",
            "wheels",
        }:
            raise DeployError("Unsupported release manifest")
        if type(manifest["version"]) is not int or manifest["version"] != 1:
            raise DeployError("Unsupported manifest version")
        for key in ("commit", "baseline"):
            if not isinstance(manifest[key], str) or not SHA.fullmatch(manifest[key]):
                raise DeployError("Expected a full commit SHA")
        if manifest["commit"] == manifest["baseline"]:
            raise DeployError("Candidate and baseline must differ")
        wheels = manifest["wheels"]
        if not isinstance(wheels, dict) or not wheels or len(wheels) > 128:
            raise DeployError("An offline wheel manifest is required")
        expected = {
            "source.tar": manifest["source_sha256"],
            "runtime-lock.txt": manifest["lock_sha256"],
        }
        for name, value in wheels.items():
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name)
                or Path(name).name != name
            ):
                raise DeployError("Invalid wheel name")
            expected["wheels/" + name] = value
        files = {}
        for name, value in expected.items():
            if not isinstance(value, str) or not DIGEST.fullmatch(value):
                raise DeployError("Invalid input digest")
            files[name] = read_file(directory / name, value)
        names = [name for name in wheels if name.startswith("megaraid_dashboard-")]
        if len(names) != 1:
            raise DeployError("Exactly one application wheel is required")
        validate_payload(files, names[0])
        return cls(manifest["commit"], manifest["baseline"], files, manifest["source_sha256"])


def validate_payload(files: Mapping[str, bytes], application_wheel: str) -> None:
    for line in files["runtime-lock.txt"].decode().splitlines():
        if (
            line.strip()
            and not line.startswith("#")
            and not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+", line)
        ):
            raise DeployError("Runtime lock must contain pinned versions, without URLs or options")
    expected = {}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(files["source.tar"])) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or not (member.isfile() or member.isdir())
                or str(path) in seen
                or path.parts[0] in {"_wheels", ".venv", "RELEASE.json"}
                or path.parts[0].startswith("_input-")
            ):
                raise DeployError("Unsafe or duplicate source archive member")
            seen.add(str(path))
            if not member.isfile():
                continue
            target = None
            if path.parts[:2] == ("src", "megaraid_dashboard"):
                target = str(path.relative_to("src"))
            elif path.parts[0] == "migrations" or str(path) == "alembic.ini":
                target = "megaraid_dashboard/" + str(path)
            if target is not None:
                stream = archive.extractfile(member)
                if stream is None:
                    raise DeployError("Source member cannot be read")
                expected[target] = stream.read()
    with zipfile.ZipFile(io.BytesIO(files["wheels/" + application_wheel])) as wheel:
        actual = {}
        wheel_seen = set()
        for item in wheel.infolist():
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or str(path) in wheel_seen
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise DeployError("Unsafe application wheel member")
            wheel_seen.add(str(path))
            if item.is_dir():
                continue
            if item.filename.startswith("megaraid_dashboard/"):
                actual[item.filename] = wheel.read(item)
            elif not (
                len(path.parts) >= 2
                and re.fullmatch(r"megaraid_dashboard-[A-Za-z0-9_.+!-]+\.dist-info", path.parts[0])
            ):
                raise DeployError("Unexpected application wheel member")
        if not expected or actual != expected:
            raise DeployError("Application wheel differs from the source archive")


@dataclass(frozen=True)
class Paths:
    releases: Path = Path("/opt")
    config: Path = Path("/etc/megaraid-dashboard-rollout")
    legacy: Path = Path("/etc/megaraid-dashboard")
    dropins: Path = Path("/etc/systemd/system/megaraid-dashboard.service.d")
    unit: Path = Path("/etc/systemd/system/megaraid-dashboard.service")
    sudoers: Path = Path("/etc/sudoers.d/megaraid-dashboard-rollout")
    binary: Path = Path("/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64")
    database: Path = Path("/var/lib/megaraid-dashboard/megaraid.db")
    status: Path = Path("/var/lib/megaraid-dashboard-deploy")
    backups: Path = Path("/var/backups")
    lock: Path = Path("/run/megaraid-dashboard-rollout.lock")

    def release(self, commit: str) -> Path:
        if not SHA.fullmatch(commit):
            raise DeployError("Invalid release identity")
        return self.releases / ("megaraid-dashboard-ui-" + commit[:7])

    def receipt(self, commit: str) -> Path:
        self.release(commit)
        return self.status / (commit + ".json")


class Host:
    """OS boundary. Tests replace commands, clocks and ownership, never real hardware."""

    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.log: Any = None

    def protect(self, path: Path) -> None:
        root_path(path)

    def protected_digest(self, path: Path) -> str:
        """Fingerprint protected files or nested directories without following links."""
        self.protect(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            return digest(read_file(path))
        evidence = {
            "uid": info.st_uid,
            "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode),
            "entries": {
                child.name: self.protected_digest(child) for child in sorted(path.iterdir())
            },
        }
        return digest(json.dumps(evidence, sort_keys=True).encode())

    def write(self, path: Path, data: bytes, mode: int = 0o644, gid: int = 0) -> None:
        self.protect(path.parent)
        atomic_write(path, data, mode=mode, gid=gid)

    def run(
        self, args: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: int = 120
    ) -> bytes:
        result = subprocess.run(
            args, capture_output=True, env=env, timeout=timeout, check=False, cwd="/"
        )
        if self.log is not None:
            self.log.write("COMMAND: " + " ".join(args) + "\n")
            self.log.write(result.stdout.decode(errors="replace"))
            self.log.write(result.stderr.decode(errors="replace"))
            self.log.flush()
        if result.returncode:
            raise DeployError("Command failed; inspect the private deployment log")
        return result.stdout

    def properties(self) -> dict[str, str]:
        output = self.run(
            [
                "/usr/bin/systemctl",
                "show",
                SERVICE,
                "-pWorkingDirectory",
                "-pUser",
                "-pGroup",
                "-pMainPID",
                "-pActiveState",
                "-pSubState",
                "-pNoNewPrivileges",
                "-pDropInPaths",
            ]
        )
        return dict(line.split("=", 1) for line in output.decode().splitlines() if "=" in line)

    def environment(self, pid: str) -> dict[str, str]:
        if not pid.isdigit() or int(pid) <= 0:
            raise DeployError("No running service process")
        return dict(
            item.decode().split("=", 1)
            for item in Path("/proc", pid, "environ").read_bytes().split(b"\0")
            if b"=" in item
        )

    def account(self) -> tuple[int, int]:
        import pwd

        account = pwd.getpwnam(ACCOUNT)
        return account.pw_uid, account.pw_gid

    def health(self) -> bool:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:8090/healthz", timeout=5) as response:
                value = json.load(response)
                return isinstance(value, dict) and value == {
                    "status": "ok",
                    "database": "ok",
                    "collector": "ok",
                }
        except (OSError, ValueError):
            return False

    def journal(self, pid: str) -> bytes:
        return self.run(
            [
                "/usr/bin/journalctl",
                "--no-pager",
                "--output=cat",
                "-n",
                "150",
                "_SYSTEMD_UNIT=" + SERVICE,
                "_PID=" + pid,
            ]
        )

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: int) -> None:
        time.sleep(seconds)

    def settings_digest(self, release: Path, env: Mapping[str, str]) -> str:
        code = (
            "import hashlib,json; from megaraid_dashboard.config import Settings; "
            "from megaraid_dashboard.storcli.policy import load_policy,verify_pinned_binary; "
            "s=Settings(_env_file=None); "
            "assert not s.controller_read_only and "
            "s.controller_action_capability=='full-supported-actions'; "
            "assert not s.maintenance_mode and not s.destructive_mode; "
            "assert s.collector_enabled and s.telegram_enabled; "
            "verify_pinned_binary(s.storcli_path); p=load_policy(s.controller_command_policy); "
            "assert len(p.commands)==79 and len(p.replacements)==8; "
            "d=s.model_dump(mode='json'); d.pop('git_sha',None); "
            "print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest())"
        )
        output = self.run(
            [
                "/usr/sbin/runuser",
                "-m",
                "-u",
                ACCOUNT,
                "--",
                str(release / ".venv/bin/python"),
                "-I",
                "-c",
                code,
            ],
            env=env,
        )
        value = output.decode().strip()
        if not DIGEST.fullmatch(value):
            raise DeployError("Settings verification did not return a digest")
        return value

    def install(self, bundle: Bundle, target: Path) -> None:
        # The unprivileged service must traverse/read application and venv files.
        # Private rollout records keep their surrounding restrictive mask.
        previous_mask = os.umask(0o022)
        try:
            self._install_application(bundle, target)
        finally:
            os.umask(previous_mask)

    def _install_application(self, bundle: Bundle, target: Path) -> None:
        target.mkdir(mode=0o755)
        for name, content in bundle.files.items():
            destination = target / (
                "_wheels/" + name.removeprefix("wheels/")
                if name.startswith("wheels/")
                else "_input-" + name
            )
            if not destination.parent.exists():
                destination.parent.mkdir(mode=0o755)
            self.write(destination, content)
        with tarfile.open(target / "_input-source.tar") as archive:
            # Source cannot replace installer-owned inputs or directories.
            for member in archive.getmembers():
                if member.name.startswith(("_input-", "_wheels", ".venv", "RELEASE.json")):
                    raise DeployError("Source archive overlaps deployment metadata")
            archive.extractall(target, filter="data")
        venv.EnvBuilder(with_pip=True).create(target / ".venv")
        python = str(target / ".venv/bin/python")
        pip_env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "PIP_CONFIG_FILE": "/dev/null",
            "PYTHONNOUSERSITE": "1",
        }
        wheel = next(name for name in bundle.files if name.startswith("wheels/megaraid_dashboard-"))
        self.run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(target / "_wheels"),
                "-r",
                str(target / "_input-runtime-lock.txt"),
                str(target / "_wheels" / Path(wheel).name),
            ],
            env=pip_env,
            timeout=240,
        )
        self.run([python, "-m", "pip", "check"], env=pip_env)
        self.write(
            target / "RELEASE.json",
            json.dumps(
                {
                    "commit": bundle.commit,
                    "source_sha256": bundle.source_hash,
                }
            ).encode(),
        )
        self.protect(target)

    def https_check(self) -> None:
        value = self.run(
            [
                "/usr/bin/curl",
                "--noproxy",
                "*",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code} %{ssl_verify_result}",
                "--resolve",
                "megaraid.alexbomber.com:443:127.0.0.1",
                "https://megaraid.alexbomber.com/",
            ]
        )
        if value != b"401 0":
            raise DeployError("HTTPS authentication or TLS verification failed")


@contextlib.contextmanager
def deployment_lock(host: Host) -> Iterator[None]:
    import fcntl

    host.protect(host.paths.lock.parent)
    descriptor = os.open(host.paths.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        host.protect(host.paths.lock)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DeployError("Invalid deployment lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def database(paths: Paths) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{paths.database}?mode=ro", uri=True, timeout=20)
    connection.execute("PRAGMA query_only=ON")
    return connection


def snapshot(connection: sqlite3.Connection) -> int:
    if connection.execute("SELECT version_num FROM alembic_version").fetchall() != [(SCHEMA,)]:
        raise DeployError("Schema change requires a separate migration procedure")
    row = connection.execute(
        "SELECT value FROM system_state WHERE key='maintenance_mode'"
    ).fetchone()
    if row:
        state = json.loads(row[0])
        expires = state.get("expires_at")
        if state.get("active") and (
            not expires or dt.datetime.fromisoformat(expires) > dt.datetime.now(dt.UTC)
        ):
            raise DeployError("Maintenance is active; deployment refused")
    latest = connection.execute("SELECT max(id) FROM controller_snapshots").fetchone()[0]
    if not isinstance(latest, int):
        raise DeployError("No saved collector evidence")
    return latest


class Deployment:
    def __init__(self, host: Host, bundle: Bundle) -> None:
        self.host = host
        self.paths = host.paths
        self.bundle = bundle
        self.old = self.paths.release(bundle.baseline)
        self.new = self.paths.release(bundle.commit)
        self.env = self.paths.config / ("ui-" + bundle.commit[:7] + ".env")
        self.override = self.paths.dropins / "zzzz-release.conf"
        self.receipt = self.paths.receipt(bundle.commit)

    def _status(self, state: str, **extra: Any) -> dict[str, Any]:
        value = {
            "state": state,
            "commit": self.bundle.commit,
            "baseline": self.bundle.baseline,
            "source_sha256": self.bundle.source_hash,
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
            "controller_writes_executed": False,
            **extra,
        }
        self.host.write(self.receipt, json.dumps(value, indent=2).encode())
        return value

    def _ensure_receipts(self) -> None:
        self.host.protect(self.paths.status.parent)
        if self.paths.status.exists() or self.paths.status.is_symlink():
            self.host.protect(self.paths.status)
        else:
            self.paths.status.mkdir(mode=0o755)
            self.paths.status.chmod(0o755)
            self.host.protect(self.paths.status)

    def _legacy(self) -> dict[str, Any]:
        self.host.protect(self.paths.legacy.parent)
        uid, gid = self.host.account()
        return legacy_config(self.paths.legacy, uid, gid)

    def _identity(self, properties: Mapping[str, str], release: Path, commit: str) -> None:
        expected = {
            "WorkingDirectory": str(release),
            "User": ACCOUNT,
            "Group": ACCOUNT,
            "ActiveState": "active",
            "SubState": "running",
            "NoNewPrivileges": "no",
        }
        if any(properties.get(key) != value for key, value in expected.items()):
            raise DeployError("Service differs from the approved running baseline")
        self.host.protect(release)
        self.host.protect(release / "RELEASE.json")
        if json.loads(read_file(release / "RELEASE.json")).get("commit") != commit:
            raise DeployError("Running release identity differs")

    def _preserved(self, context: Mapping[str, Any]) -> None:
        if self._legacy() != context["legacy"]:
            raise DeployError("Legacy configuration changed")
        for name, expected in context["protected"].items():
            path = Path(name)
            if self.host.protected_digest(path) != expected:
                raise DeployError("Protected configuration changed: " + path.name)

    def _own_files(self, context: Mapping[str, Any]) -> None:
        for path, expected in (
            (self.override, context["override_sha256"]),
            (self.env, context["env_sha256"]),
        ):
            self.host.protect(path)
            read_file(path, expected)

    def _same_migrations(self) -> None:
        def sources(release: Path) -> dict[str, bytes]:
            directory = release / "migrations"
            self.host.protect(directory)
            self.host.protect(release / "alembic.ini")
            result = {"alembic.ini": read_file(release / "alembic.ini")}
            for path in directory.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    self.host.protect(path)
                    result[str(path.relative_to(directory))] = read_file(path)
            return result

        if sources(self.old) != sources(self.new):
            raise DeployError("Migration changes require a separate deployment procedure")

    def _wait_health(self) -> None:
        deadline = self.host.now() + 60
        while not self.host.health():
            if self.host.now() >= deadline:
                raise DeployError("Service did not become healthy")
            self.host.sleep(2)

    def _protected(self, properties: Mapping[str, str]) -> dict[str, str]:
        self.host.protect(self.paths.config)
        files = {self.paths.unit, self.paths.sudoers, self.paths.binary}
        files.update(Path(value) for value in properties["DropInPaths"].split())
        files.update(self.paths.config.iterdir())
        # The tool owns one application-only override. Its previous bytes are backed up.
        files.discard(self.override)
        result = {}
        for path in sorted(files):
            result[str(path)] = self.host.protected_digest(path)
        return result

    def _already_installed(self, properties: Mapping[str, str]) -> dict[str, Any] | None:
        if not self.receipt.exists():
            return None
        self.host.protect(self.receipt)
        receipt = json.loads(read_file(self.receipt))
        if receipt.get("state") in {"refused", "preflight"} and receipt.get("no_changes") is True:
            return None
        if receipt.get("state") not in {"verified", "verified_external"}:
            raise DeployError("An earlier attempt needs status inspection or explicit rollback")
        if receipt.get("commit") != self.bundle.commit:
            raise DeployError("Receipt identity differs")
        self._identity(properties, self.new, self.bundle.commit)
        if (
            json.loads(read_file(self.new / "RELEASE.json")).get("source_sha256")
            != self.bundle.source_hash
        ):
            raise DeployError("Installed source differs from the reviewed bundle")
        external = receipt["state"] == "verified_external"
        if external:
            path = self.paths.status / "private" / (self.bundle.commit + ".json")
            self.host.protect(path)
            context = json.loads(read_file(path))
            if context.get("commit") != self.bundle.commit:
                raise DeployError("External verification identity differs")
            if properties["DropInPaths"] != context["dropins"]:
                raise DeployError("Verified external deployment configuration changed")
        else:
            context = self._context(receipt)
        self._preserved(context)
        if not external:
            self._own_files(context)
        environment = self.host.environment(properties["MainPID"])
        if environment.get("GIT_SHA") != self.bundle.commit or not self.host.health():
            raise DeployError("Installed release is not healthy or does not match its environment")
        if self.host.settings_digest(self.new, environment) != context["settings_sha256"]:
            raise DeployError("Effective settings changed since deployment")
        return {**receipt, "state": "already_installed", "persisted_state": receipt["state"]}

    def adopt(self) -> dict[str, Any]:
        """Attest a healthy legacy installation without restart, backup or controller calls."""
        with deployment_lock(self.host):
            self._ensure_receipts()
            properties = self.host.properties()
            installed = self._already_installed(properties)
            if installed is not None:
                return installed
            self._identity(properties, self.new, self.bundle.commit)
            if (
                json.loads(read_file(self.new / "RELEASE.json")).get("source_sha256")
                != self.bundle.source_hash
            ):
                raise DeployError("Installed source differs from the reviewed bundle")
            environment = self.host.environment(properties["MainPID"])
            if environment.get("GIT_SHA") != self.bundle.commit or not self.host.health():
                raise DeployError(
                    "The existing installation is not healthy or has the wrong identity"
                )
            settings = self.host.settings_digest(self.new, environment)
            legacy = self._legacy()
            protected = self._protected(properties)
            # For an external installation every current override is protected, including ours.
            if self.override.exists():
                self.host.protect(self.override)
                protected[str(self.override)] = digest(read_file(self.override))
            with contextlib.closing(database(self.paths)) as connection:
                latest = snapshot(connection)
                captured = connection.execute(
                    "SELECT captured_at FROM controller_snapshots WHERE id=?", (latest,)
                ).fetchone()
                when = dt.datetime.fromisoformat(captured[0]) if captured else None
                if when is None:
                    raise DeployError("No sample timestamp")
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.UTC)
                age = (dt.datetime.now(dt.UTC) - when).total_seconds()
                journal = self.host.journal(properties["MainPID"])
                if not -60 <= age <= 600 or b"collector_run_completed" not in journal:
                    raise DeployError("No recent normal collection from the current process")
                verify_snapshot(connection, latest, journal)
            context = {
                "commit": self.bundle.commit,
                "legacy": legacy,
                "protected": protected,
                "dropins": properties["DropInPaths"],
                "settings_sha256": settings,
            }
            self._preserved(context)
            if self.host.properties() != properties or not self.host.health():
                raise DeployError("Service changed during external verification")
            private = self.paths.status / "private"
            if private.exists() or private.is_symlink():
                self.host.protect(private)
            else:
                private.mkdir(mode=0o700)
            self.host.write(
                private / (self.bundle.commit + ".json"),
                json.dumps(context, indent=2).encode(),
                0o600,
            )
            return self._status(
                "verified_external",
                process_id=properties["MainPID"],
                snapshot_id=latest,
                origin="legacy_installation",
                managed_rollback=False,
                service_modified=False,
            )

    def _context(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        backup = Path(receipt["backup"])
        if backup.parent != self.paths.backups or not backup.name.startswith("megaraid-release-"):
            raise DeployError("Invalid recovery directory")
        self.host.protect(backup)
        path = backup / "recovery.json"
        self.host.protect(path)
        value = json.loads(read_file(path))
        if (
            value.get("commit") != self.bundle.commit
            or value.get("baseline") != self.bundle.baseline
        ):
            raise DeployError("Recovery identity differs")
        return dict(value)

    def _save_context(self, backup: Path, context: Mapping[str, Any]) -> None:
        self.host.write(backup / "recovery.json", json.dumps(context, indent=2).encode(), 0o600)

    def _backup(self, backup: Path) -> None:
        for source, name in (
            (self.paths.legacy, "app-config"),
            (self.paths.config, "rollout-config"),
            (self.paths.dropins, "systemd-dropins"),
        ):
            shutil.copytree(source, backup / name, symlinks=True)
        shutil.copy2(self.paths.unit, backup / "service.unit")
        with contextlib.closing(database(self.paths)) as source_connection:
            snapshot(source_connection)
            with contextlib.closing(sqlite3.connect(backup / "database.sqlite3")) as target:
                source_connection.backup(target)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise DeployError("Database backup integrity check failed")

    @staticmethod
    def _dropin_for(release: Path, environment: Path) -> bytes:
        python = release / ".venv/bin/uvicorn"
        return (
            "[Service]\nWorkingDirectory="
            + str(release)
            + "\nExecStart=\nExecStart="
            + str(python)
            + " --factory megaraid_dashboard.app:create_app --host 0.0.0.0"
            " --port 8090 --workers 1 --log-level info --no-access-log\nEnvironmentFile="
            + str(environment)
            + "\n"
        ).encode()

    def _dropin(self) -> bytes:
        return self._dropin_for(self.new, self.env)

    def _verify_collection(self, before: int, pid: str) -> int:
        deadline = self.host.now() + 420
        while True:
            with contextlib.closing(database(self.paths)) as connection:
                latest = snapshot(connection)
                journal = self.host.journal(pid) if latest > before else b""
                if latest > before and b"collector_run_completed" in journal:
                    verify_snapshot(connection, latest, journal)
                    return latest
            if self.host.now() >= deadline:
                raise DeployError("No normal collection from the new process was confirmed")
            self.host.sleep(5)

    def apply(self) -> dict[str, Any]:
        with deployment_lock(self.host):
            self._ensure_receipts()
            try:
                return self._apply_locked()
            except BaseException as error:
                if self.receipt.exists():
                    current = json.loads(read_file(self.receipt))
                    if current.get("state") == "preflight" and current.get("no_changes") is True:
                        self._status(
                            "refused",
                            no_changes=True,
                            failure_type=type(error).__name__,
                            failure_detail=str(error)
                            if isinstance(error, DeployError)
                            else "Inspect the preflight inputs",
                        )
                raise

    def _apply_locked(self) -> dict[str, Any]:
        initial = self.host.properties()
        installed = self._already_installed(initial)
        if installed is not None:
            return installed
        self._status("preflight", no_changes=True)
        self._identity(initial, self.old, self.bundle.baseline)
        before_env = self.host.environment(initial["MainPID"])
        required = {
            "GIT_SHA": self.bundle.baseline,
            "CONTROLLER_READ_ONLY": "false",
            "CONTROLLER_ACTION_CAPABILITY": "full-supported-actions",
            "MAINTENANCE_MODE": "false",
            "DESTRUCTIVE_MODE": "false",
            "METRICS_INTERVAL_SECONDS": "300",
        }
        if any(before_env.get(key) != value for key, value in required.items()):
            raise DeployError("Effective rollout profile differs from the reviewed baseline")
        for path in (self.new, self.env):
            self.host.protect(path.parent)
            if path.exists() or path.is_symlink():
                raise DeployError("Target already exists; inspect the earlier attempt")
        self.host.protect(self.paths.dropins)
        if any(Path(path).name > self.override.name for path in initial["DropInPaths"].split()):
            raise DeployError("Another drop-in would override the release configuration")
        if self.override.exists():
            self.host.protect(self.override)
        previous_override = read_file(self.override) if self.override.exists() else None
        previous_env = self.paths.config / ("ui-" + self.bundle.baseline[:7] + ".env")
        if previous_override is not None and previous_override != self._dropin_for(
            self.old, previous_env
        ):
            raise DeployError("Existing override is not a recognized application-only override")
        legacy = self._legacy()
        protected = self._protected(initial)
        settings = self.host.settings_digest(self.old, before_env)
        if not self.host.health():
            raise DeployError("Baseline health is not OK")
        with contextlib.closing(database(self.paths)) as connection:
            snapshot(connection)
        self.host.protect(self.paths.backups)
        backup = Path(tempfile.mkdtemp(prefix="megaraid-release-", dir=self.paths.backups))
        backup.chmod(0o700)
        context: dict[str, Any] = {
            "commit": self.bundle.commit,
            "baseline": self.bundle.baseline,
            "initial": initial,
            "legacy": legacy,
            "protected": protected,
            "settings_sha256": settings,
            "override_sha256": digest(self._dropin()),
            "env_sha256": digest(("GIT_SHA=" + self.bundle.commit + "\n").encode()),
            "had_override": previous_override is not None,
            "overrides_attempted": False,
            "restart_attempted": False,
        }
        if previous_override is not None:
            self.host.write(backup / "previous-override.conf", previous_override, 0o600)
            context["previous_override_sha256"] = digest(previous_override)
        self._save_context(backup, context)
        self.host.log = (backup / "deployment.log").open("x", encoding="utf-8")
        try:
            self._status("backing_up", backup=str(backup))
            self._backup(backup)
            self._preserved(context)
            self._status("installing_offline", backup=str(backup))
            old_mask = os.umask(0o022)
            try:
                self.host.install(self.bundle, self.new)
            finally:
                os.umask(old_mask)
            self._same_migrations()
            test_env = {
                **before_env,
                "GIT_SHA": self.bundle.commit,
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": "",
            }
            if self.host.settings_digest(self.new, test_env) != settings:
                raise DeployError("Candidate changes effective settings")
            if self.host.properties() != initial:
                raise DeployError("Service changed independently during preparation")
            with contextlib.closing(database(self.paths)) as connection:
                before = snapshot(connection)
            self._preserved(context)
            context["overrides_attempted"] = True
            self._save_context(backup, context)
            _uid, gid = self.host.account()
            self.host.write(self.env, ("GIT_SHA=" + self.bundle.commit + "\n").encode(), 0o640, gid)
            self.host.write(self.override, self._dropin())
            self.host.run(["/usr/bin/systemctl", "daemon-reload"])
            context["restart_attempted"] = True
            self._save_context(backup, context)
            self._status("activating", backup=str(backup))
            self.host.run(["/usr/bin/systemctl", "restart", SERVICE])
            self._wait_health()
            current = self.host.properties()
            self._identity(current, self.new, self.bundle.commit)
            if current["MainPID"] == initial["MainPID"]:
                raise DeployError("The old process is still running")
            expected_dropins = sorted(set(initial["DropInPaths"].split()) | {str(self.override)})
            if current["DropInPaths"].split() != expected_dropins:
                raise DeployError("Unexpected systemd drop-in set")
            active_env = self.host.environment(current["MainPID"])
            if active_env.get("GIT_SHA") != self.bundle.commit:
                raise DeployError("New process reports the wrong release")
            if self.host.settings_digest(self.new, active_env) != settings:
                raise DeployError("Effective settings were not preserved")
            self._preserved(context)
            self._own_files(context)
            self.host.https_check()
            self._status(
                "active_waiting_for_collector",
                backup=str(backup),
                process_id=current["MainPID"],
            )
            latest = self._verify_collection(before, current["MainPID"])
            if not self.host.health() or self.host.properties()["MainPID"] != current["MainPID"]:
                raise DeployError("New process became unhealthy")
            self._preserved(context)
            self._own_files(context)
            return self._status(
                "verified",
                backup=str(backup),
                process_id=current["MainPID"],
                snapshot_id=latest,
                settings_preserved=True,
                policy_unchanged=True,
                legacy_config_preserved=True,
            )
        except BaseException as error:
            self.host.log.write("FAILURE: " + type(error).__name__ + ": " + str(error) + "\n")
            self.host.log.flush()
            try:
                self._rollback(context, backup)
            except BaseException as rollback_error:
                self.host.log.write("ROLLBACK FAILURE: " + type(rollback_error).__name__ + "\n")
                self.host.log.flush()
                self._status("rollback_needs_operator", backup=str(backup))
            raise DeployError(
                "Deployment failed; inspect durable status and the private log"
            ) from None
        finally:
            self.host.log.close()
            self.host.log = None

    def _rollback(self, context: Mapping[str, Any], backup: Path) -> dict[str, Any]:
        self._preserved(context)
        current = self.host.properties()
        if current["WorkingDirectory"] not in {str(self.old), str(self.new)}:
            raise DeployError("A different release is active; rollback refused")
        self.host.protect(self.old / "RELEASE.json")
        if json.loads(read_file(self.old / "RELEASE.json")).get("commit") != self.bundle.baseline:
            raise DeployError("Rollback baseline differs")
        if context["overrides_attempted"]:
            if self.override.exists() or self.override.is_symlink():
                self.host.protect(self.override)
                current_hash = digest(read_file(self.override))
                allowed = {context["override_sha256"]}
                if context["had_override"]:
                    allowed.add(context["previous_override_sha256"])
                if current_hash not in allowed:
                    raise DeployError("Release override changed outside this deployment")
            elif context["had_override"]:
                raise DeployError("Previous release override disappeared")
            if self.env.exists() or self.env.is_symlink():
                self.host.protect(self.env)
                read_file(self.env, context["env_sha256"])
            if context["had_override"]:
                previous = read_file(
                    backup / "previous-override.conf", context["previous_override_sha256"]
                )
                self.host.write(self.override, previous)
            elif self.override.exists():
                self.override.unlink()
            if self.env.exists():
                self.env.unlink()
            self.host.run(["/usr/bin/systemctl", "daemon-reload"])
        if context["restart_attempted"]:
            self.host.run(["/usr/bin/systemctl", "restart", SERVICE])
            self._wait_health()
            restored = self.host.properties()
            self._identity(restored, self.old, self.bundle.baseline)
            if (
                self.host.settings_digest(self.old, self.host.environment(restored["MainPID"]))
                != context["settings_sha256"]
            ):
                raise DeployError("Rollback settings differ")
        self._preserved(context)
        return self._status(
            "rolled_back" if context["restart_attempted"] else "not_activated",
            backup=str(backup),
            database_restored=False,
        )

    def rollback(self) -> dict[str, Any]:
        with deployment_lock(self.host):
            self.host.protect(self.receipt)
            receipt = json.loads(read_file(self.receipt))
            if receipt.get("state") == "verified_external":
                raise DeployError(
                    "A legacy attestation has no managed rollback; use its original recovery record"
                )
            context = self._context(receipt)
            if receipt.get("state") == "rolled_back":
                self._preserved(context)
                self._identity(self.host.properties(), self.old, self.bundle.baseline)
                if not self.host.health():
                    raise DeployError("The restored baseline is unhealthy")
                return {**receipt, "state": "already_rolled_back"}
            return self._rollback(context, Path(receipt["backup"]))


def unit_name(commit: str) -> str:
    if not SHA.fullmatch(commit):
        raise DeployError("Invalid release identity")
    return "megaraid-dashboard-deploy-" + commit + ".service"


def verify_snapshot(connection: sqlite3.Connection, identifier: int, journal: bytes) -> None:
    """Retain the proven SERVER learn-date/compact-operation checks; issue no new probes."""
    if b"cachevault_next_learn_time_invalid" in journal:
        raise DeployError("Invalid Learn Cycle warning remains")
    learn = connection.execute(
        "SELECT next_learn_cycle FROM cv_snapshots WHERE snapshot_id=?", (identifier,)
    ).fetchone()
    if learn is None or learn[0] is None:
        raise DeployError("New sample has no Learn Cycle date")
    value = dt.datetime.fromisoformat(learn[0])
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    if (
        b"cachevault_next_learn_time_recovered" in journal
        and value.astimezone(dt.UTC).isoformat().encode() not in journal
    ):
        raise DeployError("Learn date differs from the recovery evidence")
    row = connection.execute(
        "SELECT raw_json FROM controller_snapshots WHERE id=?", (identifier,)
    ).fetchone()
    raw = json.loads(row[0]) if row and row[0] else {}
    patrol = raw.get("patrol_read") or {}
    interval = patrol.get("interval_hours")
    reported = patrol.get("next_start_timestamp")
    if type(interval) is not int or interval <= 0 or not isinstance(reported, str):
        raise DeployError("New sample has no validated Patrol schedule")
    dt.datetime.strptime(reported, "%m/%d/%Y, %H:%M:%S")
    foreign = raw.get("foreign_config_state")
    if foreign is not None and (
        not isinstance(foreign, dict) or not isinstance(foreign.get("present"), bool)
    ):
        raise DeployError("Invalid compact foreign observation")


def status(host: Host, commit: str) -> dict[str, Any]:
    path = host.paths.receipt(commit)
    receipt: dict[str, Any] = {"state": "not_recorded", "commit": commit}
    if path.exists():
        host.protect(path)
        receipt = json.loads(read_file(path))
        if receipt.get("commit") != commit:
            raise DeployError("Receipt identity differs")
    properties = host.properties()
    healthy = host.health()
    current = properties.get("WorkingDirectory") == str(host.paths.release(commit))
    effective = receipt["state"]
    if effective in {"verified", "verified_external"} and not (current and healthy):
        effective = "not_current_or_unhealthy"
    if effective not in TERMINAL and effective != "not_current_or_unhealthy":
        try:
            state = host.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    unit_name(commit),
                    "--property=ActiveState",
                    "--value",
                ]
            ).strip()
        except DeployError:
            state = b"inactive"
        effective = (
            "running"
            if state in {b"active", b"activating"}
            else (
                "not_recorded"
                if receipt["state"] == "not_recorded"
                else "interrupted_needs_operator"
            )
        )
    return {
        "state": effective,
        "receipt": receipt,
        "current_release": current,
        "service": properties,
        "health_ok": healthy,
    }


def start(
    host: Host, bundle: Bundle, directory: Path, manifest_hash: str, tool: Path
) -> dict[str, Any]:
    host.protect(tool)
    deployment = Deployment(host, bundle)
    if deployment.receipt.exists():
        current_status = status(host, bundle.commit)
        if current_status["state"] == "running":
            return current_status
        installed = deployment._already_installed(host.properties())
        if installed is not None:
            return installed
    elif host.properties().get("WorkingDirectory") == str(deployment.new):
        raise DeployError(
            "An unrecorded target is already active; use explicit adopt instead of restarting"
        )
    host.run(
        [
            "/usr/bin/systemd-run",
            "--unit=" + unit_name(bundle.commit).removesuffix(".service"),
            "--collect",
            "--property=Type=exec",
            "/usr/bin/python3",
            "-I",
            str(tool),
            "apply",
            "--bundle",
            str(directory.absolute()),
            "--manifest-sha256",
            manifest_hash,
        ]
    )
    return {
        "state": "queued",
        "commit": bundle.commit,
        "unit": unit_name(bundle.commit),
        "status": str(deployment.receipt),
        "controller_writes_executed": False,
    }


def make_bundle(
    source: Path, lock: Path, wheelhouse: Path, output: Path, commit: str, baseline: str
) -> dict[str, str]:
    if not SHA.fullmatch(commit) or not SHA.fullmatch(baseline) or commit == baseline:
        raise DeployError("Distinct full candidate/baseline SHAs are required")
    # Build in a sibling temporary directory; publish only after all checks pass.
    if output.exists() or output.is_symlink():
        raise DeployError("Bundle output already exists")
    temporary = Path(tempfile.mkdtemp(prefix=".release-bundle-", dir=output.parent))
    try:
        (temporary / "wheels").mkdir()
        (temporary / "source.tar").write_bytes(read_file(source))
        (temporary / "runtime-lock.txt").write_bytes(read_file(lock))
        wheels = {}
        for path in sorted(wheelhouse.glob("*.whl")):
            content = read_file(path)
            (temporary / "wheels" / path.name).write_bytes(content)
            wheels[path.name] = digest(content)
        manifest = {
            "version": 1,
            "commit": commit,
            "baseline": baseline,
            "source_sha256": digest(read_file(temporary / "source.tar")),
            "lock_sha256": digest(read_file(temporary / "runtime-lock.txt")),
            "wheels": wheels,
        }
        data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        (temporary / "release.json").write_bytes(data)
        manifest_hash = digest(data)
        Bundle.load(temporary, manifest_hash)
        temporary.rename(output)
        return {"manifest_sha256": manifest_hash, "bundle": str(output)}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "start", "apply", "adopt"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", required=True, type=Path)
        command.add_argument("--manifest-sha256", required=True)
    for name in ("status", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--commit", required=True)
    command = commands.add_parser("bundle")
    for name in ("source", "lock", "wheelhouse", "output"):
        command.add_argument("--" + name, required=True, type=Path)
    command.add_argument("--commit", required=True)
    command.add_argument("--baseline", required=True)
    args = parser.parse_args(argv)
    host = Host(Paths())
    previous_mask: int | None = None
    try:
        if args.command in {"start", "apply", "rollback", "adopt"}:
            if os.geteuid() != 0:
                raise DeployError("This action requires sudo")
            host.protect(Path(__file__).absolute())
            previous_mask = os.umask(0o077)
        if args.command == "bundle":
            result: dict[str, Any] = make_bundle(
                args.source, args.lock, args.wheelhouse, args.output, args.commit, args.baseline
            )
        elif args.command == "status":
            result = status(host, args.commit)
        elif args.command == "rollback":
            path = host.paths.receipt(args.commit)
            host.protect(path)
            receipt = json.loads(read_file(path))
            if (
                receipt.get("commit") != args.commit
                or not SHA.fullmatch(receipt["baseline"])
                or not DIGEST.fullmatch(receipt["source_sha256"])
            ):
                raise DeployError("Invalid recovery receipt")
            bundle = Bundle(args.commit, receipt["baseline"], {}, receipt["source_sha256"])
            result = Deployment(host, bundle).rollback()
        else:
            bundle = Bundle.load(args.bundle, args.manifest_sha256)
            if args.command == "plan":
                result = {
                    "state": "plan_verified",
                    "commit": bundle.commit,
                    "baseline": bundle.baseline,
                    "offline_install": True,
                    "controller_writes_executed": False,
                    "service_modified": False,
                }
            elif args.command == "start":
                result = start(
                    host, bundle, args.bundle, args.manifest_sha256, Path(__file__).absolute()
                )
            elif args.command == "adopt":
                result = Deployment(host, bundle).adopt()
            else:
                result = Deployment(host, bundle).apply()
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        DeployError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        # Never dump raw subprocess output, environment, provider credentials or a traceback.
        sys.stderr.write(
            json.dumps(
                {
                    "state": "refused_or_failed",
                    "error": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, DeployError)
                    else "Inspect inputs or the private deployment log",
                }
            )
            + "\n"
        )
        return 1
    finally:
        if previous_mask is not None:
            os.umask(previous_mask)


if __name__ == "__main__":
    raise SystemExit(main())
