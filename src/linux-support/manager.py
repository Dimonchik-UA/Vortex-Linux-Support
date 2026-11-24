"""Graphical OVLS Manager for Desktop Mode.

A thin ``kdialog`` menu (``zenity`` as fallback) over the installed package:

  * Install / Update — ``package.install(..., update=True)`` re-applies the
    Extension, NXM handler and shortcuts from the installed copy;
  * Verify           — ``package.verify`` (copy, prefix integration, shortcuts);
  * Uninstall        — ``package.uninstall``; a blocked removal keeps everything;
  * Change prefix    — refused while Vortex runs, config/NXM rolled back on error.

Destructive actions require a dialog answer; without a dialog tool they are
refused instead of running silently. No new dependencies, no Vortex changes.
"""

import os
import sys
import threading
from pathlib import Path

import desktop
import installer
import launcher
import package
import setup_ui
import updater

TITLE = "OVLS Manager"

ACTIONS = [
    ("install", "Install / Update"),
    ("verify", "Verify"),
    ("uninstall", "Uninstall"),
    ("prefix", "Change prefix"),
]

# Background GitHub update check state (startup is non-blocking; Install/Update may
# wait briefly if a check is already in progress to avoid missing an update).
_UPDATE_CHECK_WAIT_TIMEOUT = 4.0
_update_check_lock = threading.Lock()
_update_check_inflight = False
_update_check_done = threading.Event()
_update_check_done.set()


def _zenity(tool):
    return os.path.basename(tool).startswith("zenity")


def choose_action():
    tool = launcher._dialog_tool()
    if tool is None:
        return None
    if _zenity(tool):
        arguments = [
            "--list",
            f"--title={TITLE}",
            "--column=ID",
            "--column=Action",
            "--hide-column=1",
            "--print-column=1",
        ]
        for key, label in ACTIONS:
            arguments.extend([key, label])
    else:
        arguments = ["--menu", "Select an action"]
        for key, label in ACTIONS:
            arguments.extend([key, label])
    result = launcher._run_dialog(tool, arguments)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def current_prefix():
    return package.detect_prefix()


def _require_prefix():
    prefix = current_prefix()
    if prefix:
        saved = package.load_prefix()
        try:
            if not saved or Path(saved).expanduser().resolve() != prefix:
                package.save_prefix(prefix)
        except OSError as exc:
            launcher.show_message(f"Could not save the prefix setting:\n{exc}")
            return None
        return prefix
    candidates = package.detect_prefix_candidates()
    if len(candidates) > 1:
        prefix = setup_ui.choose_prefix(candidates)
        if not prefix:
            return None
        try:
            package.save_prefix(prefix)
        except OSError as exc:
            launcher.show_message(f"Could not save the prefix setting:\n{exc}")
            return None
        return prefix
    if not candidates:
        launcher.show_message(
            "Vortex not found. Add vortex-setup-*.exe as a non-Steam game in Steam, "
            "select a Proton compatibility tool (DW-Proton or GE-Proton are recommended) "
            "and run it once, or pass --prefix /path/to/compatdata."
        )
    chosen = launcher.choose_directory()
    if not chosen:
        return None
    if not package.is_prefix(chosen):
        launcher.show_message(
            "This is not a valid Vortex prefix. It must contain "
            "pfx/drive_c/users/ and pfx/drive_c/Program Files/Vortex/Vortex.exe:\n"
            f"{chosen}"
        )
        return None
    try:
        package.save_prefix(chosen)
    except OSError as exc:
        launcher.show_message(f"Could not save the prefix setting:\n{exc}")
        return None
    return Path(chosen).expanduser().resolve()


def _confirm(text):
    if launcher._dialog_tool() is None:
        print("OVLS Manager: no dialog tool available; action refused", file=sys.stderr)
        return False
    return launcher.confirm(text)


def _shortcut_notes(shortcut_result):
    """Human-readable explanation for shortcut conflicts or failures."""
    lines = []
    skipped = shortcut_result.get("skipped") or []
    if skipped:
        lines.append("Shortcuts were not overwritten (the file already exists and is not owned by OVLS):")
        lines.extend(f"  {item['path']}" for item in skipped)
    if shortcut_result.get("status") != "ok" and not skipped:
        reason = shortcut_result.get("reason") or "could not create all shortcuts"
        lines.append(f"Shortcuts: {reason}")
    return lines


