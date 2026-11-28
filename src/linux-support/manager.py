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

import json
import ntpath
import os
import pwd
import re
import subprocess
import sys
import threading
from pathlib import Path

import desktop
import extension_installer
import installer
import launcher
import package
import registry
import setup_ui
import state_v2
import updater

TITLE = "OVLS"

MAIN_ACTIONS = [
    ("install", "🔄 Install / Update"),
    ("prefix", "📂 Select Vortex prefix (pfx)"),
    ("verify", "🩺 Verify"),
    ("paths", "📁 Paths & Folders"),
    ("uninstall", "🗑️ Uninstall"),
]

BACK_ACTION = ("back", "⬅️ Back")

PATHS_ACTIONS = [
    ("open_prefix", "↗️ Open Vortex prefix folder"),
    ("mods", "🎮 Games & Mods folders"),
    ("downloads", "📥 Mod Downloads Folder"),
    BACK_ACTION,
]

GAME_ACTIONS = [
    ("open_mods", "🧩 Open mods staging folder"),
    ("open_game", "📁 Open game folder"),
    BACK_ACTION,
]

# Background GitHub update check state (startup is non-blocking; Install/Update may
# wait briefly if a check is already in progress to avoid missing an update).
_UPDATE_CHECK_WAIT_TIMEOUT = 4.0
_update_check_lock = threading.Lock()
_update_check_inflight = False
_update_check_done = threading.Event()
_update_check_done.set()


def _zenity(tool):
    return setup_ui._zenity(tool)


def choose_action(items=None, subtitle=None):
    """Show a menu; ``subtitle`` is a fixed, non-selectable section label."""
    if items is None:
        items = MAIN_ACTIONS
    tool = launcher._dialog_tool()
    if tool is None:
        return None
    if _zenity(tool):
        arguments = ["--list", f"--title={TITLE}"]
        if subtitle:
            arguments.append(f"--text={subtitle}")
        arguments.extend(
            [
                "--column=ID",
                "--column=Action",
                "--hide-column=1",
                "--print-column=1",
            ]
        )
        for key, label in items:
            arguments.extend([key, label])
    else:
        arguments = ["--title", TITLE, "--menu", subtitle or ""]
        for key, label in items:
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
    return setup_ui._shortcut_notes(shortcut_result)


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
        f"OVLS version: {updater.current_local_version() or 'unknown'}",
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


def action_open_prefix_folder():
    prefix = current_prefix()
    if not prefix:
        launcher.show_message("No Vortex prefix is selected. Select a prefix first.")
        return
    pfx = Path(prefix) / "pfx"
    if not pfx.is_dir():
        launcher.show_message(f"Vortex prefix folder not found:\n{pfx}")
        return
    try:
        subprocess.Popen(["xdg-open", str(pfx)])
    except OSError as exc:
        launcher.show_message(f"Could not open the Vortex prefix folder:\n{pfx}\n{exc}")


def _reader_prefix():
    prefix = current_prefix()
    if not prefix:
        launcher.show_message("No unique Vortex prefix is selected. Select a prefix first.")
        return None
    return prefix


def _read_state_values(prefix):
    try:
        return state_v2.read_state(prefix)
    except (state_v2.StateV2Error, OSError, ValueError) as exc:
        launcher.show_message(f"Could not read Vortex state.v2:\n{exc}")
        return None


def _setting_text(values, key):
    raw = values.get(key)
    if not isinstance(raw, bytes):
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, str) and value.strip() else None


def _discovered_game_ids(values):
    marker = b"settings###gameMode###discovered###"
    games = set()
    for key in values:
        if not isinstance(key, bytes) or not key.startswith(marker):
            continue
        if not key.endswith(b"###path"):
            continue
        parts = key.split(b"###")
        if len(parts) != 5 or not parts[3]:
            continue
        try:
            games.add(parts[3].decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return sorted(games)


def _normalise_game_name(value):
    if not isinstance(value, str):
        return None
    name = value.replace("\\t", " ").replace("\t", " ").strip()
    return name or None


def _js_literal(token, constants):
    token = token.strip()
    if token in constants:
        return constants[token]
    match = re.fullmatch(r"(['\"`])((?:\\.|(?!\1).)*)\1", token, re.DOTALL)
    if not match:
        return None
    body = match.group(2)
    if match.group(1) == "`" and "${" in body:
        return None
    escapes = {"t": "\t", "n": "\n", "r": "\r"}
    return re.sub(r"\\(.)", lambda found: escapes.get(found.group(1), found.group(1)), body)


def _entry_constants(text):
    """Simple local string constants, including comma-separated declarations."""
    constants = {}
    declarator = re.compile(
        r"(?:\b(?:const|let|var)\s+|,)\s*"
        r"([A-Za-z_$][\w$]*)\s*=\s*"
        r"(`(?:\\.|[^`])*`|'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")"
    )
    for match in declarator.finditer(text):
        name = match.group(1)
        value = _js_literal(match.group(2), {})
        if value is None:
            continue
        if name in constants and constants[name] != value:
            constants.pop(name)
        else:
            constants[name] = value
    return constants


