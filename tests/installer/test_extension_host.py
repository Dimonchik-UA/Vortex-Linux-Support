"""Tests for the staging host.json and the Extension staging fix.

Every test builds a throw-away Proton-like prefix inside this directory, so no
real Vortex installation is touched. Coverage:

  * host.json maps the host mods directory through the prefix z: drive;
  * a prefix without z: (or a home outside it) disables the fix instead of
    guessing a path;
  * install writes host.json, a repeat install stays "unchanged", verify sees
    the same bytes and uninstall removes the file with the Extension;
  * a modified host.json is reported as drifted;
  * the Extension logic (offer once, empty staging only, dispatch shape,
    explicit failure when Vortex ignores the action) passes its node harness.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import extension_installer  # noqa: E402
import installer  # noqa: E402

SOURCE = PROJECT / "src" / "extension"
HARNESS = HERE / "staging_fix_harness.js"


class HostConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-host-", dir=str(HERE)))
        self.prefix = self.root / "compatdata"
        self.dosdevices = self.prefix / "pfx" / "dosdevices"
        self.dosdevices.mkdir(parents=True)
        self.zroot = self.root / "zroot"
        self.home = self.zroot / "home" / "deck"
        self.home.mkdir(parents=True)
        (self.dosdevices / "z:").symlink_to(self.zroot, target_is_directory=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mods_root_maps_home_through_z_drive(self):
        payload = extension_installer._host_payload(self.prefix, home=self.home)
        self.assertEqual(json.loads(payload), {"modsRoot": "Z:\\home\\deck\\Vortex Mods"})
        # Verify compares bytes, so the payload must be deterministic.
        self.assertEqual(payload, extension_installer._host_payload(self.prefix, home=self.home))

    def test_missing_z_mapping_disables_host_file(self):
        (self.dosdevices / "z:").unlink()
        self.assertIsNone(extension_installer._host_payload(self.prefix, home=self.home))

    def test_home_outside_z_mapping_disables_host_file(self):
        elsewhere = self.root / "elsewhere" / "deck"
        elsewhere.mkdir(parents=True)
        self.assertIsNone(extension_installer._host_payload(self.prefix, home=elsewhere))


class ExtensionHostFileTest(HostConfigTest):
    def setUp(self):
        super().setUp()
        self.vortex = (
            self.prefix / "pfx" / "drive_c" / "users" / "steamuser"
            / "AppData" / "Roaming" / "Vortex"
        )
        (self.vortex / "plugins").mkdir(parents=True)
        self.target = self.vortex / "plugins" / extension_installer.EXTENSION_ID

    def test_install_writes_host_file_and_uninstall_removes_it(self):
        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "installed")
        self.assertIn("host.json", result["files"])
        self.assertEqual(
            (self.target / "host.json").read_bytes(),
            extension_installer._host_payload(self.prefix, home=self.home),
        )

        again = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(again["status"], "unchanged")

        removed = extension_installer.remove_extension(str(self.prefix))
        self.assertEqual(removed["status"], "removed")
        self.assertIn("host.json", removed["removed_files"])
        self.assertFalse(self.target.exists())

    def test_verify_compares_the_same_host_payload(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")
        self.assertIn("host.json", state["source_sha256"])

    def test_modified_host_file_is_reported_as_drifted(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.target / "host.json").write_text('{"modsRoot": "D:\\\\Vortex Mods"}', encoding="utf-8")
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["drifted"], ["host.json"])

    def test_prefix_without_z_installs_without_host_file(self):
        (self.dosdevices / "z:").unlink()
        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "installed")
        self.assertNotIn("host.json", result["files"])
        self.assertFalse((self.target / "host.json").exists())


class StagingFixHarnessTest(unittest.TestCase):
    def test_extension_staging_fix_harness(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STAGING_FIX_HARNESS_OK", result.stdout)