def _start_background_update_check():
    """Refresh GitHub update cache without blocking Manager startup/UI.

    Never starts a parallel check while one is already in progress.
    """
    global _update_check_inflight
    with _update_check_lock:
        if _update_check_inflight:
            return None
        _update_check_inflight = True
        _update_check_done.clear()

    def _worker():
        global _update_check_inflight
        try:
            updater.check_for_updates(force=False)
        except Exception:
            # Network/cache errors must not affect Manager operation.
            pass
        finally:
            with _update_check_lock:
                _update_check_inflight = False
            _update_check_done.set()

    thread = threading.Thread(target=_worker, name="ovls-update-check", daemon=True)
    thread.start()
    return thread


def _wait_for_background_update_check(timeout=None):
    """Wait for an in-flight startup check, if any, without starting another."""
    if timeout is None:
        timeout = _UPDATE_CHECK_WAIT_TIMEOUT
    with _update_check_lock:
        inflight = _update_check_inflight
    if not inflight:
        return True
    try:
        return _update_check_done.wait(timeout=max(0.0, float(timeout)))
    except Exception:
        return False


def _cached_update_info():
    """Return cached updater state (no network)."""
    try:
        return updater.load_cache()
    except Exception:
        return None


def _available_update_info():
    info = _cached_update_info()
    if info and getattr(info, "status", None) == "update_available":
        return info
    return None


def _self_refresh_install(prefix):
    """Preserve existing Install/Update behavior from installed copy."""
    return package.install(prefix=str(prefix), update=True)


def _github_release_install(prefix):
    """Download, verify, extract and install a GitHub release package.

    Returns (result, error_message). ``result`` is the package.install result on
    success. On failure, returns (None, message) and never leaves a prepared
    temp directory behind.
    """
    prepared = None
    try:
        try:
            fresh = updater.check_for_updates(force=True)
        except Exception as exc:
            return None, f"Could not check for a GitHub update:\n{exc}"

        status = getattr(fresh, "status", None)
        if status == "up_to_date":
            # Installed version already equals the latest release. This is not a
            # network error; clear any stale cache entry so the next check is
            # clean and self-refresh can proceed without a false prompt.
            try:
                updater.invalidate_cache()
            except OSError:
                pass
            return None, "OVLS is already up to date. No update needed."

        if status != "update_available":
            return None, (
                "GitHub update is not available right now. "
                "Check your connection and try again later."
            )

        prepared = updater.prepare_release_package(fresh)
        result = package.install(
            package_root=prepared.package_root,
            prefix=str(prefix),
            update=True,
        )
        expected_version = updater.normalize_version(prepared.version) or prepared.version
        installed_version = updater.read_installed_version()
        if installed_version != expected_version:
            raise ValueError(
                f"Installed OVLS version does not match the release: "
                f"expected {expected_version}, found {installed_version}"
            )
        # Attach release metadata for user-facing summary.
        result = dict(result)
        result["github_update"] = {
            "status": "installed",
            "version": prepared.version,
            "tag_name": prepared.tag_name,
        }
        return result, None
    except updater.VerificationError as exc:
        return None, f"Update integrity check failed (SHA-256):\n{exc}"
    except updater.PackageFormatError as exc:
        return None, f"Update package is corrupt or has an invalid format:\n{exc}"
    except updater.NetworkError as exc:
        return None, f"Network error while downloading the update:\n{exc}"
    except updater.ReleaseNotFoundError as exc:
        return None, f"Update release is not available:\n{exc}"
    except updater.UpdaterError as exc:
        return None, f"Could not prepare the update:\n{exc}"
    except (OSError, ValueError) as exc:
        return None, f"Update installation failed:\n{exc}"
    except Exception as exc:
        return None, f"Could not install the update:\n{exc}"
    finally:
        if prepared is not None:
            try:
                prepared.cleanup()
            except Exception:
                pass


