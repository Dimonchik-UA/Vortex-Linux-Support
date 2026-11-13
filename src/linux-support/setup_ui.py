"""Autonomous OVLS installer for Desktop Mode.

Started by ``bash install.sh`` without arguments from the unpacked package. It
is a thin ``kdialog`` menu (``zenity`` fallback) over the same ``package`` API
used by the CLI and by OVLS Manager, so there is no second copy of the
installer logic and no second manifest:

  * Install / Update — ``package.install`` from this package; when OVLS is
    already installed the same call is made with ``update=True``, which copies
    the new package tree over the installed copy;
  * Verify           — ``package.verify`` (copy, prefix integration, shortcuts);
  * Help             — shows the packaged ``README.md`` in a text viewer;
  * Uninstall        — ``package.uninstall``; a blocked removal keeps everything.

The prefix is taken from the OVLS configuration or the known host locations; if
none is found the user picks a directory. Changing the prefix stays in OVLS
Manager. Vortex, the prefix contents, games, mods and user settings are never
modified by this window.
"""

import os
import sys
from pathlib import Path

import installer
import launcher
import layout
import package

TITLE = "OVLS Installer"

ACTIONS = [
    ("install", "Install / Update"),
    ("verify", "Verify"),
    ("help", "Справка"),
    ("uninstall", "Uninstall"),
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
        arguments = ["--menu", "OVLS Linux Support"]
        for key, label in ACTIONS:
            arguments.extend([key, label])
    result = launcher._run_dialog(tool, arguments)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def _installed():
    return (package.app_root() / package.MANIFEST_NAME).is_file()


def readme_path():
    """README next to this package (same relative place in the installed copy)."""
    return layout.ROOT / package.README_NAME


def action_help():
    launcher.show_text_file(readme_path())


def _require_prefix():
    prefix = package.detect_prefix()
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


def action_install():
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.vortex_exe(prefix).is_file():
        launcher.show_message(f"Vortex.exe не найден в prefix:\n{launcher.vortex_exe(prefix)}")
        return
    info = installer.detect(str(prefix))
    if info["running_vortex"]:
        launcher.show_message("Vortex запущен. Закройте его и повторите.")
        return
    update = _installed()
    try:
        result = package.install(prefix=str(prefix), update=update)
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Установка не выполнена:\n{exc}")
        return
    shortcuts = result.get("shortcuts") or {}
    lines = [
        "Обновление завершено." if update else "Установка завершена.",
        "",
        f"Prefix: {result.get('prefix') or prefix}",
        f"Установленная копия: {result['copy']['status']}",
        f"Extension: {result['installation']['extension']['status']}",
        f"NXM: {result['nxm_handler']['status']}",
        f"Ярлыки: {shortcuts.get('status', 'unknown')}",
    ]
    notes = _shortcut_notes(shortcuts)
    if notes:
        lines.extend(["", *notes])
    text = "\n".join(lines)
    if result.get("status") == "ok" and shortcuts.get("status") == "ok":
        launcher.show_info(text)
    else:
        launcher.show_message(text)


def action_verify():
    prefix = _require_prefix()
    if not prefix:
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
    if not _installed():
        launcher.show_message("OVLS не установлен: удалять нечего.")
        return
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.confirm(
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
        return
    shortcuts = result.get("shortcuts") or {}
    launcher.show_info(
        "Компоненты удалены.\n\n"
        f"Extension: {result['installation']['extension']['status']}\n"
        f"NXM: {result['installation']['nxm_handler']['status']}\n"
        f"Удалено файлов ярлыков: {len(shortcuts.get('removed', []))}\n\n"
        "Vortex и prefix не изменялись.\n\n"
        "Установленная копия OVLS удалена. Это окно можно оставить открытым, "
        "чтобы установить компоненты заново."
    )


def run(argv=None):
    if launcher._dialog_tool() is None:
        print(
            "OVLS Installer: no dialog tool available (kdialog/zenity).",
            file=sys.stderr,
        )
        print("Use: bash install.sh install|update|verify|uninstall", file=sys.stderr)
        return 2
    while True:
        action = choose_action()
        if action is None:
            return 0
        if action == "install":
            action_install()
        elif action == "verify":
            action_verify()
        elif action == "help":
            action_help()
        elif action == "uninstall":
            action_uninstall()
