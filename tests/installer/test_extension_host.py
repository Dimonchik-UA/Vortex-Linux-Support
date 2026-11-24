"""Tests for the staging host.json and the Extension staging fix.

Every test builds a throw-away Proton-like prefix inside this directory, so no
real Vortex installation is touched. Coverage:

  * host.json maps the host mods directory through the prefix z: drive;
  * external volumes use the real findmnt mount point, not library_path.parent;
  * temporarily unavailable libraries reuse only a matching installed volume;
  * install, repeat install and verify stay stable when an SD card is absent;
  * a real VDF change is still reported as drift;
  * the Extension logic (offer once, empty staging only, dispatch shape,
    malformed volumes, generated host.json) passes its node harness.
"""

import json
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

import extension_installer  # noqa: E402
import installer  # noqa: E402

SOURCE = PROJECT / "src" / "extension"
HARNESS = HERE / "staging_fix_harness.js"


class HostConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-host-", dir=str(HERE)))
        self.prefix = self.root / "compatdata"
        self.dosdevices = self.prefix / "pfx" / "dosdevices"
        self.dosdevices.mkdir(parents=True)
        self.zroot = self.root / "zroot"
        self.home = self.zroot / "home" / "deck"
        self.home.mkdir(parents=True)
        (self.dosdevices / "z:").symlink_to(self.zroot, target_is_directory=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mods_root_maps_home_through_z_drive(self):
        payload = extension_installer._host_payload(self.prefix, home=self.home)
        self.assertEqual(json.loads(payload)["modsRoot"], "Z:\\home\\deck\\Vortex Mods")
        # Verify compares bytes, so the payload must be deterministic.
        self.assertEqual(payload, extension_installer._host_payload(self.prefix, home=self.home))

    def test_missing_z_mapping_disables_host_file(self):
        (self.dosdevices / "z:").unlink()
        self.assertIsNone(extension_installer._host_payload(self.prefix, home=self.home))

    def test_home_outside_z_mapping_disables_host_file(self):
        elsewhere = self.root / "elsewhere" / "deck"
        elsewhere.mkdir(parents=True)
        self.assertIsNone(extension_installer._host_payload(self.prefix, home=elsewhere))


class ExtensionHostFileTest(HostConfigTest):
    def setUp(self):
        super().setUp()
        self.vortex = (
            self.prefix / "pfx" / "drive_c" / "users" / "steamuser"
            / "AppData" / "Roaming" / "Vortex"
        )
        (self.vortex / "plugins").mkdir(parents=True)
        self.target = self.vortex / "plugins" / extension_installer.EXTENSION_ID

    def test_install_writes_host_file_and_uninstall_removes_it(self):
        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "installed")
        self.assertIn("host.json", result["files"])
        self.assertEqual(
            (self.target / "host.json").read_bytes(),
            extension_installer._host_payload(self.prefix, home=self.home),
        )

        again = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(again["status"], "unchanged")

        removed = extension_installer.remove_extension(str(self.prefix))
        self.assertEqual(removed["status"], "removed")
        self.assertIn("host.json", removed["removed_files"])
        self.assertFalse(self.target.exists())

    def test_verify_compares_the_same_host_payload(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")
        self.assertIn("host.json", state["source_sha256"])

    def test_modified_host_file_is_reported_as_drifted(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.target / "host.json").write_text('{"modsRoot": "D:\\\\Vortex Mods"}', encoding="utf-8")
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["drifted"], ["host.json"])

    def test_prefix_without_z_installs_without_host_file(self):
        (self.dosdevices / "z:").unlink()
        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "installed")
        self.assertNotIn("host.json", result["files"])
        self.assertFalse((self.target / "host.json").exists())

    def test_stale_host_file_without_z_is_not_unchanged(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.dosdevices / "z:").unlink()
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "stale_host")
        self.assertEqual(state["stale_host"], str(self.target / "host.json"))

        repeat = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repeat["status"], "stale_host")
        self.assertTrue((self.target / "host.json").is_file())

    def _make_vortex_exe(self):
        exe = (
            self.prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
        )
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"")
        return exe

    def test_stale_host_symlink_without_z_is_not_unchanged(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        host = self.target / "host.json"
        saved = host.read_bytes()
        host.unlink()
        (self.target / "elsewhere.json").write_bytes(saved)
        host.symlink_to("elsewhere.json")
        (self.dosdevices / "z:").unlink()

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "stale_host")
        self.assertEqual(state["stale_host"], str(host))

        repeat = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repeat["status"], "stale_host")
        self.assertTrue(host.is_symlink())

    def test_cyclic_z_symlink_does_not_break_diagnostics(self):
        (self.dosdevices / "z:").unlink()
        (self.dosdevices / "z:").symlink_to("z:")
        real_resolve = Path.resolve

        def resolve_with_guard(path, *args, **kwargs):
            if path.name == "z:" and path.parent.name == "dosdevices":
                raise RuntimeError("symlink loop")
            return real_resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", resolve_with_guard):
            self.assertIsNone(extension_installer._z_root(self.prefix))

    def test_directory_host_without_z_is_reported_and_kept(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        host = self.target / "host.json"
        host.unlink()
        host.mkdir()
        (host / "user-file").write_text("keep", encoding="utf-8")
        (self.dosdevices / "z:").unlink()

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "stale_host")
        self.assertNotEqual(state["status"], "unchanged")

        repeat = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repeat["status"], "stale_host")
        self.assertTrue((host / "user-file").is_file())

    def test_directory_host_with_z_is_invalid_without_partial_update(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        before = {
            name: (self.target / name).read_bytes() for name in ("info.json", "index.js")
        }
        host = self.target / "host.json"
        host.unlink()
        host.mkdir()
        (host / "user-file").write_text("keep", encoding="utf-8")

        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "invalid_host_file")
        for name, data in before.items():
            self.assertEqual((self.target / name).read_bytes(), data)
        self.assertTrue((host / "user-file").is_file())

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "invalid_host_file")

    def test_uninstall_skips_directory_host_without_deleting_it(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        host = self.target / "host.json"
        host.unlink()
        host.mkdir()
        (host / "user-file").write_text("keep", encoding="utf-8")

        removed = extension_installer.remove_extension(str(self.prefix))
        self.assertEqual(removed["status"], "removed")
        self.assertIn("host.json", removed["skipped_files"])
        self.assertTrue((host / "user-file").is_file())
        self.assertFalse((self.target / "info.json").exists())

    def test_verify_reports_attention_for_stale_host(self):
        self._make_vortex_exe()
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.dosdevices / "z:").unlink()

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home), \
             mock.patch.object(installer, "_steam_library_state", return_value={"config_present": True}), \
             mock.patch.object(installer.registry, "read_state", return_value={"status": "ok"}), \
             mock.patch.object(installer, "_running_vortex", return_value=[]):
            result = installer.verify(str(self.prefix), str(SOURCE))

        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(result["extension"]["status"], "stale_host")

    def test_dry_run_does_not_promise_stale_host_install(self):
        self._make_vortex_exe()
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.dosdevices / "z:").unlink()

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home), \
             mock.patch.object(installer, "_running_vortex", return_value=[]):
            result = installer.install(
                str(self.prefix), str(SOURCE), dry_run=True, fix_steam_library=False
            )

        self.assertEqual(result["status"], "attention_required")
        self.assertEqual(result["extension"]["status"], "stale_host")

    def test_broken_volume_map_is_counted_without_z(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        (self.target / "host.json").write_text(
            json.dumps({
                "modsRoot": "Z:\\home\\deck\\Vortex Mods",
                "volumes": "garbage",
            }),
            encoding="utf-8",
        )
        (self.dosdevices / "z:").unlink()

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "stale_host")
        self.assertGreaterEqual(state["host_map_issues"], 1)