def _object_value(body, field, constants):
    match = re.search(
        r"\b"
        + re.escape(field)
        + r"\s*:\s*([A-Za-z_$][\w$]*|'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"|`(?:\\.|[^`])*`)",
        body,
    )
    return _js_literal(match.group(1), constants) if match else None


def _registered_game_names(text):
    """Names from ``registerGame``/``registerGameStub`` in one entry script."""
    constants = _entry_constants(text)
    found = {}
    for match in re.finditer(r"\b(?:registerGame|registerGameStub)\s*\(\s*\{", text):
        start = match.end() - 1
        depth = 0
        quote = None
        escaped = False
        end = None
        for index in range(start, len(text)):
            char = text[index]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"', "`"):
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            continue
        body = text[start:end]
        game_id = _object_value(body, "id", constants)
        name = _normalise_game_name(_object_value(body, "name", constants))
        if game_id and name:
            found.setdefault(game_id, set()).add(name)
    return found


def _extension_directories(prefix, values):
    marker = b"app###extensions###"
    fields = {}
    for key in values:
        if not isinstance(key, bytes) or not key.startswith(marker):
            continue
        parts = key.split(b"###")
        if len(parts) == 4 and parts[2]:
            fields.setdefault(parts[2], set()).add(parts[3])
    directories = []
    for ext_id in sorted(fields):
        if _setting_text(values, marker + ext_id + b"###type") != "game":
            continue
        path = _setting_text(values, marker + ext_id + b"###path")
        host = _windows_to_host(path, prefix) if path else None
        if host is not None and host.is_dir():
            directories.append(host)
    bundled = (
        Path(prefix) / "pfx" / "drive_c" / "Program Files" / "Vortex"
        / "resources" / "app.asar.unpacked" / "bundledPlugins"
    )
    try:
        directories.extend(path for path in sorted(bundled.glob("game-*")) if path.is_dir())
    except OSError:
        pass
    return directories


def _entry_script(directory):
    """Entry script named by info.json.main only; info.json.name is not a game name."""
    info = directory / "info.json"
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        data = {}
    main = data.get("main") if isinstance(data, dict) else None
    candidates = []
    if isinstance(main, str) and main.strip():
        candidates.append(main.strip().lstrip("./"))
    candidates.extend(("index.js", "index.cjs"))
    for name in candidates:
        script = directory / name
        try:
            if script.is_file() and _under_host(script.resolve(), directory.resolve()):
                return script
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _extension_game_names(prefix, values, needed):
    found = {}
    for directory in _extension_directories(prefix, values):
        script = _entry_script(directory)
        if script is None:
            continue
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for game_id, names in _registered_game_names(text).items():
            if game_id in needed:
                found.setdefault(game_id, set()).update(names)
    return found


def _game_names(values, prefix):
    names = {}
    missing = set()
    for game_id in _discovered_game_ids(values):
        key = f"settings###gameMode###discovered###{game_id}###name".encode("utf-8")
        name = _normalise_game_name(_setting_text(values, key))
        if name:
            names[game_id] = name
        else:
            missing.add(game_id)
    if missing:
        for game_id, candidates in _extension_game_names(prefix, values, missing).items():
            if len(candidates) == 1:
                names[game_id] = next(iter(candidates))
    return names


def _game_labels(values, prefix):
    names = _game_names(values, prefix)
    labels = {}
    anonymous = 0
    for game_id in _discovered_game_ids(values):
        name = names.get(game_id)
        if not name:
            anonymous += 1
            name = f"Игра без названия ({anonymous})"
        labels[game_id] = name
    return labels


def _game_menu_items(labels):
    items = [("game:" + game_id, f"🎮 {name}") for game_id, name in labels.items()]
    return items + [BACK_ACTION]


def _vortex_username():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return os.environ.get("USER", "")


