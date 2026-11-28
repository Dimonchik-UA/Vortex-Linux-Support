"""Tests for src/linux-support/manager.py.

Dialogs and package operations are mocked; only the decision logic is tested.
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import manager  # noqa: E402
import package  # noqa: E402
import state_v2  # noqa: E402
import updater  # noqa: E402


def _make_prefix(root, name="compatdata"):
    prefix = Path(root) / name
    vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
    vortex.mkdir(parents=True)
    (vortex / "Vortex.exe").write_bytes(b"stub")
    (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
    return prefix


def _prepare_userdata(prefix, profile="steamuser"):
    userdata = (
        prefix / "pfx" / "drive_c" / "users" / profile
        / "AppData" / "Roaming" / "Vortex"
    )
    userdata.mkdir(parents=True)
    dosdevices = prefix / "pfx" / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "c:").symlink_to("../drive_c")
    return userdata


class ManagerTest(unittest.TestCase):
    def test_shared_display_helpers_delegate_to_setup_ui(self):
        with mock.patch.object(manager.setup_ui, "_zenity", return_value=True) as zenity:
            self.assertTrue(manager._zenity("zenity"))
        zenity.assert_called_once_with("zenity")

        result = {"status": "failed", "reason": "test"}
        expected = ["Shortcuts: test"]
        with mock.patch.object(
            manager.setup_ui, "_shortcut_notes", return_value=expected
        ) as shortcut_notes:
            self.assertIs(manager._shortcut_notes(result), expected)
        shortcut_notes.assert_called_once_with(result)

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

        # Dialog-based actions check launcher._dialog_tool() before using the
        # functions above. Headless CI has no kdialog/zenity, so without this
        # mock the guard refuses the action before any assertable call happens.
        for name in (
            "show_message", "show_info", "confirm", "choose_directory", "_dialog_tool"
        ):
            patcher = mock.patch.object(manager.launcher, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self._dialog_tool.return_value = "kdialog"
        self.confirm.return_value = True
        self.choose_directory.return_value = None

        popen = mock.patch.object(manager.subprocess, "Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)

        detect = mock.patch.object(manager.installer, "detect", return_value={"running_vortex": []})
        self.detect = detect.start()
        self.addCleanup(detect.stop)

        detect_prefix = mock.patch.object(
            manager.package, "detect_prefix", return_value=self.prefix
        )
        self.detect_prefix = detect_prefix.start()
        self.detect_prefix_patch = detect_prefix
        self.addCleanup(detect_prefix.stop)

        # The startup GitHub check is exercised in ManagerGitHubUpdateTest, not
        # here. Its daemon thread would outlive tearDown and write into the temp
        # XDG dirs after they are removed.
        background = mock.patch.object(manager, "_start_background_update_check")
        background.start()
        self.addCleanup(background.stop)

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

    def test_install_with_stale_extension_is_not_reported_as_success(self):
        result = {
            "status": "attention_required",
            "prefix": str(self.prefix),
            "copy": {"app": str(package.app_root())},
            "installation": {"extension": {"status": "stale_host"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(manager.package, "install", return_value=result):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_not_called()
        self.show_message.assert_called_once()
        text = self.show_message.call_args[0][0]
        self.assertIn("stale_host", text)
        self.assertNotIn("Components updated", text)

    def test_install_handler_auto_detects_a_single_vortex_prefix(self):
        # No prefix passed to the button handler: Manager must scan Steam.
        self.detect_prefix_patch.stop()
        compatdata = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata"
        found = _make_prefix(compatdata, "3089305857")
        steam_root = mock.patch.object(
            manager.package.steam, "find_steam_root", return_value=str(compatdata.parents[1])
        )
        steam_root.start()
        self.addCleanup(steam_root.stop)
        result = {
            "status": "ok",
            "prefix": str(found),
            "copy": {"app": str(package.app_root())},
            "installation": {"extension": {"status": "unchanged"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(manager.package, "install", return_value=result) as install, \
             mock.patch.object(manager.package, "save_prefix") as save:
            manager.action_install()

        install.assert_called_once_with(prefix=str(found), update=True)
        self.choose_directory.assert_not_called()
        save.assert_called_once_with(found)

    def test_install_handler_lists_several_vortex_prefixes(self):
        self.detect_prefix_patch.stop()
        compatdata = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata"
        first = _make_prefix(compatdata, "111")
        second = _make_prefix(compatdata, "222")
        steam_root = mock.patch.object(
            manager.package.steam, "find_steam_root", return_value=str(compatdata.parents[1])
        )
        steam_root.start()
        self.addCleanup(steam_root.stop)
        result = {
            "status": "ok",
            "prefix": str(second),
            "copy": {"app": str(package.app_root())},
            "installation": {"extension": {"status": "unchanged"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        with mock.patch.object(manager.setup_ui, "choose_prefix", return_value=second) as choose, \
             mock.patch.object(manager.package, "install", return_value=result) as install, \
             mock.patch.object(manager.package, "save_prefix"):
            manager.action_install()

        choose.assert_called_once_with([first, second])
        install.assert_called_once_with(prefix=str(second), update=True)
        self.choose_directory.assert_not_called()

    def test_install_handler_cancel_with_several_vortex_prefixes_does_not_install(self):
        self.detect_prefix_patch.stop()
        compatdata = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata"
        _make_prefix(compatdata, "111")
        _make_prefix(compatdata, "222")
        steam_root = mock.patch.object(
            manager.package.steam, "find_steam_root", return_value=str(compatdata.parents[1])
        )
        steam_root.start()
        self.addCleanup(steam_root.stop)
        with mock.patch.object(manager.setup_ui, "choose_prefix", return_value=None), \
             mock.patch.object(manager.package, "install") as install:
            manager.action_install()

        install.assert_not_called()
        self.choose_directory.assert_not_called()

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
        self.assertIn("is not owned by OVLS", message)
        self.show_info.assert_not_called()

    def test_install_refuses_when_not_installed(self):
        self.manifest.unlink()
        with mock.patch.object(manager.package, "install") as install:
            manager.action_install(prefix=str(self.prefix))

        install.assert_not_called()
        self.assertIn("is not installed", self.show_message.call_args[0][0])

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
        self.assertIn("Components removed", text)
        self.assertIn("3", text)
        self.assertIn("will now close", text)
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
        self.assertIn("Removal blocked", self.show_message.call_args[0][0])

    def test_change_prefix_refuses_while_vortex_runs(self):
        self.detect.return_value = {"running_vortex": [{"pid": 123}]}
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix):
            manager.action_change_prefix()

        self.choose_directory.assert_not_called()
        self.assertIn("Vortex is running", self.show_message.call_args[0][0])

    def test_change_prefix_cancel_keeps_the_saved_config(self):
        with mock.patch.object(manager.package, "load_prefix", return_value=str(self.prefix)), \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.choose_directory.return_value = None
            manager.action_change_prefix()

        self.choose_directory.assert_called_once_with(start=self.prefix)
        save.assert_not_called()
        self.show_info.assert_not_called()

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
        self.assertIn("Prefix was not changed", self.show_message.call_args[0][0])
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
        self.assertIn("Prefix was not changed", self.show_message.call_args[0][0])

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
        self.assertIn("Prefix was not changed", self.show_message.call_args[0][0])

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

    def test_require_prefix_uses_detected_prefix_without_dialog(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix):
            result = manager._require_prefix()

        self.assertEqual(result, self.prefix)
        self.choose_directory.assert_not_called()

    def test_require_prefix_uses_saved_prefix_without_dialog(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix), \
             mock.patch.object(manager.package, "load_prefix", return_value=str(self.prefix)):
            result = manager._require_prefix()

        self.assertEqual(result, self.prefix)
        self.choose_directory.assert_not_called()

    def test_require_prefix_opens_the_directory_chooser_when_nothing_is_detected(self):
        self.choose_directory.return_value = self.other
        with mock.patch.object(manager.package, "detect_prefix", return_value=None), \
             mock.patch.object(manager.package, "save_prefix") as save:
            result = manager._require_prefix()

        self.assertEqual(result, self.other)
        self.choose_directory.assert_called_once_with()
        save.assert_called_once_with(self.other)

    def test_require_prefix_cancel_keeps_config_unchanged(self):
        with mock.patch.object(manager.package, "detect_prefix", return_value=None), \
             mock.patch.object(manager.package, "save_prefix") as save:
            result = manager._require_prefix()

        self.assertIsNone(result)
        self.choose_directory.assert_called_once_with()
        save.assert_not_called()

    def test_require_prefix_rejects_an_invalid_manual_directory(self):
        self.choose_directory.return_value = self.root / "not-a-prefix"
        with mock.patch.object(manager.package, "detect_prefix", return_value=None), \
             mock.patch.object(manager.package, "save_prefix") as save:
            result = manager._require_prefix()

        self.assertIsNone(result)
        save.assert_not_called()
        self.assertIn("not a valid Vortex prefix", self.show_message.call_args[0][0])

    def test_require_prefix_reports_save_error_for_chosen_directory(self):
        self.choose_directory.return_value = self.other
        with mock.patch.object(manager.package, "detect_prefix", return_value=None), \
             mock.patch.object(
                 manager.package, "save_prefix", side_effect=OSError("read-only")
             ):
            result = manager._require_prefix()

        self.assertIsNone(result)
        self.assertIn("Could not save the prefix setting", self.show_message.call_args[0][0])


    def _menu_argv(self, tool, items=None, selected="install", subtitle=None):
        self._dialog_tool.return_value = tool
        dialog_result = mock.Mock(returncode=0, stdout=selected + "\n")
        with mock.patch.object(
            manager.launcher, "_run_dialog", return_value=dialog_result
        ) as run_dialog:
            choice = manager.choose_action(items, subtitle=subtitle)
        self.assertEqual(choice, selected)
        return run_dialog.call_args[0][1]

    @staticmethod
    def _flat(items):
        return [value for item in items for value in item]

    def test_main_menu_kdialog_argv(self):
        args = self._menu_argv("kdialog")
        self.assertEqual(
            args, ["--title", "OVLS", "--menu", ""] + self._flat(manager.MAIN_ACTIONS)
        )
        self.assertIn("OVLS", args)
        self.assertEqual(
            [key for key, _ in manager.MAIN_ACTIONS],
            ["install", "prefix", "verify", "paths", "uninstall"],
        )

    def test_main_menu_zenity_argv(self):
        args = self._menu_argv("zenity")
        self.assertEqual(
            args,
            [
                "--list",
                "--title=OVLS",
                "--column=ID",
                "--column=Action",
                "--hide-column=1",
                "--print-column=1",
            ]
            + self._flat(manager.MAIN_ACTIONS),
        )
        self.assertIn("--title=OVLS", args)
        self.assertFalse(any(a.startswith("--text") for a in args))

    def test_paths_menu_argv_for_both_dialogs(self):
        self.assertEqual(
            manager.PATHS_ACTIONS,
            [
                ("open_prefix", "↗️ Open Vortex prefix folder"),
                ("mods", "🎮 Games & Mods folders"),
                ("downloads", "📥 Mod Downloads Folder"),
                ("back", "⬅️ Back"),
            ],
        )
        for tool, head in (
            ("kdialog", ["--title", "OVLS", "--menu", "Paths & Folders"]),
            (
                "zenity",
                [
                    "--list",
                    "--title=OVLS",
                    "--text=Paths & Folders",
                    "--column=ID",
                    "--column=Action",
                    "--hide-column=1",
                    "--print-column=1",
                ],
            ),
        ):
            with self.subTest(tool=tool):
                args = self._menu_argv(
                    tool,
                    manager.PATHS_ACTIONS,
                    selected="back",
                    subtitle="Paths & Folders",
                )
                self.assertEqual(args, head + self._flat(manager.PATHS_ACTIONS))
                self.assertTrue(any(a.startswith("--title") for a in args))
                self.assertEqual(
                    sum("Paths & Folders" in argument for argument in args), 1
                )

    def test_game_submenu_actions_and_argv(self):
        self.assertEqual(
            manager.GAME_ACTIONS,
            [
                ("open_mods", "🧩 Open mods staging folder"),
                ("open_game", "📁 Open game folder"),
                ("back", "⬅️ Back"),
            ],
        )
        args = self._menu_argv(
            "kdialog",
            manager.GAME_ACTIONS,
            selected="back",
            subtitle="Fallout: New Vegas",
        )
        self.assertEqual(
            args,
            ["--title", "OVLS", "--menu", "Fallout: New Vegas"]
            + self._flat(manager.GAME_ACTIONS),
        )
        self.assertIn("OVLS", args)

    def test_submenu_subtitle_is_outside_the_selectable_items(self):
        subtitle = "Fallout: New Vegas"
        for tool, head in (
            ("kdialog", ["--title", "OVLS", "--menu", subtitle]),
            (
                "zenity",
                [
                    "--list",
                    "--title=OVLS",
                    f"--text={subtitle}",
                    "--column=ID",
                    "--column=Action",
                    "--hide-column=1",
                    "--print-column=1",
                ],
            ),
        ):
            with self.subTest(tool=tool):
                args = self._menu_argv(
                    tool, manager.GAME_ACTIONS, selected="back", subtitle=subtitle
                )
                self.assertEqual(args[: len(head)], head)
                self.assertEqual(args[len(head) :], self._flat(manager.GAME_ACTIONS))

    def test_subtitle_follows_menu_level_and_back_navigation(self):
        game_items = [("game:falloutnv", "🎮 Fallout: New Vegas"), manager.BACK_ACTION]
        actions = ["paths", "mods", "game:falloutnv", "back", "back", "back", None]
        with mock.patch.object(
            manager, "choose_action", side_effect=actions
        ) as choose, mock.patch.object(
            manager, "_reader_prefix", return_value=self.prefix
        ), mock.patch.object(
            manager, "_read_state_values", return_value={}
        ), mock.patch.object(
            manager, "_discovered_game_ids", return_value=["falloutnv"]
        ), mock.patch.object(
            manager, "_game_labels", return_value={"falloutnv": "Fallout: New Vegas"}
        ), mock.patch.object(
            manager, "_game_menu_items", return_value=game_items
        ):
            self.assertEqual(manager.run(), 0)

        self.assertEqual(
            [call.kwargs.get("subtitle") for call in choose.call_args_list],
            [
                None,
                "Paths & Folders",
                "Games & Mods folders",
                "Fallout: New Vegas",
                "Games & Mods folders",
                "Paths & Folders",
                None,
            ],
        )

    def test_back_from_paths_returns_to_main(self):
        with mock.patch.object(
            manager, "choose_action", side_effect=["paths", "back", None]
        ) as choose:
            self.assertEqual(manager.run(), 0)

        self.assertEqual(choose.call_count, 3)
        self.assertEqual(choose.call_args_list[0][0][0], manager.MAIN_ACTIONS)
        self.assertEqual(choose.call_args_list[1][0][0], manager.PATHS_ACTIONS)
        self.assertEqual(choose.call_args_list[2][0][0], manager.MAIN_ACTIONS)

    def test_cancel_from_paths_returns_to_main(self):
        with mock.patch.object(
            manager, "choose_action", side_effect=["paths", None, None]
        ) as choose:
            self.assertEqual(manager.run(), 0)

        self.assertEqual(choose.call_count, 3)
        self.assertEqual(choose.call_args_list[1][0][0], manager.PATHS_ACTIONS)
        self.assertEqual(choose.call_args_list[2][0][0], manager.MAIN_ACTIONS)

    def test_back_and_cancel_walk_all_four_levels(self):
        values = {
            b"settings###gameMode###discovered###game1###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###game1###name": b'"Game One"',
        }
        expected_games_menu = [("game:game1", "🎮 Game One"), ("back", "⬅️ Back")]
        expected_levels = [
            manager.MAIN_ACTIONS,
            manager.PATHS_ACTIONS,
            expected_games_menu,
            manager.GAME_ACTIONS,
            expected_games_menu,
            manager.PATHS_ACTIONS,
            manager.MAIN_ACTIONS,
        ]
        sequences = (
            ["paths", "mods", "game:game1", "back", "back", None, None],
            ["paths", "mods", "game:game1", None, "back", None, None],
        )
        with mock.patch.object(state_v2, "read_state", return_value=values):
            for actions in sequences:
                with self.subTest(actions=actions):
                    with mock.patch.object(
                        manager, "choose_action", side_effect=actions
                    ) as choose:
                        self.assertEqual(manager.run(), 0)
                    self.assertEqual([c[0][0] for c in choose.call_args_list], expected_levels)

    def test_cancel_at_games_list_returns_to_paths(self):
        values = {
            b"settings###gameMode###discovered###game1###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###game1###name": b'"Game One"',
        }
        actions = ["paths", "mods", None, None, None]
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose:
            self.assertEqual(manager.run(), 0)

        self.assertEqual(
            [call[0][0] for call in choose.call_args_list],
            [manager.MAIN_ACTIONS, manager.PATHS_ACTIONS,
             [("game:game1", "🎮 Game One"), ("back", "⬅️ Back")],
             manager.PATHS_ACTIONS, manager.MAIN_ACTIONS],
        )

    def test_main_actions_keep_existing_handlers(self):
        for action, handler in (
            ("install", "action_install"),
            ("verify", "action_verify"),
            ("prefix", "action_change_prefix"),
        ):
            with self.subTest(action=action):
                with mock.patch.object(
                    manager, "choose_action", side_effect=[action, None]
                ), mock.patch.object(manager, handler) as called:
                    self.assertEqual(manager.run(), 0)
                called.assert_called_once_with()

        with mock.patch.object(
            manager, "choose_action", return_value="uninstall"
        ), mock.patch.object(
            manager, "action_uninstall", return_value=True
        ) as uninstall:
            self.assertEqual(manager.run(), 0)
        uninstall.assert_called_once_with()

    def test_open_prefix_folder_runs_xdg_open_with_pfx_argument(self):
        with mock.patch.object(manager.subprocess, "Popen") as popen, \
             mock.patch.object(manager, "_require_prefix") as require, \
             mock.patch.object(manager.package, "save_prefix") as save:
            manager.action_open_prefix_folder()

        popen.assert_called_once_with(["xdg-open", str(self.prefix / "pfx")])
        self.assertNotIn("shell", popen.call_args[1])
        require.assert_not_called()
        save.assert_not_called()
        self.choose_directory.assert_not_called()
        self.show_message.assert_not_called()

    def test_open_prefix_folder_returns_to_paths_menu(self):
        with mock.patch.object(
            manager,
            "choose_action",
            side_effect=["paths", "open_prefix", None, None],
        ) as choose, mock.patch.object(
            manager, "action_open_prefix_folder"
        ) as opener:
            self.assertEqual(manager.run(), 0)

        opener.assert_called_once_with()
        self.assertEqual(choose.call_args_list[2][0][0], manager.PATHS_ACTIONS)

    def test_open_prefix_folder_without_prefix_reports_and_does_not_search(self):
        with mock.patch.object(manager, "current_prefix", return_value=None), \
             mock.patch.object(manager.subprocess, "Popen") as popen, \
             mock.patch.object(manager, "_require_prefix") as require:
            manager.action_open_prefix_folder()

        popen.assert_not_called()
        require.assert_not_called()
        self.choose_directory.assert_not_called()
        self.assertIn("No Vortex prefix", self.show_message.call_args[0][0])

    def test_open_prefix_folder_without_pfx_directory_reports_error(self):
        with mock.patch.object(manager, "current_prefix", return_value=self.root), \
             mock.patch.object(manager.subprocess, "Popen") as popen:
            manager.action_open_prefix_folder()

        popen.assert_not_called()
        self.assertIn("prefix folder not found", self.show_message.call_args[0][0])

    def test_open_prefix_folder_reports_launch_error(self):
        with mock.patch.object(
            manager.subprocess, "Popen", side_effect=OSError("no xdg-open")
        ):
            manager.action_open_prefix_folder()

        self.assertIn("Could not open", self.show_message.call_args[0][0])

    def test_verify_shows_exactly_one_ovls_version_line(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "files": {}},
        }
        with mock.patch.object(manager.package, "verify", return_value=result), \
             mock.patch.object(
                 manager.updater, "current_local_version", return_value="0.5.0"
             ) as version:
            manager.action_verify()

        version.assert_called_once_with()
        text = self.show_info.call_args[0][0]
        self.assertEqual(
            [line for line in text.splitlines() if line.startswith("OVLS version:")],
            ["OVLS version: 0.5.0"],
        )
        self.show_message.assert_not_called()

    def test_verify_uses_unknown_when_local_version_is_missing(self):
        result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "files": {}},
        }
        with mock.patch.object(manager.package, "verify", return_value=result), \
             mock.patch.object(
                 manager.updater, "current_local_version", return_value=None
             ):
            manager.action_verify()

        self.assertIn("OVLS version: unknown", self.show_info.call_args[0][0])

    def test_mods_opens_staging_folder_from_state_setting(self):
        userdata = _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        expected = z_root / "mods" / "falloutnv"
        expected.mkdir(parents=True)
        decoy = userdata / "falloutnv" / "mods"
        decoy.mkdir(parents=True)
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###falloutnv###name": b'"Fallout: New Vegas"',
            b"settings###gameMode###discovered###cyberpunk2077###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###cyberpunk2077###name": b'"Cyberpunk 2077"',
            b"settings###mods###installPath###falloutnv": b'"Z:\\\\mods\\\\falloutnv"',
        }
        expected_game_menu = [
            ("game:cyberpunk2077", "🎮 Cyberpunk 2077"),
            ("game:falloutnv", "🎮 Fallout: New Vegas"),
            ("back", "⬅️ Back"),
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values) as read, \
             mock.patch.object(
                 manager,
                 "choose_action",
                 side_effect=[
                     "paths", "mods", "game:falloutnv", "open_mods",
                     "back", "back", None, None,
                 ],
             ) as choose, \
             mock.patch.object(manager.subprocess, "Popen") as popen, \
             mock.patch.object(manager, "_require_prefix") as require, \
             mock.patch.object(manager, "action_change_prefix") as change, \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager.run(), 0)

        read.assert_called_once_with(self.prefix)
        require.assert_not_called()
        change.assert_not_called()
        save.assert_not_called()
        self.choose_directory.assert_not_called()
        self.assertEqual(
            [call[0][0] for call in choose.call_args_list],
            [
                manager.MAIN_ACTIONS,
                manager.PATHS_ACTIONS,
                expected_game_menu,
                manager.GAME_ACTIONS,
                manager.GAME_ACTIONS,
                expected_game_menu,
                manager.PATHS_ACTIONS,
                manager.MAIN_ACTIONS,
            ],
        )
        popen.assert_called_once_with(["xdg-open", str(expected)])
        self.assertNotIn("shell", popen.call_args[1])
        self.show_info.assert_not_called()
        self.show_message.assert_not_called()
        self.assertTrue(userdata.is_dir())

    def test_mods_without_discovered_games_reports_and_stays_in_paths(self):
        values = {b"settings###downloads###path": b'"C:\\\\Downloads"'}
        with mock.patch.object(state_v2, "read_state", return_value=values) as read, \
             mock.patch.object(
                 manager, "choose_action", side_effect=["paths", "mods", "back", None, None]
             ) as choose:
            self.assertEqual(manager.run(), 0)

        read.assert_called_once_with(self.prefix)
        self.assertEqual(self.show_message.call_args[0][0], "Обнаруженные игры не найдены")
        self.show_info.assert_not_called()
        self.assertEqual(choose.call_args_list[2][0][0], manager.PATHS_ACTIONS)
        self.assertEqual(choose.call_args_list[3][0][0], manager.MAIN_ACTIONS)

    def test_mods_invalid_staging_setting_never_uses_default(self):
        userdata = _prepare_userdata(self.prefix)
        discovered = b"settings###gameMode###discovered###game1###path"
        key = b"settings###mods###installPath###game1"
        cases = (
            ("missing", {discovered: b'{"store":"steam"}'},
             "Could not verify Vortex's standard staging folder"),
            ("not-json", {discovered: b'{"store":"steam"}', key: b'"broken'},
             "No valid user staging path"),
            ("wrong-type", {discovered: b'{"store":"steam"}', key: b"123"},
             "No valid user staging path"),
        )
        for label, values, expected in cases:
            with self.subTest(label=label):
                self.show_message.reset_mock()
                self.show_info.reset_mock()
                with mock.patch.object(state_v2, "read_state", return_value=values), \
                     mock.patch.object(
                         manager,
                         "choose_action",
                         side_effect=[
                              "paths", "mods", "game:game1", "open_mods",
                              "back", None, None, None,
                          ],
                     ), \
                     mock.patch.object(manager.subprocess, "Popen") as popen:
                    self.assertEqual(manager.run(), 0)

                message = self.show_message.call_args[0][0]
                self.assertIn(expected, message)
                popen.assert_not_called()
                if label != "missing":
                    self.assertIn("default is not used", message)
                    self.assertNotIn(str(userdata / "game1" / "mods"), message)
                self.show_info.assert_not_called()

    def test_downloads_uses_actual_setting_from_state(self):
        _prepare_userdata(self.prefix)
        values = {
            b"settings###downloads###path": b'"C:\\\\users\\\\steamuser\\\\Downloads"'
        }
        expected = self.prefix / "pfx" / "drive_c" / "users" / "steamuser" / "Downloads"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values) as read, \
             mock.patch.object(manager, "choose_action", side_effect=["paths", "downloads", None, None]), \
             mock.patch.object(manager, "_require_prefix") as require, \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager.run(), 0)

        read.assert_called_once_with(self.prefix)
        require.assert_not_called()
        save.assert_not_called()
        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()
        self.show_message.assert_not_called()

    def test_downloads_substitutes_userdata_and_username_and_converts_once(self):
        userdata = _prepare_userdata(self.prefix)
        values = {
            b"settings###downloads###path": b'"{USERDATA}\\\\downloads\\\\{USERNAME}"'
        }
        expected = userdata / "downloads" / "dynamicuser"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "_vortex_username", return_value="dynamicuser") as username, \
             mock.patch.object(
                 manager, "_windows_to_host", wraps=manager._windows_to_host
             ) as convert, \
             mock.patch.object(manager, "choose_action", side_effect=["paths", "downloads", None, None]):
            self.assertEqual(manager.run(), 0)

        username.assert_called_once_with()
        convert.assert_called_once()
        self.assertEqual(
            convert.call_args[0][0],
            "C:\\users\\steamuser\\AppData\\Roaming\\Vortex\\downloads\\dynamicuser",
        )
        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_missing_setting_opens_default_folder_without_info(self):
        userdata = _prepare_userdata(self.prefix)
        (userdata / "downloads").mkdir()
        with mock.patch.object(state_v2, "read_state", return_value={}), \
             mock.patch.object(
                 manager, "choose_action", side_effect=["paths", "downloads", None, None]
             ):
            self.assertEqual(manager.run(), 0)

        self.popen.assert_called_once_with(["xdg-open", str(userdata / "downloads")])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()
        self.show_message.assert_not_called()

    def test_downloads_missing_default_folder_reports_error(self):
        userdata = _prepare_userdata(self.prefix)
        with mock.patch.object(state_v2, "read_state", return_value={}), \
             mock.patch.object(
                 manager, "choose_action", side_effect=["paths", "downloads", None, None]
             ):
            self.assertEqual(manager.run(), 0)

        self.popen.assert_not_called()
        self.choose_directory.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("does not exist", message)
        self.assertIn(str(userdata / "downloads"), message)

    def test_downloads_saved_setting_missing_directory_reports_error(self):
        userdata = _prepare_userdata(self.prefix)
        values = {b"settings###downloads###path": b'"C:\\\\missing-downloads"'}
        missing = self.prefix / "pfx" / "drive_c" / "missing-downloads"
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "current_prefix", return_value=self.prefix):
            manager.action_downloads_folder()

        self.choose_directory.assert_not_called()
        self.popen.assert_not_called()
        self.show_info.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("does not exist", message)
        self.assertIn(str(missing), message)
        self.assertNotIn(str(userdata / "downloads"), message)
        self.assertNotIn("default path", message)

    def test_downloads_invalid_explicit_setting_does_not_use_default(self):
        _prepare_userdata(self.prefix)
        key = b"settings###downloads###path"
        cases = (
            ("not-json", {key: b'"broken'}),
            ("wrong-type", {key: b"123"}),
            ("empty-string", {key: b'""'}),
        )
        for label, values in cases:
            with self.subTest(label=label):
                self.show_message.reset_mock()
                self.show_info.reset_mock()
                self.choose_directory.reset_mock()
                self.popen.reset_mock()
                with mock.patch.object(state_v2, "read_state", return_value=values), \
                     mock.patch.object(
                         manager, "choose_action", side_effect=["paths", "downloads", None, None]
                     ):
                    self.assertEqual(manager.run(), 0)

                self.choose_directory.assert_not_called()
                self.popen.assert_not_called()
                self.show_info.assert_not_called()
                message = self.show_message.call_args[0][0]
                self.assertIn("invalid or cannot be converted", message)
                self.assertNotIn("default path", message)

    def test_reader_actions_report_missing_or_ambiguous_prefix_without_chooser(self):
        for action in ("mods", "downloads"):
            with self.subTest(action=action):
                self.show_message.reset_mock()
                self.show_info.reset_mock()
                self.choose_directory.reset_mock()
                self.popen.reset_mock()
                actions = (
                    ["paths", action, "back", None, None]
                    if action == "mods"
                    else ["paths", action, None, None]
                )
                with mock.patch.object(manager, "current_prefix", return_value=None), \
                     mock.patch.object(state_v2, "read_state") as read, \
                     mock.patch.object(manager, "_require_prefix") as require, \
                     mock.patch.object(manager.package, "save_prefix") as save, \
                     mock.patch.object(manager, "choose_action", side_effect=actions):
                    self.assertEqual(manager.run(), 0)

                read.assert_not_called()
                require.assert_not_called()
                save.assert_not_called()
                self.choose_directory.assert_not_called()
                self.popen.assert_not_called()
                self.assertIn("No unique Vortex prefix", self.show_message.call_args[0][0])
                self.show_info.assert_not_called()

    def test_reader_errors_keep_menu_level_and_do_not_use_defaults(self):
        for error in (state_v2.StateV2Error("broken db"), OSError("broken db")):
            for action in ("mods", "downloads"):
                with self.subTest(error=type(error).__name__, action=action):
                    self.show_message.reset_mock()
                    self.show_info.reset_mock()
                    self.choose_directory.reset_mock()
                    self.popen.reset_mock()
                    actions = (
                        ["paths", action, "back", None, None]
                        if action == "mods"
                        else ["paths", action, None, None]
                    )
                    with mock.patch.object(state_v2, "read_state", side_effect=error), \
                         mock.patch.object(manager, "choose_action", side_effect=actions) as choose:
                        self.assertEqual(manager.run(), 0)

                    message = self.show_message.call_args[0][0]
                    self.assertIn("Could not read Vortex state.v2", message)
                    self.assertIn(str(error), message)
                    self.show_info.assert_not_called()
                    self.choose_directory.assert_not_called()
                    self.popen.assert_not_called()
                    self.assertEqual(choose.call_args_list[2][0][0], manager.PATHS_ACTIONS)

    def test_reader_actions_use_current_prefix_without_prefix_handler_or_settings_json(self):
        userdata = _prepare_userdata(self.prefix)
        values = {
            b"settings###gameMode###discovered###game1###path": b'{"store":"steam"}',
            b"settings###downloads###path": b'"C:\\\\Downloads"',
        }
        real_open = Path.open

        def guarded_open(path_self, *args, **kwargs):
            if path_self.name == "settings.json":
                raise AssertionError("settings.json was accessed")
            return real_open(path_self, *args, **kwargs)

        downloads = self.prefix / "pfx" / "drive_c" / "Downloads"
        downloads.mkdir(parents=True)
        with mock.patch.object(Path, "open", guarded_open), \
             mock.patch.object(manager, "current_prefix", return_value=self.prefix) as current, \
             mock.patch.object(manager, "_require_prefix") as require, \
             mock.patch.object(manager, "action_change_prefix") as change, \
             mock.patch.object(manager.package, "save_prefix") as save, \
             mock.patch.object(state_v2, "read_state", return_value=values) as read:
            with mock.patch.object(
                manager,
                "choose_action",
                side_effect=[
                    "paths", "mods", "game:game1", "open_mods",
                    "back", None, None, None,
                ],
            ):
                self.assertEqual(manager.run(), 0)
            with mock.patch.object(
                manager, "choose_action", side_effect=["paths", "downloads", None, None]
            ):
                self.assertEqual(manager.run(), 0)

        self.assertEqual(read.call_args_list, [mock.call(self.prefix), mock.call(self.prefix)])
        current.assert_called()
        require.assert_not_called()
        change.assert_not_called()
        save.assert_not_called()
        self.popen.assert_called_once_with(["xdg-open", str(downloads)])
        self.choose_directory.assert_not_called()
        self.assertFalse((userdata / "settings.json").exists())

    def test_downloads_relative_path_resolves_against_userdata(self):
        userdata = _prepare_userdata(self.prefix)
        values = {b"settings###downloads###path": b'"my-downloads"'}
        expected = userdata / "my-downloads"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_drive_relative_path_resolves_against_userdata(self):
        userdata = _prepare_userdata(self.prefix)
        for raw, tail in (
            ("C:custom-downloads", "custom-downloads"),
            ("C:downloads", "downloads"),
        ):
            with self.subTest(raw=raw):
                self.show_info.reset_mock()
                self.choose_directory.reset_mock()
                self.popen.reset_mock()
                values = {b"settings###downloads###path": ('"' + raw + '"').encode()}
                expected = userdata / tail
                expected.mkdir(parents=True)
                with mock.patch.object(state_v2, "read_state", return_value=values):
                    manager.action_downloads_folder()
                self.popen.assert_called_once_with(["xdg-open", str(expected)])
                self.choose_directory.assert_not_called()
                self.show_info.assert_not_called()

    def test_downloads_absolute_c_path_continues_to_work(self):
        _prepare_userdata(self.prefix)
        values = {b"settings###downloads###path": b'"C:\\\\tools\\\\downloads"'}
        expected = self.prefix / "pfx" / "drive_c" / "tools" / "downloads"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_absolute_z_path_continues_to_work(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        values = {b"settings###downloads###path": b'"Z:\\\\files\\\\downloads"'}
        expected = z_root / "files" / "downloads"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_userdata_and_username_absolute_converted_once(self):
        userdata = _prepare_userdata(self.prefix)
        values = {
            b"settings###downloads###path": b'"{USERDATA}\\\\{USERNAME}\\\\cache"'
        }
        (userdata / "dynamicuser" / "cache").mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "_vortex_username", return_value="dynamicuser"), \
             mock.patch.object(
                 manager, "_windows_to_host", wraps=manager._windows_to_host
             ) as convert:
            manager.action_downloads_folder()

        convert.assert_called_once()
        self.assertEqual(
            convert.call_args[0][0],
            "C:\\users\\steamuser\\AppData\\Roaming\\Vortex\\dynamicuser\\cache",
        )
        self.popen.assert_called_once_with(
            ["xdg-open", str(userdata / "dynamicuser" / "cache")]
        )
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_rejects_c_drive_escape(self):
        _prepare_userdata(self.prefix)
        values = {
            b"settings###downloads###path": b'"C:\\\\..\\\\..\\\\..\\\\etc\\\\passwd"'
        }
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.choose_directory.assert_not_called()
        self.popen.assert_not_called()
        self.show_info.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("invalid or cannot be converted", message)
        self.assertNotIn("default path", message)

    def test_downloads_rejects_z_drive_escape(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        values = {
            b"settings###downloads###path": b'"Z:\\\\..\\\\..\\\\..\\\\etc\\\\passwd"'
        }
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.choose_directory.assert_not_called()
        self.popen.assert_not_called()
        self.show_info.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("invalid or cannot be converted", message)
        self.assertNotIn("default path", message)

    def test_downloads_rejects_symlink_escape_from_drive_root(self):
        _prepare_userdata(self.prefix)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret-downloads").mkdir()
        (self.prefix / "pfx" / "drive_c" / "escape").symlink_to(
            outside, target_is_directory=True
        )
        values = {
            b"settings###downloads###path": b'"C:\\\\escape\\\\secret-downloads"'
        }
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.choose_directory.assert_not_called()
        self.popen.assert_not_called()
        self.show_info.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("invalid or cannot be converted", message)
        self.assertNotIn("default path", message)

    def test_downloads_relative_path_is_not_replaced_by_default(self):
        userdata = _prepare_userdata(self.prefix)
        values = {b"settings###downloads###path": b'"downloads-custom"'}
        expected = userdata / "downloads-custom"
        expected.mkdir(parents=True)
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.popen.assert_called_once_with(["xdg-open", str(expected)])
        self.choose_directory.assert_not_called()
        self.show_info.assert_not_called()

    def test_downloads_invalid_path_does_not_open_a_folder(self):
        _prepare_userdata(self.prefix)
        bad = b'"C:\\\\..\\\\..\\\\..\\\\etc\\\\passwd"'
        values = {b"settings###downloads###path": bad}
        with mock.patch.object(state_v2, "read_state", return_value=values):
            manager.action_downloads_folder()

        self.choose_directory.assert_not_called()
        self.popen.assert_not_called()
        self.show_info.assert_not_called()
        message = self.show_message.call_args[0][0]
        self.assertIn("invalid or cannot be converted", message)
        self.assertNotIn("passwd", message)
        self.assertNotIn("default path", message)

    @staticmethod
    def _write_extension(directory, script, info_name="Wrong Extension Name", main="index.js"):
        directory.mkdir(parents=True)
        (directory / "info.json").write_text(
            json.dumps({"name": info_name, "main": main}), encoding="utf-8"
        )
        (directory / main).write_text(script, encoding="utf-8")

    def _run_menu(self, actions, values):
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose:
            self.assertEqual(manager.run(), 0)
        return choose

    def _games_menu(self, values):
        choose = self._run_menu(["paths", "mods", None, None, None], values)
        return choose.call_args_list[2][0][0]

    def test_discovered_name_wins_over_extension_and_info_json(self):
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins" / "game-falloutnv"
        )
        script = r"""
