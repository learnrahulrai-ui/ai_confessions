from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentbox.config import AgentboxConfig, OracleConfig, SshConfig
from agentbox.errors import AgentboxError
from agentbox.oci import OciClient
from agentbox.runner import Runner


class FreeTierGuardTests(unittest.TestCase):
    def config(self, *, shape: str = "VM.Standard.A1.Flex", ocpus: float = 2, memory: float = 12, disk: int = 100) -> AgentboxConfig:
        temporary = Path(tempfile.gettempdir())
        return AgentboxConfig(
            oracle=OracleConfig(
                profile="DEFAULT",
                compartment_id="ocid1.compartment.test",
                availability_domain="TEST:AD-1",
                subnet_id="ocid1.subnet.test",
                image_id="ocid1.image.test",
                shape=shape,
                ocpus=ocpus,
                memory_gb=memory,
                boot_volume_gb=disk,
            ),
            ssh=SshConfig(
                user="ubuntu",
                private_key=temporary / "key",
                public_key=temporary / "key.pub",
            ),
            workspace="/workspace",
            state_file=temporary / "agentbox-state-test.json",
        )

    def test_accepts_safe_limits(self) -> None:
        OciClient(self.config(), Runner()).validate_free_tier()

    def test_rejects_paid_shape(self) -> None:
        with self.assertRaises(AgentboxError):
            OciClient(self.config(shape="VM.Standard3.Flex"), Runner()).validate_free_tier()

    def test_rejects_excess_memory(self) -> None:
        with self.assertRaises(AgentboxError):
            OciClient(self.config(memory=13), Runner()).validate_free_tier()


if __name__ == "__main__":
    unittest.main()
