from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cloud_init import CLOUD_INIT
from .config import (
    AgentboxConfig,
    InstanceRecord,
    load_state,
    save_state,
    validate_machine_name,
)
from .errors import AgentboxError
from .runner import Runner


class OciClient:
    def __init__(self, config: AgentboxConfig, runner: Runner) -> None:
        self.config = config
        self.runner = runner

    def _base(self) -> list[str]:
        return ["oci", "--profile", self.config.oracle.profile]

    def _full(self, args: list[str]) -> list[str]:
        return [*self._base(), *args, "--output", "json"]

    def _json(self, args: list[str]) -> Any:
        result = self.runner.run(self._full(args))
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentboxError(
                f"OCI returned invalid JSON: {result.stdout[:500]}"
            ) from exc

    def validate_free_tier(self) -> None:
        oracle = self.config.oracle
        failures: list[str] = []
        if oracle.shape != "VM.Standard.A1.Flex":
            failures.append("shape must be VM.Standard.A1.Flex")
        if oracle.ocpus <= 0 or oracle.ocpus > 2:
            failures.append("ocpus must be greater than 0 and no more than 2")
        if oracle.memory_gb <= 0 or oracle.memory_gb > 12:
            failures.append("memory_gb must be greater than 0 and no more than 12")
        if oracle.boot_volume_gb < 50 or oracle.boot_volume_gb > 200:
            failures.append("boot_volume_gb must be between 50 and 200")
        if failures:
            raise AgentboxError(
                "Free-tier guard rejected config:\n"
                + "\n".join(f"- {item}" for item in failures)
            )

    def discover_image(self) -> dict[str, str]:
        payload = self._json(
            [
                "compute",
                "image",
                "list",
                "--compartment-id",
                self.config.oracle.compartment_id,
                "--shape",
                self.config.oracle.shape,
                "--operating-system",
                "Canonical Ubuntu",
                "--lifecycle-state",
                "AVAILABLE",
                "--sort-by",
                "TIMECREATED",
                "--sort-order",
                "DESC",
                "--all",
            ]
        )
        images = payload.get("data", [])
        if not images:
            raise AgentboxError(
                "No available Canonical Ubuntu image compatible with "
                f"{self.config.oracle.shape} was found"
            )
        image = images[0]
        return {
            "id": str(image.get("id", "")),
            "display_name": str(image.get("display-name", "")),
            "operating_system": str(image.get("operating-system", "")),
            "operating_system_version": str(
                image.get("operating-system-version", "")
            ),
            "time_created": str(image.get("time-created", "")),
        }

    def resolve_image_id(self) -> str:
        if self.config.oracle.image_id:
            return self.config.oracle.image_id
        image = self.discover_image()
        if not image["id"]:
            raise AgentboxError("Image discovery response did not contain an id")
        return image["id"]

    def create_command(
        self,
        name: str,
        *,
        image_id: str,
        cloud_init_path: Path,
    ) -> list[str]:
        validate_machine_name(name)
        shape_config = json.dumps(
            {
                "ocpus": self.config.oracle.ocpus,
                "memoryInGBs": self.config.oracle.memory_gb,
            },
            separators=(",", ":"),
        )
        tags = json.dumps(
            {
                "agentbox-managed": "true",
                "agentbox-name": name,
                "agentbox-version": "0.2.0",
            },
            separators=(",", ":"),
        )
        return self._full(
            [
                "compute",
                "instance",
                "launch",
                "--availability-domain",
                self.config.oracle.availability_domain,
                "--compartment-id",
                self.config.oracle.compartment_id,
                "--subnet-id",
                self.config.oracle.subnet_id,
                "--image-id",
                image_id,
                "--shape",
                self.config.oracle.shape,
                "--shape-config",
                shape_config,
                "--boot-volume-size-in-gbs",
                str(self.config.oracle.boot_volume_gb),
                "--assign-public-ip",
                "true",
                "--display-name",
                name,
                "--ssh-authorized-keys-file",
                str(self.config.ssh.public_key),
                "--user-data-file",
                str(cloud_init_path),
                "--freeform-tags",
                tags,
                "--wait-for-state",
                "RUNNING",
            ]
        )

    def preview_create(self, name: str) -> dict[str, object]:
        self.validate_free_tier()
        image_id = self.config.oracle.image_id or "<auto-discovered-ubuntu-arm64-image>"
        command = self.create_command(
            name,
            image_id=image_id,
            cloud_init_path=Path("<temporary-cloud-init.yaml>"),
        )
        return {
            "command": self.runner.format_command(command),
            "shape": self.config.oracle.shape,
            "ocpus": self.config.oracle.ocpus,
            "memory_gb": self.config.oracle.memory_gb,
            "boot_volume_gb": self.config.oracle.boot_volume_gb,
            "image_id": image_id,
            "modifies_cloud": False,
        }

    def create(self, name: str) -> InstanceRecord:
        self.validate_free_tier()
        validate_machine_name(name)
        records = load_state(self.config.state_file)
        if name in records:
            raise AgentboxError(f"Machine already exists in local state: {name}")
        if not self.config.ssh.public_key.exists():
            raise AgentboxError(
                f"SSH public key not found: {self.config.ssh.public_key}\n"
                f"Create it with: ssh-keygen -t ed25519 -f {self.config.ssh.private_key}"
            )
        if not self.config.ssh.private_key.exists():
            raise AgentboxError(
                f"SSH private key not found: {self.config.ssh.private_key}"
            )
        image_id = self.resolve_image_id()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="-agentbox-cloud-init.yaml", encoding="utf-8"
        ) as cloud_init:
            cloud_init.write(CLOUD_INIT)
            cloud_init.flush()
            result = self.runner.run(
                self.create_command(
                    name,
                    image_id=image_id,
                    cloud_init_path=Path(cloud_init.name),
                )
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentboxError(
                f"OCI returned invalid JSON: {result.stdout[:500]}"
            ) from exc
        data = payload.get("data", payload)
        instance_id = str(data.get("id", ""))
        if not instance_id:
            raise AgentboxError("OCI launch response did not contain an instance id")
        public_ip = self.public_ip(instance_id)
        record = InstanceRecord(
            name=name,
            instance_id=instance_id,
            public_ip=public_ip,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        records[name] = record
        save_state(self.config.state_file, records)
        return record

    def record(self, name: str) -> InstanceRecord:
        records = load_state(self.config.state_file)
        try:
            return records[name]
        except KeyError as exc:
            raise AgentboxError(f"Unknown machine: {name}") from exc

    def public_ip(self, instance_id: str) -> str:
        payload = self._json(
            [
                "compute",
                "instance",
                "list-vnics",
                "--instance-id",
                instance_id,
                "--compartment-id",
                self.config.oracle.compartment_id,
            ]
        )
        vnics = payload.get("data", [])
        if not vnics:
            return ""
        return str(vnics[0].get("public-ip") or "")

    def status(self, name: str) -> dict[str, str]:
        record = self.record(name)
        payload = self._json(
            ["compute", "instance", "get", "--instance-id", record.instance_id]
        )
        data = payload.get("data", {})
        public_ip = self.public_ip(record.instance_id) or record.public_ip
        if public_ip and public_ip != record.public_ip:
            records = load_state(self.config.state_file)
            records[name].public_ip = public_ip
            save_state(self.config.state_file, records)
        shape_config = data.get("shape-config") or {}
        return {
            "name": name,
            "state": str(data.get("lifecycle-state", "UNKNOWN")),
            "shape": str(data.get("shape", "")),
            "ocpus": str(shape_config.get("ocpus", "")),
            "memory_gb": str(shape_config.get("memory-in-gbs", "")),
            "public_ip": public_ip,
            "instance_id": record.instance_id,
        }

    def list(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for name in sorted(load_state(self.config.state_file)):
            try:
                rows.append(self.status(name))
            except AgentboxError as exc:
                rows.append(
                    {"name": name, "state": f"ERROR: {exc}", "public_ip": ""}
                )
        return rows

    def action_command(self, name: str, action: str) -> list[str]:
        record = self.record(name)
        action = action.upper()
        wait_state = {"START": "RUNNING", "STOP": "STOPPED"}.get(action)
        args = [
            "compute",
            "instance",
            "action",
            "--instance-id",
            record.instance_id,
            "--action",
            action,
        ]
        if wait_state:
            args.extend(["--wait-for-state", wait_state])
        return self._full(args)

    def action(self, name: str, action: str, *, dry_run: bool = False) -> dict[str, str]:
        command = self.action_command(name, action)
        if dry_run:
            return {
                "name": name,
                "action": action.upper(),
                "command": self.runner.format_command(command),
                "modifies_cloud": False,
            }
        self.runner.run(command)
        return self.status(name)

    def delete_command(self, name: str) -> list[str]:
        record = self.record(name)
        return [
            *self._base(),
            "compute",
            "instance",
            "terminate",
            "--instance-id",
            record.instance_id,
            "--preserve-boot-volume",
            "false",
            "--preserve-data-volumes-created-at-launch",
            "false",
            "--force",
        ]

    def delete(self, name: str, *, dry_run: bool = False) -> dict[str, str]:
        command = self.delete_command(name)
        if dry_run:
            return {
                "name": name,
                "command": self.runner.format_command(command),
                "modifies_cloud": False,
            }
        self.runner.run(command)
        records = load_state(self.config.state_file)
        records.pop(name, None)
        save_state(self.config.state_file, records)
        return {"deleted": name}

    def setup_commands(self) -> list[str]:
        profile = self.config.oracle.profile
        compartment = self.config.oracle.compartment_id
        return [
            f"oci --profile {profile} iam availability-domain list --compartment-id {compartment} --output table",
            f"oci --profile {profile} network subnet list --compartment-id {compartment} --all --output table",
            f"oci --profile {profile} compute image list --compartment-id {compartment} --shape VM.Standard.A1.Flex --operating-system 'Canonical Ubuntu' --sort-by TIMECREATED --sort-order DESC --all --output table",
        ]
