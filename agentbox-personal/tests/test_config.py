from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentbox.config import InstanceRecord, load_state, save_state


class StateTests(unittest.TestCase):
    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            original = {
                "devbox": InstanceRecord(
                    name="devbox",
                    instance_id="ocid1.instance.test",
                    public_ip="203.0.113.9",
                    created_at="2026-07-31T00:00:00+00:00",
                )
            }
            save_state(path, original)
            loaded = load_state(path)
            self.assertEqual(loaded["devbox"].instance_id, "ocid1.instance.test")
            self.assertEqual(loaded["devbox"].public_ip, "203.0.113.9")


if __name__ == "__main__":
    unittest.main()