const GAME_ID = "falloutnv";
context.registerGame({ id: GAME_ID, name: "Extension Name" });
"""
        self._write_extension(bundled, script, info_name="Wrong Extension Name")
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###falloutnv###name": b'"Discovered Name"',
        }

        menu = self._games_menu(values)

        self.assertEqual(
            menu, [("game:falloutnv", "🎮 Discovered Name"), ("back", "⬅️ Back")]
        )
        self.assertNotIn("Extension Name", str(menu))
        self.assertNotIn("Wrong Extension Name", str(menu))

    def test_bundled_extension_name_uses_constants_and_tab_in_label(self):
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins" / "game-falloutnv"
        )
        script = r"""
const GAME_ID = "falloutnv";
const EXTENSION_NAME_VORTEX = "Fallout:\tNew Vegas";
context.registerGame({
  id: GAME_ID,
  name: EXTENSION_NAME_VORTEX,
  shortName: "FNV",
  queryModPath: () => "Mods",
});
"""
        self._write_extension(bundled, script)
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}'
        }

        menu = self._games_menu(values)

        self.assertEqual(menu[0], ("game:falloutnv", "🎮 Fallout: New Vegas"))
        self.assertNotIn("FNV", menu[0][1])
        self.assertNotIn("falloutnv", menu[0][1])
        self.assertIn(r"\t", (bundled / "index.js").read_text(encoding="utf-8"))

    def test_user_extension_name_from_state_and_info_json_main(self):
        userdata = _prepare_userdata(self.prefix)
        extension = userdata / "plugins" / "BatmanExtension"
        self._write_extension(
            extension,
            "context.registerGameStub({ id: 'batmanarkhamknight', name: 'Batman Arkham Knight' });",
            main="index.cjs",
        )
        values = {
            b"app###extensions###ext-1###type": b'"game"',
            b"app###extensions###ext-1###path": (
                b'"C:\\\\users\\\\steamuser\\\\AppData\\\\Roaming\\\\Vortex\\\\plugins\\\\BatmanExtension"'
            ),
            b"settings###gameMode###discovered###batmanarkhamknight###path": b'{"store":"steam"}',
        }

        menu = self._games_menu(values)

        self.assertEqual(menu[0], ("game:batmanarkhamknight", "🎮 Batman Arkham Knight"))
        self.assertNotIn("Wrong Extension Name", menu[0][1])

    def test_conflicting_extension_names_use_numbered_placeholders(self):
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins"
        )
        self._write_extension(
            bundled / "game-first",
            "context.registerGame({ id: 'game1', name: 'First Name' });",
        )
        self._write_extension(
            bundled / "game-second",
            "context.registerGame({ id: 'game1', name: 'Second Name' });",
        )
        values = {
            b"settings###gameMode###discovered###game1###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###game2###path": b'{"store":"steam"}',
        }

        menu = self._games_menu(values)

        self.assertEqual(
            menu,
            [
                ("game:game1", "🎮 Игра без названия (1)"),
                ("game:game2", "🎮 Игра без названия (2)"),
                ("back", "⬅️ Back"),
            ],
        )

    def test_ignored_name_sources_do_not_affect_vortex_name(self):
        userdata = _prepare_userdata(self.prefix)
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins" / "game-cyberpunk2077"
        )
        self._write_extension(
            bundled,
            r"""