class HostVolumesTest(ExtensionHostFileTest):
    """host.json v2: anchors come from real mount points, not parent paths."""

    def setUp(self):
        super().setUp()
        self.home_mount = self.zroot / "home"
        self.sd_mount = self.zroot / "run" / "media" / "deck" / "SD"
        self.sd2_mount = self.zroot / "run" / "media" / "deck" / "SD2"
        self.home_sd_mount = self.home / "SDCard"
        self.external_mounts = [self.sd_mount, self.sd2_mount, self.home_sd_mount]
        self.unavailable_mounts = []

        self.steam_root = self.home / ".local" / "share" / "Steam"
        (self.steam_root / "steamapps").mkdir(parents=True)
        self.sd_library = self.sd_mount / "SteamLibrary"
        self.sd_library.mkdir(parents=True)
        self._write_libraries([str(self.sd_library)])

        self.finder = mock.patch.object(
            extension_installer.steam, "findmnt_target", side_effect=self._fake_findmnt
        )
        self.finder.start()
        self.addCleanup(self.finder.stop)

    @staticmethod
    def _under(path, anchor):
        try:
            Path(path).resolve(strict=False).relative_to(Path(anchor).resolve(strict=False))
            return True
        except ValueError:
            return False

    def _fake_findmnt(self, path):
        current = Path(path).resolve(strict=False)
        scoped = sorted(
            self.unavailable_mounts + self.external_mounts,
            key=lambda item: len(str(item)),
            reverse=True,
        )
        for mount in scoped:
            if self._under(current, mount):
                return "" if mount in self.unavailable_mounts else str(mount)
        if not self._under(current, self.zroot):
            return ""
        return str(self.home_mount)

    def _write_libraries(self, paths):
        entries = []
        for index, library_path in enumerate(paths):
            entries.append(
                '\t"%d"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n' % (index, library_path)
            )
        vdf = '"libraries"\n{\n' + "".join(entries) + "}\n"
        (self.steam_root / "steamapps" / "libraryfolders.vdf").write_text(
            vdf, encoding="utf-8"
        )

    def _payload(self, existing_host=None):
        return extension_installer._host_payload(
            self.prefix, home=self.home, existing_host=existing_host
        )

    def test_standard_sd_library_uses_mount_point(self):
        data = json.loads(self._payload())
        self.assertEqual(data["modsRoot"], "Z:\\home\\deck\\Vortex Mods")
        self.assertEqual(
            data["volumes"],
            [
                {"anchor": "Z:\\home\\deck", "modsRoot": "Z:\\home\\deck\\Vortex Mods"},
                {
                    "anchor": "Z:\\run\\media\\deck\\SD",
                    "modsRoot": "Z:\\run\\media\\deck\\SD\\Vortex Mods",
                },
            ],
        )

    def test_nested_library_uses_mount_point_not_parent(self):
        nested = self.sd_mount / "Games" / "SteamLibrary"
        nested.mkdir(parents=True)
        self._write_libraries([str(nested)])
        volumes = json.loads(self._payload())["volumes"]
        self.assertEqual(
            volumes[1],
            {
                "anchor": "Z:\\run\\media\\deck\\SD",
                "modsRoot": "Z:\\run\\media\\deck\\SD\\Vortex Mods",
            },
        )
        self.assertNotIn("Games", volumes[1]["anchor"])

    def test_nested_mounted_library_and_detached_directory_keep_mount_anchor(self):
        nested = self.sd_mount / "Games" / "SteamLibrary"
        nested.mkdir(parents=True)
        self._write_libraries([str(nested)])
        installed = self._payload()
        self.assertEqual(self._payload(existing_host=installed), installed)

        # SD is detached but the mountpoint directory still exists; findmnt
        # then reports the parent filesystem instead of an empty target.
        with mock.patch.object(
            extension_installer.steam, "findmnt_target", return_value=str(self.zroot)
        ):
            detached = self._payload(existing_host=installed)
        self.assertEqual(detached, installed)
        self.assertEqual(
            [entry["anchor"] for entry in json.loads(detached)["volumes"]],
            ["Z:\\home\\deck", "Z:\\run\\media\\deck\\SD"],
        )

    def test_unavailable_home_library_reuses_specific_anchor(self):
        home_sd_library = self.home_sd_mount / "SteamLibrary"
        home_sd_library.mkdir(parents=True)
        self._write_libraries([str(home_sd_library)])
        installed = self._payload()
        self.unavailable_mounts = [self.home_sd_mount]
        fresh = self._payload(existing_host=installed)
        self.assertEqual(fresh, installed)
        self.assertEqual(
            [entry["anchor"] for entry in json.loads(fresh)["volumes"]],
            ["Z:\\home\\deck", "Z:\\home\\deck\\SDCard"],
        )

    def test_partial_malformed_volumes_are_reported_and_repaired(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]

        corrupt = json.loads(installed)
        corrupt["volumes"].insert(0, {
            "anchor": "NOT_A_Z_PATH",
            "modsRoot": "Z:\\home\\deck\\Vortex Mods",
        })
        corrupt_bytes = (json.dumps(corrupt, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        (self.target / "host.json").write_bytes(corrupt_bytes)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["host_map_issues"], 1)

        repaired = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repaired["status"], "installed")
        self.assertEqual(repaired["host_map_issues_repaired"], 1)
        self.assertEqual(
            [entry["anchor"] for entry in json.loads(self._payload(existing_host=corrupt_bytes))["volumes"]],
            ["Z:\\home\\deck", "Z:\\run\\media\\deck\\SD"],
        )
        self.assertEqual((self.target / "host.json").read_bytes(), installed)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            final = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(final["status"], "unchanged")
        self.assertEqual(final["host_map_issues"], 0)

    def test_two_libraries_on_one_mount_are_deduplicated(self):
        nested = self.sd_mount / "Games" / "SteamLibrary"
        nested.mkdir(parents=True)
        self._write_libraries([str(self.sd_library), str(nested)])
        anchors = [entry["anchor"] for entry in json.loads(self._payload())["volumes"]]
        self.assertEqual(
            anchors, ["Z:\\home\\deck", "Z:\\run\\media\\deck\\SD"]
        )

    def test_two_libraries_on_different_mounts(self):
        sd2_library = self.sd2_mount / "SteamLibrary"
        sd2_library.mkdir(parents=True)
        self._write_libraries([str(self.sd_library), str(sd2_library)])
        anchors = [entry["anchor"] for entry in json.loads(self._payload())["volumes"]]
        self.assertEqual(
            anchors,
            [
                "Z:\\home\\deck",
                "Z:\\run\\media\\deck\\SD",
                "Z:\\run\\media\\deck\\SD2",
            ],
        )

    def test_home_library_on_separate_filesystem_gets_own_volume(self):
        home_sd_library = self.home_sd_mount / "SteamLibrary"
        home_sd_library.mkdir(parents=True)
        self._write_libraries([str(home_sd_library)])
        volumes = json.loads(self._payload())["volumes"]
        self.assertEqual(
            [entry["anchor"] for entry in volumes],
            ["Z:\\home\\deck", "Z:\\home\\deck\\SDCard"],
        )
        self.assertEqual(
            volumes[1]["modsRoot"], "Z:\\home\\deck\\SDCard\\Vortex Mods"
        )

    def test_determinism_when_library_order_changes(self):
        sd2_library = self.sd2_mount / "SteamLibrary"
        sd2_library.mkdir(parents=True)
        self._write_libraries([str(self.sd_library), str(sd2_library)])
        first = self._payload()
        self._write_libraries([str(sd2_library), str(self.sd_library)])
        self.assertEqual(first, self._payload())

    def test_hardcoded_v1_payload_bytes(self):
        shutil.rmtree(self.steam_root)
        self.assertEqual(
            self._payload(),
            b'{"modsRoot": "Z:\\\\home\\\\deck\\\\Vortex Mods"}\n',
        )

    def test_library_outside_z_mapping_is_skipped(self):
        outside = self.root / "outside" / "SteamLibrary"
        outside.mkdir(parents=True)
        self._write_libraries([str(outside)])
        data = json.loads(self._payload())
        self.assertEqual(
            [entry["anchor"] for entry in data["volumes"]], ["Z:\\home\\deck"]
        )

    def test_unavailable_library_reuses_matching_installed_volume(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]
        self.assertEqual(self._payload(existing_host=installed), installed)

    def test_malformed_existing_host_is_not_reused(self):
        malformed = b'{"modsRoot":"Z:\\\\home\\\\deck\\\\Vortex Mods","volumes":{"anchor":"Z:\\\\sd"}}'
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]
        data = json.loads(self._payload(existing_host=malformed))
        self.assertEqual(
            [entry["anchor"] for entry in data["volumes"]], ["Z:\\home\\deck"]
        )

    def test_verify_with_unmounted_sd_is_unchanged(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")

    def test_repeat_install_with_unmounted_sd_is_unchanged(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]
        again = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(again["status"], "unchanged")

    def test_removed_library_is_reported_as_drifted(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]
        self._write_libraries([])
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["drifted"], ["host.json"])

    def test_embedded_null_in_anchor_is_counted_and_skipped(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        corrupt = json.loads(installed)
        corrupt["volumes"].append({
            "anchor": "Z:\\a\u0000b",
            "modsRoot": "Z:\\a\u0000b\\Vortex Mods",
        })
        corrupt_bytes = (json.dumps(corrupt, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        (self.target / "host.json").write_bytes(corrupt_bytes)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["host_map_issues"], 1)
        self.assertEqual(state["drifted"], ["host.json"])

        repaired = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repaired["status"], "installed")
        self.assertEqual(repaired["host_map_issues_repaired"], 1)
        self.assertEqual((self.target / "host.json").read_bytes(), installed)

    def test_wrong_saved_mods_root_is_not_reused_as_unchanged(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        shutil.rmtree(self.sd_library)
        self.unavailable_mounts = [self.sd_mount]

        corrupt = json.loads(installed)
        for entry in corrupt["volumes"]:
            if entry["anchor"] == "Z:\\run\\media\\deck\\SD":
                entry["modsRoot"] = "Z:\\run\\media\\deck\\SD\\Games\\Vortex Mods"
        (self.target / "host.json").write_text(
            json.dumps(corrupt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
        )

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "drifted")
        self.assertEqual(state["host_map_issues"], 1)
        self.assertNotEqual(
            state["installed_sha256"]["host.json"], state["source_sha256"]["host.json"]
        )

        repaired = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(repaired["status"], "installed")
        self.assertEqual(json.loads((self.target / "host.json").read_bytes())["volumes"], [
            {"anchor": "Z:\\home\\deck", "modsRoot": "Z:\\home\\deck\\Vortex Mods"},
        ])

    def test_damaged_v2_map_without_steam_root_is_not_downgraded(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        host = self.target / "host.json"
        corrupt = json.loads(host.read_bytes())
        corrupt["volumes"] = "garbage"
        corrupt_bytes = (json.dumps(corrupt, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        host.write_bytes(corrupt_bytes)
        shutil.rmtree(self.steam_root)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "host_map_unrepairable")
        self.assertGreaterEqual(state["host_map_issues"], 1)

        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "host_map_unrepairable")
        self.assertEqual(host.read_bytes(), corrupt_bytes)
        self.assertEqual(
            self._payload(), b'{"modsRoot": "Z:\\\\home\\\\deck\\\\Vortex Mods"}\n'
        )

    def test_valid_v2_without_steam_root_is_preserved_byte_for_byte(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        self.assertIn("volumes", json.loads(installed))
        shutil.rmtree(self.steam_root)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")
        self.assertEqual(state["host_map_issues"], 0)
        self.assertEqual((self.target / "host.json").read_bytes(), installed)

        again = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual((self.target / "host.json").read_bytes(), installed)

    def test_verify_with_valid_v2_without_steam_root_is_ok(self):
        self._make_vortex_exe()
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()
        shutil.rmtree(self.steam_root)

        with mock.patch.object(extension_installer, "_host_home", return_value=self.home), \
             mock.patch.object(installer, "_steam_library_state", return_value={"config_present": True}), \
             mock.patch.object(installer.registry, "read_state", return_value={"status": "ok"}), \
             mock.patch.object(installer, "_running_vortex", return_value=[]):
            result = installer.verify(str(self.prefix), str(SOURCE))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["extension"]["status"], "unchanged")
        self.assertEqual((self.target / "host.json").read_bytes(), installed)

    def test_v1_install_without_steam_root_stays_byte_for_byte(self):
        shutil.rmtree(self.steam_root)
        expected = b'{"modsRoot": "Z:\\\\home\\\\deck\\\\Vortex Mods"}\n'

        result = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(result["status"], "installed")
        self.assertEqual((self.target / "host.json").read_bytes(), expected)

        again = extension_installer.install_extension(
            SOURCE, str(self.prefix), home=self.home
        )
        self.assertEqual(again["status"], "unchanged")
        with mock.patch.object(extension_installer, "_host_home", return_value=self.home):
            state = installer.installed_state(str(self.prefix), str(SOURCE))
        self.assertEqual(state["status"], "unchanged")
        self.assertEqual((self.target / "host.json").read_bytes(), expected)

    def test_missing_home_mount_keeps_sd_volume(self):
        installed = self._payload()

        def findmnt_without_home(path):
            current = Path(path).resolve(strict=False)
            if self._under(current, self.home_mount):
                return None
            if self._under(current, self.sd_mount):
                return str(self.sd_mount)
            return None

        with mock.patch.object(
            extension_installer, "_mount_point", side_effect=findmnt_without_home
        ):
            fresh = self._payload(existing_host=installed)
        self.assertEqual(fresh, installed)
        self.assertIn("Z:\\run\\media\\deck\\SD", [
            entry["anchor"] for entry in json.loads(fresh)["volumes"]
        ])

    def test_changed_sd_mountpoint_is_drift_not_unconditional_reuse(self):
        extension_installer.install_extension(SOURCE, str(self.prefix), home=self.home)
        installed = (self.target / "host.json").read_bytes()

        sd2_library = self.sd2_mount / "SteamLibrary"
        sd2_library.mkdir(parents=True)
        self._write_libraries([str(sd2_library)])
        fresh = self._payload(existing_host=installed)

        self.assertNotEqual(fresh, installed)
        anchors = [entry["anchor"] for entry in json.loads(fresh)["volumes"]]
        self.assertIn("Z:\\run\\media\\deck\\SD2", anchors)
        self.assertNotIn("Z:\\run\\media\\deck\\SD", anchors)

    def test_mixed_mounts_choose_most_specific_anchor(self):
        nested = self.sd_mount / "Games" / "SteamLibrary"
        nested.mkdir(parents=True)
        home_sd_library = self.home_sd_mount / "SteamLibrary"
        home_sd_library.mkdir(parents=True)
        self._write_libraries([str(nested), str(home_sd_library)])
        installed = self._payload()
        self.assertEqual(
            [entry["anchor"] for entry in json.loads(installed)["volumes"]],
            ["Z:\\home\\deck", "Z:\\home\\deck\\SDCard", "Z:\\run\\media\\deck\\SD"],
        )

        def findmnt_with_detached_home_sd(path):
            current = Path(path).resolve(strict=False)
            if self._under(current, self.home_sd_mount):
                return str(self.zroot)
            if self._under(current, self.sd_mount):
                return str(self.sd_mount)
            return str(self.home_mount)

        with mock.patch.object(
            extension_installer.steam, "findmnt_target", side_effect=findmnt_with_detached_home_sd
        ):
            fresh = self._payload(existing_host=installed)
        self.assertEqual(fresh, installed)

    def test_generated_host_is_read_by_extension(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        host_file = self.root / "host-generated.json"
        host_file.write_bytes(self._payload())
        result = subprocess.run(
            [node, str(HARNESS), "--host", str(host_file)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CROSS_HOST_OK", result.stdout)


class StagingFixHarnessTest(unittest.TestCase):
    def test_extension_staging_fix_harness(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")
        result = subprocess.run(
            [node, str(HARNESS)], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STAGING_FIX_HARNESS_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
