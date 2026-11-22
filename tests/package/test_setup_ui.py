"""Tests for src/linux-support/setup_ui.py.

Dialogs and package operations are mocked; only the decision logic is tested.
"""

import os
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
sys.path.insert(0, str(PROJECT / "scripts"))

import package  # noqa: E402
import setup_ui  # noqa: E402
import build_package  # noqa: E402


def _make_prefix(root, name="compatdata"):
    prefix = Path(root) / name
    vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
    vortex.mkdir(parents=True)
    (vortex / "Vortex.exe").write_bytes(b"stub")
    (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
    return prefix


class SetupUiTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-setup-", dir=str(HERE)))
        self.prefix = _make_prefix(self.root)
        self.other = _make_prefix(self.root, "other")

        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_STATE_HOME": str(self.root / "state"),
                "XDG_DESKTOP_DIR": str(self.root / "Desktop"),
                "HOME": str(self.root / "home"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        # Dialog-based actions check launcher._dialog_tool() before using the
        # functions above. Headless CI has no kdialog/zenity, so without this
        # mock the guard refuses the action before any assertable call happens.
        for name in (
            "show_message", "show_info", "confirm", "choose_directory", "_dialog_tool"
        ):
            patcher = mock.patch.object(setup_ui.launcher, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self._dialog_tool.return_value = "kdialog"
        self.confirm.return_value = True
        self.choose_directory.return_value = None

        detect = mock.patch.object(setup_ui.installer, "detect", return_value={"running_vortex": []})
        self.detect = detect.start()
        self.addCleanup(detect.stop)

        detect_prefix = mock.patch.object(
            setup_ui.package, "detect_prefix", return_value=self.prefix
        )
        self.detect_prefix = detect_prefix.start()
        self.addCleanup(detect_prefix.stop)

        vortex_exe = mock.patch.object(
            setup_ui.launcher,
            "vortex_exe",
            return_value=self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe",
        )
        vortex_exe.start()
        self.addCleanup(vortex_exe.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _manifest(self):
        manifest = package.app_root() / package.MANIFEST_NAME
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("", encoding="utf-8")
        return manifest

    def test_install_from_package_copies_the_tree(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"status": "installed"},
            "installation": {"extension": {"status": "installed"}},
            "nxm_handler": {"status": "installed"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(setup_ui.package, "install", return_value=result) as install:
            setup_ui.action_install()

        install.assert_called_once_with(prefix=str(self.prefix), update=False)
        self.show_info.assert_called_once()
        self.assertIn("Установка завершена", self.show_info.call_args[0][0])

    def test_install_updates_an_existing_copy(self):
        self._manifest()
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"status": "updated"},
            "installation": {"extension": {"status": "unchanged"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(setup_ui.package, "install", return_value=result) as install:
            setup_ui.action_install()

        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.assertIn("Обновление завершено", self.show_info.call_args[0][0])

    def test_install_refuses_while_vortex_runs(self):
        self.detect.return_value = {"running_vortex": [{"pid": 123}]}
        with mock.patch.object(setup_ui.package, "install") as install:
            setup_ui.action_install()

        install.assert_not_called()
        self.assertIn("Vortex запущен", self.show_message.call_args[0][0])

    def test_install_rejects_a_directory_without_pfx(self):
        self.detect_prefix.return_value = None
        self.choose_directory.return_value = self.root / "not-a-prefix"
        with mock.patch.object(setup_ui.package, "save_prefix") as save, \
             mock.patch.object(setup_ui.package, "install") as install:
            setup_ui.action_install()

        save.assert_not_called()
        install.assert_not_called()
        self.assertIn("не prefix Proton", self.show_message.call_args[0][0])

    def test_install_saves_a_chosen_prefix(self):
        self.detect_prefix.return_value = None
        self.choose_directory.return_value = self.other
        result = {
            "status": "ok",
            "prefix": str(self.other),
            "copy": {"status": "installed"},
            "installation": {"extension": {"status": "installed"}},
            "nxm_handler": {"status": "installed"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(setup_ui.package, "is_prefix", return_value=True), \
             mock.patch.object(setup_ui.package, "save_prefix") as save, \
             mock.patch.object(setup_ui.package, "install", return_value=result) as install:
            setup_ui.action_install()

        save.assert_called_once_with(self.other)
        install.assert_called_once()
        self.show_info.assert_called_once()

    def test_install_explains_a_skipped_shortcut(self):
        skipped = {"path": "~/.local/share/applications/ovls-vortex.desktop"}
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"status": "installed"},
            "installation": {"extension": {"status": "installed"}},
            "nxm_handler": {"status": "installed"},
            "shortcuts": {"status": "attention_required", "skipped": [skipped]},
        }
        with mock.patch.object(setup_ui.package, "install", return_value=result):
            setup_ui.action_install()

        message = self.show_message.call_args[0][0]
        self.assertIn(skipped["path"], message)
        self.show_info.assert_not_called()

    def test_uninstall_without_installation_does_nothing(self):
        with mock.patch.object(setup_ui.package, "uninstall") as uninstall:
            setup_ui.action_uninstall()

        uninstall.assert_not_called()
        self.assertIn("нечего", self.show_message.call_args[0][0])

    def test_uninstall_requires_confirmation(self):
        self._manifest()
        self.confirm.return_value = False
        with mock.patch.object(setup_ui.package, "uninstall") as uninstall:
            setup_ui.action_uninstall()

        uninstall.assert_not_called()

    def test_uninstall_blocked_keeps_everything(self):
        self._manifest()
        with mock.patch.object(
            setup_ui.package, "uninstall", return_value={"status": "blocked", "message": "kept"}
        ):
            setup_ui.action_uninstall()

        self.assertIn("kept", self.show_message.call_args[0][0])
        self.show_info.assert_not_called()

    def test_uninstall_success_keeps_the_window_open(self):
        self._manifest()
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "installation": {"extension": {"status": "removed"}, "nxm_handler": {"status": "removed"}},
            "shortcuts": {"removed": ["a", "b"]},
        }
        with mock.patch.object(setup_ui.package, "uninstall", return_value=result):
            setup_ui.action_uninstall()

        text = self.show_info.call_args[0][0]
        self.assertIn("Компоненты удалены", text)
        self.assertIn("можно оставить открытым", text)
        self.show_message.assert_not_called()

    def test_verify_reports_attention_required(self):
        result = {
            "status": "attention_required",
            "prefix": str(self.prefix),
            "copy": {"status": "missing"},
            "shortcuts": {"status": "missing", "files": {}},
            "reason": "copy_missing",
        }
        with mock.patch.object(setup_ui.package, "verify", return_value=result):
            setup_ui.action_verify()

        text = self.show_message.call_args[0][0]
        self.assertIn("требует внимания", text)
        self.assertIn("copy_missing", text)

    def test_help_shows_packaged_readme(self):
        with mock.patch.object(setup_ui.launcher, "show_text_file") as show:
            setup_ui.action_help()
        show.assert_called_once()
        self.assertEqual(show.call_args[0][0], PROJECT / "README.md")
        self.assertTrue((PROJECT / "README.md").is_file())

    def test_help_reports_missing_readme(self):
        with mock.patch.object(
            setup_ui, "readme_path", return_value=Path("/nonexistent/README.md")
        ), mock.patch.object(setup_ui.launcher, "show_text_file") as show:
            setup_ui.action_help()
        show.assert_called_once()

    def test_run_dispatches_help(self):
        with mock.patch.object(
            setup_ui, "choose_action", side_effect=["help", None]
        ), mock.patch.object(setup_ui, "action_help") as help_action:
            self.assertEqual(setup_ui.run(), 0)
        help_action.assert_called_once()

    def test_run_without_dialog_tool_reports_usage(self):
        with mock.patch.object(setup_ui.launcher, "_dialog_tool", return_value=None):
            self.assertEqual(setup_ui.run(), 2)

    def test_run_returns_after_exit(self):
        with mock.patch.object(
            setup_ui, "choose_action", side_effect=["verify", None]
        ) as choose, mock.patch.object(setup_ui, "action_verify") as verify:
            self.assertEqual(setup_ui.run(), 0)

        self.assertEqual(choose.call_count, 2)
        verify.assert_called_once()


class ShowTextFileTest(unittest.TestCase):
    def test_kdialog_uses_textbox(self):
        with mock.patch.object(setup_ui.launcher, "_dialog_tool", return_value="kdialog"), \
                mock.patch.object(setup_ui.launcher, "_run_dialog") as run:
            setup_ui.launcher.show_text_file(PROJECT / "README.md")
        arguments = run.call_args[0][1]
        self.assertEqual(arguments[0], "--textbox")
        self.assertEqual(arguments[1], str(PROJECT / "README.md"))

    def test_zenity_uses_text_info(self):
        with mock.patch.object(setup_ui.launcher, "_dialog_tool", return_value="zenity"), \
                mock.patch.object(setup_ui.launcher, "_run_dialog") as run:
            setup_ui.launcher.show_text_file(PROJECT / "README.md")
        arguments = run.call_args[0][1]
        self.assertEqual(arguments[0], "--text-info")
        self.assertIn(f"--filename={PROJECT / 'README.md'}", arguments)

    def test_missing_file_reports_message(self):
        with mock.patch.object(setup_ui.launcher, "show_message") as message:
            setup_ui.launcher.show_text_file(Path("/nonexistent/README.md"))
        self.assertIn("не найден", message.call_args[0][0])


DESKTOP_ENTRY = PROJECT / "installer" / "OVLS Installer.desktop"
DESKTOP_NAME = "OVLS Installer.desktop"


class LauncherEntryTest(unittest.TestCase):
    """The package ships a .desktop file for double-click launch."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-launcher-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        build_package.build(self.root)

    def test_entry_is_non_terminal_and_resolves_its_own_package(self):
        text = (self.root / DESKTOP_NAME).read_text(encoding="utf-8")
        self.assertIn("Terminal=false", text)
        self.assertIn("install.sh", text)
        # %k keeps the entry independent of an absolute package path.
        self.assertIn("%k", text)
        self.assertNotIn("X-OVLS-Managed", text)

    def test_entry_is_executable(self):
        self.assertTrue(os.access(self.root / DESKTOP_NAME, os.X_OK))

    def test_entry_passes_desktop_file_validate(self):
        if not shutil.which("desktop-file-validate"):
            self.skipTest("desktop-file-validate is not installed")
        result = subprocess.run(
            ["desktop-file-validate", str(self.root / DESKTOP_NAME)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)