context.registerGame({
  id: 'cyberpunk2077',
  name: "Cyberpunk 2077",
  shortName: "CP77",
});
""",
        )
        (userdata / "temp").mkdir()
        (userdata / "temp" / "nexus_gamelist.json").write_text(
            '[{"name": "Wrong Nexus Name"}]', encoding="utf-8"
        )
        steamapps = (
            self.prefix / "pfx" / "drive_c" / "Program Files (x86)" / "Steam" / "steamapps"
        )
        steamapps.mkdir(parents=True)
        (steamapps / "appmanifest_1091500.acf").write_text(
            '"name"\t\t"Wrong Steam Name"', encoding="utf-8"
        )
        values = {
            b"settings###gameMode###discovered###cyberpunk2077###path": b'{"store":"steam"}',
            b"persistent###categories###cyberpunk2077###1###name": b'"Wrong Category Name"',
        }

        menu = self._games_menu(values)

        self.assertEqual(
            menu,
            [
                ("game:cyberpunk2077", "🎮 Cyberpunk 2077"),
                ("back", "⬅️ Back"),
            ],
        )
        for wrong in ("CP77", "Wrong Nexus Name", "Wrong Steam Name", "Wrong Category Name"):
            self.assertNotIn(wrong, str(menu))

    def test_tab_in_discovered_name_is_replaced_only_in_menu_label(self):
        key = b"settings###gameMode###discovered###falloutnv###name"
        raw = b'"Fallout:\\tNew Vegas"'
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}',
            key: raw,
        }

        menu = self._games_menu(values)

        self.assertEqual(menu[0], ("game:falloutnv", "🎮 Fallout: New Vegas"))
        self.assertEqual(values[key], raw)

    def test_real_bundled_template_literals_provide_game_names(self):
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins"
        )
        self._write_extension(
            bundled / "game-falloutnv",
            "const d={en:`English`},f=`falloutnv`,p=`store`;context.registerGame("
            "{id:f,name:`Fallout:\\tNew Vegas`,shortName:`New Vegas`});",
            main="index.cjs",
        )
        self._write_extension(
            bundled / "game-7daystodie",
            "const b=`7dtd`,y=`7daystodie`;context.registerGame({id:y,name:`7 Days to Die`});",
            main="index.cjs",
        )
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###7daystodie###path": b'{"store":"steam"}',
        }

        menu = self._games_menu(values)

        self.assertEqual(
            menu,
            [
                ("game:7daystodie", "🎮 7 Days to Die"),
                ("game:falloutnv", "🎮 Fallout: New Vegas"),
                ("back", "⬅️ Back"),
            ],
        )

    def test_template_literal_with_interpolation_is_not_used_as_name(self):
        script = (
            "const who=`Unknown`;context.registerGame({id:`game1`,name:`Bad ${who}`});"
        )
        self.assertEqual(manager._registered_game_names(script), {})

    def test_discovered_name_still_wins_over_real_template_literal(self):
        bundled = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
            / "resources" / "app.asar.unpacked" / "bundledPlugins"
        )
        self._write_extension(
            bundled / "game-falloutnv",
            "const f=`falloutnv`;context.registerGame({id:f,name:`Extension Name`});",
            main="index.cjs",
        )
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###falloutnv###name": b'"Vortex Name"',
        }

        menu = self._games_menu(values)

        self.assertEqual(menu[0], ("game:falloutnv", "🎮 Vortex Name"))

    def test_open_game_folder_uses_discovered_path(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        game_dir = z_root / "games" / "falloutnv"
        game_dir.mkdir(parents=True)
        values = {
            b"settings###gameMode###discovered###falloutnv###path": b'"Z:\\\\games\\\\falloutnv"',
            b"settings###gameMode###discovered###falloutnv###name": b'"Fallout: New Vegas"',
        }
        actions = [
            "paths", "mods", "game:falloutnv", "open_game",
            "back", "back", None, None,
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose, \
             mock.patch.object(manager.subprocess, "Popen") as popen, \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager.run(), 0)

        popen.assert_called_once_with(["xdg-open", str(game_dir)])
        save.assert_not_called()
        self.show_message.assert_not_called()
        self.assertEqual(choose.call_args_list[3][0][0], manager.GAME_ACTIONS)
        self.assertEqual(choose.call_args_list[4][0][0], manager.GAME_ACTIONS)

    def test_missing_or_nonexistent_folders_do_not_call_xdg_open(self):
        userdata = _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        discovered = b"settings###gameMode###discovered###game1###path"
        name = b"settings###gameMode###discovered###game1###name"
        staging = b"settings###mods###installPath###game1"
        cases = (
            ("missing staging", "open_mods", {discovered: b'{"store":"steam"}', name: b'"Game One"'},
             "Could not verify Vortex's standard staging folder"),
            ("staging dir missing", "open_mods",
             {discovered: b'{"store":"steam"}', name: b'"Game One"',
              staging: b'"Z:\\\\mods\\\\missing"'}, "Staging folder not found"),
            ("missing game path", "open_game",
             {discovered: b'"broken', name: b'"Game One"'}, "No valid game folder path"),
            ("game dir missing", "open_game",
             {discovered: b'"Z:\\\\games\\\\missing"', name: b'"Game One"'},
             "Game folder not found"),
        )
        for label, action, values, expected_message in cases:
            with self.subTest(label=label):
                self.show_message.reset_mock()
                with mock.patch.object(state_v2, "read_state", return_value=values), \
                     mock.patch.object(
                         manager,
                         "choose_action",
                         side_effect=["paths", "mods", "game:game1", action,
                                      "back", None, None, None],
                     ), \
                     mock.patch.object(manager.subprocess, "Popen") as popen:
                    self.assertEqual(manager.run(), 0)

                popen.assert_not_called()
                self.assertIn(expected_message, self.show_message.call_args[0][0])
                if label != "missing staging":
                    self.assertNotIn(str(userdata / "game1" / "mods"),
                                     self.show_message.call_args[0][0])

    def test_missing_and_invalid_staging_and_game_paths_keep_game_submenu(self):
        discovered = b"settings###gameMode###discovered###game1###path"
        staging = b"settings###mods###installPath###game1"
        cases = (
            ("missing staging", "open_mods", {discovered: b'{"store":"steam"}'},
             "Could not verify Vortex's standard staging folder"),
            ("broken staging json", "open_mods", {discovered: b'{"store":"steam"}', staging: b'"broken'},
             "No valid user staging path"),
            ("wrong staging type", "open_mods", {discovered: b'{"store":"steam"}', staging: b"123"},
             "No valid user staging path"),
            ("broken game json", "open_game", {discovered: b'"broken'},
             "No valid game folder path"),
            ("wrong game type", "open_game", {discovered: b"123"},
             "No valid game folder path"),
        )
        for label, action, values, expected_message in cases:
            with self.subTest(label=label):
                self.show_message.reset_mock()
                with mock.patch.object(state_v2, "read_state", return_value=values), \
                     mock.patch.object(
                         manager, "choose_action",
                         side_effect=["paths", "mods", "game:game1", action,
                                      "back", None, None, None],
                     ) as choose, \
                     mock.patch.object(manager.subprocess, "Popen") as popen:
                    self.assertEqual(manager.run(), 0)

                popen.assert_not_called()
                self.assertIn(expected_message, self.show_message.call_args[0][0])
                self.assertEqual(choose.call_args_list[3][0][0], manager.GAME_ACTIONS)
                self.assertEqual(choose.call_args_list[4][0][0], manager.GAME_ACTIONS)

    def test_xdg_open_error_is_reported_and_keeps_game_submenu(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        staging = z_root / "mods" / "game1"
        staging.mkdir(parents=True)
        values = {
            b"settings###gameMode###discovered###game1###path": b'{"store":"steam"}',
            b"settings###gameMode###discovered###game1###name": b'"Game One"',
            b"settings###mods###installPath###game1": b'"Z:\\\\mods\\\\game1"',
        }
        actions = [
            "paths", "mods", "game:game1", "open_mods",
            "back", None, None, None,
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose, \
             mock.patch.object(
                 manager.subprocess, "Popen", side_effect=OSError("no xdg-open")
             ), \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager.run(), 0)

        message = self.show_message.call_args[0][0]
        self.assertIn("Could not open the staging folder", message)
        self.assertIn("no xdg-open", message)
        self.assertIn("Game One", message)
        self.assertNotIn("🎮 game1", message)
        self.assertEqual(choose.call_args_list[3][0][0], manager.GAME_ACTIONS)
        self.assertEqual(choose.call_args_list[4][0][0], manager.GAME_ACTIONS)
        save.assert_not_called()

    def test_game_folder_xdg_open_error_is_reported_and_keeps_game_submenu(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        game_dir = z_root / "games" / "game1"
        game_dir.mkdir(parents=True)
        values = {
            b"settings###gameMode###discovered###game1###path": b'"Z:\\\\games\\\\game1"',
            b"settings###gameMode###discovered###game1###name": b'"Game One"',
        }
        actions = [
            "paths", "mods", "game:game1", "open_game",
            "back", None, None, None,
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose, \
             mock.patch.object(
                 manager.subprocess, "Popen", side_effect=OSError("no xdg-open")
             ):
            self.assertEqual(manager.run(), 0)

        message = self.show_message.call_args[0][0]
        self.assertIn("Could not open the game folder", message)
        self.assertIn("no xdg-open", message)
        self.assertIn("Game One", message)
        self.assertNotIn("🎮 game1", message)
        self.assertEqual(choose.call_args_list[3][0][0], manager.GAME_ACTIONS)
        self.assertEqual(choose.call_args_list[4][0][0], manager.GAME_ACTIONS)

    def test_placeholder_selection_opens_its_own_game_folder(self):
        _prepare_userdata(self.prefix)
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        first = z_root / "games" / "aaa"
        second = z_root / "games" / "zzz"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        values = {
            b"settings###gameMode###discovered###aaa###path": b'"Z:\\\\games\\\\aaa"',
            b"settings###gameMode###discovered###zzz###path": b'"Z:\\\\games\\\\zzz"',
        }
        actions = [
            "paths", "mods", "game:zzz", "open_game",
            "back", "back", None, None,
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose, \
             mock.patch.object(manager.subprocess, "Popen") as popen:
            self.assertEqual(manager.run(), 0)

        games_menu = choose.call_args_list[2][0][0]
        self.assertEqual(
            games_menu,
            [
                ("game:aaa", "🎮 Игра без названия (1)"),
                ("game:zzz", "🎮 Игра без названия (2)"),
                ("back", "⬅️ Back"),
            ],
        )
        popen.assert_called_once_with(["xdg-open", str(second)])
        self.assertNotEqual(popen.call_args[0][0][1], str(first))

    def test_no_steam_fallback_and_no_in_game_mod_folder_for_staging(self):
        userdata = _prepare_userdata(self.prefix)
        game_dir = userdata / "Skyrim"
        game_dir.mkdir()
        in_game_mods = game_dir / "Mods"
        in_game_mods.mkdir()
        staging = userdata / "staging"
        staging.mkdir()
        steamapps = (
            self.prefix / "pfx" / "drive_c" / "Program Files (x86)" / "Steam" / "steamapps"
        )
        steamapps.mkdir(parents=True)
        (steamapps / "appmanifest_489830.acf").write_text(
            '"name"\t\t"Steam Only Game"', encoding="utf-8"
        )
        values = {
            b"settings###gameMode###discovered###skyrim###path": (
                b'"C:\\\\users\\\\steamuser\\\\AppData\\\\Roaming\\\\Vortex\\\\Skyrim"'
            ),
            b"settings###mods###installPath###skyrim": (
                b'"C:\\\\users\\\\steamuser\\\\AppData\\\\Roaming\\\\Vortex\\\\staging"'
            ),
        }
        actions = [
            "paths", "mods", "game:skyrim", "open_mods",
            "back", "back", None, None,
        ]

        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions) as choose, \
             mock.patch.object(manager.subprocess, "Popen") as popen:
            self.assertEqual(manager.run(), 0)

        self.assertEqual(
            choose.call_args_list[2][0][0],
            [("game:skyrim", "🎮 Игра без названия (1)"), ("back", "⬅️ Back")],
        )
        popen.assert_called_once_with(["xdg-open", str(staging)])
        self.assertNotEqual(popen.call_args[0][0][1], str(in_game_mods))
        self.assertNotIn("Steam Only Game", str(choose.call_args_list[2][0][0]))

    def _staging_values(self, game_id, instance="instance-1"):
        prefix_key = b"settings###gameMode###discovered###" + game_id.encode("utf-8")
        return {
            prefix_key + b"###path": b'{"store":"steam"}',
            prefix_key + b"###name": b'"Game One"',
            b"app###instanceId": json.dumps(instance).encode("utf-8"),
        }

    def _valid_standard_staging(self, game_id="game1", instance="instance-1",
                                marker=None, write_marker=True):
        userdata = _prepare_userdata(self.prefix)
        staging = userdata / game_id / "mods"
        staging.mkdir(parents=True)
        tag = staging / "__vortex_staging_folder"
        if write_marker:
            if marker is None:
                marker = {"instance": instance, "game": game_id}
            if isinstance(marker, bytes):
                tag.write_bytes(marker)
            else:
                tag.write_text(json.dumps(marker), encoding="utf-8")
        return userdata, staging, tag, self._staging_values(game_id, instance)

    def _open_mods_with_values(self, values, game_id="game1"):
        actions = ["paths", "mods", f"game:{game_id}", "open_mods",
                   "back", None, None, None]
        with mock.patch.object(state_v2, "read_state", return_value=values), \
             mock.patch.object(manager, "choose_action", side_effect=actions), \
             mock.patch.object(manager.subprocess, "Popen") as popen:
            self.assertEqual(manager.run(), 0)
        return popen

    @staticmethod
    def _snapshot_tree(root):
        snapshot = {}
        root_stat = root.stat()
        snapshot["."] = (
            root_stat.st_size, root_stat.st_mtime_ns,
            root_stat.st_ctime_ns, root_stat.st_mode, None,
        )
        for path in sorted(root.rglob("*")):
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (
                stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_mode,
                path.read_bytes() if path.is_file() else None,
            )
        return snapshot

    def test_explicit_staging_path_wins_over_verified_standard_folder(self):
        _userdata, standard, _tag, values = self._valid_standard_staging()
        z_root = self.root / "zdrive"
        z_root.mkdir()
        (self.prefix / "pfx" / "dosdevices" / "z:").symlink_to(z_root)
        explicit = z_root / "mods" / "game1"
        explicit.mkdir(parents=True)
        values[b"settings###mods###installPath###game1"] = b'"Z:\\\\mods\\\\game1"'

        popen = self._open_mods_with_values(values)

        popen.assert_called_once_with(["xdg-open", str(explicit)])
        self.assertNotEqual(popen.call_args[0][0][1], str(standard))
        self.show_message.assert_not_called()

    def test_invalid_explicit_staging_setting_does_not_use_verified_standard_folder(self):
        _userdata, _standard, _tag, values = self._valid_standard_staging()
        values[b"settings###mods###installPath###game1"] = b'"broken'

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("No valid user staging path", self.show_message.call_args[0][0])

    def test_missing_setting_with_valid_standard_marker_opens_folder(self):
        _userdata, staging, _tag, values = self._valid_standard_staging()

        popen = self._open_mods_with_values(values)

        popen.assert_called_once_with(["xdg-open", str(staging)])
        self.show_message.assert_not_called()

    def test_missing_standard_staging_folder_reports_error(self):
        userdata = _prepare_userdata(self.prefix)
        values = self._staging_values("game1")

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("Vortex standard staging folder does not exist",
                      self.show_message.call_args[0][0])

    def test_missing_staging_marker_reports_error(self):
        _userdata, _staging, _tag, values = self._valid_standard_staging(write_marker=False)

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("staging marker is not a regular file",
                      self.show_message.call_args[0][0])

    def test_staging_marker_directory_reports_error(self):
        _userdata, staging, tag, values = self._valid_standard_staging(write_marker=False)
        tag.mkdir()

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertTrue(tag.is_dir())
        self.assertEqual(list(staging.iterdir()), [tag])
        self.assertIn("staging marker is not a regular file",
                      self.show_message.call_args[0][0])

    def test_corrupt_staging_marker_json_reports_error(self):
        _userdata, _staging, tag, values = self._valid_standard_staging(marker=b'{broken')

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("staging marker is damaged", self.show_message.call_args[0][0])
        self.assertEqual(tag.read_bytes(), b'{broken')

    def test_staging_marker_missing_required_fields_reports_error(self):
        _userdata, _staging, tag, values = self._valid_standard_staging()
        for marker in ({"game": "game1"}, {"instance": "instance-1"}, {}):
            with self.subTest(marker=marker):
                self.show_message.reset_mock()
                tag.write_text(json.dumps(marker), encoding="utf-8")

                popen = self._open_mods_with_values(values)

                popen.assert_not_called()
                self.assertIn("missing game or instance",
                              self.show_message.call_args[0][0])

    def test_staging_marker_game_mismatch_reports_error(self):
        _userdata, _staging, _tag, values = self._valid_standard_staging(
            marker={"instance": "instance-1", "game": "othergame"}
        )

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("marker game does not match", self.show_message.call_args[0][0])

    def test_staging_marker_instance_mismatch_reports_error(self):
        _userdata, _staging, _tag, values = self._valid_standard_staging(
            marker={"instance": "other-instance", "game": "game1"}
        )

        popen = self._open_mods_with_values(values)

        popen.assert_not_called()
        self.assertIn("marker instance does not match", self.show_message.call_args[0][0])

    def test_standard_staging_rejects_unsafe_game_ids_and_symlink_escape(self):
        with self.subTest(case="unsafe game id"):
            self.show_message.reset_mock()
            values = self._staging_values("..")

            popen = self._open_mods_with_values(values, game_id="..")

            popen.assert_not_called()
            self.assertIn("not a valid folder name", self.show_message.call_args[0][0])

        with self.subTest(case="symlink escape"):
            self.show_message.reset_mock()
            userdata = _prepare_userdata(self.prefix)
            outside = self.root / "outside_mods"
            outside.mkdir()
            (outside / "__vortex_staging_folder").write_text(
                json.dumps({"instance": "instance-1", "game": "game1"}),
                encoding="utf-8",
            )
            (userdata / "game1").mkdir(parents=True)
            (userdata / "game1" / "mods").symlink_to(outside, target_is_directory=True)

            popen = self._open_mods_with_values(self._staging_values("game1"))

            popen.assert_not_called()
            self.assertIn("leaves Vortex userData", self.show_message.call_args[0][0])

    def test_standard_staging_verification_does_not_modify_staging_files(self):
        _userdata, staging, _tag, values = self._valid_standard_staging()
        (staging / "mod file.txt").write_text("mod contents", encoding="utf-8")
        before = self._snapshot_tree(staging)

        popen = self._open_mods_with_values(values)

        self.assertEqual(self._snapshot_tree(staging), before)
        popen.assert_called_once_with(["xdg-open", str(staging)])

    def test_standard_staging_logic_is_identical_for_different_game_ids(self):
        userdata = _prepare_userdata(self.prefix)
        for game_id in ("game1", "batmanarkhamknight", "cyberpunk2077"):
            with self.subTest(game_id=game_id):
                staging = userdata / game_id / "mods"
                staging.mkdir(parents=True)
                (staging / "__vortex_staging_folder").write_text(
                    json.dumps({"instance": "instance-1", "game": game_id}),
                    encoding="utf-8",
                )

                host, reason = manager._standard_staging_host(
                    self._staging_values(game_id), self.prefix, game_id
                )

                self.assertIsNone(reason)
                self.assertEqual(host, staging.resolve())


class ManagerGitHubUpdateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-manager-gh-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.prefix = _make_prefix(self.root)

        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.data),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state),
                "XDG_DESKTOP_DIR": str(self.root / "Desktop"),
                "HOME": str(self.home),
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        # Reset background-check globals for isolation between tests.
        with manager._update_check_lock:
            manager._update_check_inflight = False
        manager._update_check_done.set()
        self.addCleanup(self._reset_update_check_state)

        # Remember every background check thread, including those started
        # inside manager.run(), so teardown can join them before XDG dirs go.
        self._bg_threads = []
        self._release_events = []
        real_start = manager._start_background_update_check

        def _tracked_start():
            thread = real_start()
            if thread is not None:
                self._bg_threads.append(thread)
            return thread

        start_patch = mock.patch.object(
            manager, "_start_background_update_check", side_effect=_tracked_start
        )
        start_patch.start()
        self.addCleanup(start_patch.stop)

        self.manifest = package.app_root() / package.MANIFEST_NAME
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.manifest.write_text("", encoding="utf-8")
        (package.app_root() / package.VERSION_NAME).write_text("0.1.0\n", encoding="utf-8")

        for name in ("show_message", "show_info", "confirm", "choose_directory", "_dialog_tool"):
            target = manager.launcher
            patcher = mock.patch.object(target, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self._dialog_tool.return_value = "kdialog"
        self.confirm.return_value = True
        self.choose_directory.return_value = None

        detect = mock.patch.object(manager.installer, "detect", return_value={"running_vortex": []})
        self.detect = detect.start()
        self.addCleanup(detect.stop)

        detect_prefix = mock.patch.object(manager.package, "detect_prefix", return_value=self.prefix)
        self.detect_prefix = detect_prefix.start()
        self.detect_prefix_patch = detect_prefix
        self.addCleanup(detect_prefix.stop)

        self.normal_result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"app": str(package.app_root()), "status": "unchanged"},
            "installation": {"extension": {"status": "unchanged"}},
            "nxm_handler": {"status": "unchanged"},
            "shortcuts": {"status": "ok", "skipped": []},
        }

    def _reset_update_check_state(self):
        with manager._update_check_lock:
            manager._update_check_inflight = False
        manager._update_check_done.set()

    def tearDown(self):
        # Unblock slow_check first, then wait for the worker's own completion
        # signal and join its thread. Only after that is it safe to remove the
        # temporary XDG tree: no worker can recreate update-check.json there.
        for event in self._release_events:
            event.set()
        if self._bg_threads or manager._update_check_inflight:
            self.assertTrue(
                manager._update_check_done.wait(timeout=5),
                "background update check did not finish before teardown",
            )
        for thread in self._bg_threads:
            thread.join(timeout=5)
            self.assertFalse(
                thread.is_alive(), "background update-check thread still running"
            )
        self._reset_update_check_state()
        shutil.rmtree(self.root, ignore_errors=True)

    def _cache_update_available(self, current="0.1.0", latest="0.2.0"):
        info = updater.UpdateCheckResult(
            status="update_available",
            current_version=current,
            latest_version=latest,
            tag_name=f"v{latest}",
            release_url="https://example.invalid/release",
            installer_url="https://example.invalid/OVLS-Installer.tar.gz",
            checksums_url="https://example.invalid/SHA256SUMS",
            installer_name="OVLS-Installer.tar.gz",
            installer_size=123,
            checked_at=9999999999.0,
        )
        updater.save_cache(info)
        return info

    def _prepared(self):
        prepared = mock.Mock()
        prepared.package_root = Path("/tmp/prepared-ovls")
        prepared.version = "0.2.0"
        prepared.tag_name = "v0.2.0"
        prepared.cleanup = mock.Mock()
        return prepared

    def _assert_available_update_version_resolution(self, current, installed, read_calls):
        cached = self._cache_update_available(current=current)
        self.confirm.return_value = False
        with mock.patch.object(updater, "read_installed_version", return_value=installed) as read_version, \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare, \
             mock.patch.object(manager.updater, "check_for_updates") as force_check:
            manager.action_install(prefix=str(self.prefix))

        self.assertEqual(read_version.call_count, read_calls)
        prompt = self.confirm.call_args[0][0]
        self.assertIn(f"Installed version: {current or installed or 'unknown'}", prompt)
        self.assertIn(cached.latest_version, prompt)
        prepare.assert_not_called()
        force_check.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.show_info.assert_called_once()
        self.show_message.assert_not_called()

    def test_available_update_with_no_current_version_reads_updater_version(self):
        self._assert_available_update_version_resolution(None, "0.1.4", 1)

    def test_available_update_with_empty_current_version_reads_updater_version(self):
        self._assert_available_update_version_resolution("", "0.1.5", 1)

    def test_available_update_with_known_current_version_skips_updater_lookup(self):
        self._assert_available_update_version_resolution("0.1.0", "0.9.9", 0)

    def test_run_starts_background_check_without_blocking_menu(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        self._release_events.append(release)

        def slow_check(*_a, **_k):
            started.set()
            release.wait(timeout=5)
            finished.set()
            return updater.UpdateCheckResult(status="unknown", checked_at=1.0)

        with mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check) as check, \
             mock.patch.object(manager, "choose_action", return_value=None):
            result = manager.run()

        self.assertEqual(result, 0)
        self.assertTrue(started.wait(timeout=1), "background update check did not start")
        release.set()
        self.assertTrue(finished.wait(timeout=5), "background check did not finish")
        self.assertTrue(check.called)

    def test_run_network_error_in_background_does_not_break_manager(self):
        with mock.patch.object(
            manager.updater, "check_for_updates", side_effect=OSError("offline")
        ), mock.patch.object(manager, "choose_action", return_value=None):
            self.assertEqual(manager.run(), 0)

    def test_available_update_shown_and_decline_keeps_self_refresh(self):
        cached = self._cache_update_available()
        self.confirm.return_value = False
        with mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare, \
             mock.patch.object(manager.updater, "check_for_updates") as force_check:
            manager.action_install(prefix=str(self.prefix))

        prompt = self.confirm.call_args[0][0]
        self.assertIn(cached.latest_version, prompt)
        self.assertIn(cached.current_version, prompt)
        self.assertIn("GitHub", prompt)
        prepare.assert_not_called()
        force_check.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.show_info.assert_called_once()
        self.show_message.assert_not_called()

    def test_no_update_available_uses_self_refresh(self):
        info = updater.UpdateCheckResult(
            status="up_to_date", current_version="0.1.0", latest_version="0.1.0", checked_at=9999999999.0
        )
        updater.save_cache(info)
        with mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager.action_install(prefix=str(self.prefix))

        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)

    def test_unknown_or_no_cache_keeps_self_refresh(self):
        with mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager.action_install(prefix=str(self.prefix))

        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)

    def test_confirmed_github_update_prepares_and_installs_verified_package(self):
        cached = self._cache_update_available()
        prepared = self._prepared()
        fresh = updater.UpdateCheckResult(
            status="update_available",
            current_version="0.1.0",
            latest_version="0.2.0",
            tag_name="v0.2.0",
            installer_url="https://example.invalid/OVLS-Installer.tar.gz",
            checksums_url="https://example.invalid/SHA256SUMS",
            installer_name="OVLS-Installer.tar.gz",
            installer_size=123,
        )
        gh_result = {
            "status": "ok",
            "prefix": str(self.prefix),
            "copy": {"app": str(package.app_root()), "status": "updated"},
            "installation": {"extension": {"status": "installed"}},
            "nxm_handler": {"status": "installed"},
            "shortcuts": {"status": "ok", "skipped": []},
        }
        self.confirm.return_value = True
        with mock.patch.object(manager.updater, "check_for_updates", return_value=fresh) as force_check, \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared) as prepare, \
             mock.patch.object(manager.package, "install", return_value=gh_result) as install, \
             mock.patch.object(updater, "read_installed_version", return_value="0.2.0"):
            manager.action_install(prefix=str(self.prefix))

        force_check.assert_called_once_with(force=True)
        prepare.assert_called_once_with(fresh)
        install.assert_called_once_with(
            package_root=prepared.package_root,
            prefix=str(self.prefix),
            update=True,
        )
        prepared.cleanup.assert_called_once()
        self.show_info.assert_called_once()
        self.assertIn("0.2.0", self.show_info.call_args[0][0])
        self.show_message.assert_not_called()

    def test_successful_github_update_invalidates_stale_cache(self):
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()
        self.assertTrue(cache_file.is_file())

        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result), \
             mock.patch.object(updater, "read_installed_version", return_value="0.2.0"):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_called_once()
        self.assertFalse(cache_file.exists())
        self.assertIsNone(updater.load_cache())

    def test_failed_github_update_keeps_cache_untouched(self):
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()

        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", side_effect=OSError("disk full")):
            manager.action_install(prefix=str(self.prefix))

        self.show_message.assert_called_once()
        self.assertTrue(cache_file.is_file())
        self.assertIsNotNone(updater.load_cache())

    def test_successful_self_refresh_invalidates_stale_cache(self):
        updater.save_cache(
            updater.UpdateCheckResult(
                status="up_to_date",
                current_version="0.1.0",
                latest_version="0.1.0",
                checked_at=9999999999.0,
            )
        )
        cache_file = updater.cache_path()
        with mock.patch.object(manager.package, "install", return_value=self.normal_result):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_called_once()
        self.assertFalse(cache_file.exists())

    def test_installed_version_mismatch_is_reported_as_failure(self):
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()

        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result), \
             mock.patch.object(updater, "read_installed_version", return_value="0.1.0"):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_not_called()
        self.assertIn("does not match the release", self.show_message.call_args[0][0])
        self.assertTrue(cache_file.is_file())

    def test_up_to_date_is_not_interpreted_as_network_error(self):
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()

        # Simulate: GitHub check returns up_to_date (already installed)
        up_to_date_result = updater.UpdateCheckResult(
            status="up_to_date",
            current_version="0.2.0",
            latest_version="0.2.0",
            tag_name="v0.2.0",
            checked_at=9999999999.0,
        )

        with mock.patch.object(manager.updater, "check_for_updates", return_value=up_to_date_result), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(updater, "read_installed_version", return_value="0.2.0"):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_not_called()
        self.show_message.assert_called_once()
        msg = self.show_message.call_args[0][0]
        self.assertIn("already up to date", msg)
        self.assertNotIn("Check your connection", msg)
        install.assert_not_called()
        # Cache should be invalidated (cleaned up)
        self.assertFalse(cache_file.exists())

    def test_network_error_still_reported_as_connection_failure(self):
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()

        # Simulate: GitHub check returns unknown (network error)
        network_error_result = updater.UpdateCheckResult(
            status="unknown",
            current_version="0.1.0",
            checked_at=9999999999.0,
            error="Network error: connection refused",
        )

        with mock.patch.object(manager.updater, "check_for_updates", return_value=network_error_result), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install:
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_not_called()
        self.show_message.assert_called_once()
        msg = self.show_message.call_args[0][0]
        self.assertIn("Check your connection", msg)
        install.assert_not_called()

    def test_repeated_click_after_success_does_not_trigger_unnecessary_install(self):
        # First: simulate successful install that invalidates cache
        self._cache_update_available()
        prepared = self._prepared()
        cache_file = updater.cache_path()
        self.assertTrue(cache_file.is_file())

        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result), \
             mock.patch.object(updater, "read_installed_version", return_value="0.2.0"):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_called_once()
        self.assertFalse(cache_file.exists())

        # Second click: cache is gone (up_to_date or absent), should go to self-refresh
        self.show_info.reset_mock()
        self.show_message.reset_mock()
        with mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager.action_install(prefix=str(self.prefix))

        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.show_info.assert_called_once()
        self.show_message.assert_not_called()

    def test_cache_clear_failure_keeps_install_success_but_warns(self):
        self._cache_update_available()
        prepared = self._prepared()

        def fail_to_clear():
            raise OSError("permission denied")

        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result), \
             mock.patch.object(updater, "read_installed_version", return_value="0.2.0"), \
             mock.patch.object(updater, "invalidate_cache", side_effect=fail_to_clear):
            manager.action_install(prefix=str(self.prefix))

        self.show_info.assert_called_once()
        self.assertIn("could not be cleared", self.show_info.call_args[0][0])

    def test_slow_background_check_cannot_restore_cache_after_install(self):
        started = threading.Event()
        release = threading.Event()
        install_called = threading.Event()
        self._release_events.append(release)
        stale = updater.UpdateCheckResult(
            status="update_available",
            current_version="0.1.0",
            latest_version="0.2.0",
            checked_at=1.0,
        )

        def slow_check(*_a, **_k):
            started.set()
            release.wait(timeout=5)
            updater.save_cache(stale)
            return stale

        def fake_install(*_a, **_k):
            install_called.set()
            return self.normal_result

        def release_after_install():
            self.assertTrue(install_called.wait(timeout=5))
            release.set()

        threading.Thread(target=release_after_install, daemon=True).start()
        with mock.patch.object(manager, "_UPDATE_CHECK_WAIT_TIMEOUT", 0.05), \
             mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check), \
             mock.patch.object(manager.package, "install", side_effect=fake_install):
            thread = manager._start_background_update_check()
            self.assertIsNotNone(thread)
            self.assertTrue(started.wait(timeout=1), "background check did not start")
            manager.action_install(prefix=str(self.prefix))

        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(updater.cache_path().exists(), "slow check recreated the stale cache")

    def test_sha256_error_does_not_install_and_cleans_up(self):
        self._cache_update_available()
        prepared = self._prepared()
        self.confirm.return_value = True
        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(
                 manager.updater, "prepare_release_package", side_effect=updater.VerificationError("bad hash")
             ) as prepare, \
             mock.patch.object(manager.package, "install") as install:
            manager.action_install(prefix=str(self.prefix))

        prepare.assert_called_once()
        install.assert_not_called()
        self.show_message.assert_called_once()
        self.assertIn("SHA-256", self.show_message.call_args[0][0])

    def test_prepare_error_cleans_up_temp_and_does_not_install(self):
        self._cache_update_available()
        prepared = self._prepared()

        def raise_and_note(*_a, **_k):
            # Simulate updater cleaning its own temp on failure by leaving no prepared
            # object; manager must still not install and must report error.
            raise updater.PackageFormatError("unsafe tar")

        self.confirm.return_value = True
        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", side_effect=raise_and_note), \
             mock.patch.object(manager.package, "install") as install:
            manager.action_install(prefix=str(self.prefix))

        install.assert_not_called()
        self.assertIn("invalid format", self.show_message.call_args[0][0])

    def test_cleanup_called_even_if_install_fails(self):
        self._cache_update_available()
        prepared = self._prepared()
        self.confirm.return_value = True
        with mock.patch.object(manager.updater, "check_for_updates", return_value=self._cache_update_available()), \
             mock.patch.object(manager.updater, "prepare_release_package", return_value=prepared), \
             mock.patch.object(manager.package, "install", side_effect=OSError("disk full")) as install:
            manager.action_install(prefix=str(self.prefix))

        install.assert_called_once()
        prepared.cleanup.assert_called_once()
        self.assertIn("Update installation failed", self.show_message.call_args[0][0])
        self.show_info.assert_not_called()

    def test_github_check_failure_after_confirm_does_not_install(self):
        self._cache_update_available()
        self.confirm.return_value = True
        with mock.patch.object(manager.updater, "check_for_updates", side_effect=updater.NetworkError("timeout")), \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare, \
             mock.patch.object(manager.package, "install") as install:
            manager.action_install(prefix=str(self.prefix))

        prepare.assert_not_called()
        install.assert_not_called()
        self.show_message.assert_called_once()

    def test_install_waits_for_inflight_check_and_shows_update(self):
        started = threading.Event()
        release = threading.Event()
        waiting = threading.Event()
        self._release_events.append(release)

        def slow_check(*_a, **_k):
            started.set()
            release.wait(timeout=5)
            info = updater.UpdateCheckResult(
                status="update_available",
                current_version="0.1.0",
                latest_version="0.2.0",
                tag_name="v0.2.0",
                installer_url="https://example.invalid/OVLS-Installer.tar.gz",
                checksums_url="https://example.invalid/SHA256SUMS",
                installer_name="OVLS-Installer.tar.gz",
                installer_size=123,
                checked_at=9999999999.0,
            )
            updater.save_cache(info)
            return info

        real_wait = manager._wait_for_background_update_check

        def _note_wait(*_a, **_k):
            waiting.set()
            return real_wait(*_a, **_k)

        def _release_when_waiting():
            self.assertTrue(waiting.wait(timeout=5))
            release.set()

        self.confirm.return_value = False  # decline GitHub update, keep self-refresh
        with mock.patch.object(manager, "_UPDATE_CHECK_WAIT_TIMEOUT", 1.0), \
             mock.patch.object(manager, "_wait_for_background_update_check", side_effect=_note_wait), \
             mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check) as check, \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager._start_background_update_check()
            self.assertTrue(started.wait(timeout=1), "background check did not start")
            threading.Thread(target=_release_when_waiting, daemon=True).start()
            manager.action_install(prefix=str(self.prefix))

        self.assertTrue(waiting.is_set(), "action_install did not wait for the in-flight check")
        check.assert_called_once_with(force=False)
        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.confirm.assert_called_once()
        self.assertIn("0.2.0", self.confirm.call_args[0][0])

    def test_install_timeout_during_inflight_check_falls_back_without_parallel_request(self):
        started = threading.Event()
        release = threading.Event()
        install_called = threading.Event()
        calls = {"count": 0}
        self._release_events.append(release)

        def slow_check(*_a, **_k):
            calls["count"] += 1
            started.set()
            release.wait(timeout=5)
            info = updater.UpdateCheckResult(status="unknown", checked_at=9999999999.0, error="timeout")
            updater.save_cache(info)
            return info

        def fake_install(*_a, **_k):
            install_called.set()
            return self.normal_result

        def release_after_install():
            self.assertTrue(install_called.wait(timeout=5))
            release.set()

        threading.Thread(target=release_after_install, daemon=True).start()
        with mock.patch.object(manager, "_UPDATE_CHECK_WAIT_TIMEOUT", 0.05), \
             mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check), \
             mock.patch.object(manager.package, "install", side_effect=fake_install) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager._start_background_update_check()
            self.assertTrue(started.wait(timeout=1), "background check did not start")
            manager.action_install(prefix=str(self.prefix))

        self.assertEqual(calls["count"], 1, "parallel/repeated background check started")
        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.confirm.assert_not_called()

    def test_background_check_finishes_before_temp_tree_is_removed(self):
        release = threading.Event()
        finished = threading.Event()
        self._release_events.append(release)

        def slow_check(*_a, **_k):
            release.wait(timeout=5)
            updater.save_cache(updater.UpdateCheckResult(status="unknown", checked_at=1.0))
            finished.set()
            return updater.UpdateCheckResult(status="unknown", checked_at=1.0)

        cache_file = updater.cache_path()
        with mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check):
            thread = manager._start_background_update_check()
            self.assertIsNotNone(thread)
            self.assertFalse(cache_file.exists())
            release.set()
            self.assertTrue(finished.wait(timeout=5), "slow_check did not finish")
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(cache_file.is_file())
            self.assertEqual(len(self._bg_threads), 1)

        self.assertFalse(manager._update_check_inflight)
        self.assertTrue(manager._update_check_done.is_set())

    def test_background_check_not_started_in_parallel_when_inflight(self):
        started = threading.Event()
        release = threading.Event()
        calls = {"count": 0}
        self._release_events.append(release)

        def slow_check(*_a, **_k):
            calls["count"] += 1
            started.set()
            release.wait(timeout=5)
            return updater.UpdateCheckResult(status="unknown", checked_at=1.0)

        with mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check):
            first = manager._start_background_update_check()
            self.assertIsNotNone(first)
            self.assertTrue(started.wait(timeout=1), "background check did not start")
            second = manager._start_background_update_check()
            self.assertIsNone(second)
            self.assertEqual(calls["count"], 1)

        release.set()
        if first is not None:
            first.join(timeout=5)
            self.assertFalse(first.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
