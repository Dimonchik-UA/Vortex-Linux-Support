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
        storage_patch = mock.patch.object(steam, "_findmnt_source", return_value="")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        # Messages use a real dialog tool by default; keep these tests offline.
        message_patch = mock.patch.object(launcher, "show_message")
        message_patch.start()
        self.addCleanup(message_patch.stop)

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
                str(first), "Proton not detected — Storage type unknown",
                str(second), "Proton not detected — Storage type unknown",
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
            arguments[:7],
            [
                "--list",
                f"--title={setup_ui.TITLE}",
                "--hide-column=1",
                "--column=Prefix",
                "--column=Location",
                "--text=Several Vortex prefixes were found. Choose one:",
                "--print-column=1",
            ],
        )
        self.assertEqual(
            arguments[7:],
            [
                str(first), "Proton not detected — Storage type unknown",
                str(second), "Proton not detected — Storage type unknown",
            ],
        )

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


class SteamLibraryPrefixDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-libs-", dir=str(HERE)))
        self.home = self.root / "home"
        self.primary = self.home / ".local" / "share" / "Steam"
        self.primary_compatdata = self.primary / "steamapps" / "compatdata"
        self.primary_compatdata.mkdir(parents=True)
        self.additional = self.root / "sd" / "SteamLibrary"
        self.additional_compatdata = self.additional / "steamapps" / "compatdata"
        self.additional_compatdata.mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.root / "state"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        storage_patch = mock.patch.object(steam, "_findmnt_source", return_value="")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_prefix(self, parent, appid):
        prefix = parent / str(appid)
        (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
        vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
        vortex.mkdir(parents=True, exist_ok=True)
        (vortex / "Vortex.exe").write_bytes(b"stub")
        return prefix.resolve()

    def _write_library_folders(self, libraries):
        vdf = self.primary / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True, exist_ok=True)
        lines = ['"libraryfolders"', "{"]
        for index, library in enumerate(libraries):
            lines.append(f'    "{index}"')
            lines.append("    {")
            lines.append(f'        "path"        "{str(library)}"')
            lines.append("    }")
        lines.append("}")
        vdf.write_text("\n".join(lines), encoding="utf-8")

    def test_vortex_prefix_in_additional_library_is_discovered(self):
        additional_prefix = self._make_prefix(self.additional_compatdata, 222)

        self._write_library_folders([self.primary, self.additional])

        self.assertEqual(steam.find_vortex_prefixes(), [additional_prefix])
        self.assertEqual(package.detect_prefix_candidates(), [additional_prefix])
        self.assertEqual(package.detect_prefix(), additional_prefix)

    def test_vortex_prefixes_in_several_libraries_are_discovered(self):
        primary_prefix = self._make_prefix(self.primary_compatdata, 111)
        additional_prefix = self._make_prefix(self.additional_compatdata, 222)

        self._write_library_folders([self.primary, self.additional])

        self.assertEqual(
            steam.find_vortex_prefixes(),
            sorted([primary_prefix, additional_prefix]),
        )

    def test_missing_additional_library_is_skipped(self):
        primary_prefix = self._make_prefix(self.primary_compatdata, 111)
        missing = self.root / "missing" / "SteamLibrary"

        self._write_library_folders([self.primary, missing])

        self.assertEqual(steam.find_vortex_prefixes(), [primary_prefix])

    def test_library_without_compatdata_is_skipped(self):
        primary_prefix = self._make_prefix(self.primary_compatdata, 111)
        empty_library = self.root / "empty" / "SteamLibrary"
        empty_library.mkdir(parents=True)

        self._write_library_folders([self.primary, empty_library])

        self.assertEqual(steam.find_vortex_prefixes(), [primary_prefix])

    def test_duplicate_library_paths_are_scanned_once(self):
        prefix = self._make_prefix(self.primary_compatdata, 111)

        with mock.patch.object(
            steam, "read_library_folders", return_value=[self.primary, self.primary]
        ):
            self.assertEqual(steam.find_vortex_prefixes(self.primary), [prefix])

    def test_empty_library_list_returns_no_prefixes(self):
        with mock.patch.object(steam, "read_library_folders", return_value=[]):
            self.assertEqual(steam.find_vortex_prefixes(self.primary), [])

    def test_inaccessible_compatdata_is_skipped(self):
        prefix = self._make_prefix(self.primary_compatdata, 111)
        inaccessible = self.root / "locked" / "SteamLibrary"
        (inaccessible / "steamapps" / "compatdata").mkdir(parents=True)
        self._write_library_folders([self.primary, inaccessible])

        target = inaccessible / "steamapps" / "compatdata"
        original_iterdir = Path.iterdir

        def failing_iterdir(path):
            if Path(path) == target:
                raise PermissionError("denied")
            return original_iterdir(path)

        with mock.patch.object(Path, "iterdir", failing_iterdir):
            self.assertEqual(steam.find_vortex_prefixes(), [prefix])


class PrefixDisplayLabelTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-label-", dir=str(HERE)))
        self.home = self.root / "home"
        self.primary = self.home / ".local" / "share" / "Steam"
        self.primary_compatdata = self.primary / "steamapps" / "compatdata"
        self.primary_compatdata.mkdir(parents=True)
        self.additional = self.root / "sd" / "SteamLibrary"
        self.additional_compatdata = self.additional / "steamapps" / "compatdata"
        self.additional_compatdata.mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.root / "config"),
                "XDG_DATA_HOME": str(self.root / "data"),
                "XDG_STATE_HOME": str(self.root / "state"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        storage_patch = mock.patch.object(steam, "_findmnt_source", return_value="")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _make_prefix(self, parent, appid):
        prefix = parent / str(appid)
        (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
        vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
        vortex.mkdir(parents=True, exist_ok=True)
        (vortex / "Vortex.exe").write_bytes(b"stub")
        return prefix.resolve()

    def test_proton_display_name_normalizes_known_versions(self):
        self.assertEqual(steam.proton_display_name(None), "Proton not detected")
        self.assertEqual(steam.proton_display_name("Proton - Experimental"), "Proton Experimental")
        self.assertEqual(steam.proton_display_name("Proton 11.0"), "Proton 11.0")
        self.assertEqual(steam.proton_display_name("GE-Proton9-20"), "Proton-GE Latest")
        self.assertEqual(steam.proton_display_name("Proton Hotfix"), "Proton Hotfix")
        self.assertEqual(steam.proton_display_name("MyCustom Proton 42"), "MyCustom Proton 42")

    def test_prefix_location_uses_primary_and_additional_libraries(self):
        primary_prefix = self._make_prefix(self.primary_compatdata, 111)
        additional_prefix = self._make_prefix(self.additional_compatdata, 222)

        vdf = self.primary / "steamapps" / "libraryfolders.vdf"
        vdf.parent.mkdir(parents=True, exist_ok=True)
        vdf.write_text(
            '"libraryfolders"\n{\n'
            '    "0"\n    {\n        "path"        "%s"\n    }\n'
            '    "1"\n    {\n        "path"        "%s"\n    }\n}\n'
            % (self.primary, self.additional),
            encoding="utf-8",
        )

        self.assertEqual(steam.prefix_location(primary_prefix), "Storage type unknown")
        self.assertEqual(steam.prefix_location(additional_prefix), "Storage type unknown")

    def test_prefix_display_label_combines_proton_and_location(self):
        prefix = self._make_prefix(self.primary_compatdata, 333)
        (self.primary / "config").mkdir(parents=True, exist_ok=True)
        (self.primary / "config" / "config.vdf").write_text(
            '"CompatToolMapping"\n{\n'
            '    "333"\n    {\n'
            '        "name"        "Proton 11.0"\n'
            '    }\n}\n',
            encoding="utf-8",
        )

        self.assertEqual(
            steam.prefix_display_label(prefix),
            "Proton 11.0 — Storage type unknown",
        )

    def _record_config_info(self, prefix, tool_base, tool_name):
        tool = tool_base / tool_name
        tool.mkdir(parents=True, exist_ok=True)
        executable = tool / "proton"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        (prefix / "config_info").write_text(
            "11.0-100\n"
            f"{tool}/files/share/fonts/\n"
            f"{tool}/files/lib/\n"
            f"{self.primary}\n",
            encoding="utf-8",
        )

    def test_prefix_without_mapping_uses_proton_from_config_info(self):
        # Real case: AppID 2300422584 has no CompatToolMapping entry, but Steam
        # created its prefix with Proton 11.0 and recorded that in config_info.
        prefix = self._make_prefix(self.primary_compatdata, 2300422584)
        self._record_config_info(
            prefix, self.primary / "steamapps" / "common", "Proton 11.0"
        )

        self.assertEqual(steam.selected_proton_name(prefix), "Proton 11.0")
        self.assertEqual(
            steam.prefix_display_label(prefix),
            "Proton 11.0 — Storage type unknown",
        )

    def test_explicit_mapping_still_reports_dw_proton(self):
        # Real case: AppID 4042394972 has an explicit CompatToolMapping entry.
        prefix = self._make_prefix(self.primary_compatdata, 4042394972)
        (self.primary / "config").mkdir(parents=True, exist_ok=True)
        (self.primary / "config" / "config.vdf").write_text(
            '"CompatToolMapping"\n{\n'
            '    "4042394972"\n    {\n'
            '        "name"        "DW-Proton Latest"\n'
            '    }\n}\n',
            encoding="utf-8",
        )
        self._record_config_info(
            prefix, self.primary / "compatibilitytools.d", "DW-Proton Latest"
        )

        self.assertEqual(steam.selected_proton_name(prefix), "DW-Proton Latest")
        self.assertEqual(
            steam.prefix_display_label(prefix),
            "DW-Proton Latest — Storage type unknown",
        )

    def test_unknown_proton_uses_english_placeholder(self):
        prefix = self._make_prefix(self.primary_compatdata, 999)

        self.assertIsNone(steam.selected_proton_name(prefix))
        self.assertEqual(
            steam.prefix_display_label(prefix),
            "Proton not detected — Storage type unknown",
        )

    def test_choose_prefix_returns_selected_path_with_compact_labels(self):
        first = self._make_prefix(self.primary_compatdata, 111)
        second = self._make_prefix(self.primary_compatdata, 222)
        result = mock.Mock(returncode=0, stdout=str(second))

        with mock.patch.object(
            setup_ui.launcher, "_dialog_tool", return_value="/usr/bin/kdialog"
        ), mock.patch.object(
            setup_ui.launcher, "_run_dialog", return_value=result
        ) as run:
            self.assertEqual(setup_ui.choose_prefix([first, second]), second)

        self.assertEqual(
            run.call_args[0][1],
            [
                "--menu",
                "Several Vortex prefixes were found. Choose one:",
                str(first), "Proton not detected — Storage type unknown",
                str(second), "Proton not detected — Storage type unknown",
            ],
        )

    def test_choose_prefix_zenity_returns_path_from_second_column(self):
        first = self._make_prefix(self.primary_compatdata, 111)
        second = self._make_prefix(self.primary_compatdata, 222)
        result = mock.Mock(returncode=0, stdout=str(second) + "\n")

        with mock.patch.object(
            setup_ui.launcher, "_dialog_tool", return_value="/usr/bin/zenity"
        ), mock.patch.object(
            setup_ui.launcher, "_run_dialog", return_value=result
        ) as run:
            self.assertEqual(setup_ui.choose_prefix([first, second]), second)

        arguments = run.call_args[0][1]
        self.assertEqual(
            arguments[:7],
            [
                "--list",
                f"--title={setup_ui.TITLE}",
                "--hide-column=1",
                "--column=Prefix",
                "--column=Location",
                "--text=Several Vortex prefixes were found. Choose one:",
                "--print-column=1",
            ],
        )
        self.assertEqual(
            arguments[7:],
            [
                str(first), "Proton not detected — Storage type unknown",
                str(second), "Proton not detected — Storage type unknown",
            ],
        )


class PrefixLocationRobustnessTest(unittest.TestCase):
    def test_media_mount_path_alone_is_never_sd(self):
        with mock.patch.object(steam, "_findmnt_source", return_value="/dev/sdb1"), \
             mock.patch.object(steam, "_udev_id_path", return_value="usb-0000:00:14.0-2"):
            self.assertEqual(
                steam.storage_device_type(
                    Path("/run/media/deck/SD/SteamLibrary/steamapps/compatdata/1")
                ),
                "Storage type unknown",
            )

    def test_missing_findmnt_result_is_not_false_sd(self):
        with mock.patch.object(steam, "_findmnt_source", return_value=""):
            self.assertEqual(
                steam.prefix_location(Path("/mnt/extra/SteamLibrary/steamapps/compatdata/1")),
                "Storage type unknown",
            )

    def test_mmc_substring_without_card_type_is_not_false_sd(self):
        # eMMC is MMC too: a substring in ID_PATH must not become "SD card".
        with mock.patch.object(steam, "_findmnt_source", return_value="/dev/mmcblk0p1"), \
             mock.patch.object(steam, "_udev_id_path", return_value="platform-fd80000.sdhci-mmc-1"):
            self.assertEqual(
                steam.prefix_location(
                    Path("/home/deck/extra/SteamLibrary/steamapps/compatdata/1"),
                    steam_root=Path("/home/deck/.local/share/Steam"),
                ),
                "Storage type unknown",
            )


class StorageDeviceTypeTest(unittest.TestCase):
    """Classification from findmnt SOURCE and the kernel MMC card ``type``."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-storage-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.devices = self.root / "devices"
        self.block_root = self.root / "block"
        self.mmc_root = self.root / "mmc"
        self.block_root.mkdir()
        self.mmc_root.mkdir()
        block_patch = mock.patch.object(steam, "_BLOCK_ROOT", str(self.block_root))
        mmc_patch = mock.patch.object(steam, "_MMC_DEVICES_ROOT", str(self.mmc_root))
        for patch in (block_patch, mmc_patch):
            patch.start()
            self.addCleanup(patch.stop)

    def _add_mmc_card(self, card_type, card_name="mmcblk0", partition="mmcblk0p1"):
        """Emulate sysfs: a card with a ``type`` file and its partition."""
        card = self.devices / card_name
        part = card / partition
        part.mkdir(parents=True, exist_ok=True)
        (card / "type").write_text(card_type + "\n", encoding="utf-8")
        (self.block_root / partition).symlink_to(part)
        (self.mmc_root / "mmc0:aaaa").symlink_to(card)
        return "/dev/" + partition

    def _type_for(self, source, id_path=""):
        # Mirror the real helper: an empty block name never yields an ID_PATH.
        def udev_for(name):
            return id_path if name else ""

        with mock.patch.object(steam, "_findmnt_source", return_value=source), \
             mock.patch.object(steam, "_udev_id_path", side_effect=udev_for):
            return steam.storage_device_type(Path("/mnt/test/prefix"))

    def test_sd_card_type_is_sd(self):
        source = self._add_mmc_card("SD")
        self.assertEqual(
            self._type_for(source, "platform-fd80000.sdhci-mmc-1"),
            "SD card",
        )

    def test_emmc_type_is_internal(self):
        source = self._add_mmc_card("MMC")
        self.assertEqual(
            self._type_for(source, "platform-fd80000.sdhci-mmc-0"),
            "Internal storage",
        )

    def test_internal_nvme_is_internal(self):
        self.assertEqual(
            self._type_for("/dev/nvme0n1p8", "pci-0000:61:00.0-nvme-1"),
            "Internal storage",
        )

    def test_nvme_with_btrfs_subvolume_suffix_is_internal(self):
        self.assertEqual(
            self._type_for("/dev/nvme0n1p8[/@home]", "pci-0000:61:00.0-nvme-1"),
            "Internal storage",
        )

    def test_mmc_with_btrfs_subvolume_suffix_is_sd(self):
        source = self._add_mmc_card("SD")
        self.assertEqual(
            self._type_for(source + "[/@]", "platform-fd80000.sdhci-mmc-1"),
            "SD card",
        )

    def test_plain_sda_source_is_unknown(self):
        self.assertEqual(
            self._type_for("/dev/sda1", "usb-0000:00:14.0-2"),
            "Storage type unknown",
        )

    def test_unknown_and_malformed_sources_are_unknown(self):
        self.assertEqual(
            self._type_for("garbage"),
            "Storage type unknown",
        )
        for source in ("/dev/nvme0n1p8[/@home", "/dev/mapper/vg-lv[/@]junk"):
            with self.subTest(source=source):
                self.assertEqual(
                    self._type_for(source, "pci-0000:61:00.0-nvme-1"),
                    "Storage type unknown",
                )

    def test_findmnt_source_uses_nofsroot(self):
        completed = mock.Mock(returncode=0, stdout="/dev/nvme0n1p8\n")
        with mock.patch.object(steam.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                steam._findmnt_source(Path("/mnt/test/prefix")),
                "/dev/nvme0n1p8",
            )
        self.assertEqual(run.call_args[0][0][0], "findmnt")
        self.assertIn("-v", run.call_args[0][0])

    def test_block_device_name_accepts_plain_and_suffixed_sources(self):
        with mock.patch.object(
            steam.os.path, "realpath", side_effect=lambda value: value
        ):
            for source, expected in (
                ("/dev/nvme0n1p8", "nvme0n1p8"),
                ("/dev/nvme0n1p8[/@home]", "nvme0n1p8"),
                ("/dev/mmcblk0p1[/@]", "mmcblk0p1"),
                ("/dev/sda1", "sda1"),
                ("/dev/mapper/vg-lv[/@]", "vg-lv"),
            ):
                with self.subTest(source=source):
                    self.assertEqual(steam._block_device_name(source), expected)

    def test_block_device_name_rejects_invalid_sources(self):
        with mock.patch.object(
            steam.os.path, "realpath", side_effect=lambda value: value
        ):
            for source in (
                "",
                "/dev/",
                "/dev/nvme0n1p8[/@home",
                "/dev/nvme0n1p8[@]",
                "/dev/mapper/vg-lv[/@]junk",
            ):
                with self.subTest(source=source):
                    self.assertEqual(steam._block_device_name(source), "")

    def test_nvme_without_internal_confirmation_is_unknown(self):
        self.assertEqual(self._type_for("/dev/nvme0n1p8"), "Storage type unknown")

    def test_usb_storage_is_unknown(self):
        self.assertEqual(
            self._type_for("/dev/sdb1", "usb-0000:00:14.0-2"),
            "Storage type unknown",
        )

    def test_usb_card_reader_is_unknown(self):
        # usb-storage exposes the card as /dev/sdX with no MMC card in sysfs.
        self.assertEqual(
            self._type_for("/dev/sdb1", "usb-0000:00:14.0-2-scsi-0:0:0:0"),
            "Storage type unknown",
        )

    def test_missing_sysfs_is_unknown(self):
        self.assertEqual(
            self._type_for("/dev/mmcblk0p1", "platform-fd80000.sdhci-mmc-1"),
            "Storage type unknown",
        )

    def test_unknown_card_type_is_unknown(self):
        source = self._add_mmc_card("SDIO")
        self.assertEqual(self._type_for(source), "Storage type unknown")

    def test_findmnt_error_is_unknown(self):
        failed = mock.Mock(returncode=1, stdout="")
        with mock.patch.object(steam.subprocess, "run", return_value=failed):
            self.assertEqual(
                steam.storage_device_type(Path("/mnt/test/prefix")),
                "Storage type unknown",
            )

    def test_findmnt_unavailable_is_unknown(self):
        with mock.patch.object(steam.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(
                steam.storage_device_type(Path("/mnt/test/prefix")),
                "Storage type unknown",
            )

    def test_non_device_mount_source_is_unknown(self):
        self.assertEqual(self._type_for("proc"), "Storage type unknown")
