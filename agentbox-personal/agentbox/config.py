from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import AgentboxError


DEFAULT_CONFIG_PATH = Path.home() / ".agentbox" / "config.toml"
DEFAULT_STATE_PATH = Path.home() / ".agentbox" / "state.json"
MACHINE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class OracleConfig:
    profile: str
    compartment_id: str
    availability_domain: str
    subnet_id: str
    image_id: str
    shape: str
    ocpus: float
    memory_gb: float
    boot_volume_gb: int


@dataclass(frozen=True)
class SshConfig:
    user: str
    private_key: Path
    public_key: Path


@dataclass(frozen=True)
class AgentboxConfig:
    oracle: OracleConfig
    ssh: SshConfig
    workspace: str
    state_file: Path


@dataclass
class InstanceRecord:
    name: str
    instance_id: str
    public_ip: str
    created_at: str


CONFIG_TEMPLATE = """# Agentbox never stores your Oracle API private key.
# OCI CLI reads it from ~/.oci/config using profile below.

[oracle]
profile = "DEFAULT"
compartment_id = "PASTE_COMPARTMENT_OCID"
availability_domain = "PASTE_AVAILABILITY_DOMAIN"
subnet_id = "PASTE_PUBLIC_SUBNET_OCID"
# Leave blank to discover the newest Canonical Ubuntu image compatible with A1.
image_id = ""
shape = "VM.Standard.A1.Flex"
ocpus = 2
memory_gb = 12
boot_volume_gb = 100

[ssh]
user = "ubuntu"
private_key = "~/.ssh/agentbox_ed25519"
public_key = "~/.ssh/agentbox_ed25519.pub"

[agentbox]
workspace = "/workspace"
state_file = "~/.agentbox/state.json"
"""


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def validate_machine_name(name: str) -> None:
    if not MACHINE_NAME_RE.fullmatch(name):
        raise AgentboxError(
            "Machine name must start with a lowercase letter and contain only "
            "lowercase letters, digits, or hyphens; maximum length is 32"
        )


def write_template(path: Path = DEFAULT_CONFIG_PATH, *, overwrite: bool = False) -> Path:
    path = _expand_path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise AgentboxError(f"Config already exists: {path}")
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AgentboxConfig:
    path = _expand_path(str(path))
    if not path.exists():
        raise AgentboxError(f"Config not found: {path}\nRun: agentbox init-config")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        oracle = data["oracle"]
        ssh = data["ssh"]
        agentbox = data["agentbox"]
        config = AgentboxConfig(
            oracle=OracleConfig(
                profile=str(oracle.get("profile", "DEFAULT")),
                compartment_id=str(oracle["compartment_id"]),
                availability_domain=str(oracle["availability_domain"]),
                subnet_id=str(oracle["subnet_id"]),
                image_id=str(oracle.get("image_id", "")).strip(),
                shape=str(oracle.get("shape", "VM.Standard.A1.Flex")),
                ocpus=float(oracle.get("ocpus", 2)),
                memory_gb=float(oracle.get("memory_gb", 12)),
                boot_volume_gb=int(oracle.get("boot_volume_gb", 100)),
            ),
            ssh=SshConfig(
                user=str(ssh.get("user", "ubuntu")),
                private_key=_expand_path(str(ssh["private_key"])),
                public_key=_expand_path(str(ssh["public_key"])),
            ),
            workspace=str(agentbox.get("workspace", "/workspace")).rstrip("/"),
            state_file=_expand_path(str(agentbox.get("state_file", DEFAULT_STATE_PATH))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AgentboxError(f"Invalid config file {path}: {exc}") from exc
    validate_placeholders(config)
    return config


def validate_placeholders(config: AgentboxConfig) -> None:
    required = {
        "compartment_id": config.oracle.compartment_id,
        "availability_domain": config.oracle.availability_domain,
        "subnet_id": config.oracle.subnet_id,
    }
    bad = [name for name, value in required.items() if not value or "PASTE_" in value]
    if bad:
        raise AgentboxError(f"Complete these config values first: {', '.join(bad)}")
    if not config.workspace.startswith("/") or config.workspace == "/":
        raise AgentboxError("workspace must be an absolute directory other than /")


def load_state(path: Path) -> dict[str, InstanceRecord]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records: dict[str, InstanceRecord] = {}
        for name, value in raw.get("instances", {}).items():
            records[name] = InstanceRecord(
                name=name,
                instance_id=str(value["instance_id"]),
                public_ip=str(value.get("public_ip", "")),
                created_at=str(value.get("created_at", "")),
            )
        return records
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AgentboxError(f"Invalid state file {path}: {exc}") from exc


def save_state(path: Path, records: dict[str, InstanceRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "instances": {name: asdict(record) for name, record in sorted(records.items())}
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
