"""Tests for src/linux-support/registry.py.

Every test builds a throw-away Proton-like prefix inside this directory, so the
real prefix and the real user registry are never touched. The registry under
test is a plain Wine ``system.reg`` file.

Coverage:
  * missing key is created, with a verified backup;
  * correct key is left byte-for-byte untouched (no backup);
  * missing value inside an existing section is added;
  * wrong value is corrected, only that one line changes;
  * Vortex missing / uninstaller missing -> skipped, nothing written;
  * install path with spaces -> quoted UninstallString;
  * repeated install and update keep the registration intact;
  * uninstall of Linux Support keeps the registration;
  * backup failure and write failure leave the registry unchanged;
  * failed post-write verification restores the backup;
  * dry run writes nothing;
  * foreign keys and values are preserved;
  * verify reports attention when the key is missing.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import installer  # noqa: E402
import registry  # noqa: E402

SOURCE = PROJECT / "src" / "extension"

GUID = "57979c68-f490-55b8-8fed-8b017a5af2fe"
SECTION_HEADER = (
    "[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\"
    + GUID
    + "]"
)
EXPECTED_VALUE = '"C:\\Program Files\\Vortex\\Uninstall Vortex.exe"'

SYSTEM_REG_TEMPLATE = (
    "WINE REGISTRY Version 2\n"
    ";; All keys relative to REGISTRY\\\\Machine\n"
    "\n"
    "#arch=win64\n"
    "\n"
    "[Software\\\\Classes\\\\nxm] 1791559462\n"
    '#time=1dd580242a7a816\n'
    '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n'
    "\n"
    "[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall"
    "\\\\{11111111-2222-3333-4444-555555555555}] 1791559462\n"
    '"DisplayName"="Other App"\n'
    '"UninstallString"="\\"C:\\\\Other App\\\\uninst.exe\\""\n'
)


class PrefixCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-registry-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def make_prefix(
        self,
        install_parts=("Program Files", "Vortex"),
        vortex=True,
        uninstaller=True,
        registry_text=SYSTEM_REG_TEMPLATE,
        registry_file=True,
    ):
        prefix = self.root / "prefix"
        pfx = prefix / "pfx"
        drive_c = pfx / "drive_c"
        app_dir = drive_c.joinpath(*install_parts)
        app_dir.mkdir(parents=True, exist_ok=True)
        if vortex:
            (app_dir / "Vortex.exe").write_bytes(b"stub")
        if uninstaller:
            (app_dir / "Uninstall Vortex.exe").write_bytes(b"stub")
        (drive_c / "users" / "steamuser").mkdir(parents=True, exist_ok=True)
        (drive_c / "users" / "steamuser" / "AppData" / "Roaming" / "Vortex" / "plugins").mkdir(
            parents=True, exist_ok=True
        )
        dosdevices = pfx / "dosdevices"
        dosdevices.mkdir()
        (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
        (dosdevices / "z:").symlink_to("/", target_is_directory=True)
        if registry_file:
            (pfx / "system.reg").write_text(registry_text, encoding="utf-8")
        return prefix, app_dir, pfx / "system.reg"


class RegistryRepairTest(PrefixCase):
    def test_missing_key_is_created_with_backup(self):
        prefix, _app, system_reg = self.make_prefix()
        original = system_reg.read_bytes()

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["value"], EXPECTED_VALUE)
        self.assertIsNotNone(result["backup"])
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), original)

        text = system_reg.read_text(encoding="utf-8")
        self.assertIn(SECTION_HEADER, text)
        self.assertIn(f'"UninstallString"={registry._encode_reg_string(EXPECTED_VALUE)}', text)
        # Foreign keys are still there, untouched.
        self.assertIn('"DisplayName"="Other App"', text)
        self.assertIn('@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""', text)

        state = registry.read_state(prefix)
        self.assertEqual(state["status"], "ok")
        self.assertTrue(state["key_present"])
        self.assertEqual(state["value"], EXPECTED_VALUE)

    def test_correct_key_is_left_untouched(self):
        prefix, _app, system_reg = self.make_prefix()
        registry.ensure_uninstall_key(prefix)
        before = system_reg.read_bytes()
        backups = sorted(system_reg.parent.glob("system.reg.ovls-backup-*"))

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "ok")
        self.assertIsNone(result["backup"])
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(sorted(system_reg.parent.glob("system.reg.ovls-backup-*")), backups)

    def test_missing_value_is_added(self):
        text = SYSTEM_REG_TEMPLATE + (
            "\n" + SECTION_HEADER + " 1791559462\n" + '"DisplayName"="Vortex"\n'
        )
        prefix, _app, system_reg = self.make_prefix(registry_text=text)
        before = system_reg.read_text(encoding="utf-8").splitlines()

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "updated")
        after = system_reg.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), len(before) + 1)
        self.assertIn(f'"UninstallString"={registry._encode_reg_string(EXPECTED_VALUE)}', after)
        self.assertIn('"DisplayName"="Vortex"', after)

    def test_wrong_value_is_corrected_only_there(self):
        text = SYSTEM_REG_TEMPLATE + (
            "\n"
            + SECTION_HEADER
            + " 1791559462\n"
            + '"DisplayName"="Vortex"\n'
            + '"UninstallString"="@NEW_UNINSTALLSTRING@.exe"\n'
        )
        prefix, _app, system_reg = self.make_prefix(registry_text=text)
        before = system_reg.read_text(encoding="utf-8").splitlines()

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "updated")
        after = system_reg.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(after), len(before))
        changed = [(old, new) for old, new in zip(before, after) if old != new]
        self.assertEqual(len(changed), 1)
        self.assertIn("UninstallString", changed[0][1])
        self.assertIn(registry._encode_reg_string(EXPECTED_VALUE), changed[0][1])
        self.assertIn('"DisplayName"="Vortex"', after)
        self.assertEqual(registry.read_state(prefix)["status"], "ok")

    def test_vortex_missing_is_skipped(self):
        prefix, _app, system_reg = self.make_prefix(vortex=False)
        before = system_reg.read_bytes()

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "skipped_no_vortex")
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(registry.read_state(prefix)["status"], "no_vortex")

    def test_missing_uninstaller_is_skipped(self):
        prefix, _app, system_reg = self.make_prefix(uninstaller=False)
        before = system_reg.read_bytes()

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "skipped_no_uninstaller")
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(registry.read_state(prefix)["status"], "no_uninstaller")

    def test_path_with_spaces_is_quoted(self):
        prefix, app_dir, _system_reg = self.make_prefix(
            install_parts=("Program Files", "Vortex Test (x64)")
        )

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "created")
        expected = '"C:\\Program Files\\Vortex Test (x64)\\Uninstall Vortex.exe"'
        self.assertEqual(result["value"], expected)
        self.assertTrue(result["value"].startswith('"'))
        self.assertTrue(result["value"].endswith('"'))
        self.assertEqual(registry.read_state(prefix)["status"], "ok")

    def test_dry_run_writes_nothing(self):
        prefix, _app, system_reg = self.make_prefix()
        before = system_reg.read_bytes()

        result = registry.ensure_uninstall_key(prefix, dry_run=True)

        self.assertEqual(result["action"], "would_create")
        self.assertIsNone(result["backup"])
        self.assertEqual(system_reg.read_bytes(), before)

    def test_registry_file_missing_is_skipped(self):
        prefix, _app, _system_reg = self.make_prefix(registry_file=False)

        result = registry.ensure_uninstall_key(prefix)

        self.assertEqual(result["action"], "skipped_no_registry")
        self.assertFalse((prefix / "pfx" / "system.reg").exists())


class RegistryInstallerTest(PrefixCase):
    def test_repeated_install_and_update_keep_registration(self):
        prefix, _app, system_reg = self.make_prefix()

        first = installer.install(str(prefix), str(SOURCE))
        self.assertEqual(first["vortex_registry"]["action"], "created")
        after_first = system_reg.read_bytes()
        backups = sorted(system_reg.parent.glob("system.reg.ovls-backup-*"))
        self.assertEqual(len(backups), 1)

        second = installer.install(str(prefix), str(SOURCE))
        self.assertEqual(second["vortex_registry"]["action"], "ok")
        self.assertEqual(system_reg.read_bytes(), after_first)
        self.assertEqual(sorted(system_reg.parent.glob("system.reg.ovls-backup-*")), backups)

        # An "update" is a normal install run over the newer package; it must
        # not remove or rewrite the Vortex registration.
        third = installer.install(str(prefix), str(SOURCE))
        self.assertEqual(third["vortex_registry"]["status"], "ok")
        self.assertEqual(system_reg.read_bytes(), after_first)

        verified = installer.verify(str(prefix), str(SOURCE))
        self.assertEqual(verified["vortex_registry"]["status"], "ok")

    def test_uninstall_keeps_registration(self):
        prefix, _app, system_reg = self.make_prefix()
        installer.install(str(prefix), str(SOURCE))
        before = system_reg.read_bytes()

        result = installer.uninstall(str(prefix))

        self.assertEqual(result["vortex_registry"]["status"], "kept")
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(registry.read_state(prefix)["status"], "ok")

    def test_verify_reports_attention_after_key_removed(self):
        prefix, _app, system_reg = self.make_prefix()
        installer.install(str(prefix), str(SOURCE))
        system_reg.write_text(SYSTEM_REG_TEMPLATE, encoding="utf-8")

        verified = installer.verify(str(prefix), str(SOURCE))

        self.assertEqual(verified["status"], "attention_required")
        self.assertEqual(verified["vortex_registry"]["status"], "missing_key")


class RegistryFailureTest(PrefixCase):
    def test_backup_failure_changes_nothing(self):
        prefix, _app, system_reg = self.make_prefix()
        before = system_reg.read_bytes()

        with mock.patch.object(registry.shutil, "copy2", side_effect=OSError("read-only")):
            with self.assertRaises(registry.RegistryError) as caught:
                registry.ensure_uninstall_key(prefix)

        self.assertIn("Cannot back up", str(caught.exception))
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(list(system_reg.parent.glob("system.reg.ovls-backup-*")), [])
        self.assertEqual(list(system_reg.parent.glob("*.ovls.tmp")), [])

    def test_write_failure_keeps_original_registry(self):
        prefix, _app, system_reg = self.make_prefix()
        before = system_reg.read_bytes()

        with mock.patch.object(registry.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(registry.RegistryError):
                registry.ensure_uninstall_key(prefix)

        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(list(system_reg.parent.glob("*.ovls.tmp")), [])
        leftovers = list(system_reg.parent.glob("system.reg.ovls-backup-*"))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0].read_bytes(), before)

    def test_failed_verification_restores_backup(self):
        prefix, _app, system_reg = self.make_prefix()
        before = system_reg.read_bytes()

        with mock.patch.object(
            registry, "_read_value_from_file", return_value='"C:\\Wrong\\Vortex.exe"'
        ):
            with self.assertRaises(registry.RegistryError) as caught:
                registry.ensure_uninstall_key(prefix)

        self.assertIn("verification failed", str(caught.exception))
        self.assertEqual(system_reg.read_bytes(), before)
        self.assertEqual(registry.read_state(prefix)["status"], "missing_key")
        backups = list(system_reg.parent.glob("system.reg.ovls-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
