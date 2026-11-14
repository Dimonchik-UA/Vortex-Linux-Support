"""Tests for automatic cleanup of OVLS backup snapshots (MAX_BACKUP_COUNT).

Every test builds a throw-away HOME/XDG tree inside a temporary directory.
No real user directories, Vortex installation or Proton prefix are touched.

Backup snapshot structure created by copy_tree() is:

    backups_root() / <stamp> / app/     (and optionally extension/, registry/)
where <stamp> matches _stamp() i.e. YYYY-MM-DD_HHMMSS_ffffff.
"""

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
import installer  # noqa: E402
import package  # noqa: E402


class BackupCleanupTest(unittest.TestCase):
    def setUp(self):
        # Create isolated temp tree for XDG dirs and fake app root
        self.root = Path(tempfile.mkdtemp(prefix="ovls-backup-cleanup-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.desktop_dir = self.root / "Desktop"
        self.app = self.data / "ovls"

        # Ensure app dir exists
        self.app.mkdir(parents=True)

        # Mock XDG environment to point into our temp tree
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

        # Mock desktop.data_home() to return our test data dir
        self.desktop_patcher = mock.patch.object(desktop, "data_home", return_value=self.data)
        self.desktop_patcher.start()
        self.addCleanup(self.desktop_patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _create_snapshot(self, backups_root, stamp):
        """Create one snapshot directory backups_root/<stamp>/app."""
        snapshot_dir = backups_root / stamp
        (snapshot_dir / "app").mkdir(parents=True)
        return snapshot_dir

    def _snapshot_names(self, backups_root):
        """Return sorted names of snapshot directories matching _stamp()."""
        return sorted(
            d.name
            for d in backups_root.iterdir()
            if d.is_dir() and not d.is_symlink() and package._BACKUP_STAMP_RE.match(d.name)
        )

    def test_cleanup_keeps_only_latest_backups(self):
        """Five correctly formed backups → only three newest remain."""
        backups_root = package.backups_root()
        backups_root.mkdir(parents=True)

        stamps = []
        for _ in range(5):
            stamp = package._stamp()
            stamps.append(stamp)
            self._create_snapshot(backups_root, stamp)

        # Arbitrary-named directory (should NOT be removed)
        (backups_root / "arbitrary-name").mkdir()

        # Symlink (should NOT be removed nor followed)
        symlink_target = backups_root / "symlink-target"
        symlink_target.symlink_to("/nonexistent", target_is_directory=True)

        # Regular file (should NOT be removed)
        (backups_root / "regular-file.txt").write_text("data")

        package._cleanup_old_backups()

        remaining_dirs = sorted(
            d.name for d in backups_root.iterdir() if d.is_dir() and not d.is_symlink()
        )
        # 3 newest snapshots + arbitrary-name directory = 4 directories
        self.assertEqual(len(remaining_dirs), 4)

        # The 3 newest timestamped snapshots remain
        expected_newest = stamps[-3:]
        for stamp in expected_newest:
            self.assertTrue((backups_root / stamp / "app").is_dir(), f"Missing backup: {stamp}")
        # The 2 oldest snapshots are removed
        for stamp in stamps[:2]:
            self.assertFalse((backups_root / stamp).exists(), f"Old backup should be removed: {stamp}")

        # Arbitrary-named directory survives
        self.assertTrue((backups_root / "arbitrary-name").is_dir())

        # Regular file survives
        self.assertTrue((backups_root / "regular-file.txt").is_file())

        # Symlink survives and is not followed
        self.assertTrue(symlink_target.is_symlink())

    def test_cleanup_missing_backups_root_no_error(self):
        """Absent backups_root() is handled without error."""
        # Do not create the backups directory at all
        package._cleanup_old_backups()  # Should not raise

    def test_cleanup_idempotent(self):
        """Calling _cleanup_old_backups twice is safe."""
        backups_root = package.backups_root()
        backups_root.mkdir(parents=True)

        stamps = []
        for _ in range(4):
            stamps.append(package._stamp())
            self._create_snapshot(backups_root, stamps[-1])

        package._cleanup_old_backups()
        remaining_after_first = self._snapshot_names(backups_root)
        self.assertEqual(len(remaining_after_first), 3)

        # Second call should not error and state stays intact
        package._cleanup_old_backups()
        remaining_after_second = self._snapshot_names(backups_root)
        self.assertEqual(remaining_after_second, remaining_after_first)
        self.assertEqual(set(remaining_after_second), set(stamps[-3:]))

    def test_cleanup_symlink_not_followed(self):
        """Symlinks are not followed and not removed."""
        backups_root = package.backups_root()
        backups_root.mkdir(parents=True)

        stamps = []
        for _ in range(4):
            stamps.append(package._stamp())
            self._create_snapshot(backups_root, stamps[-1])

        # Symlink pointing outside the test tree with a real file behind it
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "target.txt").write_text("secret")
        symlink = backups_root / "symlink-to-outside"
        symlink.symlink_to(outside, target_is_directory=True)

        package._cleanup_old_backups()

        # Symlink survives
        self.assertTrue(symlink.is_symlink())
        # Target outside is NOT deleted even though cleanup removed older snapshots
        self.assertTrue((outside / "target.txt").is_file())

        # 3 newest snapshots remain; oldest removed
        remaining = self._snapshot_names(backups_root)
        self.assertEqual(len(remaining), 3)
        self.assertEqual(set(remaining), set(stamps[-3:]))

    def test_cleanup_preserves_arbitrary_named_directory(self):
        """Directories not matching _stamp() format are never touched."""
        backups_root = package.backups_root()
        backups_root.mkdir(parents=True)

        stamps = []
        for _ in range(3):  # exactly MAX_BACKUP_COUNT → nothing should be removed
            stamps.append(package._stamp())
            self._create_snapshot(backups_root, stamps[-1])

        arbitrary_dirs = [
            "extension-backup",
            "my-custom-backup",
            "backup-old",
            "2026-11-13_120000",          # different format → no match
        ]
        for name in arbitrary_dirs:
            (backups_root / name).mkdir()

        package._cleanup_old_backups()

        # All arbitrary dirs survive
        for name in arbitrary_dirs:
            self.assertTrue((backups_root / name).is_dir(), f"Arbitrary dir removed: {name}")

        # All 3 valid backups remain
        for stamp in stamps:
            self.assertTrue((backups_root / stamp / "app").is_dir())

    def test_cleanup_preserves_extension_backups_in_same_snapshot(self):
        """Extension backup directories under the same timestamp snapshot are kept together.

        Snapshot structure: backups/<stamp>/{app,extension,registry} — all
        subdirectories belong to one snapshot and are removed together.
        """
        backups_root = package.backups_root()
        backups_root.mkdir(parents=True)

        stamps = []
        for _ in range(5):
            stamp = package._stamp()
            stamps.append(stamp)
            snapshot_dir = backups_root / stamp
            (snapshot_dir / "app").mkdir(parents=True)
            (snapshot_dir / "extension").mkdir(parents=True)
            (snapshot_dir / "registry").mkdir(parents=True)
            (snapshot_dir / "app" / "file.txt").write_text(stamp)
            (snapshot_dir / "extension" / "ext.dll").write_text(stamp)

        package._cleanup_old_backups()

        remaining_snapshots = self._snapshot_names(backups_root)
        self.assertEqual(len(remaining_snapshots), 3)
        self.assertEqual(set(remaining_snapshots), set(stamps[-3:]))

        # For kept snapshots, app + extension + registry all survive
        for stamp in stamps[-3:]:
            self.assertTrue((backups_root / stamp / "app").is_dir())
            self.assertTrue((backups_root / stamp / "extension").is_dir())
            self.assertTrue((backups_root / stamp / "registry").is_dir())

        # For removed snapshots, the entire tree is gone
        for stamp in stamps[:2]:
            self.assertFalse((backups_root / stamp).exists())

    def test_cleanup_after_successful_update(self):
        """After a successful update, cleanup runs and old backups are pruned.

        Integration-style test using the real copy_tree/install flow with a
        mocked installer so no real Proton operations happen.
        """
        pkg_root = self.root / "testpkg"
        pkg_root.mkdir()
        (pkg_root / "install.sh").write_text("#!/bin/sh\necho test\n")
        (pkg_root / "README.md").write_text("test\n")
        support = pkg_root / "src" / "linux-support"
        support.mkdir(parents=True)
        (support / "ovls.py").write_text("print('test')\n")

        prefix = self.root / "compatdata"
        pfx = prefix / "pfx"
        profile = pfx / "drive_c" / "users" / "steamuser"
        plugins = profile / "AppData" / "Roaming" / "Vortex" / "plugins"
        plugins.mkdir(parents=True)
        vortex_app = pfx / "drive_c" / "Program Files" / "Vortex"
        vortex_app.mkdir(parents=True)
        (vortex_app / "Vortex.exe").write_bytes(b"stub")
        (pfx / "system.reg").write_text(
            "WINE REGISTRY Version 2\n#arch=win64\n"
            "[Software\\\\Classes\\\\nxm] 1791559462\n"
            '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n',
            encoding="utf-8",
        )
        dosdevices = pfx / "dosdevices"
        dosdevices.mkdir()
        (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
        (dosdevices / "z:").symlink_to("/", target_is_directory=True)

        installer_return = {"status": "ok", "extension": {"status": "ok"}}
        with mock.patch.object(installer, "install", return_value=installer_return):
            result1 = package.install(package_root=str(pkg_root), prefix=str(prefix))
            self.assertEqual(result1["status"], "ok")
            self.assertEqual(result1["copy"]["status"], "installed")

            backups_root = package.backups_root()
            first_count = len(self._snapshot_names(backups_root))
            self.assertEqual(first_count, 1)

            (support / "ovls.py").write_text("print('updated')\n")
            result2 = package.install(package_root=str(pkg_root), prefix=str(prefix), update=True)
            self.assertEqual(result2["status"], "ok")
            self.assertEqual(result2["copy"]["status"], "updated")

            second_count = len(self._snapshot_names(backups_root))
            self.assertEqual(second_count, 2)

            (support / "ovls.py").write_text("print('updated2')\n")
            result3 = package.install(package_root=str(pkg_root), prefix=str(prefix), update=True)
            self.assertEqual(result3["status"], "ok")
            self.assertEqual(result3["copy"]["status"], "updated")

            third_count = len(self._snapshot_names(backups_root))
            self.assertEqual(third_count, 3)

            (support / "ovls.py").write_text("print('updated3')\n")
            result4 = package.install(package_root=str(pkg_root), prefix=str(prefix), update=True)
            self.assertEqual(result4["status"], "ok")

            fourth_count = len(self._snapshot_names(backups_root))
            self.assertEqual(fourth_count, 3)

            kept_stamps = [
                Path(result["copy"]["backup"]).parent.name
                for result in (result2, result3, result4)
            ]
            remaining = self._snapshot_names(backups_root)
            self.assertEqual(set(remaining), set(kept_stamps))

    def test_cleanup_skipped_on_failed_update(self):
        """If update fails (exception), old backups are preserved and cleanup does not run.

        The backup created for the failed attempt is preserved for rollback.
        """
        pkg_root = self.root / "testpkg"
        pkg_root.mkdir()
        (pkg_root / "install.sh").write_text("#!/bin/sh\necho test\n")
        (pkg_root / "README.md").write_text("test\n")
        support = pkg_root / "src" / "linux-support"
        support.mkdir(parents=True)
        (support / "ovls.py").write_text("print('test')\n")

        prefix = self.root / "compatdata"
        pfx = prefix / "pfx"
        profile = pfx / "drive_c" / "users" / "steamuser"
        plugins = profile / "AppData" / "Roaming" / "Vortex" / "plugins"
        plugins.mkdir(parents=True)
        vortex_app = pfx / "drive_c" / "Program Files" / "Vortex"
        vortex_app.mkdir(parents=True)
        (vortex_app / "Vortex.exe").write_bytes(b"stub")
        (pfx / "system.reg").write_text(
            "WINE REGISTRY Version 2\n#arch=win64\n"
            "[Software\\\\Classes\\\\nxm] 1791559462\n"
            '@="\\"C:\\\\Program Files\\\\Vortex\\\\Vortex.exe\\" -d \\"%1\\""\n',
            encoding="utf-8",
        )
        dosdevices = pfx / "dosdevices"
        dosdevices.mkdir()
        (dosdevices / "c:").symlink_to("../drive_c", target_is_directory=True)
        (dosdevices / "z:").symlink_to("/", target_is_directory=True)

        installer_return = {"status": "ok", "extension": {"status": "ok"}}
        with mock.patch.object(installer, "install", return_value=installer_return):
            result1 = package.install(package_root=str(pkg_root), prefix=str(prefix))
            self.assertEqual(result1["status"], "ok")

        backups_root = package.backups_root()
        first_count = len(self._snapshot_names(backups_root))
        self.assertEqual(first_count, 1)

        (support / "ovls.py").write_text("print('updated')\n")

        with mock.patch.object(installer, "install", side_effect=OSError("installer failed")):
            with self.assertRaises(OSError):
                package.install(package_root=str(pkg_root), prefix=str(prefix), update=True)

        backups_after_failure = self._snapshot_names(backups_root)
        # Original backup + the backup created for the failed attempt (for rollback)
        self.assertEqual(len(backups_after_failure), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)