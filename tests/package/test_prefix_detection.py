"""Prefix discovery tests for the Release 0.2.0 approved scope."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import core  # noqa: E402
import diagnostics  # noqa: E402
import launcher  # noqa: E402
import manager  # noqa: E402
import package  # noqa: E402
import registry  # noqa: E402
import setup_ui  # noqa: E402
import steam  # noqa: E402


class PrefixDetectionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-prefix-", dir=str(HERE)))
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.compatdata = self.home / ".local" / "share" / "Steam" / "steamapps" / "compatdata"
        self.compatdata.mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.root / "state"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_prefix(self, parent, appid):
        prefix = parent / str(appid)
        (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
        vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
        vortex.mkdir(parents=True, exist_ok=True)
        (vortex / "Vortex.exe").write_bytes(b"stub")
        return prefix.resolve()

    def test_random_appid_is_auto_detected_by_install(self):
        prefix = self._make_prefix(self.compatdata, 3089305857)

        self.assertEqual(steam.find_vortex_prefixes(), [prefix])
        self.assertEqual(package.detect_prefix_candidates(), [prefix])
        self.assertEqual(package.detect_prefix(), prefix)

    def test_prefix_without_vortex_is_not_a_candidate(self):
        empty = self.compatdata / "111"
        (empty / "pfx" / "drive_c" / "users").mkdir(parents=True)
        valid = self._make_prefix(self.compatdata, 222)

        self.assertEqual(package.detect_prefix_candidates(), [valid])

    def test_no_vortex_explains_how_to_install_it(self):
        self.assertIsNone(package.detect_prefix())
        message = package.prefix_search_message()
        for expected in (
            "Vortex not found",
            "non-Steam game",
            "DW-Proton",
            "GE-Proton",
            "--prefix",
        ):
            self.assertIn(expected, message)

    def test_multiple_prefixes_require_an_explicit_choice(self):
        first = self._make_prefix(self.compatdata, 111)
        second = self._make_prefix(self.compatdata, 222)

        self.assertIsNone(package.detect_prefix())
        message = package.prefix_search_message()
        self.assertIn(str(first), message)
        self.assertIn(str(second), message)
        self.assertIn("--prefix", message)

        with mock.patch.object(setup_ui.launcher, "_dialog_tool", return_value="kdialog"), \
             mock.patch.object(
                 setup_ui.launcher,
                 "_run_dialog",
                 return_value=mock.Mock(returncode=0, stdout=str(second) + "\n"),
             ):
            self.assertEqual(setup_ui.choose_prefix([first, second]), second)

    def test_explicit_and_saved_prefixes_take_priority(self):
        found = self._make_prefix(self.compatdata, 3089305857)
        saved = self._make_prefix(self.root, "saved-prefix")
        explicit = self._make_prefix(self.root, "explicit-prefix")
        package.save_prefix(saved)

        self.assertEqual(package.detect_prefix_candidates(), [saved, found])
        self.assertEqual(package.detect_prefix_candidates(explicit), [explicit, saved, found])
        self.assertEqual(package.detect_prefix(explicit), explicit)

    def test_ui_uses_detected_prefix_without_opening_a_dialog(self):
        prefix = self._make_prefix(self.compatdata, 333)
        with mock.patch.object(setup_ui.launcher, "choose_directory") as choose, \
             mock.patch.object(setup_ui.package, "save_prefix") as save:
            self.assertEqual(setup_ui._require_prefix(), prefix)
        choose.assert_not_called()
        save.assert_called_once_with(prefix)

    def test_ui_opens_the_directory_chooser_for_a_manual_choice(self):
        chosen = self._make_prefix(self.root, "manual-choice")
        with mock.patch.object(setup_ui.package, "detect_prefix", return_value=None), \
             mock.patch.object(
                 setup_ui.package, "detect_prefix_candidates", return_value=[]
             ), \
             mock.patch.object(setup_ui.package, "save_prefix") as save, \
             mock.patch.object(
                 setup_ui.launcher, "choose_directory", return_value=chosen
             ) as choose:
            self.assertEqual(setup_ui._require_prefix(), chosen)
        choose.assert_called_once_with()
        save.assert_called_once_with(chosen)

    def test_ui_explains_zero_candidates(self):
        with mock.patch.object(setup_ui.package, "detect_prefix", return_value=None), \
             mock.patch.object(setup_ui.package, "detect_prefix_candidates", return_value=[]), \
             mock.patch.object(setup_ui.launcher, "choose_directory", return_value=None), \
             mock.patch.object(setup_ui.launcher, "show_message") as show:
            self.assertIsNone(setup_ui._require_prefix())
        self.assertIn("Vortex not found", show.call_args[0][0])

    def test_cli_prints_prefix_choice_guidance(self):
        with mock.patch.object(core, "setup_logging", return_value=mock.Mock()):
            import ovls  # noqa: E402

        result = {
            "status": "attention_required",
            "reason": "prefix_not_found",
            "message": package.prefix_search_message(),
        }
        args = SimpleNamespace(package_command="verify", prefix=None, dry_run=False)
        stderr = io.StringIO()
        with mock.patch.object(ovls.package, "verify", return_value=result), \
             mock.patch("sys.stderr", stderr), mock.patch("sys.stdout", io.StringIO()):
            ovls.cmd_package(args)
        self.assertIn("Proton prefix not found", stderr.getvalue())
        self.assertIn("--prefix", stderr.getvalue())

    def test_generic_appid_is_discovered_and_selected_by_install(self):
        prefix = self._make_prefix(self.compatdata, 2718281828)

        self.assertEqual(package.detect_prefix_candidates(), [prefix])
        self.assertEqual(package.detect_prefix(), prefix)

    def test_multiple_generic_prefixes_require_choice(self):
        first = self._make_prefix(self.compatdata, 2718281828)
        second = self._make_prefix(self.compatdata, 3089305857)

        self.assertIsNone(package.detect_prefix())
        message = package.prefix_search_message()
        self.assertIn(str(first), message)
        self.assertIn(str(second), message)

    def test_known_location_is_legacy_only_without_vortex_check(self):
        legacy = self.home / "Windows" / "prefix"
        (legacy / "pfx" / "drive_c" / "users").mkdir(parents=True)
        generic = self._make_prefix(self.compatdata, 222)

        # A directory without Vortex.exe is not a candidate: Install must find
        # real Vortex installations, not arbitrary compatdata directories.
        self.assertEqual(package.detect_prefix_candidates(), [generic])
        self.assertEqual(package.detect_prefix(), generic)

    def test_saved_prefix_still_wins_over_generic_prefix(self):
        saved = self._make_prefix(self.root, "saved-prefix")
        self._make_prefix(self.compatdata, 3141592653)
        package.save_prefix(saved)

        self.assertEqual(package.detect_prefix(), saved)

    def test_launcher_auto_selects_a_single_random_appid_prefix(self):
        prefix = self._make_prefix(self.compatdata, 3089305857)

        self.assertEqual(launcher.find_prefix({}), prefix)

    def test_launcher_ignores_prefix_without_vortex(self):
        foreign = self.compatdata / "1618033988"
        (foreign / "pfx" / "drive_c" / "users").mkdir(parents=True)

        self.assertFalse(launcher.is_prefix(foreign))
        self.assertIsNone(launcher.find_prefix({}))

    def test_launcher_does_not_choose_between_multiple_prefixes(self):
        self._make_prefix(self.compatdata, 111)
        self._make_prefix(self.compatdata, 222)

        self.assertIsNone(launcher.find_prefix({}))

    def test_launcher_keeps_configured_prefix(self):
        prefix = self._make_prefix(self.root, "manual-prefix")

        self.assertEqual(launcher.find_prefix({"prefix": str(prefix)}), prefix)

    def test_launcher_run_accepts_manually_chosen_prefix(self):
        prefix = self._make_prefix(self.root, "manual-choice")
        with mock.patch.object(launcher, "choose_directory", return_value=prefix), \
             mock.patch.object(launcher, "_select_proton", return_value="/proton"), \
             mock.patch.object(launcher.proton, "spawn_vortex", return_value={"pid": 1}), \
             mock.patch.object(launcher, "show_message"), \
             mock.patch("sys.stdout", io.StringIO()):
            result = launcher.run([])

        self.assertEqual(result, 0)

    def test_manager_auto_selects_a_single_vortex_prefix(self):
        prefix = self._make_prefix(self.compatdata, 2718281828)

        with mock.patch.object(manager.package, "load_prefix", return_value=None), \
             mock.patch.object(manager.launcher, "choose_directory") as choose, \
             mock.patch.object(manager.launcher, "show_message") as show, \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager._require_prefix(), prefix)

        choose.assert_not_called()
        save.assert_called_once_with(prefix)
        show.assert_not_called()

    def test_manager_lists_multiple_vortex_prefixes_for_a_choice(self):
        first = self._make_prefix(self.compatdata, 111)
        second = self._make_prefix(self.compatdata, 222)

        with mock.patch.object(manager.package, "load_prefix", return_value=None), \
             mock.patch.object(manager.setup_ui, "choose_prefix", return_value=second) as choose, \
             mock.patch.object(manager.launcher, "choose_directory") as directory, \
             mock.patch.object(manager.package, "save_prefix") as save:
            self.assertEqual(manager._require_prefix(), second)

        choose.assert_called_once_with([first, second])
        directory.assert_not_called()
        save.assert_called_once_with(second)

    def test_manager_offers_manual_choice_when_vortex_is_not_found(self):
        chosen = self._make_prefix(self.root, "manual-after-no-results")

        with mock.patch.object(manager.package, "load_prefix", return_value=None), \
             mock.patch.object(manager.package, "detect_prefix", return_value=None), \
             mock.patch.object(manager.package, "detect_prefix_candidates", return_value=[]), \
             mock.patch.object(manager.launcher, "choose_directory", return_value=chosen) as choose, \
             mock.patch.object(manager.package, "is_prefix", return_value=True), \
             mock.patch.object(manager.package, "save_prefix") as save, \
             mock.patch.object(manager.launcher, "show_message") as show:
            self.assertEqual(manager._require_prefix(), chosen)

        choose.assert_called_once_with()
        save.assert_called_once_with(chosen)
        self.assertIn("Vortex not found", show.call_args[0][0])

    def test_cli_package_returns_json_and_zero_for_ok(self):
        with mock.patch.object(core, "setup_logging", return_value=mock.Mock()):
            import ovls  # noqa: E402

        args = SimpleNamespace(package_command="verify", prefix=str(self.root), dry_run=False)
        stdout = io.StringIO()
        with mock.patch.object(ovls.package, "verify", return_value={"status": "ok"}), \
             mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", io.StringIO()):
            code = ovls.cmd_package(args)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "ok"})

    def test_cli_package_reports_error_without_json(self):
        with mock.patch.object(core, "setup_logging", return_value=mock.Mock()):
            import ovls  # noqa: E402

        for command, error in (
            ("install", OSError("disk read-only")),
            ("verify", ValueError("bad prefix")),
        ):
            with self.subTest(command=command):
                args = SimpleNamespace(
                    package_command=command, prefix=str(self.root), dry_run=False
                )
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(ovls.package, command, side_effect=error), \
                     mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
                    code = ovls.cmd_package(args)

                self.assertEqual(code, 2)
                self.assertIn(f"Package {command} failed", stderr.getvalue())
                self.assertEqual(stdout.getvalue(), "")

    def test_choose_prefix_passes_all_candidates_to_kdialog(self):
        first = self._make_prefix(self.root, "dialog-first")
        second = self._make_prefix(self.root, "dialog-second")
        result = mock.Mock(returncode=0, stdout=str(second))

        with mock.patch.object(
            setup_ui.launcher, "_dialog_tool", return_value="/usr/bin/kdialog"
        ), mock.patch.object(setup_ui.launcher, "_run_dialog", return_value=result) as run:
            self.assertEqual(setup_ui.choose_prefix([first, second]), second)

        self.assertEqual(
            run.call_args[0][1],
            [
                "--menu",
                "Several Vortex prefixes were found. Choose one:",
                str(first), str(first), str(second), str(second),
            ],
        )

    def test_choose_prefix_passes_all_candidates_to_zenity(self):
        first = self._make_prefix(self.root, "zenity-first")
        second = self._make_prefix(self.root, "zenity-second")
        result = mock.Mock(returncode=0, stdout=str(second) + "\n")

        with mock.patch.object(
            setup_ui.launcher, "_dialog_tool", return_value="/usr/bin/zenity"
        ), mock.patch.object(setup_ui.launcher, "_run_dialog", return_value=result) as run:
            self.assertEqual(setup_ui.choose_prefix([first, second]), second)

        arguments = run.call_args[0][1]
        self.assertEqual(
            arguments[:5],
            [
                "--list",
                f"--title={setup_ui.TITLE}",
                "--column=Prefix",
                "--text=Several Vortex prefixes were found. Choose one:",
                "--print-column=1",
            ],
        )
        self.assertEqual(arguments[5:], [str(first), str(second)])

    def test_choose_prefix_cancel_and_failed_dialog_return_none(self):
        first = self._make_prefix(self.root, "dialog-cancel")

        for result in (None, mock.Mock(returncode=1, stdout=str(first))):
            with self.subTest(result=result):
                with mock.patch.object(
                    setup_ui.launcher, "_dialog_tool", return_value="/usr/bin/kdialog"
                ), mock.patch.object(setup_ui.launcher, "_run_dialog", return_value=result):
                    self.assertIsNone(setup_ui.choose_prefix([first]))

    def test_stale_saved_prefix_is_discarded(self):
        prefix = self._make_prefix(self.root, "stale")
        package.save_prefix(prefix)
        (prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe").unlink()

        self.assertFalse(package.is_prefix(prefix))
        self.assertEqual(package.detect_prefix_candidates(), [])
        self.assertIsNone(package.detect_prefix())

    def test_ui_reports_prefix_save_error(self):
        prefix = self._make_prefix(self.root, "save-error")

        with mock.patch.object(setup_ui.package, "detect_prefix", return_value=None), \
             mock.patch.object(
                 setup_ui.package, "detect_prefix_candidates", return_value=[]
             ), \
             mock.patch.object(
                 setup_ui.launcher, "choose_directory", return_value=prefix
             ), \
             mock.patch.object(
                 setup_ui.package, "save_prefix", side_effect=OSError("read-only")
             ), \
             mock.patch.object(setup_ui.launcher, "show_message") as show:
            self.assertIsNone(setup_ui._require_prefix())

        self.assertIn("Could not save the prefix setting", show.call_args[0][0])

    def test_diagnostics_reads_install_from_explicit_prefix(self):
        prefix = self._make_prefix(self.root, "diagnostics-prefix")
        install = registry.find_vortex_install(prefix)
        self.assertIsNotNone(install)

        result = diagnostics.collect_all(steam_root=str(self.home), prefix_path=str(prefix))

        self.assertEqual(result["vortex_install"], install["install_dir"])
        self.assertIn("games_count", result)

    def test_diagnostics_does_not_infer_install_from_a_game_appid(self):
        steamapps = self.home / ".local" / "share" / "Steam" / "steamapps"
        (steamapps / "appmanifest_3089305857.acf").write_text(
            '"AppState"\n{\n}\n', encoding="utf-8"
        )

        self.assertFalse(hasattr(steam, "find_vortex_install"))
        result = diagnostics.collect_all(steam_root=str(steamapps.parent), prefix_path=None)

        self.assertEqual(result["games_count"], 1)
        self.assertIn("vortex_install", result)
        self.assertIsNone(result["vortex_install"])


if __name__ == "__main__":
    unittest.main()
