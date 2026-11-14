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
        arguments = ["--menu", "Выберите действие"]
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
        return prefix
    chosen = launcher.choose_directory()
    if not chosen:
        return None
    if not package.is_prefix(chosen):
        launcher.show_message(f"Это не prefix Proton (нет pfx/drive_c/users):\n{chosen}")
        return None
    try:
        package.save_prefix(chosen)
    except OSError as exc:
        launcher.show_message(f"Не удалось сохранить настройку:\n{exc}")
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
        lines.append("Ярлыки не перезаписаны (файл уже существует и не принадлежит OVLS):")
        lines.extend(f"  {item['path']}" for item in skipped)
    if shortcut_result.get("status") != "ok" and not skipped:
        reason = shortcut_result.get("reason") or "не удалось создать все ярлыки"
        lines.append(f"Ярлыки: {reason}")
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
            return None, f"Не удалось проверить обновление GitHub:\n{exc}"

        if not fresh or getattr(fresh, "status", None) != "update_available":
            return None, (
                "Обновление из GitHub сейчас недоступно. "
                "Проверьте соединение и повторите попытку позже."
            )

        prepared = updater.prepare_release_package(fresh)
        result = package.install(
            package_root=prepared.package_root,
            prefix=str(prefix),
            update=True,
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
        return None, f"Проверка целостности обновления не пройдена (SHA-256):\n{exc}"
    except updater.PackageFormatError as exc:
        return None, f"Пакет обновления повреждён или имеет неверный формат:\n{exc}"
    except updater.NetworkError as exc:
        return None, f"Ошибка сети при загрузке обновления:\n{exc}"
    except updater.ReleaseNotFoundError as exc:
        return None, f"Релиз обновления недоступен:\n{exc}"
    except updater.UpdaterError as exc:
        return None, f"Не удалось подготовить обновление:\n{exc}"
    except (OSError, ValueError) as exc:
        return None, f"Установка обновления не выполнена:\n{exc}"
    except Exception as exc:
        return None, f"Не удалось установить обновление:\n{exc}"
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
        launcher.show_message(f"Vortex.exe не найден в prefix:\n{launcher.vortex_exe(prefix)}")
        return
    if not (package.app_root() / package.MANIFEST_NAME).is_file():
        launcher.show_message(
            "OVLS не установлен в ~/.local/share/ovls.\n\n"
            "Manager работает из установленной копии и после удаления не может "
            "восстановить её сам. Запустите в терминале из каталога пакета:\n"
            "bash install.sh install"
        )
        return
    info = installer.detect(str(prefix))
    if info["running_vortex"]:
        launcher.show_message("Vortex запущен. Закройте его и повторите.")
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
        current = available.current_version or package.read_installed_version() or "неизвестна"
        latest = available.latest_version or "неизвестна"
        use_github = launcher.confirm(
            "Доступно обновление OVLS из GitHub Releases.\n\n"
            f"Установленная версия: {current}\n"
            f"Доступная версия: {latest}\n\n"
            "Скачать, проверить (SHA-256) и установить это обновление сейчас?\n\n"
            "При отказе будет выполнено обычное обновление компонентов из установленной копии."
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
                launcher.show_message(f"Установка не выполнена:\n{exc}")
                return
    else:
        try:
            result = _self_refresh_install(str(prefix))
        except (OSError, ValueError) as exc:
            launcher.show_message(f"Установка не выполнена:\n{exc}")
            return

    shortcuts = result.get("shortcuts") or {}
    gh = result.get("github_update") or {}
    title = "Компоненты обновлены."
    if gh.get("status") == "installed":
        version = gh.get("version") or "новая версия"
        title = f"Обновление OVLS установлено ({version})."

    lines = [
        title,
        "",
        f"Extension: {result['installation']['extension']['status']}",
        f"NXM: {result['nxm_handler']['status']}",
        f"Ярлыки: {shortcuts.get('status', 'unknown')}",
        f"Установленная копия: {result['copy']['app']}",
    ]
    notes = _shortcut_notes(shortcuts)
    if notes:
        lines.extend(["", *notes])
    if shortcuts.get("status") == "ok":
        launcher.show_info("\n".join(lines))
    else:
        launcher.show_message("\n".join(lines))


def action_verify():
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.vortex_exe(prefix).is_file():
        launcher.show_message(f"Vortex.exe не найден в prefix:\n{launcher.vortex_exe(prefix)}")
        return
    try:
        result = package.verify(prefix=str(prefix))
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Проверка не выполнена:\n{exc}")
        return
    ok = result["status"] == "ok"
    lines = [
        "Проверка завершена: " + ("OK" if ok else "требует внимания"),
        "",
        f"Prefix: {result.get('prefix') or prefix}",
        f"Копия кода: {result['copy']['status']}",
        f"Ярлыки: {result['shortcuts']['status']}",
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
        lines.append(f"Причина: {result['reason']}")
    for key, item in result["shortcuts"]["files"].items():
        if item["status"] != "ok":
            lines.append(f"  {key}: {item['status']} — {item['path']}")
    text = "\n".join(lines)
    if ok:
        launcher.show_info(text)
    else:
        launcher.show_message(text)


def action_uninstall():
    prefix = current_prefix()
    if not prefix:
        launcher.show_message("Prefix не настроен. Сначала выберите его через Change prefix.")
        return
    if not _confirm(
        "Удалить компоненты OVLS (Extension, NXM, ярлыки)?\n\n"
        "Vortex, моды, настройки и prefix останутся без изменений."
    ):
        return
    try:
        result = package.uninstall(prefix=str(prefix))
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Удаление не выполнено:\n{exc}")
        return
    if result.get("status") == "blocked":
        launcher.show_message(
            result.get("message")
            or "Удаление заблокировано. Компоненты, ярлыки и настройки сохранены."
        )
        return False
    shortcuts = result.get("shortcuts") or {}
    launcher.show_info(
        "Компоненты удалены.\n\n"
        f"Extension: {result['installation']['extension']['status']}\n"
        f"NXM: {result['installation']['nxm_handler']['status']}\n"
        f"Удалено файлов ярлыков: {len(shortcuts.get('removed', []))}\n\n"
        "Vortex и prefix не изменялись.\n\n"
        "OVLS Manager сейчас закроется: его установленная копия удалена.\n"
        "Для повторной установки запустите из каталога пакета:\n"
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
                "Vortex запущен. Закройте его перед сменой prefix."
            )
            return
    chosen = launcher.choose_directory(start=previous_prefix)
    if not chosen:
        return
    chosen = Path(chosen).expanduser().resolve()
    if not package.is_prefix(chosen):
        launcher.show_message(f"Это не prefix Proton (нет pfx/drive_c/users):\n{chosen}")
        return
    if not launcher.vortex_exe(chosen).is_file():
        launcher.show_message(f"Vortex.exe не найден в prefix:\n{launcher.vortex_exe(chosen)}")
        return
    try:
        package.save_prefix(chosen)
        handler = desktop.install_handler(
            str(chosen),
            str(package.app_root()),
            ovls_py=str(package.installed_entry_point()),
        )
        if handler.get("status") == "attention_required":
            raise ValueError("NXM handler не зарегистрирован (проверьте xdg-mime)")
    except (OSError, ValueError) as exc:
        _restore_prefix(previous_config, previous_prefix)
        launcher.show_message(f"Prefix не изменён:\n{exc}")
        return
    launcher.show_info(
        "Prefix сохранён:\n"
        f"{chosen}\n\n"
        "Нажмите Install / Update, чтобы установить компоненты в новый prefix."
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
