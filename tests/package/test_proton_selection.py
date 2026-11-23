"""Proton selection follows Steam; recommendations never block Vortex launch."""

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

import launcher  # noqa: E402
import proton  # noqa: E402
import steam  # noqa: E402


class ProtonSelectionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-proton-", dir=str(HERE)))
        self.home = self.root / "home"
        self.steam = self.home / ".local" / "share" / "Steam"
        self.tools = self.steam / "compatibilitytools.d"
        self.common = self.steam / "steamapps" / "common"
        self.compatdata = self.steam / "steamapps" / "compatdata"
        self.config_dir = self.steam / "config"
        for directory in (self.tools, self.common, self.compatdata, self.config_dir):
            directory.mkdir(parents=True)

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

        for name in ("show_info", "show_message", "confirm", "choose_directory"):
            patcher = mock.patch.object(launcher, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.confirm.return_value = False

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _tool(self, base, name):
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        executable = directory / "proton"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        return executable

    def _prefix(self, appid="3089305857"):
        prefix = self.compatdata / appid
        (prefix / "pfx" / "drive_c" / "users").mkdir(parents=True)
        vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex"
        vortex.mkdir(parents=True, exist_ok=True)
        (vortex / "Vortex.exe").write_bytes(b"stub")
        return prefix

    def _map(self, name, appid="3089305857"):
        (self.config_dir / "config.vdf").write_text(
            '"InstallContentStats"\n{\n}\n'
            '"CompatToolMapping"\n{\n'
            f'\t"{appid}"\n\t{{\n'
            f'\t\t"name"\t\t"{name}"\n'
            '\t\t"config"\t\t""\n'
            '\t\t"priority"\t\t"250"\n'
            '\t}\n}\n',
            encoding="utf-8",
        )

    def test_dw_proton_selected_is_used_without_warning(self):
        tool = self._tool(self.tools, "DW-Proton Latest")
        prefix = self._prefix()
        self._map("DW-Proton Latest")

        self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_not_called()
        self.confirm.assert_not_called()

    def test_ge_proton_selected_is_used_without_warning(self):
        tool = self._tool(self.tools, "GE-Proton9-20")
        prefix = self._prefix()
        self._map("GE-Proton9-20")

        self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_not_called()

    def test_proton_11_selected_is_used_without_warning(self):
        tool = self._tool(self.common, "Proton 11.0")
        prefix = self._prefix()
        self._map("Proton 11.0")

        self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_not_called()

    def test_experimental_and_hotfix_selections_are_used(self):
        prefix = self._prefix()
        for name in ("Proton - Experimental", "Proton Hotfix"):
            tool = self._tool(self.common, name)
            self._map(name)
            self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_not_called()

    def test_unlisted_proton_version_is_used(self):
        tool = self._tool(self.tools, "MyCustom Proton 42")
        prefix = self._prefix()
        self._map("MyCustom Proton 42")

        self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_not_called()

    def test_missing_mapping_warns_but_never_blocks(self):
        tool = self._tool(self.common, "Proton 11.0")
        prefix = self._prefix()

        self.assertEqual(launcher._select_proton(prefix), str(tool))
        self.show_info.assert_called_once()
        self.assertIn("DW-Proton or Proton-GE", self.show_info.call_args[0][0])

    def test_refusing_recommendation_does_not_abort_launch(self):
        self._tool(self.tools, "DW-Proton Latest")
        prefix = self._prefix()

        self.confirm.return_value = False
        self.assertIsNotNone(launcher._select_proton(prefix))
        self.show_message.assert_not_called()

    def test_unresolvable_selection_falls_back_without_blocking(self):
        fallback = self._tool(self.tools, "DW-Proton Latest")
        prefix = self._prefix()
        self._map("Tool That Is Not Installed")

        self.assertEqual(launcher._select_proton(prefix), str(fallback))

    def test_no_selected_proton_name_still_allows_launch(self):
        self._tool(self.common, "Proton 11.0")
        prefix = self._prefix(appid="999")
        self._map("DW-Proton Latest", appid="3089305857")

        self.assertIsNotNone(launcher._select_proton(prefix))

    def test_no_proton_installed_is_the_only_genuine_blocker(self):
        prefix = self._prefix()

        self.assertIsNone(launcher._select_proton(prefix))
        self.show_message.assert_called_once()

    def test_launch_uses_selected_tool_unchanged(self):
        tool = self._tool(self.tools, "DW-Proton Latest")
        prefix = self._prefix()
        self._map("DW-Proton Latest")
        vortex = prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"

        command, env = proton._prepare_launch(str(tool), str(vortex), str(prefix))

        self.assertEqual(command[0], str(tool))
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(prefix))

    def test_default_proton_does_not_force_proton_11(self):
        self._tool(self.common, "Proton 11.0")
        experimental = self._tool(self.common, "Proton - Experimental")

        self.assertEqual(proton.default_proton(), str(experimental))


if __name__ == "__main__":
    unittest.main()
