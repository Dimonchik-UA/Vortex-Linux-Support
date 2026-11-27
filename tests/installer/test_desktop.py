"""Tests for src/linux-support/desktop.py and the NXM launch path.

All paths are throw-away directories inside Tests/Installer. No real desktop
entry, mimeapps.list or Vortex process is touched.
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import desktop  # noqa: E402
import proton  # noqa: E402


class FakeRunner:
    """Stand-in for `xdg-mime` that keeps the default in memory."""

    def __init__(self, default=None):
        self.default = default
        self.calls = []

    def __call__(self, arguments):
        self.calls.append(list(arguments))
        if arguments[:2] == ["query", "default"]:
            return CompletedProcess(arguments, 0, (self.default or "") + "\n", "")
        if arguments[0] == "default":
            self.default = arguments[1]
            return CompletedProcess(arguments, 0, "", "")
        return CompletedProcess(arguments, 0, "", "")


class DesktopHandlerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-desktop-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.apps = self.data / "applications"
        self.apps.mkdir(parents=True)
        self.prefix = self.root / "compatdata"
        self.prefix.mkdir()
        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.data),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state),
                "HOME": str(self.root / "home"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.database = mock.patch.object(desktop, "_update_desktop_database")
        self.database.start()
        self.addCleanup(self.database.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_install_creates_wrapper_and_desktop(self):
        runner = FakeRunner("com.nexusmods.vortex.dev.desktop")
        result = desktop.install_handler(
            self.prefix, PROJECT, applications_dir=self.apps, runner=runner
        )

        self.assertEqual(result["status"], "installed")
        self.assertTrue(result["registered"])
        self.assertEqual(result["previous"], "com.nexusmods.vortex.dev.desktop")

        wrapper = Path(result["wrapper"])
        self.assertTrue(wrapper.is_file())
        self.assertTrue(wrapper.stat().st_mode & stat.S_IXUSR)
        wrapper_text = wrapper.read_text(encoding="utf-8")
        self.assertIn(str(PROJECT / "src" / "linux-support" / "ovls.py"), wrapper_text)
        self.assertIn("--prefix", wrapper_text)
        self.assertIn('--uri "$1"', wrapper_text)

        entry = Path(result["desktop"])
        self.assertEqual(entry.parent, self.apps)
        text = entry.read_text(encoding="utf-8")
        self.assertIn("Terminal=false", text)
        self.assertIn("MimeType=x-scheme-handler/nxm;", text)
        self.assertIn(f"Exec={wrapper} %u", text)

        previous = self.data / "ovls" / "nxm-previous-default"
        self.assertEqual(previous.read_text(encoding="utf-8").strip(), "com.nexusmods.vortex.dev.desktop")
        self.assertIn(
            ["default", "ovls-nxm.desktop", "x-scheme-handler/nxm"], runner.calls
        )

    def test_repeated_install_is_unchanged(self):
        runner = FakeRunner(None)
        desktop.install_handler(self.prefix, PROJECT, applications_dir=self.apps, runner=runner)
        second = desktop.install_handler(
            self.prefix, PROJECT, applications_dir=self.apps, runner=runner
        )
        self.assertEqual(second["status"], "unchanged")
        self.assertTrue(second["registered"])
        self.assertEqual(runner.default, desktop.HANDLER_ID)

    def test_dry_run_writes_nothing(self):
        result = desktop.install_handler(
            self.prefix, PROJECT, dry_run=True, applications_dir=self.apps, runner=FakeRunner(None)
        )
        self.assertEqual(result["status"], "would_install")
        self.assertFalse((self.data / "ovls").exists())
        self.assertFalse((self.apps / desktop.HANDLER_ID).exists())

    def test_handler_state(self):
        missing = desktop.handler_state(applications_dir=self.apps, runner=FakeRunner(None))
        self.assertEqual(missing["status"], "missing")

        desktop.install_handler(
            self.prefix, PROJECT, applications_dir=self.apps, runner=FakeRunner(None)
        )
        runner = FakeRunner(desktop.HANDLER_ID)
        state = desktop.handler_state(applications_dir=self.apps, runner=runner)
        self.assertEqual(state["status"], "ok")
        self.assertTrue(state["registered"])

    def test_remove_restores_previous_handler(self):
        previous_id = "com.nexusmods.vortex.dev.desktop"
        (self.apps / previous_id).write_text("[Desktop Entry]\n", encoding="utf-8")
        runner = FakeRunner(previous_id)
        desktop.install_handler(self.prefix, PROJECT, applications_dir=self.apps, runner=runner)

        removed = desktop.remove_handler(applications_dir=self.apps, runner=runner)
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(removed["restored"], previous_id)
        self.assertEqual(runner.default, previous_id)
        self.assertFalse((self.apps / desktop.HANDLER_ID).exists())
        self.assertFalse((self.data / "ovls").exists())

    def test_remove_without_previous_cleans_mimeapps(self):
        mimeapps = self.config / "mimeapps.list"
        mimeapps.parent.mkdir(parents=True)
        mimeapps.write_text(
            "[Default Applications]\n"
            "x-scheme-handler/nxm=ovls-nxm.desktop;org.example.Other.desktop;\n"
            "\n"
            "[Added Associations]\n"
            "x-scheme-handler/nxm=ovls-nxm.desktop;foo.desktop;\n",
            encoding="utf-8",
        )
        runner = FakeRunner(desktop.HANDLER_ID)
        desktop.install_handler(self.prefix, PROJECT, applications_dir=self.apps, runner=runner)
        desktop.remove_handler(applications_dir=self.apps, runner=runner)

        text = mimeapps.read_text(encoding="utf-8")
        self.assertIn("x-scheme-handler/nxm=org.example.Other.desktop;", text)
        self.assertIn("x-scheme-handler/nxm=foo.desktop;", text)
        self.assertNotIn("ovls-nxm.desktop", text)
        backups = list(self.config.glob("mimeapps.list.ovls-backup-*"))
        self.assertEqual(len(backups), 1)

    def test_desktop_temp_cleanup_preserves_live_and_unrelated_files(self):
        target = self.apps / desktop.HANDLER_ID
        active = self.apps / f".{target.name}-{os.getpid()}-active01.tmp"
        orphan = self.apps / f".{target.name}-2147483647-orphan01.tmp"
        unrelated = self.apps / ".someone-elses.tmp"
        for item in (active, orphan, unrelated):
            item.write_text("temp", encoding="utf-8")

        desktop._write_text(target, "replacement")

        self.assertTrue(active.exists())
        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.exists())

    def write_mimeapps(self, text):
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / "mimeapps.list").write_text(text, encoding="utf-8")

    def handler_entry(self):
        return (
            "[Default Applications]\n"
            "x-scheme-handler/nxm=ovls-nxm.desktop;org.example.Other.desktop;\n"
        )

    def backups(self):
        return sorted(self.config.glob("mimeapps.list.ovls-backup-*"))

    def test_identical_content_does_not_add_a_backup(self):
        self.write_mimeapps(self.handler_entry())
        self.assertTrue(desktop._remove_default_entry())
        self.assertEqual(len(self.backups()), 1)

        self.write_mimeapps(self.handler_entry())
        with mock.patch.object(desktop.shutil, "copy2", wraps=desktop.shutil.copy2) as copy:
            self.assertTrue(desktop._remove_default_entry())
        copy.assert_not_called()
        self.assertEqual(len(self.backups()), 1)

    def test_changed_content_replaces_older_backup_and_keeps_restore_point(self):
        self.write_mimeapps(self.handler_entry())
        desktop._remove_default_entry()
        first = self.backups()[0]

        before_second = self.handler_entry() + "[Added Associations]\nfoo/bar=other.desktop;\n"
        self.write_mimeapps(before_second)
        self.assertTrue(desktop._remove_default_entry())

        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertNotEqual(backups[0], first)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), before_second)

    def test_backup_survives_when_write_fails(self):
        self.write_mimeapps(self.handler_entry())
        desktop._remove_default_entry()
        first = self.backups()[0]

        self.write_mimeapps(self.handler_entry() + "[Added Associations]\nfoo/bar=x.desktop;\n")
        with mock.patch.object(desktop, "_write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                desktop._remove_default_entry()

        self.assertTrue(first.is_file())
        self.assertGreaterEqual(len(self.backups()), 1)

    def test_repeated_register_remove_cycles_stay_bounded(self):
        for cycle in range(5):
            self.write_mimeapps(
                self.handler_entry() + f"[Added Associations]\nfoo/{cycle}=x.desktop;\n"
            )
            desktop._remove_default_entry()
        self.assertEqual(len(self.backups()), 1)


class NxmCommandTest(unittest.TestCase):
    def test_prepare_launch_forwards_download_argument(self):
        root = Path(tempfile.mkdtemp(prefix="ovls-nxm-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, root, True)
        proton_path = root / "proton"
        proton_path.write_text("#!/bin/sh\n", encoding="utf-8")
        proton_path.chmod(0o755)
        vortex_path = root / "Vortex.exe"
        vortex_path.write_bytes(b"stub")
        prefix = root / "compatdata"
        (prefix / "pfx").mkdir(parents=True)
        steam = root / "steam"
        steam.mkdir()

        command, env = proton._prepare_launch(
            str(proton_path),
            str(vortex_path),
            str(prefix),
            steam_client_path=str(steam),
            vortex_args=["-d", "nxm://falloutnv/mods/1/files/2"],
        )
        self.assertEqual(command[0], str(proton_path))
        self.assertEqual(command[1], "run")
        self.assertEqual(command[2], str(vortex_path))
        self.assertEqual(command[-2:], ["-d", "nxm://falloutnv/mods/1/files/2"])
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix))


if __name__ == "__main__":
    unittest.main(verbosity=2)
