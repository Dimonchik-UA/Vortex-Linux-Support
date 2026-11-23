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
    ("help", "Help"),
    ("uninstall", "Uninstall"),
]


def _zenity(tool):
    return os.path.basename(tool).startswith("zenity")


def choose_prefix(candidates):
    """Ask the user to pick one of several discovered Vortex prefixes."""
    tool = launcher._dialog_tool()
    if tool is None:
        return None
    question = "Several Vortex prefixes were found. Choose one:"
    if _zenity(tool):
        arguments = [
            "--list",
            f"--title={TITLE}",
            "--column=Prefix",
            f"--text={question}",
            "--print-column=1",
        ]
        arguments.extend(str(path) for path in candidates)
    else:
        arguments = ["--menu", question]
        for path in candidates:
            arguments.extend([str(path), str(path)])
    result = launcher._run_dialog(tool, arguments)
    if result is None or result.returncode != 0:
        return None
    chosen = result.stdout.strip()
    return Path(chosen).expanduser().resolve() if chosen else None


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
        try:
            package.save_prefix(prefix)
        except OSError as exc:
            launcher.show_message(f"Could not save the prefix setting:\n{exc}")
            return None
        return prefix
    candidates = package.detect_prefix_candidates()
    if len(candidates) > 1:
        prefix = choose_prefix(candidates)
        if not prefix:
            return None
        try:
            package.save_prefix(prefix)
        except OSError as exc:
            launcher.show_message(f"Could not save the prefix setting:\n{exc}")
            return None
        return prefix
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


def action_install():
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.vortex_exe(prefix).is_file():
        launcher.show_message(f"Vortex.exe not found in prefix:\n{launcher.vortex_exe(prefix)}")
        return
    info = installer.detect(str(prefix))
    if info["running_vortex"]:
        launcher.show_message("Vortex is running. Close it and try again.")
        return
    update = _installed()
    try:
        result = package.install(prefix=str(prefix), update=update)
    except (OSError, ValueError) as exc:
        launcher.show_message(f"Installation failed:\n{exc}")
        return
    shortcuts = result.get("shortcuts") or {}
    lines = [
        "Update completed." if update else "Installation completed.",
        "",
        f"Prefix: {result.get('prefix') or prefix}",
        f"Installed copy: {result['copy']['status']}",
        f"Extension: {result['installation']['extension']['status']}",
        f"NXM: {result['nxm_handler']['status']}",
        f"Shortcuts: {shortcuts.get('status', 'unknown')}",
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
    if not _installed():
        launcher.show_message("OVLS is not installed: nothing to remove.")
        return
    prefix = _require_prefix()
    if not prefix:
        return
    if not launcher.confirm(
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
        return
    shortcuts = result.get("shortcuts") or {}
    launcher.show_info(
        "Components removed.\n\n"
        f"Extension: {result['installation']['extension']['status']}\n"
        f"NXM: {result['installation']['nxm_handler']['status']}\n"
        f"Shortcut files removed: {len(shortcuts.get('removed', []))}\n\n"
        "Vortex and prefix were not changed.\n\n"
        "The installed OVLS copy was removed. You can leave this window open "
        "to install the components again."
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
