"""Tests for exact owner-aware OVLS temp cleanup."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import tempfiles  # noqa: E402


class TempFilesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-tempfiles-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_file_cleanup_is_exact_and_preserves_live_owner(self):
        active = self.root / f".known-{os.getpid()}-active01.tmp"
        orphan = self.root / ".known-2147483647-orphan01.tmp"
        unrelated = self.root / ".other.tmp"
        legacy = self.root / ".known-legacy.tmp"
        for path in (active, orphan, unrelated, legacy):
            path.write_text("data", encoding="utf-8")

        tempfiles.cleanup_orphaned_files(self.root, ".known-")

        self.assertTrue(active.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(legacy.exists(), "untagged files are not assumed orphaned")

    def test_directory_cleanup_is_exact_and_preserves_live_owner(self):
        active = self.root / f".stage-{os.getpid()}-active01.installing"
        orphan = self.root / ".stage-2147483647-orphan01.installing"
        unrelated = self.root / ".stage-legacy.installing"
        for path in (active, orphan, unrelated):
            path.mkdir()
            (path / "marker").write_text("data", encoding="utf-8")

        tempfiles.cleanup_orphaned_dirs(self.root, ".stage-", ".installing")

        self.assertTrue(active.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists(), "untagged directories are not assumed orphaned")

    def test_real_mkstemp_and_mkdtemp_names_are_matched_by_cleanup(self):
        pid = str(os.getpid())
        dead = "2147483647"
        file_cases = [
            (".ovls-nxm.desktop-", ".tmp"),
            (".VERSION-", ".tmp"),
            (".system.reg-", ".ovls.tmp"),
            (".update-check-", ".tmp"),
            (".download-", ".tmp"),
            (".extract-", ".tmp"),
            (".libraryfolders-", ".tmp"),
        ]
        for name_prefix, suffix in file_cases:
            with self.subTest(prefix=name_prefix):
                fd, real = tempfile.mkstemp(prefix=name_prefix + pid + "-", suffix=suffix, dir=str(self.root))
                os.close(fd)
                real_path = Path(real)
                token = real_path.name[len(name_prefix) + len(pid) + 1:len(real_path.name) - len(suffix)]
                orphan = self.root / (name_prefix + dead + "-" + token + suffix)
                real_path.rename(orphan)
                fd, active = tempfile.mkstemp(prefix=name_prefix + pid + "-", suffix=suffix, dir=str(self.root))
                os.close(fd)

                tempfiles.cleanup_orphaned_files(self.root, name_prefix, suffix)

                self.assertFalse(orphan.exists(), "real mkstemp name with dead owner must be removed")
                self.assertTrue(Path(active).exists(), "real mkstemp name of a live owner must be kept")

        dir_cases = [
            (".ovls-stage-", ""),
            ("ovls-update-", ""),
            (".ovls-json-ipc-v2-", ".installing"),
        ]
        for name_prefix, suffix in dir_cases:
            with self.subTest(prefix=name_prefix):
                real_path = Path(tempfile.mkdtemp(prefix=name_prefix + pid + "-", suffix=suffix, dir=str(self.root)))
                token = real_path.name[len(name_prefix) + len(pid) + 1:len(real_path.name) - len(suffix)]
                orphan = self.root / (name_prefix + dead + "-" + token + suffix)
                real_path.rename(orphan)
                active = Path(tempfile.mkdtemp(prefix=name_prefix + pid + "-", suffix=suffix, dir=str(self.root)))

                tempfiles.cleanup_orphaned_dirs(self.root, name_prefix, suffix)

                self.assertFalse(orphan.exists(), "real mkdtemp name with dead owner must be removed")
                self.assertTrue(active.exists(), "real mkdtemp name of a live owner must be kept")


if __name__ == "__main__":
    unittest.main(verbosity=2)
