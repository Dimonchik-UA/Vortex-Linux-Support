"""Tests for src/linux-support/manager.py.

Dialogs and package operations are mocked; only the decision logic is tested.
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

import manager  # noqa: E402
import package  # noqa: E402


def _make_prefix(root, name="compatdata"):
    prefix = Path(root) / name
    vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
    vortex.mkdir(parents=True)
    (vortex / "Vortex.exe").write_bytes(b"stub")
    (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
    return prefix


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-manager-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.desktop = self.root / "Desktop"
        self.prefix = _make_prefix(self.root)
        self.other = _make_prefix(self.root, "other")

        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.data),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state),
                "XDG_DESKTOP_DIR": str(self.desktop),
                "HOME": str(self.home),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.manifest = package.app_root() / package.MANIFEST_NAME
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text("", encoding="utf-8")

        for name in ("show_message", "show_info", "confirm", "choose_directory"):
            patcher = mock.patch.object(manager.launcher, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.confirm.return_value = True
        self.choose_directory.return_value = None

        detect = mock.patch.object(manager.installer, "detect", return_value={"running_vortex": []})
        self.detect = detect.start()
        self.addCleanup(detect.stop)

        detect_prefix = mock.patch.object(
            manager.package, "detect_prefix", return_value=self.prefix
        )
        self.detect_prefix = detect_prefix.start()
        self.addCleanup(detect_prefix.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_install_refreshes_the_installed_copy(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"app": str(package.app_root())},
            "installation": {"extension": {"status": "unchanged"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(manager.package, "install", return_value=result) as install:
            manager.action_install(prefix=str(self.prefix))

        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.show_info.assert_called_once()
        self.show_message.assert_not_called()

    def test_install_explains_a_skipped_shortcut(self):
        skipped = {"path": "~/.local/share/applications/ovls-vortex.desktop", "reason": "not_owned_by_ovls"}
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"app": str(package.app_root())},
            "installation": {"extension": {"status": "installed"}},
            "nxm_handler": {"status": "installed"},
            "shortcuts": {"status": "attention_required", "skipped": [skipped]},
        }
        with mock.patch.object(manager.package, "install", return_value=result):
            manager.action_install(prefix=str(self.prefix))

        message = self.show_message.call_args[0][0]
        self.assertIn(skipped["path"], message)
        self.assertIn("не принадлежит OVLS", message)
        self.show_info.assert_not_called()

    def test_install_refuses_when_not_installed(self):
        self.manifest.unlink()
        with mock.patch.object(manager.package, "install") as install:
            manager.action_install(prefix=str(self.prefix))

        install.assert_not_called()
        self.assertIn("не установлен", self.show_message.call_args[0][0])

    def test_uninstall_blocked_keeps_everything(self):
        result = {"status": "blocked", "message": "prefix not found; kept"}
        with mock.patch.object(manager.package, "uninstall", return_value=result):
            manager.action_uninstall()

        self.assertIn("kept", self.show_message.call_args[0][0])
        self.show_info.assert_not_called()

    def test_uninstall_success_reports_removed_shortcuts(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "installation": {"extension": {"status": "removed"}, "nxm_handler": {"status": "removed"}},
            "shortcuts": {"removed": ["a", "b", "c"]},
            "copy": {"app_removed": True},
        }
        with mock.patch.object(manager.package, "uninstall", return_value=result):
            manager.action_uninstall()

        text = self.show_info.call_args[0][0]
        self.assertIn("Компоненты удалены", text)
        self.assertIn("3", text)
        self.assertIn("закроется", text)
        self.show_message.assert_not_called()

    def test_run_closes_after_successful_uninstall(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "installation": {"extension": {"status": "removed"}, "nxm_handler": {"status": "removed"}},
            "shortcuts": {"removed": []},
            "copy": {"app_removed": True},
        }
        with mock.patch.object(
            manager, "choose_action", side_effect=["uninstall", "install"]
        ) as choose, mock.patch.object(
            manager.package, "uninstall", return_value=result
        ), mock.patch.object(manager.package, "install") as install:
            self.assertEqual(manager.run(), 0)

        self.assertEqual(choose.call_count, 1)
        install.assert_not_called()

    def test_run_keeps_working_after_blocked_uninstall(self):
        with mock.patch.object(
            manager, "choose_action", side_effect=["uninstall", None]
        ) as choose, mock.patch.object(
            manager.package, "uninstall", return_value={"status": "blocked"}
        ):
            self.assertEqual(manager.run(), 0)

        self.assertEqual(choose.call_count, 2)
        self.assertIn("заблокировано", self.show_message.call_args[0][0])

    def test_change_prefix_refuses_while_vortex_runs(self):
        self.detect.return_value = {"running_vortex": [{"pid": 123}]}
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix):
            manager.action_change_prefix()

        self.choose_directory.assert_not_called()
        self.assertIn("Vortex запущен", self.show_message.call_args[0][0])

    def test_change_prefix_rolls_back_config_and_nxm_on_error(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix), \
             mock.patch.object(manager.package, "load_prefix", return_value=str(self.prefix)), \
             mock.patch.object(manager.package, "is_prefix", return_value=True), \
             mock.patch.object(manager.package, "save_prefix") as save, \
             mock.patch.object(
                 manager.desktop,
                 "install_handler",
                 side_effect=[OSError("boom"), {"status": "installed"}],
             ) as handler:
            self.choose_directory.return_value = self.other
            manager.action_change_prefix()

        self.assertEqual(save.call_args_list[0], mock.call(self.other))
        self.assertEqual(save.call_args_list[1], mock.call(str(self.prefix)))
        self.assertEqual(handler.call_args_list[0][0][0], str(self.other))
        self.assertEqual(handler.call_args_list[1][0][0], str(self.prefix))
        self.assertIn("Prefix не изменён", self.show_message.call_args[0][0])
        self.show_info.assert_not_called()

    def test_change_prefix_rolls_back_when_nxm_is_unregistered(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix), \
             mock.patch.object(manager.package, "load_prefix", return_value=None), \
             mock.patch.object(manager.package, "is_prefix", return_value=True), \
             mock.patch.object(manager.package, "save_prefix") as save, \
             mock.patch.object(manager.package, "remove_config") as remove_config, \
             mock.patch.object(
                 manager.desktop,
                 "install_handler",
                 side_effect=[{"status": "attention_required"}, {"status": "installed"}],
             ):
            self.choose_directory.return_value = self.other
            manager.action_change_prefix()

        self.assertEqual(save.call_args_list[0], mock.call(self.other))
        remove_config.assert_called_once()
        self.assertIn("Prefix не изменён", self.show_message.call_args[0][0])

    def test_change_prefix_rolls_back_when_config_save_fails(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix), \
             mock.patch.object(manager.package, "load_prefix", return_value=str(self.prefix)), \
             mock.patch.object(manager.package, "is_prefix", return_value=True), \
             mock.patch.object(
                 manager.package, "save_prefix", side_effect=[OSError("read-only"), None]
             ) as save, \
             mock.patch.object(
                 manager.desktop, "install_handler", return_value={"status": "installed"}
             ) as handler:
            self.choose_directory.return_value = self.other
            manager.action_change_prefix()

        self.assertEqual(save.call_args_list[0], mock.call(self.other))
        self.assertEqual(save.call_args_list[1], mock.call(str(self.prefix)))
        handler.assert_called_once()
        self.assertEqual(handler.call_args[0][0], str(self.prefix))
        self.assertIn("Prefix не изменён", self.show_message.call_args[0][0])

    def test_change_prefix_saves_when_handler_is_registered(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix), \
             mock.patch.object(manager.package, "load_prefix", return_value=str(self.prefix)), \
             mock.patch.object(manager.package, "is_prefix", return_value=True), \
             mock.patch.object(manager.package, "save_prefix") as save, \
             mock.patch.object(
                 manager.desktop, "install_handler", return_value={"status": "installed"}
             ):
            self.choose_directory.return_value = self.other
            manager.action_change_prefix()

        save.assert_called_once_with(self.other)
        self.show_info.assert_called_once()
        self.show_message.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
