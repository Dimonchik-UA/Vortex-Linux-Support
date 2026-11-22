"""Tests for src/linux-support/package.py.

Every test builds a throw-away HOME/XDG tree and a fake Proton prefix inside
this directory. No real prefix, desktop entry or installed copy is touched.
"""

import hashlib
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

import desktop  # noqa: E402
import extension_installer  # noqa: E402
import installer  # noqa: E402
import package  # noqa: E402
import updater  # noqa: E402

EXTENSION_SOURCE = PROJECT / "src" / "extension"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _copy_package(destination):
    """Create a temporary package tree from the real project sources."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / "installer" / "install.sh", destination / "install.sh")
    shutil.copy2(PROJECT / "README.md", destination / "README.md")
    shutil.copytree(
        PROJECT / "src" / "linux-support",
        destination / "src" / "linux-support",
        ignore=shutil.ignore_patterns("__pycache__", "*.log"),
    )
    shutil.copytree(
        EXTENSION_SOURCE,
        destination / "src" / "extension",
    )
    return destination


def _build_prefix(root):
    prefix = Path(root) / "compatdata"
    pfx = prefix / "pfx"
    profile = pfx / "drive_c" / "users" / "steamuser"
    plugins = profile / "AppData" / "Roaming" / "Vortex" / "plugins"
    plugins.mkdir(parents=True)
    vortex_app = pfx / "drive_c" / "Program Files" / "Vortex"
    vortex_app.mkdir(parents=True)
    (vortex_app / "Vortex.exe").write_bytes(b"stub")
    (vortex_app / "Uninstall Vortex.exe").write_bytes(b"stub")
    (pfx / "system.reg").write_text(
        "WINE REGISTRY Version 2\n"
        ";; All keys relative to REGISTRY\\\\Machine\n"
        "\n"
        "#arch=win64\n"
        "\n"
        "[Software\\\\Classes\\\\nxm] 1791559462\n"
        '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n',
        encoding="utf-8",
    )
    steam = pfx / "drive_c" / "Program Files (x86)" / "Steam"
    steam.mkdir(parents=True)
    dosdevices = pfx / "dosdevices"
    dosdevices.mkdir()
    (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
    (dosdevices / "z:").symlink_to("/", target_is_directory=True)
    return prefix


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-package-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.desktop_dir = self.root / "Desktop"
        self.apps_dir = self.data / "applications"
        self.app = self.data / "ovls"
        self.package = _copy_package(self.root / "package")
        self.prefix = _build_prefix(self.root)
        # A fake Steam root keeps installer.ensure_steam_library inside the test.
        self.steam_root = self.home / ".local" / "share" / "Steam"
        (self.steam_root / "steamapps").mkdir(parents=True)

        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.data),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state),
                "XDG_DESKTOP_DIR": str(self.desktop_dir),
                "HOME": str(self.home),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.default = {"value": None}

        def current_default(runner=None):
            return self.default["value"]

        def set_default(desktop_id, runner=None):
            self.default["value"] = desktop_id
            return True

        for target, name, replacement in (
            (desktop, "current_default", current_default),
            (desktop, "_set_default", set_default),
            (desktop, "_update_desktop_database", lambda directory: None),
        ):
            patcher = mock.patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _install(self, **kwargs):
        return package.install(
            package_root=str(self.package), prefix=str(self.prefix), **kwargs
        )

    def test_install_copies_package_and_configures_prefix(self):
        result = self._install()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["copy"]["status"], "installed")
        self.assertTrue((self.app / "MANIFEST.sha256").is_file())
        self.assertTrue((self.app / "README.md").is_file(), "user guide must be installed")
        self.assertIn("README.md", (self.app / "MANIFEST.sha256").read_text(encoding="utf-8"))
        self.assertEqual(
            (self.app / "VERSION").read_text(encoding="utf-8").strip(), "0.1.0"
        )
        self.assertTrue((self.app / "install.sh").is_file())
        self.assertTrue((self.app / "src" / "linux-support" / "ovls.py").is_file())

        wrapper = (self.app / "ovls-nxm").read_text(encoding="utf-8")
        self.assertIn(
            str(self.app / "src" / "linux-support" / "ovls.py"), wrapper
        )
        self.assertNotIn(
            str(PROJECT / "src" / "linux-support" / "ovls.py"), wrapper
        )

        for path in self.app.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".sh"):
                self.assertNotIn("Work_ChatGPT", path.read_text(encoding="utf-8"), str(path))

        saved = json.loads(
            (self.config / "ovls" / "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["prefix"], str(self.prefix.resolve()))

        self.assertEqual(result["shortcuts"]["status"], "ok", result["shortcuts"])
        for path in (
            self.apps_dir / "ovls-vortex.desktop",
            self.apps_dir / "ovls-manager.desktop",
            self.desktop_dir / "ovls-vortex.desktop",
            self.app / "bin" / "ovls-vortex",
            self.app / "bin" / "ovls-manager",
        ):
            self.assertTrue(path.is_file(), str(path))
        self.assertFalse((self.desktop_dir / "ovls-manager.desktop").exists())
        self.assertIn(
            "X-OVLS-Managed=true",
            (self.apps_dir / "ovls-vortex.desktop").read_text(encoding="utf-8"),
        )
        for name in ("ovls-vortex", "ovls-manager"):
            wrapper = (self.app / "bin" / name).read_text(encoding="utf-8")
            self.assertIn(
                str(self.app / "src" / "linux-support" / "ovls.py"), wrapper
            )
            self.assertNotIn(
                str(PROJECT / "src" / "linux-support" / "ovls.py"), wrapper
            )

        target = self.prefix / (
            "pfx/drive_c/users/steamuser/AppData/Roaming/Vortex/plugins/"
            + extension_installer.EXTENSION_ID
        )
        for name in extension_installer.MANAGED_FILES:
            self.assertEqual((target / name).read_bytes(), (EXTENSION_SOURCE / name).read_bytes())

        verified = package.verify(prefix=str(self.prefix))
        self.assertEqual(verified["status"], "ok", verified)

    def test_repeated_install_is_idempotent(self):
        self._install()
        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        second = self._install()

        self.assertEqual(second["copy"]["status"], "unchanged")
        self.assertEqual(second["installation"]["extension"]["status"], "unchanged")
        self.assertEqual(
            (self.app / "MANIFEST.sha256").read_text(encoding="utf-8"), manifest
        )

    def test_update_refreshes_shortcuts(self):
        self._install()
        entry = self.apps_dir / "ovls-vortex.desktop"
        entry.write_text(
            entry.read_text(encoding="utf-8") + "# stale\n", encoding="utf-8"
        )

        result = self._install(update=True)

        self.assertEqual(result["shortcuts"]["status"], "ok")
        self.assertIn("X-OVLS-Managed=true", entry.read_text(encoding="utf-8"))
        self.assertNotIn("# stale", entry.read_text(encoding="utf-8"))

    def test_dry_run_writes_no_shortcuts(self):
        result = self._install(dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertFalse(self.app.exists())
        self.assertFalse((self.apps_dir / "ovls-vortex.desktop").exists())
        self.assertFalse((self.desktop_dir / "ovls-vortex.desktop").exists())
        self.assertIn("would_write", result["shortcuts"])

    def test_self_refresh_from_installed_copy(self):
        self._install()

        with mock.patch.object(package, "copy_tree") as copy_tree:
            result = package.install(
                package_root=str(self.app), prefix=str(self.prefix), update=True
            )

        copy_tree.assert_not_called()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["copy"]["status"], "unchanged")
        self.assertTrue(result["copy"]["refresh"])
        self.assertEqual(result["shortcuts"]["status"], "ok")
        self.assertTrue((self.app / "MANIFEST.sha256").is_file())

    def test_failed_self_refresh_keeps_the_installed_copy(self):
        self._install()
        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                package.install(
                    package_root=str(self.app), prefix=str(self.prefix), update=True
                )

        self.assertEqual(
            (self.app / "MANIFEST.sha256").read_text(encoding="utf-8"), manifest
        )
        self.assertTrue((self.app / "src" / "linux-support" / "ovls.py").is_file())

    def test_foreign_shortcut_is_not_overwritten(self):
        self.apps_dir.mkdir(parents=True)
        foreign = self.apps_dir / "ovls-vortex.desktop"
        foreign.write_text("[Desktop Entry]\nName=My Vortex\n", encoding="utf-8")

        result = self._install()

        self.assertEqual(result["shortcuts"]["status"], "attention_required")
        self.assertIn(str(foreign), [item["path"] for item in result["shortcuts"]["skipped"]])
        self.assertIn("My Vortex", foreign.read_text(encoding="utf-8"))

        package.uninstall(prefix=str(self.prefix))
        self.assertTrue(foreign.is_file())

    def test_update_replaces_files_and_removes_stale(self):
        self._install()
        notes = self.app / "notes.txt"
        notes.write_text("user file\n", encoding="utf-8")

        support = self.package / "src" / "linux-support"
        (support / "core.py").write_text(
            (support / "core.py").read_text(encoding="utf-8") + "\n# updated\n",
            encoding="utf-8",
        )
        (support / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")
        (support / "diagnostics.py").unlink()

        result = self._install(update=True)

        self.assertEqual(result["copy"]["status"], "updated")
        self.assertIn("src/linux-support/extra.py", result["copy"]["changed"])
        self.assertIn("src/linux-support/diagnostics.py", result["copy"]["stale"])

        installed_support = self.app / "src" / "linux-support"
        self.assertIn("# updated", (installed_support / "core.py").read_text(encoding="utf-8"))
        self.assertTrue((installed_support / "extra.py").is_file())
        self.assertFalse((installed_support / "diagnostics.py").exists())
        self.assertEqual(notes.read_text(encoding="utf-8"), "user file\n")

        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertIn("src/linux-support/extra.py", manifest)
        self.assertNotIn("diagnostics.py", manifest)

        backup = Path(result["copy"]["backup"])
        self.assertTrue((backup / "src" / "linux-support" / "diagnostics.py").is_file())
        self.assertTrue((backup / "src" / "linux-support" / "core.py").is_file())

    def test_failed_update_restores_previous_copy(self):
        self._install()
        notes = self.app / "notes.txt"
        notes.write_text("user file\n", encoding="utf-8")
        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        original = (self.app / "src" / "linux-support" / "core.py").read_bytes()

        support = self.package / "src" / "linux-support"
        (support / "core.py").write_text("# new version\n", encoding="utf-8")
        (support / "extra.py").write_text("EXTRA = True\n", encoding="utf-8")

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install(update=True)

        self.assertEqual(
            (self.app / "src" / "linux-support" / "core.py").read_bytes(), original
        )
        self.assertFalse((self.app / "src" / "linux-support" / "extra.py").exists())
        self.assertEqual(
            (self.app / "MANIFEST.sha256").read_text(encoding="utf-8"), manifest
        )
        self.assertEqual(notes.read_text(encoding="utf-8"), "user file\n")

    def test_failed_first_install_leaves_no_copy(self):
        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install()

        self.assertFalse((self.app / "MANIFEST.sha256").exists())
        self.assertFalse((self.app / "src").exists())

    def test_uninstall_keeps_user_files_and_backups(self):
        self._install()
        notes = self.app / "notes.txt"
        notes.write_text("user file\n", encoding="utf-8")
        backups = self.app / "backups"
        self.assertTrue(backups.is_dir())

        result = package.uninstall(prefix=str(self.prefix))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["installation"]["extension"]["status"], "removed")
        self.assertFalse((self.app / "MANIFEST.sha256").exists())
        self.assertFalse((self.app / "src").exists())
        self.assertFalse((self.app / "ovls-nxm").exists())
        self.assertFalse((self.apps_dir / "ovls-vortex.desktop").exists())
        self.assertFalse((self.apps_dir / "ovls-manager.desktop").exists())
        self.assertFalse((self.desktop_dir / "ovls-vortex.desktop").exists())
        self.assertFalse((self.app / "bin" / "ovls-vortex").exists())
        self.assertTrue(notes.is_file())
        self.assertTrue(backups.is_dir())
        self.assertFalse((self.config / "ovls" / "config.json").exists())

    def test_uninstall_without_prefix_keeps_copy(self):
        self._install()
        with mock.patch.object(package, "is_prefix", return_value=False):
            result = package.uninstall()

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["copy_kept"])
        self.assertTrue((self.app / "src" / "linux-support" / "ovls.py").is_file())
        self.assertTrue((self.config / "ovls" / "config.json").is_file())
        self.assertTrue((self.apps_dir / "ovls-vortex.desktop").is_file())
        self.assertTrue((self.desktop_dir / "ovls-vortex.desktop").is_file())

    def test_manifest_cannot_delete_outside_the_copy(self):
        self._install()
        outside = self.root / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("keep me\n", encoding="utf-8")

        link = self.app / "linkdir"
        link.symlink_to(outside, target_is_directory=True)

        manifest = self.app / "MANIFEST.sha256"
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + f"{_sha256(secret)}  ../outside/secret.txt\n"
            + f"{_sha256(secret)}  {secret}\n"
            + f"{_sha256(secret)}  linkdir/secret.txt\n",
            encoding="utf-8",
        )

        result = package.remove_copy()

        self.assertTrue(secret.is_file())
        self.assertTrue(link.is_symlink())
        self.assertIn("../outside/secret.txt", result["skipped"])
        self.assertIn(str(secret), result["skipped"])
        self.assertIn("linkdir/secret.txt", result["skipped"])

    def test_install_requires_a_prefix(self):
        with mock.patch.object(package, "is_prefix", return_value=False):
            with self.assertRaises(ValueError):
                self._install()
        self.assertFalse((self.app / "MANIFEST.sha256").exists())

    def test_install_refuses_to_run_from_the_installed_copy(self):
        self._install()
        with self.assertRaises(ValueError):
            package.install(package_root=str(self.app), prefix=str(self.prefix))

    def test_update_writes_version_from_package_core_not_running_process(self):
        self._install()
        core_file = self.package / "src" / "linux-support" / "core.py"
        original = core_file.read_text(encoding="utf-8")
        self.assertIn('VERSION = "0.1.0"', original)
        core_file.write_text(
            original.replace('VERSION = "0.1.0"', 'VERSION = "0.2.0"'),
            encoding="utf-8",
        )

        # Running process is deliberately older than the package being installed.
        with mock.patch.object(package.core, "VERSION", "0.0.1"):
            result = self._install(update=True)

        self.assertEqual(result["copy"]["status"], "updated")
        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8").strip(), "0.2.0")
        installed_core = (self.app / "src" / "linux-support" / "core.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('VERSION = "0.2.0"', installed_core)
        self.assertEqual(updater.current_local_version(), "0.2.0")

    def test_update_repairs_stale_version_without_file_changes(self):
        self._install()
        (self.app / "VERSION").write_text("0.0.1\n", encoding="utf-8")

        result = self._install(update=True)

        self.assertEqual(result["copy"]["status"], "updated")
        self.assertEqual(result["copy"]["changed"], [])
        self.assertEqual(result["copy"]["stale"], [])
        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8").strip(), "0.1.0")
        self.assertTrue((self.app / "MANIFEST.sha256").is_file())

    def test_repeated_update_of_same_version_is_still_idempotent(self):
        self._install()
        first = (self.app / "VERSION").read_text(encoding="utf-8")

        result = self._install(update=True)

        self.assertEqual(result["copy"]["status"], "unchanged")
        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8"), first)

    def test_failed_update_restores_previous_version(self):
        self._install()
        original_version = (self.app / "VERSION").read_text(encoding="utf-8")
        core_file = self.package / "src" / "linux-support" / "core.py"
        core_file.write_text(
            core_file.read_text(encoding="utf-8").replace(
                'VERSION = "0.1.0"', 'VERSION = "0.2.0"'
            ),
            encoding="utf-8",
        )

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install(update=True)

        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8"), original_version)
        self.assertIn(
            'VERSION = "0.1.0"',
            (self.app / "src" / "linux-support" / "core.py").read_text(encoding="utf-8"),
        )

    def test_rollback_removes_version_file_when_it_was_absent(self):
        self._install()
        (self.app / "VERSION").unlink()
        core_file = self.package / "src" / "linux-support" / "core.py"
        core_file.write_text(
            core_file.read_text(encoding="utf-8").replace(
                'VERSION = "0.1.0"', 'VERSION = "0.2.0"'
            ),
            encoding="utf-8",
        )

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install(update=True)

        self.assertFalse((self.app / "VERSION").exists())
        self.assertIn(
            'VERSION = "0.1.0"',
            (self.app / "src" / "linux-support" / "core.py").read_text(encoding="utf-8"),
        )

    def test_rollback_restores_empty_version_file_exactly(self):
        self._install()
        (self.app / "VERSION").write_text("", encoding="utf-8")
        core_file = self.package / "src" / "linux-support" / "core.py"
        core_file.write_text(
            core_file.read_text(encoding="utf-8").replace(
                'VERSION = "0.1.0"', 'VERSION = "0.2.0"'
            ),
            encoding="utf-8",
        )

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install(update=True)

        version_file = self.app / "VERSION"
        self.assertTrue(version_file.is_file())
        self.assertEqual(version_file.read_text(encoding="utf-8"), "")

    def test_manifest_status_accepts_version_matching_installed_core(self):
        self._install()

        state = package.manifest_status()

        self.assertEqual(state["status"], "ok")
        self.assertEqual(state["version_status"], "ok")
        self.assertEqual(state["version"], "0.1.0")
        self.assertEqual(state["expected_version"], "0.1.0")

    def test_manifest_status_rejects_outdated_version(self):
        self._install()
        (self.app / "VERSION").write_text("0.0.9\n", encoding="utf-8")

        state = package.manifest_status()

        self.assertEqual(state["status"], "attention_required")
        self.assertEqual(state["version_status"], "outdated")
        self.assertEqual(state["version"], "0.0.9")

    def test_manifest_status_rejects_empty_version(self):
        self._install()
        (self.app / "VERSION").write_text("", encoding="utf-8")

        state = package.manifest_status()

        self.assertEqual(state["status"], "attention_required")
        self.assertEqual(state["version_status"], "empty")
        self.assertEqual(state["version"], "")

    def test_manifest_status_rejects_missing_version(self):
        self._install()
        (self.app / "VERSION").unlink()

        state = package.manifest_status()

        self.assertEqual(state["status"], "attention_required")
        self.assertEqual(state["version_status"], "missing")
        self.assertIsNone(state["version"])


    def _refresh_manifest_digest(self, relative):
        manifest = self.app / "MANIFEST.sha256"
        entries = package._parse_manifest(manifest.read_text(encoding="utf-8"))
        entries[relative] = _sha256(self.app / relative)
        manifest.write_text(package._manifest_text(entries), encoding="utf-8")

    def test_self_refresh_repairs_stale_version_without_copy_tree(self):
        self._install()
        (self.app / "VERSION").write_text("0.0.1\n", encoding="utf-8")

        with mock.patch.object(package, "copy_tree", side_effect=AssertionError("copy_tree called")), \
                mock.patch.object(package.installer, "install", return_value={"status": "unchanged"}), \
                mock.patch.object(package.desktop, "install_handler", return_value={"status": "unchanged"}):
            result = package.install(
                package_root=str(self.app), prefix=str(self.prefix), update=True
            )

        self.assertEqual(result["copy"]["status"], "unchanged")
        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8"), "0.1.0\n")
        self.assertEqual(package.manifest_status()["status"], "ok")

    def test_self_refresh_dry_run_with_stale_version_writes_nothing(self):
        self._install()
        (self.app / "VERSION").write_text("0.0.1\n", encoding="utf-8")
        manifest = (self.app / "MANIFEST.sha256").read_bytes()

        with mock.patch.object(package, "copy_tree", side_effect=AssertionError("copy_tree called")) as copy_tree:
            result = package.install(
                package_root=str(self.app),
                prefix=str(self.prefix),
                update=True,
                dry_run=True,
            )

        copy_tree.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertEqual((self.app / "VERSION").read_text(encoding="utf-8"), "0.0.1\n")
        self.assertEqual((self.app / "MANIFEST.sha256").read_bytes(), manifest)
        self.assertEqual(result["copy"]["status"], "would_update")
        self.assertEqual(
            result["copy"]["version_repair"],
            {"current": "0.0.1", "expected": "0.1.0"},
        )

    def test_failed_chmod_removes_already_copied_file(self):
        real_chmod = package.os.chmod
        state = {"failed": False}

        def fail_on_install_chmod(path, mode):
            target = Path(path)
            if (not state["failed"] and self.app in target.parents
                    and "backups" not in target.parts and ".ovls-stage-" not in target.parts):
                state["failed"] = True
                raise OSError("chmod boom")
            return real_chmod(path, mode)

        with mock.patch.object(package.os, "chmod", side_effect=fail_on_install_chmod):
            with self.assertRaises(OSError):
                self._install()

        self.assertTrue(state["failed"])
        leftovers = [
            p for p in self.app.rglob("*")
            if p.is_file() and "backups" not in p.parts
        ]
        self.assertEqual(leftovers, [], leftovers)

    def test_manifest_status_rejects_version_symlink_and_update_replaces_it(self):
        self._install()
        target = self.app / "version-target"
        target.write_text("0.1.0\n", encoding="utf-8")
        version = self.app / "VERSION"
        version.unlink()
        version.symlink_to(target)

        state = package.manifest_status()
        self.assertEqual(state["status"], "attention_required")
        self.assertEqual(state["version_status"], "unsafe")

        self._install(update=True)

        self.assertFalse(version.is_symlink())
        self.assertEqual(version.read_text(encoding="utf-8"), "0.1.0\n")

    def test_manifest_status_rejects_conflicting_core_versions(self):
        self._install()
        legacy = self.app / "legacy" / "core.py"
        legacy.parent.mkdir()
        legacy.write_text('VERSION = "0.2.0"\n', encoding="utf-8")
        self._refresh_manifest_digest("legacy/core.py")
        (self.app / "VERSION").write_text("0.2.0\n", encoding="utf-8")

        state = package.manifest_status()

        self.assertEqual(state["status"], "attention_required")
        self.assertEqual(state["version_status"], "unknown")

    def test_version_parser_rejects_malformed_core_assignments(self):
        parse = package._version_from_core_text
        self.assertEqual(parse('VERSION = "0.1.0"\n'), "0.1.0")
        self.assertEqual(
            parse('"""\nVERSION = "9.9.9"\n"""\nVERSION = "0.1.0"\n'), "0.1.0"
        )
        for text in (
            'VERSION = "0.1.0\'\n',
            'VERSION = "0.1.\n0"\n',
            'VERSION = 0.1.0\n',
            'VERSION = "0.1.0"\nVERSION = "0.2.0"\n',
        ):
            self.assertIsNone(parse(text), text)

    def test_manifest_status_survives_unreadable_file(self):
        self._install()
        with mock.patch.object(package, "_sha256", side_effect=OSError("nope")):
            state = package.manifest_status()
        self.assertEqual(state["status"], "attention_required")
        self.assertTrue(state["modified"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
