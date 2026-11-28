"""Desktop-mode launcher for the official Vortex.

Owns exactly three things:

  * ``$XDG_CONFIG_HOME/ovls/config.json`` — the remembered Proton prefix;
  * the prefix search order, shared with the main OVLS component
    (configured path, ``~/Windows/prefix``, generic Steam ``compatdata/*``);
  * a detached launch with a log at ``$XDG_STATE_HOME/ovls/launch.log``.

Dialog windows use ``kdialog`` and fall back to ``zenity``; neither is a new
dependency on SteamOS. ``OVLS_NO_GUI=1`` disables dialogs (used by tests and by
terminal runs). Vortex files, the prefix and Vortex settings are never changed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import desktop
import package
import proton
import steam
import tempfiles

CONFIG_FILE = "config.json"
VORTEX_EXE_REL = Path("pfx") / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
TITLE = "OVLS"


def config_path():
    return desktop.config_home() / "ovls" / CONFIG_FILE


def load_config():
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tempfiles.cleanup_orphaned_files(path.parent, f".{path.name}-")
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-{os.getpid()}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def is_prefix(path):
    return steam.is_vortex_prefix(path)


def vortex_exe(prefix):
    return Path(prefix).expanduser() / VORTEX_EXE_REL


def find_prefix(config=None):
    """Use the main OVLS detection; return None when the choice is ambiguous."""
    return package.detect_prefix(explicit=(config or {}).get("prefix"))


def _gui_disabled():
    return os.environ.get("OVLS_NO_GUI", "") not in ("", "0")


def _dialog_tool():
    if _gui_disabled():
        return None
    explicit = os.environ.get("OVLS_DIALOG")
    if explicit:
        return explicit
    for name in ("kdialog", "zenity"):
        if shutil.which(name):
            return name
    return None


def _run_dialog(tool, arguments):
    try:
        return subprocess.run(
            [tool, *arguments], capture_output=True, text=True, timeout=300, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def show_message(message, kind="error"):
    tool = _dialog_tool()
    if tool is None:
        print(message, file=sys.stderr)
        return
    if os.path.basename(tool).startswith("zenity"):
        _run_dialog(tool, [f"--{kind}", f"--title={TITLE}", f"--text={message}"])
    else:
        _run_dialog(tool, ["--title", TITLE, f"--{kind}", message])


def show_info(message):
    tool = _dialog_tool()
    if tool is None:
        print(message)
        return
    if os.path.basename(tool).startswith("zenity"):
        _run_dialog(tool, ["--info", f"--title={TITLE}", f"--text={message}"])
    else:
        _run_dialog(tool, ["--title", TITLE, "--msgbox", message])


def show_text_file(path):
    """Show a text file in the dialog tool's read-only viewer."""
    source = Path(path)
    if not source.is_file():
        show_message(f"File not found:\n{source}")
        return
    tool = _dialog_tool()
    if tool is None:
        print(source.read_text(encoding="utf-8"))
        return
    if os.path.basename(tool).startswith("zenity"):
        _run_dialog(
            tool,
            [
                "--text-info",
                f"--title={TITLE}",
                f"--filename={source}",
                "--width=900",
                "--height=700",
            ],
        )
    else:
        _run_dialog(tool, ["--title", TITLE, "--textbox", str(source), "900", "700"])


def confirm(message):
    tool = _dialog_tool()
    if tool is None:
        print(message, file=sys.stderr)
        return True
    if os.path.basename(tool).startswith("zenity"):
        result = _run_dialog(tool, ["--question", f"--title={TITLE}", f"--text={message}"])
    else:
        result = _run_dialog(tool, ["--title", TITLE, "--warningyesno", message])
    return bool(result is not None and result.returncode == 0)


def choose_directory(
    start=None,
    prompt="Select the Vortex prefix directory (with pfx/drive_c/users/ and Vortex.exe)",
):
    tool = _dialog_tool()
    if tool is None:
        return None
    start_text = str(start or Path.home())
    if os.path.basename(tool).startswith("zenity"):
        result = _run_dialog(
            tool,
            ["--file-selection", "--directory", f"--title={prompt}", f"--filename={start_text}/"],
        )
    else:
        result = _run_dialog(tool, ["--getexistingdirectory", start_text, "--title", prompt])
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def _select_proton(prefix=None):
    selected = proton.selected_proton(prefix)
    if selected:
        return selected
    try:
        path = proton.default_proton()
    except ValueError as exc:
        show_message(f"OVLS: {exc}")
        return None
    # Recommendation only: never refuse to launch a user-selected Proton.
    if not proton.is_recommended_proton(os.path.dirname(path)):
        show_info(
            "For best compatibility, DW-Proton or Proton-GE is recommended. "
            "You can continue with your current Proton version."
        )
    return path


def run(argv=None):
    """Launcher entry point: return an exit code, never raise on user input."""
    config = load_config()
    prefix = find_prefix(config)
    if prefix is None:
        prefix = choose_directory()
    if prefix is None:
        show_message(
            "Vortex not found.\n\n"
            "Could not determine a single Vortex prefix. Specify a directory with "
            "pfx/drive_c/users/ and pfx/drive_c/Program Files/Vortex/Vortex.exe."
        )
        return 2

    prefix = Path(prefix).expanduser().resolve()
    if not is_prefix(prefix):
        show_message(f"This is not a Vortex prefix (pfx/drive_c/users/ and Vortex.exe are required):\n{prefix}")
        return 2

    vortex = vortex_exe(prefix)
    if not vortex.is_file():
        show_message(f"Vortex.exe not found in prefix:\n{vortex}")
        return 2

    if str(prefix) != config.get("prefix"):
        try:
            config["prefix"] = str(prefix)
            save_config(config)
        except OSError as exc:
            print(f"OVLS: cannot save config: {exc}", file=sys.stderr)

    proton_path = _select_proton(prefix)
    if not proton_path:
        return 1

    # Default Electron args for better Wine performance; can be overridden via OVLS_VORTEX_ARGS
    if os.environ.get("OVLS_VORTEX_ARGS"):
        extra_vortex_args = os.environ["OVLS_VORTEX_ARGS"].split()
    else:
        extra_vortex_args = ["--disable-gpu"]

    try:
        result = proton.spawn_vortex(
            proton_path,
            str(vortex),
            str(prefix),
            log_path=str(desktop.state_home() / "ovls" / "launch.log"),
            vortex_args=extra_vortex_args,
        )
    except (OSError, ValueError) as exc:
        show_message(f"Could not start Vortex:\n{exc}")
        return 2

    print(
        json.dumps(
            {
                "status": "launched",
                "prefix": str(prefix),
                "vortex": str(vortex),
                "proton": proton_path,
                **result,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0
