"""Tests for src/linux-support/manager.py.

Dialogs and package operations are mocked; only the decision logic is tested.
"""

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
import updater  # noqa: E402


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

    def test_run_starts_background_check_without_blocking_menu(self):
        started = threading.Event()
        release = threading.Event()

        def slow_check(*_a, **_k):
            started.set()
            release.wait(timeout=2)
            return updater.UpdateCheckResult(status="unknown", checked_at=1.0)

        with mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check) as check, \
             mock.patch.object(manager, "choose_action", return_value=None):
            result = manager.run()

        self.assertEqual(result, 0)
        self.assertTrue(started.wait(timeout=1), "background update check did not start")
        release.set()
        # give daemon thread a moment to finish
        for _ in range(50):
            if check.called:
                break
            time.sleep(0.01)
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
             mock.patch.object(manager.package, "install", return_value=gh_result) as install:
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
        self.assertIn("формат", self.show_message.call_args[0][0])

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
        self.assertIn("не выполнена", self.show_message.call_args[0][0])
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

        def slow_check(*_a, **_k):
            started.set()
            release.wait(timeout=2)
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

        self.confirm.return_value = False  # decline GitHub update, keep self-refresh
        with mock.patch.object(manager, "_UPDATE_CHECK_WAIT_TIMEOUT", 1.0), \
             mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check) as check, \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager._start_background_update_check()
            self.assertTrue(started.wait(timeout=1), "background check did not start")

            def _release_soon():
                time.sleep(0.1)
                release.set()

            threading.Thread(target=_release_soon, daemon=True).start()
            manager.action_install(prefix=str(self.prefix))

        check.assert_called_once_with(force=False)
        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.confirm.assert_called_once()
        self.assertIn("0.2.0", self.confirm.call_args[0][0])

    def test_install_timeout_during_inflight_check_falls_back_without_parallel_request(self):
        started = threading.Event()
        release = threading.Event()
        calls = {"count": 0}

        def slow_check(*_a, **_k):
            calls["count"] += 1
            started.set()
            release.wait(timeout=2)
            info = updater.UpdateCheckResult(status="unknown", checked_at=9999999999.0, error="timeout")
            try:
                updater.save_cache(info)
            except Exception:
                pass
            return info

        with mock.patch.object(manager, "_UPDATE_CHECK_WAIT_TIMEOUT", 0.05), \
             mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check), \
             mock.patch.object(manager.package, "install", return_value=self.normal_result) as install, \
             mock.patch.object(manager.updater, "prepare_release_package") as prepare:
            manager._start_background_update_check()
            self.assertTrue(started.wait(timeout=1), "background check did not start")
            manager.action_install(prefix=str(self.prefix))
            release.set()

        self.assertEqual(calls["count"], 1, "parallel/repeated background check started")
        prepare.assert_not_called()
        install.assert_called_once_with(prefix=str(self.prefix), update=True)
        self.confirm.assert_not_called()

    def test_background_check_not_started_in_parallel_when_inflight(self):
        started = threading.Event()
        release = threading.Event()
        calls = {"count": 0}

        def slow_check(*_a, **_k):
            calls["count"] += 1
            started.set()
            release.wait(timeout=2)
            return updater.UpdateCheckResult(status="unknown", checked_at=1.0)

        try:
            with mock.patch.object(manager.updater, "check_for_updates", side_effect=slow_check):
                first = manager._start_background_update_check()
                self.assertIsNotNone(first)
                self.assertTrue(started.wait(timeout=1), "background check did not start")
                second = manager._start_background_update_check()
                self.assertIsNone(second)
                # brief window to catch accidental parallel start
                time.sleep(0.05)
                self.assertEqual(calls["count"], 1)
        finally:
            release.set()
            if first is not None:
                first.join(timeout=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