def action_install(prefix=None):
    prefix = prefix or _require_prefix()
    if not prefix:
        return
    if not launcher.vortex_exe(prefix).is_file():
        launcher.show_message(f"Vortex.exe not found in prefix:\n{launcher.vortex_exe(prefix)}")
        return
    if not (package.app_root() / package.MANIFEST_NAME).is_file():
        launcher.show_message(
            "OVLS is not installed in ~/.local/share/ovls.\n\n"
            "Manager is running from the installed copy and cannot restore "
            "it after removal. Run it from the package directory in a terminal:\n"
            "bash install.sh install"
        )
        return
    info = installer.detect(str(prefix))
    if info["running_vortex"]:
        launcher.show_message("Vortex is running. Close it and try again.")
        return

    # If a verified GitHub release update is available, offer it through the
    # existing Install / Update action. Refusal/unknown/unavailable keeps the
    # previous self-refresh behavior from the installed copy.
    try:
        available = _available_update_info()
    except Exception:
        available = None

    # Race fix: if startup background check is still running, wait briefly for its
    # result instead of skipping a just-discovered update. No parallel check is
    # started here; on timeout/error we keep the previous self-refresh behavior.
    if available is None:
        try:
            _wait_for_background_update_check()
            available = _available_update_info()
        except Exception:
            available = None

    if available is not None:
        current = available.current_version or updater.read_installed_version() or "unknown"
        latest = available.latest_version or "unknown"
        use_github = launcher.confirm(
            "An OVLS update is available from GitHub Releases.\n\n"
            f"Installed version: {current}\n"
            f"Available version: {latest}\n\n"
            "Download, verify (SHA-256) and install this update now?\n\n"
            "If you decline, the usual component update from the installed copy will run."
        )
        if use_github:
            result, error = _github_release_install(str(prefix))
            if error:
                launcher.show_message(error)
                return
        else:
            try:
                result = _self_refresh_install(str(prefix))
            except (OSError, ValueError) as exc:
                launcher.show_message(f"Installation failed:\n{exc}")
                return
    else:
        try:
            result = _self_refresh_install(str(prefix))
        except (OSError, ValueError) as exc:
            launcher.show_message(f"Installation failed:\n{exc}")
            return

    # A startup check may still be writing its result. Let it finish before
    # clearing the cache, otherwise it can recreate a stale "update_available"
    # entry after this successful install.
    _wait_for_background_update_check(timeout=updater.DEFAULT_TIMEOUT_SECONDS + 2)
    try:
        updater.invalidate_cache()
        cache_warning = None
    except OSError as exc:
        cache_warning = f"Update cache could not be cleared: {exc}"

    shortcuts = result.get("shortcuts") or {}
    gh = result.get("github_update") or {}
    ext_status = result["installation"]["extension"]["status"]
    extension_ok = ext_status in ("installed", "unchanged", "would_install")
    title = "Components updated."
    if not extension_ok:
        title = f"Extension needs attention ({ext_status})."
    elif gh.get("status") == "installed":
        version = gh.get("version") or "new version"
        title = f"OVLS update installed ({version})."

    lines = [
        title,
        "",
        f"Extension: {result['installation']['extension']['status']}",
        f"NXM: {result['nxm_handler']['status']}",
        f"Shortcuts: {shortcuts.get('status', 'unknown')}",
        f"Installed copy: {result['copy']['app']}",
    ]
    notes = _shortcut_notes(shortcuts)
    if notes:
        lines.extend(["", *notes])
    if cache_warning:
        lines.extend(["", cache_warning])
    if extension_ok and shortcuts.get("status") == "ok":
        launcher.show_info("\n".join(lines))
    else:
        launcher.show_message("\n".join(lines))


def action_verify():
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.vortex_exe(prefix).is_file():
        launcher.show_message(f"Vortex.exe not found in prefix:\n{launcher.vortex_exe(prefix)}")
        return
    try:
        result = package.verify(prefix=str(prefix))
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Verification failed:\n{exc}")
        return
    ok = result["status"] == "ok"
    lines = [
        "Verification completed: " + ("OK" if ok else "needs attention"),
        "",
        f"Prefix: {result.get('prefix') or prefix}",
        f"Code copy: {result['copy']['status']}",
        f"Shortcuts: {result['shortcuts']['status']}",
    ]
    installed = result.get("installation")
    if installed:
        lines.extend(
            [
                f"Extension: {installed['extension']['status']}",
                f"Vortex uninstall key: {installed['vortex_registry']['status']}",
                f"Steam library: {'ok' if installed['steam_library']['config_present'] else 'missing'}",
                f"NXM handler: {installed['nxm_handler']['status']}",
            ]
        )
    elif result.get("reason"):
        lines.append(f"Reason: {result['reason']}")
    for key, item in result["shortcuts"]["files"].items():
        if item["status"] != "ok":
            lines.append(f"  {key}: {item['status']} — {item['path']}")
    text = "\n".join(lines)
    if ok:
        launcher.show_info(text)
    else:
        launcher.show_message(text)


