"""Tests for scripts/build_package.py.

The build folder must be a complete, relocatable installer with a readable
layout (``ovls/`` + ``ovls/extension/``), and it must install without the
working project directory.
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

    def test_readme_is_installer_guide(self):
        build_package.build(self.out)

        content = (self.out / "README.md").read_bytes()
        self.assertEqual(content, (PROJECT / "installer" / "README.md").read_bytes())
        self.assertNotEqual(content, (PROJECT / "README.md").read_bytes())
        text = content.decode("utf-8")
        self.assertIn("OVLS Installer — Standalone Package", text)
        self.assertNotIn("docs/assets/ovls-preview.png", text)

    def test_archive_contains_exact_installer_readme(self):
        build_package.build(self.out)
        archive_path = self.root / "OVLS-Installer.tar.gz"
        sums_path = self.root / "SHA256SUMS"
        digest = build_package.write_archive(self.out, archive_path, sums_path)

        self.assertEqual(len(digest), 64)
        self.assertIn(archive_path.name, sums_path.read_text(encoding="utf-8"))
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            readme = next(m for m in members if Path(m.name).name == "README.md")
            content = tar.extractfile(readme).read()
        self.assertEqual(content, (PROJECT / "installer" / "README.md").read_bytes())
        self.assertNotIn(b"docs/assets/ovls-preview.png", content)

    def test_version_consistency_accepts_current_sources(self):
        version = json.loads(
            (PROJECT / "src" / "extension" / "info.json").read_text(encoding="utf-8")
        )["version"]
        self.assertEqual(build_package.check_version_consistency(), version)

    def test_version_consistency_rejects_mismatch(self):
        project = self.root / "version-project"
        (project / "src" / "linux-support").mkdir(parents=True)
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "linux-support" / "core.py").write_text(
            'VERSION = "0.2.0"\n', encoding="utf-8"
        )
        (project / "src" / "extension" / "info.json").write_text(
            json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            build_package.check_version_consistency(project)

        self.assertIn("version mismatch", str(caught.exception))


    def test_main_blocks_build_before_creating_output(self):
        with mock.patch.object(
            build_package, "check_version_consistency", side_effect=SystemExit("ERROR: drift")
        ) as check, mock.patch.object(build_package, "build") as build:
            with self.assertRaises(SystemExit):
                build_package.main(["--out", str(self.out)])

        check.assert_called_once_with()
        build.assert_not_called()
        self.assertFalse(self.out.exists())

    def test_version_check_rejects_missing_core(self):
        project = self.root / "missing-core"
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "extension" / "info.json").write_text(
            json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            build_package.check_version_consistency(project)

        self.assertIn("cannot read", str(caught.exception))

    def test_version_check_rejects_invalid_json(self):
        project = self.root / "invalid-json"
        (project / "src" / "linux-support").mkdir(parents=True)
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "linux-support" / "core.py").write_text(
            'VERSION = "0.1.0"\n', encoding="utf-8"
        )
        (project / "src" / "extension" / "info.json").write_text(
            "{not json", encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            build_package.check_version_consistency(project)

        self.assertIn("cannot read", str(caught.exception))

    def test_version_check_accepts_bom(self):
        project = self.root / "bom"
        (project / "src" / "linux-support").mkdir(parents=True)
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "linux-support" / "core.py").write_text(
            '\ufeffVERSION = "0.1.0"\n', encoding="utf-8"
        )
        (project / "src" / "extension" / "info.json").write_text(
            '\ufeff' + json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )

        self.assertEqual(build_package.check_version_consistency(project), "0.1.0")

    def test_version_check_rejects_non_semver_version(self):
        project = self.root / "non-semver"
        (project / "src" / "linux-support").mkdir(parents=True)
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "linux-support" / "core.py").write_text(
            'VERSION = "latest"\n', encoding="utf-8"
        )
        (project / "src" / "extension" / "info.json").write_text(
            json.dumps({"version": "latest"}), encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            build_package.check_version_consistency(project)

        self.assertIn("invalid VERSION", str(caught.exception))

    def test_version_check_rejects_conflicting_core_assignments(self):
        project = self.root / "dual-version"
        (project / "src" / "linux-support").mkdir(parents=True)
        (project / "src" / "extension").mkdir(parents=True)
        (project / "src" / "linux-support" / "core.py").write_text(
            'VERSION = "0.1.0"\nVERSION = "0.2.0"\n', encoding="utf-8"
        )
        (project / "src" / "extension" / "info.json").write_text(
            json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )

        with self.assertRaises(SystemExit) as caught:
            build_package.check_version_consistency(project)

        self.assertIn("exactly one valid VERSION", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
