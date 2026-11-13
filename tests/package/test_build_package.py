"""Tests for Tools/build_package.py.

The build folder must be a complete, relocatable installer with a readable
layout (``ovls/`` + ``ovls/extension/``), and it must install without the
working project directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))
sys.path.insert(0, str(PROJECT / "scripts"))

import build_package  # noqa: E402
import package  # noqa: E402


def _expected():
    return {str(target) for target, _source in build_package._sources()}


def _build_prefix(root):
    prefix = Path(root) / "compatdata"
    pfx = prefix / "pfx"
    profile = pfx / "drive_c" / "users" / "steamuser"
    (profile / "AppData" / "Roaming" / "Vortex" / "plugins").mkdir(parents=True)
    vortex_app = pfx / "drive_c" / "Program Files" / "Vortex"
    vortex_app.mkdir(parents=True)
    (vortex_app / "Vortex.exe").write_bytes(b"stub")
    (vortex_app / "Uninstall Vortex.exe").write_bytes(b"stub")
    (pfx / "system.reg").write_text(
        "WINE REGISTRY Version 2\n"
        ";; All keys relative to REGISTRY\\\\Machine\n\n#arch=win64\n\n"
        "[Software\\\\Classes\\\\nxm] 1791559462\n"
        '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n',
        encoding="utf-8",
    )
    (pfx / "drive_c" / "Program Files (x86)" / "Steam").mkdir(parents=True)
    (pfx / "dosdevices").mkdir()
    (pfx / "dosdevices" / "c:").symlink_to("../drive_c", target_is_directory=True)
    (pfx / "dosdevices" / "z:").symlink_to("/", target_is_directory=True)
    return prefix


class BuildPackageTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-build-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.out = self.root / "OVLS Installer"
        self.home = self.root / "home"
        (self.home / ".local" / "share" / "Steam" / "steamapps").mkdir(parents=True)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DESKTOP_DIR": str(self.root / "Desktop"),
            "OVLS_NO_GUI": "1",
        }

    def _files(self, out):
        return {
            str(path.relative_to(out))
            for path in out.rglob("*")
            if path.is_file() and path.name != build_package.MARKER
        }

    def test_build_copies_the_complete_package(self):
        built, count = build_package.build(self.out)

        self.assertEqual(built, self.out.resolve())
        self.assertEqual(count, len(_expected()))
        self.assertEqual(self._files(self.out), _expected())
        self.assertTrue((self.out / build_package.MARKER).is_file())
        self.assertEqual(build_package.check(self.out), [])

    def test_build_uses_the_readable_layout(self):
        build_package.build(self.out)

        self.assertTrue((self.out / "ovls" / "ovls.py").is_file())
        self.assertTrue((self.out / "ovls" / "extension" / "index.js").is_file())
        self.assertFalse((self.out / "Development").exists())
        for name in ("install.sh", "README.md", "OVLS Installer.desktop"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_build_is_relocatable(self):
        build_package.build(self.out)
        moved = self.root / "Downloads" / "OVLS"
        shutil.move(str(self.out), str(moved))

        for path in moved.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".sh", ".js"):
                self.assertNotIn(str(PROJECT), path.read_text(encoding="utf-8"), str(path))

        result = subprocess.run(
            ["bash", "install.sh"],
            cwd=moved,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no dialog tool", result.stderr)

    def test_built_package_installs_and_verifies(self):
        build_package.build(self.out)
        prefix = _build_prefix(self.root)
        data = self.root / "data"

        install = subprocess.run(
            ["bash", "install.sh", "install", "--prefix", str(prefix)],
            cwd=self.out,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(install.returncode, 0, install.stdout + install.stderr)
        result = json.loads(install.stdout[install.stdout.index("{"):])
        self.assertEqual(result["status"], "ok", result)

        installed_code = data / "ovls" / "ovls" / "ovls.py"
        self.assertTrue(installed_code.is_file())
        wrapper = (data / "ovls" / "bin" / "ovls-vortex").read_text(encoding="utf-8")
        self.assertIn(str(installed_code), wrapper)

        verify = subprocess.run(
            ["bash", "install.sh", "verify"],
            cwd=self.out,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        verified = json.loads(verify.stdout[verify.stdout.index("{"):])
        self.assertEqual(verified["status"], "ok", verified)

        uninstall = subprocess.run(
            ["bash", "install.sh", "uninstall"],
            cwd=self.out,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(uninstall.returncode, 0, uninstall.stdout + uninstall.stderr)
        self.assertFalse((data / "ovls" / "MANIFEST.sha256").exists())

    def test_rebuild_overwrites_previous_build(self):
        build_package.build(self.out)
        stale = self.out / "ovls" / "stale.py"
        stale.write_text("STALE = True\n", encoding="utf-8")

        build_package.build(self.out)

        self.assertFalse(stale.exists())
        self.assertEqual(self._files(self.out), _expected())

    def test_refuses_foreign_directory(self):
        self.out.mkdir(parents=True)
        (self.out / "important.txt").write_text("keep me\n", encoding="utf-8")

        with self.assertRaises(SystemExit):
            build_package.build(self.out)

        self.assertTrue((self.out / "important.txt").is_file())

    def test_desktop_launcher_is_valid(self):
        build_package.build(self.out)

        result = subprocess.run(
            ["desktop-file-validate", str(self.out / "OVLS Installer.desktop")],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
