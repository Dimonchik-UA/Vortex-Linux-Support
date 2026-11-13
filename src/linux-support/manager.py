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
from pathlib import Path

import desktop
import installer
import launcher
import package

TITLE = "OVLS Manager"

ACTIONS = [
    ("install", "Install / Update"),
    ("verify", "Verify"),
    ("uninstall", "Uninstall"),
    ("prefix", "Change prefix"),
]


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
    try:
        result = package.install(prefix=str(prefix), update=True)
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Установка не выполнена:\n{exc}")
        return
    shortcuts = result.get("shortcuts") or {}
    lines = [
        "Компоненты обновлены.",
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
