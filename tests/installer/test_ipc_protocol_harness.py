"""Wrapper test for the IPC protocol Node harness.

Runs tests/installer/ipc_protocol_harness.js with node and verifies it prints
IPC_PROTOCOL_HARNESS_OK. Mirrors the pattern used by StagingFixHarnessTest.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
HARNESS = HERE / "ipc_protocol_harness.js"


class IpcProtocolHarnessTest(unittest.TestCase):
    def test_ipc_protocol_harness(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=300
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IPC_PROTOCOL_HARNESS_OK", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)