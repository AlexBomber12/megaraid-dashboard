"""Root-controlled, evidence-reviewed exact command contract for the final rollout."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

PINNED_BINARY = "/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64"
PINNED_SHA256 = "642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547"


class ApprovedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str
    evidence: str = Field(min_length=1)


class ReplacementTopology(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: int = Field(ge=0, le=7)
    dg: int = Field(ge=0, le=63)
    array: int = Field(ge=0, le=63)
    row: int = Field(ge=0, le=255)


class CommandPolicy(BaseModel):
    """No inferred topology or command support; an operator reviews the evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)
    service_user: str = "megaraid"
    controller_serial: str = Field(min_length=1)
    virtual_drive: int = Field(ge=0, le=63)
    disk_group: int = Field(ge=0, le=63)
    replacements: list[ReplacementTopology]
    commands: list[ApprovedCommand] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_commands(self) -> CommandPolicy:
        # Import lazily: runner also uses this policy at the subprocess boundary.
        from megaraid_dashboard.storcli.exceptions import StorcliCommandFailed
        from megaraid_dashboard.storcli.runner import _validate_command

        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", self.service_user):
            raise ValueError("Invalid service account")
        topologies = {item.slot: item for item in self.replacements}
        if len(topologies) != len(self.replacements):
            raise ValueError("Duplicate replacement slot")
        seen: set[str] = set()
        for item in self.commands:
            tokens = item.command.split(" ")
            try:
                _validate_command(tokens)
            except StorcliCommandFailed as exc:
                raise ValueError("Unsupported command; no sudo grant may be generated") from exc
            if item.command in seen or tokens[-1] != "J" or "show" in tokens:
                raise ValueError("Expected unique JSON write commands")
            seen.add(item.command)
            drive = re.fullmatch(r"/c0/e252/s([0-7])", tokens[0])
            if tokens[0].startswith("/c0/e") and drive is None:
                raise ValueError("Only approved enclosure 252 slots 0-7")
            if tokens[0].startswith("/c0/v") and tokens[0] not in {
                "/c0/vall",
                f"/c0/v{self.virtual_drive}",
            }:
                raise ValueError("Wrong virtual drive")
            if "add" in tokens and f"dgs={self.disk_group}" not in tokens:
                raise ValueError("Wrong disk group")
            if "insert" in tokens:
                topology = topologies.get(int(tokens[0].rsplit("s", 1)[1]))
                if (
                    topology is None
                    or tokens[2:5]
                    != [f"dg={topology.dg}", f"array={topology.array}", f"row={topology.row}"]
                    or topology.dg != self.disk_group
                ):
                    raise ValueError("Replacement requires explicitly approved topology")
        return self

    def permits(self, args: list[str]) -> bool:
        command = " ".join(args if args[-1:] == ["J"] else [*args, "J"])
        return command in {item.command for item in self.commands}

    def sudoers(self, reads: tuple[str, ...]) -> str:
        commands = dict.fromkeys((*reads, *(item.command for item in self.commands)))
        body = ", \\\n  ".join(f"{PINNED_BINARY} {command}" for command in commands)
        return (
            f"Cmnd_Alias MEGARAID_APPROVED = {body}\n"
            f"{self.service_user} ALL=(root) NOPASSWD: MEGARAID_APPROVED\n"
        )


def protected_path(path: Path) -> None:
    """Reject links, non-root ownership and group/other writable ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Use an absolute canonical policy path")
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError(f"Path must be root-controlled without symlinks: {component}")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected a regular file")


def load_policy(path: str) -> CommandPolicy:
    protected_path(Path(path))
    return CommandPolicy.model_validate_json(Path(path).read_bytes())


def verify_pinned_binary(binary: str) -> None:
    if binary != PINNED_BINARY:
        raise ValueError("Full actions require the pinned StorCLI 1.23.02 path")
    protected_path(Path(binary))
    if hashlib.sha256(Path(binary).read_bytes()).hexdigest() != PINNED_SHA256:
        raise ValueError("Pinned StorCLI SHA-256 mismatch")


def action_commands(policy: CommandPolicy, path: str) -> list[str]:
    """Map real route paths to approved command candidates, including mode alternatives."""
    suffixes = {
        "/controller/buzzer/silence": ["/c0 set alarm=silence J"],
        "/controller/buzzer/disable": ["/c0 set alarm=off J"],
        "/controller/buzzer/enable": ["/c0 set alarm=on J"],
        "/controller/patrol-read/start": ["/c0 start patrolread J"],
        "/controller/patrol-read/stop": ["/c0 stop patrolread J"],
        "/controller/patrol-read/mode": [
            "/c0 set patrolread=on mode=auto J",
            "/c0 set patrolread=on mode=manual J",
            "/c0 set patrolread=off J",
        ],
        "/controller/consistency-check/start": [
            "/c0/vall start cc J",
            f"/c0/v{policy.virtual_drive} start cc J",
        ],
        "/controller/consistency-check/stop": ["/c0/vall stop cc J"],
        "/controller/consistency-check/mode": ["/c0 set cc=conc J", "/c0 set cc=seq J"],
        "/controller/foreign-config/import": ["/c0/fall import J"],
        "/controller/foreign-config/clear": ["/c0/fall delete J"],
    }
    for suffix, commands in suffixes.items():
        if path.endswith(suffix):
            return commands
    match = re.search(r"/drives/252:([0-7])/(.+)$", path)
    if match is None:
        return []
    slot, action = match.groups()
    drive = f"/c0/e252/s{slot}"
    tails = {
        "locate/start": "start locate J",
        "locate/stop": "stop locate J",
        "actions/mark-ugood": "set good J",
        "actions/spindown": "spindown J",
        "actions/hotspare": f"add hotsparedrive dgs={policy.disk_group} J",
        "replace/offline": "set offline J",
        "replace/missing": "set missing J",
    }
    if action == "replace/insert":
        topology = next((item for item in policy.replacements if item.slot == int(slot)), None)
        if topology is None:
            return []
        return [f"{drive} insert dg={topology.dg} array={topology.array} row={topology.row} J"]
    return [f"{drive} {tails[action]}"] if action in tails else []


def action_refusal(
    policy: CommandPolicy, path: str, selected: list[str] | None = None
) -> str | None:
    commands = action_commands(policy, path)
    if selected is not None:
        command = " ".join(selected if selected[-1:] == ["J"] else [*selected, "J"])
        commands = [candidate for candidate in commands if candidate == command]
    if not any(policy.permits(command.split()) for command in commands):
        return (
            "Unavailable: exact StorCLI 1.23.02 syntax or topology is not approved. "
            "Ask the administrator to review read-only help and regenerate the exact sudo policy."
        )
    return None


def replacement_refusal(policy: CommandPolicy, path: str) -> str | None:
    """Approve the entire combined workflow before it can take a drive offline."""
    base = path.rsplit("/", 1)[0]
    for step in ("offline", "missing", "insert"):
        if action_refusal(policy, f"{base}/{step}") is not None:
            return (
                f"Replacement unavailable: {step.title()} command or topology is not approved. "
                "Ask the administrator to approve every replacement step "
                "before taking a drive offline."
            )
    return None


def replacement_command_refusal(policy: CommandPolicy, args: list[str]) -> str | None:
    """Recheck the complete contract at the wrapper boundary, even after policy edits."""
    if args[1:3] in (["set", "offline"], ["set", "missing"]) or args[1] == "insert":
        slot = args[0].removeprefix("/c0/e").replace("/s", ":")
        return replacement_refusal(policy, f"/drives/{slot}/replace/step")
    return None
