from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentbox.config import AgentboxConfig, InstanceRecord, OracleConfig, SshConfig, save_state
from agentbox.oci import OciClient
from agentbox.remote import RemoteClient
from agentbox.runner import CommandResult, Runner


class FakeRunner(Runner):
    def __init__(self, outputs: list[str] | None = None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[tuple[str, ...]] = []

    def which(self, executable: str) -> str | None:
        return f"/usr/bin/{executable}"

    def run(self, args, **kwargs):  # type: ignore[override]
        call = tuple(str(value) for value in args)
        self.calls.append(call)
        output = self.outputs.pop(0) if self.outputs else ""
        return CommandResult(call, output, "", 0)


def make_config(directory: Path, *, image_id: str = "ocid1.image.test") -> AgentboxConfig:
    private_key = directory / "agentbox"
    public_key = directory / "agentbox.pub"
    private_key.write_text("PRIVATE TEST PLACEHOLDER", encoding="utf-8")
    public_key.write_text("ssh-ed25519 AAAATEST test", encoding="utf-8")
    return AgentboxConfig(
        oracle=OracleConfig(
            profile="DEFAULT",
            compartment_id="ocid1.compartment.test",
            availability_domain="TEST:AD-1",
            subnet_id="ocid1.subnet.test",
            image_id=image_id,
            shape="VM.Standard.A1.Flex",
            ocpus=2,
            memory_gb=12,
            boot_volume_gb=100,
        ),
        ssh=SshConfig(
            user="ubuntu",
            private_key=private_key,
            public_key=public_key,
        ),
        workspace="/workspace",
        state_file=directory / "state.json",
    )


class OciCommandTests(unittest.TestCase):
    def test_create_command_uses_safe_launch_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_config(root)
            client = OciClient(config, FakeRunner())
            command = client.create_command(
                "devbox",
                image_id="ocid1.image.test",
                cloud_init_path=root / "cloud-init.yaml",
            )
            joined = " ".join(command)
            self.assertIn("compute instance launch", joined)
            self.assertIn("--shape VM.Standard.A1.Flex", joined)
            self.assertIn("--ssh-authorized-keys-file", command)
            self.assertIn("--user-data-file", command)
            self.assertIn("--wait-for-state RUNNING", joined)
            self.assertNotIn("--metadata", command)

    def test_delete_does_not_use_invalid_instance_wait_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_config(root)
            save_state(
                config.state_file,
                {
                    "devbox": InstanceRecord(
                        "devbox", "ocid1.instance.test", "203.0.113.8", "time"
                    )
                },
            )
            command = OciClient(config, FakeRunner()).delete_command("devbox")
            self.assertIn("terminate", command)
            self.assertIn("--preserve-boot-volume", command)
            self.assertNotIn("--wait-for-state", command)

    def test_discover_image_chooses_first_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = json.dumps(
                {
                    "data": [
                        {
                            "id": "ocid1.image.newest",
                            "display-name": "Canonical-Ubuntu-test-aarch64",
                            "operating-system": "Canonical Ubuntu",
                            "operating-system-version": "24.04",
                            "time-created": "2026-07-31T00:00:00Z",
                        }
                    ]
                }
            )
            runner = FakeRunner([payload])
            image = OciClient(make_config(root, image_id=""), runner).discover_image()
            self.assertEqual(image["id"], "ocid1.image.newest")
            joined = " ".join(runner.calls[0])
            self.assertIn("--shape VM.Standard.A1.Flex", joined)
            self.assertIn("--operating-system Canonical Ubuntu", joined)

    def test_action_waits_for_correct_lifecycle_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_config(root)
            save_state(
                config.state_file,
                {
                    "devbox": InstanceRecord(
                        "devbox", "ocid1.instance.test", "203.0.113.8", "time"
                    )
                },
            )
            command = OciClient(config, FakeRunner()).action_command("devbox", "STOP")
            joined = " ".join(command)
            self.assertIn("--action STOP", joined)
            self.assertIn("--wait-for-state STOPPED", joined)


class RemoteCommandTests(unittest.TestCase):
    def test_upload_dry_run_keeps_git_and_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / ".git").mkdir()
            config = make_config(root)
            runner = FakeRunner()
            remote = RemoteClient(config, runner)
            record = InstanceRecord("devbox", "id", "203.0.113.8", "time")
            command, path = remote.upload_command(record, project, dry_run=True)
            self.assertEqual(path, "/workspace/project")
            self.assertIn("--dry-run", command)
            ignore_path = Path(command[command.index("--exclude-from") + 1])
            ignores = ignore_path.read_text(encoding="utf-8")
            self.assertIn(".env", ignores)
            self.assertNotIn(".git\n", ignores)

    def test_download_command_uses_separate_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_config(root)
            remote = RemoteClient(config, FakeRunner())
            record = InstanceRecord("devbox", "id", "203.0.113.8", "time")
            target = root / "result"
            command = remote.download_command(record, "project", target, dry_run=True)
            self.assertIn("--dry-run", command)
            self.assertEqual(command[-1], str(target) + "/")
            self.assertIn("/workspace/project/", command[-2])

    def test_codex_command_is_detached_and_writes_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task = root / "TASK.txt"
            task.write_text("Run tests", encoding="utf-8")
            remote = RemoteClient(make_config(root), FakeRunner())
            command = remote.codex_remote_command(
                "/workspace/project", task, "codex-123"
            )
            self.assertIn("tmux new-session -d", command)
            self.assertIn("run_codex.sh", command)
            self.assertIn("TASK.txt", command)


if __name__ == "__main__":
    unittest.main()