def _under_host(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_windows_pattern(value, userdata_wine):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) >= 3 and text[1] == ":" and text[2] in ("\\", "/"):
        return text
    if not userdata_wine:
        return None
    return ntpath.join(userdata_wine, text)


def _windows_to_host(value, prefix):
    if not isinstance(value, str):
        return None
    text = value.strip().replace("/", "\\")
    if len(text) < 3 or text[1] != ":" or text[2] != "\\":
        return None
    drive = text[0].upper()
    rest = text[3:].replace("\\", "/").strip("/")
    try:
        if drive == "Z":
            z_root = extension_installer._z_root(prefix)
            if z_root is None:
                return None
            host = extension_installer._host_path_from_wine(text, z_root)
            if host is None:
                return None
            z_root = z_root.resolve(strict=False)
            host_path = Path(host).resolve(strict=False)
            return host_path if _under_host(host_path, z_root) else None
        if drive == "C":
            root = (Path(prefix) / "pfx" / "drive_c").resolve(strict=False)
            host_path = (root / rest).resolve(strict=False)
            return host_path if _under_host(host_path, root) else None
        link = Path(prefix) / "pfx" / "dosdevices" / f"{drive.lower()}:"
        if not link.is_symlink():
            return None
        target = Path(os.readlink(link))
        if not target.is_absolute():
            target = link.parent / target
        root = target.resolve(strict=False)
        host_path = (root / rest).resolve(strict=False)
        return host_path if _under_host(host_path, root) else None
    except (OSError, RuntimeError, ValueError):
        return None


def _userdata_paths(prefix):
    vortex_dir, _plugins_dir = extension_installer.resolve_plugin_directory(prefix)
    userdata = Path(vortex_dir)
    wine = installer._wine_path(prefix, userdata) or registry._windows_path(prefix, userdata)
    return userdata, wine


def _substitute_download_pattern(pattern, userdata_wine, username):
    result = pattern
    for token, value in (("{USERDATA}", userdata_wine or ""), ("{USERNAME}", username)):
        result = re.sub(re.escape(token), lambda _match, _value=value: _value, result, flags=re.IGNORECASE)
    return result


def _downloads_host_path(prefix, pattern=None):
    try:
        userdata, userdata_wine = _userdata_paths(prefix)
    except (OSError, RuntimeError, ValueError):
        return None, False
    if pattern is None:
        return userdata / "downloads", True
    if not userdata_wine and re.search(r"\{USERDATA\}", pattern, re.IGNORECASE):
        return None, False
    filled = _substitute_download_pattern(pattern, userdata_wine, _vortex_username())
    absolute = _resolve_windows_pattern(filled, userdata_wine)
    host = _windows_to_host(absolute, prefix) if absolute else None
    if host is None:
        return None, False
    return host, False


def action_downloads_folder():
    prefix = _reader_prefix()
    if not prefix:
        return
    values = _read_state_values(prefix)
    if values is None:
        return
    key = b"settings###downloads###path"
    raw = values.get(key) if isinstance(values, dict) else None
    pattern = _setting_text(values, key)
    invalid = (
        "Vortex mod downloads path is invalid or cannot be converted; no folder was opened."
    )
    if raw is not None and pattern is None:
        launcher.show_message(invalid)
        return
    host, _is_default = _downloads_host_path(prefix, pattern)
    if host is None:
        launcher.show_message(invalid)
        return
    _open_folder(
        host,
        f"Vortex mod downloads folder does not exist:\n{host}\nNo folder was opened.",
        f"Could not open the mod downloads folder:\n{host}",
    )


def _open_folder(path, missing_message, error_message):
    if path is None or not path.is_dir():
        launcher.show_message(missing_message)
        return
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        launcher.show_message(f"{error_message}\n{path}\n{exc}")


def _game_setting_host(values, key, prefix):
    value = _setting_text(values, key.encode("utf-8"))
    return _windows_to_host(value, prefix) if value else None


def _safe_game_component(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value not in (".", "..")
        and not any(char in value for char in ("\\", "/", ":", "\x00"))
    )


