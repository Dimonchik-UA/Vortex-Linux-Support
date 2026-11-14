"""Regression tests: standalone release layout must install correctly.

Covers the GitHub Releases updater flow where package.install receives an
extracted standalone package (OVLS Installer layout produced by
scripts/build_package.py), not the project checkout layout.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
SUPPORT = PROJECT / "src" / "linux-support"
sys.path.insert(0, str(SUPPORT))

import desktop  # noqa: E402
import extension_installer  # noqa: E402
import installer  # noqa: E402
import package  # noqa: E402
import updater  # noqa: E402

BUILD_SCRIPT = PROJECT / "scripts" / "build_package.py"


def _build_standalone(out_dir: Path) -> Path:
    """Build standalone OVLS Installer folder using the real build script."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    cmd = [sys.executable, str(BUILD_SCRIPT), "--out", str(out_dir)]
    result = subprocess.run(cmd, cwd=str(PROJECT), capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            f"build_package.py failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    if not (out_dir / "ovls" / "ovls.py").is_file():
        raise AssertionError("built standalone package missing ovls/ovls.py")
    return out_dir


def _build_prefix(root: Path) -> Path:
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
        ";; All keys relative to REGISTRY\\\\Machine\n\n#arch=win64\n",
        encoding="utf-8",
    )
    steam_dir = pfx / "drive_c" / "Program Files (x86)" / "Steam"
    steam_dir.mkdir(parents=True)
    dosdevices = pfx / "dosdevices"
    dosdevices.mkdir()
    (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
    (dosdevices / "z:").symlink_to("/", target_is_directory=True)
    return prefix


class StandaloneReleaseInstallTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-standalone-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.desktop_dir = self.root / "Desktop"
        self.apps_dir = self.data / "applications"
        self.app = self.data / "ovls"
        self.standalone = _build_standalone(self.root / "OVLS Installer")
        self.prefix = _build_prefix(self.root)
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
            package_root=str(self.standalone),
            prefix=str(self.prefix),
            **kwargs,
        )

    def _expected_python_modules(self):
        # Core modules that must be present in a working OVLS install.
        return [
            "ovls/ovls.py",
            "ovls/core.py",
            "ovls/package.py",
            "ovls/manager.py",
            "ovls/updater.py",
            "ovls/installer.py",
            "ovls/launcher.py",
            "ovls/desktop.py",
            "ovls/shortcuts.py",
            "ovls/proton.py",
            "ovls/steam.py",
            "ovls/ipc.py",
            "ovls/layout.py",
            "ovls/extension_installer.py",
            "ovls/diagnostics.py",
            "ovls/filesystem.py",
            "ovls/registry.py",
            "ovls/setup_ui.py",
            "ovls/external/client.py",
        ]

    def test_install_from_standalone_layout_copies_full_code(self):
        # First-time install from standalone release layout into a test prefix.
        result = self._install(update=False)
        self.assertEqual(result["status"], "ok", result)
        self.assertIn(result["copy"]["status"], ("installed", "updated"))
        self.assertGreater(result["copy"]["files"], 10)

        # All expected Python modules present
        for rel in self._expected_python_modules():
            self.assertTrue((self.app / rel).is_file(), f"missing: {rel}")

        # Extension files
        self.assertTrue((self.app / "ovls/extension/index.js").is_file())
        self.assertTrue((self.app / "ovls/extension/info.json").is_file())
        # Assets
        self.assertTrue((self.app / "ovls/assets/ovls.png").is_file())

        # Manifest exists and lists installed files (including key ones)
        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertIn("ovls/ovls.py", manifest)
        self.assertIn("ovls/updater.py", manifest)
        self.assertIn("ovls/external/client.py", manifest)
        self.assertIn("ovls/extension/index.js", manifest)
        self.assertIn("ovls/extension/info.json", manifest)
        self.assertTrue((self.app / "VERSION").is_file())

        # install.sh/README are managed at root, not inside ovls/ as code
        self.assertTrue((self.app / "install.sh").is_file())
        self.assertTrue((self.app / "README.md").is_file())
        self.assertFalse((self.app / "ovls/install.sh").exists())

        # Extension installed into test prefix plugin directory
        target = self.prefix / (
            "pfx/drive_c/users/steamuser/AppData/Roaming/Vortex/plugins/"
            + extension_installer.EXTENSION_ID
        )
        self.assertTrue((target / "index.js").is_file())
        self.assertTrue((target / "info.json").is_file())

        verified = package.verify(prefix=str(self.prefix))
        self.assertEqual(verified["status"], "ok", verified)

    def test_update_existing_installation_from_standalone(self):
        # First install, then update from modified standalone package (update=True)
        self._install(update=False)
        original = (self.app / "ovls/core.py").read_bytes()
        manifest1 = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")

        # Modify source standalone package to simulate a newer release
        (self.standalone / "ovls" / "core.py").write_text(
            (self.standalone / "ovls" / "core.py").read_text(encoding="utf-8")
            + "\n# release-update\n",
            encoding="utf-8",
        )
        (self.standalone / "ovls" / "new_module.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )

        result = self._install(update=True)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["copy"]["status"], "updated")
        self.assertIn("# release-update", (self.app / "ovls/core.py").read_text(encoding="utf-8"))
        self.assertTrue((self.app / "ovls/new_module.py").is_file())
        manifest2 = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        self.assertIn("ovls/new_module.py", manifest2)
        self.assertNotEqual(manifest1, manifest2)
        self.assertNotEqual(original, (self.app / "ovls/core.py").read_bytes())

        verified = package.verify(prefix=str(self.prefix))
        self.assertEqual(verified["status"], "ok", verified)

    def test_rollback_on_install_failure_restores_previous_standalone_install(self):
        self._install(update=False)
        original = (self.app / "ovls/core.py").read_bytes()
        manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
        notes = self.app / "notes.txt"
        notes.write_text("user file\n", encoding="utf-8")

        # Prepare an update in the source
        (self.standalone / "ovls" / "core.py").write_text(
            (self.standalone / "ovls" / "core.py").read_text(encoding="utf-8")
            + "\n# broken\n",
            encoding="utf-8",
        )
        (self.standalone / "ovls" / "extra_broken.py").write_text("X=1\n", encoding="utf-8")

        with mock.patch.object(installer, "install", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self._install(update=True)

        # Previous code restored, no orphan files, manifest intact, user file kept
        self.assertEqual((self.app / "ovls/core.py").read_bytes(), original)
        self.assertFalse((self.app / "ovls/extra_broken.py").exists())
        self.assertEqual((self.app / "MANIFEST.sha256").read_text(encoding="utf-8"), manifest)
        self.assertEqual(notes.read_text(encoding="utf-8"), "user file\n")
        self.assertTrue((self.app / "ovls/updater.py").is_file())

    def test_install_does_not_touch_files_outside_test_prefix(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        outside_prefix = self.root / "other_prefix"
        outside_prefix.mkdir()

        self._install(update=False)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
        # No files created in unrelated prefix-like directory
        self.assertEqual(list(outside_prefix.rglob("*")), [])

    def test_refuses_formally_successful_install_without_code(self):
        # An incomplete standalone package (only install.sh/README) must be rejected.
        broken = self.root / "broken-pkg"
        broken.mkdir()
        (broken / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (broken / "README.md").write_text("readme\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            package.install(
                package_root=str(broken),
                prefix=str(self.prefix),
                update=True,
            )
        # Nothing installed
        self.assertFalse((self.app / "MANIFEST.sha256").exists())
        self.assertFalse((self.app / "ovls").exists())


class StandaloneUpdaterFlowTest(unittest.TestCase):
    """End-to-end local flow: dist archive -> verify -> extract -> validate -> install -> cleanup."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-updflow-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.desktop_dir = self.root / "Desktop"
        self.apps_dir = self.data / "applications"
        self.app = self.data / "ovls"
        self.prefix = _build_prefix(self.root)
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

    def test_full_updater_install_flow_with_real_dist_archive(self):
        dist = PROJECT / "dist"
        archive = dist / "OVLS-Installer.tar.gz"
        sums = dist / "SHA256SUMS"
        self.assertTrue(archive.is_file(), "dist/OVLS-Installer.tar.gz missing")
        self.assertTrue(sums.is_file(), "dist/SHA256SUMS missing")

        # SHA256 verification
        sums_text = sums.read_text(encoding="utf-8")
        expected = updater.expected_checksum_for(sums_text, archive.name)
        updater.verify_sha256(archive, expected)

        # Safe extraction -> detect/validate
        temp_root = self.root / "updtmp"
        temp_root.mkdir()
        extract_dir = temp_root / "extract"
        extract_dir.mkdir()
        updater._safe_extract_tar(archive, extract_dir)
        pkg_root = updater.detect_package_root(extract_dir)
        version = updater.read_package_version(pkg_root)
        self.assertTrue(version)
        updater.validate_package_root(pkg_root, expected_version=version)

        prepared = updater.PreparedRelease(
            package_root=pkg_root,
            version=str(version),
            tag_name=f"v{version}",
            temp_dir=temp_root,
            archive_path=archive,
            checksums_path=sums,
        )
        try:
            # Install from prepared extracted package root (fresh install in test env)
            result = package.install(
                package_root=str(prepared.package_root),
                prefix=str(self.prefix),
                update=False,
            )
            self.assertEqual(result["status"], "ok", result)
            # Installed composition checks - must not be "formally successful" without code
            for rel in (
                "ovls/ovls.py",
                "ovls/core.py",
                "ovls/updater.py",
                "ovls/manager.py",
                "ovls/package.py",
                "ovls/external/client.py",
                "ovls/extension/index.js",
                "ovls/extension/info.json",
            ):
                self.assertTrue((self.app / rel).is_file(), f"missing installed: {rel}")
            manifest = (self.app / "MANIFEST.sha256").read_text(encoding="utf-8")
            self.assertIn("ovls/updater.py", manifest)
            self.assertIn("ovls/extension/index.js", manifest)
            self.assertGreater(len(manifest.strip().splitlines()), 10)
            verified = package.verify(prefix=str(self.prefix))
            self.assertEqual(verified["status"], "ok", verified)

            # Update existing test installation via same prepared root path (update=True)
            result2 = package.install(
                package_root=str(prepared.package_root),
                prefix=str(self.prefix),
                update=True,
            )
            self.assertEqual(result2["status"], "ok", result2)
            self.assertTrue((self.app / "ovls/ovls.py").is_file())
        finally:
            prepared.cleanup()
        self.assertFalse(temp_root.exists(), "prepared.cleanup() must remove temp dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