def action_uninstall():
    prefix = _require_prefix()
    if not prefix:
        return
    if not _confirm(
        "Remove OVLS components (Extension, NXM, shortcuts)?\n\n"
        "Vortex, mods, settings and prefix will remain unchanged."
    ):
        return
    try:
        result = package.uninstall(prefix=str(prefix))
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Removal failed:\n{exc}")
        return
    if result.get("status") == "blocked":
        launcher.show_message(
            result.get("message")
            or "Removal blocked. Components, shortcuts and settings were kept."
        )
        return False
    shortcuts = result.get("shortcuts") or {}
    launcher.show_info(
        "Components removed.\n\n"
        f"Extension: {result['installation']['extension']['status']}\n"
        f"NXM: {result['installation']['nxm_handler']['status']}\n"
        f"Shortcut files removed: {len(shortcuts.get('removed', []))}\n\n"
        "Vortex and prefix were not changed.\n\n"
        "OVLS Manager will now close: its installed copy has been removed.\n"
        "To reinstall, run from the package directory:\n"
        "bash install.sh install"
    )
    return True


def _restore_prefix(previous_config, previous_prefix):
    try:
        if previous_config:
            package.save_prefix(previous_config)
        else:
            package.remove_config()
    except OSError as exc:
        print(f"OVLS Manager: cannot restore prefix config: {exc}", file=sys.stderr)
    if not previous_prefix:
        return
    try:
        desktop.install_handler(
            str(previous_prefix),
            str(package.app_root()),
            ovls_py=str(package.installed_entry_point()),
        )
    except (OSError, ValueError) as exc:
        print(f"OVLS Manager: cannot restore nxm handler: {exc}", file=sys.stderr)


def action_change_prefix():
    previous_prefix = current_prefix()
    previous_config = package.load_prefix()
    if previous_prefix:
        info = installer.detect(str(previous_prefix))
        if info["running_vortex"]:
            launcher.show_message(
                "Vortex is running. Close it before changing prefix."
            )
            return
    chosen = launcher.choose_directory(start=previous_prefix)
    if not chosen:
        return
    chosen = Path(chosen).expanduser().resolve()
    if not package.is_prefix(chosen):
        launcher.show_message(
            "This is not a valid Vortex prefix. It must contain "
            "pfx/drive_c/users/ and pfx/drive_c/Program Files/Vortex/Vortex.exe:\n"
            f"{chosen}"
        )
        return
    if not launcher.vortex_exe(chosen).is_file():
        launcher.show_message(f"Vortex.exe not found in prefix:\n{launcher.vortex_exe(chosen)}")
        return
    try:
        package.save_prefix(chosen)
        handler = desktop.install_handler(
            str(chosen),
            str(package.app_root()),
            ovls_py=str(package.installed_entry_point()),
        )
        if handler.get("status") == "attention_required":
            raise ValueError("NXM handler is not registered (check xdg-mime)")
    except (OSError, ValueError) as exc:
        _restore_prefix(previous_config, previous_prefix)
        launcher.show_message(f"Prefix was not changed:\n{exc}")
        return
    launcher.show_info(
        "Prefix saved:\n"
        f"{chosen}\n\n"
        "Click Install / Update to install components in the new prefix."
    )


def run(argv=None):
    # Non-blocking: refresh GitHub release cache in background so menu opening
    # does not wait for network; offline/errors keep Manager fully usable.
    if launcher._dialog_tool() is not None:
        _start_background_update_check()

    while True:
        action = choose_action()
        if action is None:
            return 0
        if action == "install":
            action_install()
        elif action == "verify":
            action_verify()
        elif action == "uninstall":
            if action_uninstall():
                return 0
        elif action == "prefix":
            action_change_prefix()