def _standard_staging_host(values, prefix, game_id):
    """Return Vortex's standard staging folder only when its tag is valid."""
    if not _safe_game_component(game_id):
        return None, "The discovered game id is not a valid folder name."
    try:
        userdata, _wine = _userdata_paths(prefix)
        root = userdata.resolve(strict=False)
        candidate = (root / game_id / "mods").resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None, "Vortex userData folder cannot be resolved for the selected prefix."
    if not _under_host(candidate, root):
        return None, f"Standard staging path leaves Vortex userData:\n{candidate}"
    if not candidate.is_dir():
        return None, f"Vortex standard staging folder does not exist:\n{candidate}"
    tag = candidate / "__vortex_staging_folder"
    if tag.is_symlink() or not tag.is_file():
        return None, f"Vortex staging marker is not a regular file:\n{tag}"
    try:
        marker = json.loads(tag.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None, f"Vortex staging marker is damaged:\n{tag}"
    instance = _setting_text(values, b"app###instanceId")
    if not isinstance(marker, dict):
        return None, "Vortex staging marker has an invalid structure."
    if not isinstance(marker.get("game"), str) or not isinstance(marker.get("instance"), str):
        return None, "Vortex staging marker is missing game or instance."
    if marker["game"] != game_id:
        return None, f"Vortex staging marker game does not match {game_id}."
    if not instance or marker["instance"] != instance:
        return None, "Vortex staging marker instance does not match this Vortex state."
    return candidate, None


def _open_game_subfolder(prefix, values, game_id, action, label):
    if action == "open_mods":
        key = f"settings###mods###installPath###{game_id}"
        if key.encode("utf-8") in values:
            host = _game_setting_host(values, key, prefix)
            if host is None:
                launcher.show_message(
                    f"No valid user staging path is configured for {label}.\n"
                    "The Vortex default is not used."
                )
                return
            _open_folder(
                host,
                f"Staging folder not found for {label}:\n{host}\nThe Vortex default is not used.",
                f"Could not open the staging folder for {label}:",
            )
            return
        host, reason = _standard_staging_host(values, prefix, game_id)
        if host is None:
            launcher.show_message(
                f"Could not verify Vortex's standard staging folder for {label}.\n"
                f"{reason}\nNo folder was opened."
            )
            return
        _open_folder(
            host,
            f"Vortex standard staging folder not found for {label}:\n{host}",
            f"Could not open the staging folder for {label}:",
        )
        return
    host = _game_setting_host(
        values, f"settings###gameMode###discovered###{game_id}###path", prefix
    )
    if host is None:
        launcher.show_message(f"No valid game folder path is configured for {label}.")
        return
    _open_folder(
        host,
        f"Game folder not found for {label}:\n{host}",
        f"Could not open the game folder for {label}:",
    )


def run(argv=None):
    # Non-blocking: refresh GitHub release cache in background so menu opening
    # does not wait for network; offline/errors keep Manager fully usable.
    if launcher._dialog_tool() is not None:
        _start_background_update_check()

    level = "main"
    game_items = list(GAME_ACTIONS)
    game_labels = {}
    game_values = {}
    game_prefix = None
    selected_game = None
    while True:
        if level == "main":
            items = MAIN_ACTIONS
        elif level == "paths":
            items = PATHS_ACTIONS
        elif level == "games":
            items = game_items
        else:
            items = GAME_ACTIONS

        if level == "paths":
            subtitle = "Paths & Folders"
        elif level == "games":
            subtitle = "Games & Mods folders"
        elif level == "game":
            subtitle = game_labels.get(selected_game, "Игра")
        else:
            subtitle = None
        action = choose_action(items, subtitle=subtitle)
        if action is None or action == "back":
            if level == "main":
                return 0
            if level == "paths":
                level = "main"
            elif level == "games":
                level = "paths"
            else:
                level = "games"
                selected_game = None
            continue

        if level == "main":
            if action == "install":
                action_install()
            elif action == "prefix":
                action_change_prefix()
            elif action == "verify":
                action_verify()
            elif action == "paths":
                level = "paths"
            elif action == "uninstall":
                if action_uninstall():
                    return 0
        elif level == "paths":
            if action == "open_prefix":
                action_open_prefix_folder()
            elif action == "mods":
                prefix = _reader_prefix()
                if not prefix:
                    continue
                values = _read_state_values(prefix)
                if values is None:
                    continue
                games = _discovered_game_ids(values)
                if not games:
                    launcher.show_message("Обнаруженные игры не найдены")
                    continue
                game_labels = _game_labels(values, prefix)
                game_items = _game_menu_items(game_labels)
                game_prefix = prefix
                game_values = values
                level = "games"
            elif action == "downloads":
                action_downloads_folder()
        elif level == "games":
            if action.startswith("game:"):
                selected_game = action.split(":", 1)[1]
                level = "game"
        elif action in ("open_mods", "open_game"):
            _open_game_subfolder(
                game_prefix,
                game_values,
                selected_game,
                action,
                game_labels.get(selected_game, "игра"),
            )
