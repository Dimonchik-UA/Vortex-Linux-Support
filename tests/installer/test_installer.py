"""Tests for src/linux-support/installer.py.

Every test builds a throw-away Proton-like prefix inside this directory, so no
real Vortex installation is touched. Coverage:

  * install into an empty prefix creates the Extension and Steam library file;
  * the Vortex uninstall key is created and reported by verify;
  * repeated install is idempotent ("unchanged") and keeps the registry key;
  * verify reports ok for a clean install;
  * a modified Extension file is detected as drifted;
  * --backup copies the actually overwritten file only;
  * uninstall removes owned files and keeps the Steam config and other plugins;
  * dry-run writes nothing;
  * a prefix without a Steam directory is skipped for the library file.
"""

import json
import os
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
import extension_installer  # noqa: E402
import desktop  # noqa: E402

SOURCE = PROJECT / "src" / "extension"


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-installer-", dir=str(HERE)))
        self.prefix = self.root / "compatdata"
        pfx = self.prefix / "pfx"
        self.vortex_dir = (
            pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "Vortex"
        )
        self.plugins = self.vortex_dir / "plugins"
        self.plugins.mkdir(parents=True)
        vortex_app = pfx / "drive_c" / "Program Files" / "Vortex"
        vortex_app.mkdir(parents=True)
        (vortex_app / "Vortex.exe").write_bytes(b"stub")
        (vortex_app / "Uninstall Vortex.exe").write_bytes(b"stub")
        self.system_reg = pfx / "system.reg"
        self.system_reg.write_text(
            "WINE REGISTRY Version 2\n"
            ";; All keys relative to REGISTRY\\\\Machine\n"
            "\n"
            "#arch=win64\n"
            "\n"
            "[Software\\\\Classes\\\\nxm] 1791559462\n"
            '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n',
            encoding="utf-8",
        )
        self.steam = pfx / "drive_c" / "Program Files (x86)" / "Steam"
        self.steam.mkdir(parents=True)
        self.vdf = self.steam / "config" / "libraryfolders.vdf"
        dosdevices = pfx / "dosdevices"
        dosdevices.mkdir()
        (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
        (dosdevices / "z:").symlink_to("/", target_is_directory=True)

        # Host Steam must not leak into the tests. installer.py imports the
        # steam module, so patch the function on that module; a fake root with
        # steamapps/ is enough for read_library_folders() to return one library.
        self.host_steam = self.root / "host-steam"
        (self.host_steam / "steamapps").mkdir(parents=True)
        find_steam = mock.patch.object(
            installer.steam, "find_steam_root", return_value=str(self.host_steam)
        )
        self.find_steam_root = find_steam.start()
        self.addCleanup(find_steam.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_install_verify_uninstall(self):
        result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extension"]["status"], "installed")
        self.assertEqual(result["vortex_registry"]["action"], "created")
        self.assertEqual(
            result["vortex_registry"]["value"],
            '"C:\\Program Files\\Vortex\\Uninstall Vortex.exe"',
        )
        self.assertTrue(Path(result["vortex_registry"]["backup"]).is_file())

        target = self.plugins / extension_installer.EXTENSION_ID
        for name in extension_installer.MANAGED_FILES:
            self.assertEqual(
                (target / name).read_bytes(), (SOURCE / name).read_bytes(), name
            )

        self.assertEqual(result["steam_library"]["action"], "created")
        self.assertTrue(self.vdf.is_file())
        text = self.vdf.read_text(encoding="utf-8")
        self.assertIn('"LibraryFolders"', text)
        self.assertIn('"path"', text)
        self.assertIn("Z:", text)

        state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")

        verified = installer.verify(str(self.prefix), str(SOURCE))
        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["vortex_registry"]["status"], "ok")

        removed = installer.uninstall(str(self.prefix))
        self.assertEqual(removed["extension"]["status"], "removed")
        self.assertEqual(removed["vortex_registry"]["status"], "kept")
        self.assertFalse(target.exists())
        self.assertTrue(self.vdf.is_file(), "Steam config must survive uninstall")
        self.assertEqual(removed["steam_library"]["config_present"], True)
        self.assertIn("57979c68", self.system_reg.read_text(encoding="utf-8"))

    def test_install_is_idempotent(self):
        installer.install(str(self.prefix), str(SOURCE))
        after_first = self.system_reg.read_bytes()
        again = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(again["extension"]["status"], "unchanged")
        self.assertEqual(again["steam_library"]["action"], "present")
        self.assertEqual(again["vortex_registry"]["action"], "ok")
        self.assertEqual(self.system_reg.read_bytes(), after_first)

    def test_dry_run_writes_nothing(self):
        result = installer.install(str(self.prefix), str(SOURCE), dry_run=True)
        self.assertEqual(result["extension"]["status"], "would_install")
        self.assertEqual(result["steam_library"]["action"], "would_create")
        self.assertEqual(result["vortex_registry"]["action"], "would_create")
        self.assertFalse((self.plugins / extension_installer.EXTENSION_ID).exists())
        self.assertFalse(self.vdf.exists())
        self.assertNotIn("57979c68", self.system_reg.read_text(encoding="utf-8"))

    def test_drift_detected_and_backed_up(self):
        installer.install(str(self.prefix), str(SOURCE))
        target = self.plugins / extension_installer.EXTENSION_ID
        drifted = target / "index.js"
        drifted.write_text("// user modified\n", encoding="utf-8")

        state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["drifted"], ["index.js"])

        backup_dir = self.root / "backups"
        result = installer.install(
            str(self.prefix), str(SOURCE), backup_root=str(backup_dir)
        )
        self.assertEqual(result["extension"]["status"], "installed")
        self.assertEqual(len(result["extension"]["backups"]), 1)
        self.assertEqual(
            (backup_dir / "index.js").read_text(encoding="utf-8"),
            "// user modified\n",
        )
        self.assertEqual(drifted.read_bytes(), (SOURCE / "index.js").read_bytes())

    def test_existing_steam_config_is_never_rewritten(self):
        self.vdf.parent.mkdir(parents=True)
        original = '"LibraryFolders"\n{\n}\n'
        self.vdf.write_text(original, encoding="utf-8")
        result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["steam_library"]["action"], "present")
        self.assertEqual(self.vdf.read_text(encoding="utf-8"), original)

    def test_missing_steam_dir_is_skipped(self):
        shutil.rmtree(self.steam)
        result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["steam_library"]["action"], "skipped_no_steam")
        self.assertFalse(self.vdf.exists())

    def test_other_plugins_are_preserved(self):
        other = self.plugins / "some-other-extension"
        other.mkdir()
        (other / "info.json").write_text('{"id": "other"}', encoding="utf-8")

        installer.install(str(self.prefix), str(SOURCE))
        installer.uninstall(str(self.prefix))

        self.assertTrue((other / "info.json").is_file())
        self.assertEqual(
            json.loads((other / "info.json").read_text(encoding="utf-8"))["id"],
            "other",
        )

    def _legacy_ipc_tree(self):
        root = self.vortex_dir / "OVLS_IPC_V2"
        for sub in (
            "sessions/abc/requests", "sessions/abc/responses", "acks",
            "dedup", "processed", "client_state",
        ):
            (root / sub).mkdir(parents=True)
        (root / "dedup" / "req-1.json").write_text("{}", encoding="utf-8")
        (root / "status.json").write_text("{}", encoding="utf-8")
        (root / "status.json.node.1.2.abcdef0123456789.tmp").write_text("x", encoding="utf-8")
        return root

    def test_legacy_ipc_data_removed_on_install_and_uninstall(self):
        root = self._legacy_ipc_tree()
        result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["legacy_ipc"]["status"], "removed")
        self.assertFalse(root.exists())
        self.assertTrue((self.plugins / extension_installer.EXTENSION_ID / "index.js").is_file())

        root = self._legacy_ipc_tree()
        removed = installer.uninstall(str(self.prefix))
        self.assertEqual(removed["legacy_ipc"]["status"], "removed")
        self.assertFalse(root.exists())

    def test_legacy_ipc_cleanup_is_idempotent(self):
        first = extension_installer.remove_legacy_ipc_data(str(self.prefix))
        self.assertEqual(first["status"], "absent")
        installer.install(str(self.prefix), str(SOURCE))
        second = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(second["legacy_ipc"]["status"], "absent")

    def test_legacy_ipc_cleanup_keeps_foreign_files(self):
        root = self._legacy_ipc_tree()
        foreign = root / "user-note.txt"
        foreign.write_text("keep", encoding="utf-8")
        result = extension_installer.remove_legacy_ipc_data(str(self.prefix))
        self.assertEqual(result["status"], "removed")
        self.assertEqual(result["kept_foreign"], ["user-note.txt"])
        self.assertFalse(result["directory_removed"])
        self.assertFalse((root / "sessions").exists())
        self.assertTrue(foreign.is_file())

    def test_legacy_ipc_cleanup_leaves_vortex_data_alone(self):
        self._legacy_ipc_tree()
        state = self.vortex_dir / "state.json"
        state.write_text("{}", encoding="utf-8")
        other = self.plugins / "some-other-extension"
        other.mkdir()
        (other / "info.json").write_text("{}", encoding="utf-8")

        extension_installer.remove_legacy_ipc_data(str(self.prefix))

        self.assertTrue(state.is_file())
        self.assertTrue((other / "info.json").is_file())

    def test_symlinked_legacy_directory_is_not_followed(self):
        target = self.root / "outside"
        target.mkdir()
        (target / "keep.txt").write_text("x", encoding="utf-8")
        (self.vortex_dir / "OVLS_IPC_V2").symlink_to(target, target_is_directory=True)

        result = extension_installer.remove_legacy_ipc_data(str(self.prefix))

        self.assertEqual(result["status"], "skipped")
        self.assertTrue((target / "keep.txt").is_file())

    def test_legacy_ipc_cleanup_dry_run_deletes_nothing(self):
        root = self._legacy_ipc_tree()
        result = extension_installer.remove_legacy_ipc_data(str(self.prefix), dry_run=True)
        self.assertEqual(result["status"], "would_remove")
        self.assertTrue((root / "sessions").is_dir())

    def test_legacy_ipc_cleanup_error_does_not_break_install(self):
        self._legacy_ipc_tree()
        real_rmtree = shutil.rmtree

        def failing_rmtree(path, *args, **kwargs):
            if "OVLS_IPC_V2" in str(path):
                raise OSError("denied")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch.object(extension_installer.shutil, "rmtree", side_effect=failing_rmtree):
            result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["legacy_ipc"]["status"], "error")

    def test_install_requires_vortex(self):
        (self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe").unlink()
        with self.assertRaises(ValueError):
            installer.install(str(self.prefix), str(SOURCE))

    def test_desktop_handler_through_installer(self):
        """install/verify/uninstall toggle the NXM handler with with_desktop."""
        apps = self.root / "applications"
        apps.mkdir()
        data = self.root / "data"
        env = mock.patch.dict(
            os.environ,
            {"XDG_DATA_HOME": str(data), "XDG_CONFIG_HOME": str(self.root / "config")},
        )
        env.start()
        self.addCleanup(env.stop)
        wrapper = data / "ovls" / "ovls-nxm"

        with mock.patch.object(desktop, "_update_desktop_database"), mock.patch.object(
            desktop, "current_default", return_value=None
        ), mock.patch.object(desktop, "_set_default", return_value=True):
            result = installer.install(
                str(self.prefix),
                str(SOURCE),
                with_desktop=True,
                applications_dir=apps,
                project_root=PROJECT,
            )
        self.assertEqual(result["nxm_handler"]["status"], "installed")
        self.assertTrue(wrapper.is_file())
        self.assertTrue((apps / desktop.HANDLER_ID).is_file())

        with mock.patch.object(
            desktop, "current_default", return_value=desktop.HANDLER_ID
        ), mock.patch.object(desktop, "_update_desktop_database"):
            verified = installer.verify(
                str(self.prefix), str(SOURCE), with_desktop=True, applications_dir=apps
            )
            self.assertEqual(verified["nxm_handler"]["status"], "ok")
            self.assertEqual(verified["status"], "ok")

            removed = installer.uninstall(
                str(self.prefix), with_desktop=True, applications_dir=apps
            )
        self.assertEqual(removed["nxm_handler"]["status"], "removed")
        self.assertFalse(wrapper.exists())
        self.assertFalse((apps / desktop.HANDLER_ID).exists())
        self.assertTrue(self.vdf.is_file(), "Steam config must survive uninstall")

    def test_desktop_handler_is_disabled_by_default(self):
        result = installer.install(str(self.prefix), str(SOURCE))
        self.assertEqual(result["nxm_handler"]["status"], "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
