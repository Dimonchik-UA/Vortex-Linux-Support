"""Desktop-mode launcher for the official Vortex.

Owns exactly three things:

  * ``$XDG_CONFIG_HOME/ovls/config.json`` — the remembered Proton prefix;
  * the prefix search order (configured path, ``~/Windows/prefix``,
    ``~/Windows/prefix``, Steam ``compatdata/1712840``);
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
import proton
import steam

CONFIG_FILE = "config.json"
VORTEX_EXE_REL = Path("pfx") / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
STEAM_COMPATDATA_APPID = "1712840"
TITLE = "Vortex"


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
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
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
    return (Path(path).expanduser() / "pfx" / "drive_c" / "users").is_dir()


def vortex_exe(prefix):
    return Path(prefix).expanduser() / VORTEX_EXE_REL


def known_prefixes():
    yield Path.home() / "Windows" / "prefix"
    steam_root = steam.find_steam_root()
    if steam_root:
        yield Path(steam_root) / "steamapps" / "compatdata" / STEAM_COMPATDATA_APPID


def find_prefix(config=None):
    configured = (config or {}).get("prefix")
    if configured:
        path = Path(configured).expanduser()
        if is_prefix(path):
            return path
    for candidate in known_prefixes():
        if is_prefix(candidate):
            return candidate
    return None


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
        _run_dialog(tool, [f"--{kind}", message])


def show_info(message):
    tool = _dialog_tool()
    if tool is None:
        print(message)
        return
    if os.path.basename(tool).startswith("zenity"):
        _run_dialog(tool, ["--info", f"--title={TITLE}", f"--text={message}"])
    else:
        _run_dialog(tool, ["--msgbox", message])


def show_text_file(path):
    """Show a text file in the dialog tool's read-only viewer."""
    source = Path(path)
    if not source.is_file():
        show_message(f"Файл не найден:\n{source}")
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
        _run_dialog(tool, ["--textbox", str(source), "900", "700"])


def confirm(message):
    tool = _dialog_tool()
    if tool is None:
        print(message, file=sys.stderr)
        return True
    if os.path.basename(tool).startswith("zenity"):
        result = _run_dialog(tool, ["--question", f"--title={TITLE}", f"--text={message}"])
    else:
        result = _run_dialog(tool, ["--warningyesno", message])
    return bool(result is not None and result.returncode == 0)


def choose_directory(start=None, prompt="Выберите каталог prefix Vortex (папка с pfx/)"):
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


def _select_proton():
    try:
        path = proton.default_proton()
    except ValueError as exc:
        show_message(f"OVLS: {exc}")
        return None
    directory = os.path.dirname(path)
    if not proton.is_ge_proton(directory):
        proceed = confirm(
            "Proton-GE не найден.\n\n"
            "Без него Vortex работает под Proton 11.0, где возможна белая полоса "
            "сверху и смещение кликов.\n\n"
            f"Запустить с {os.path.basename(directory)}?"
        )
        if not proceed:
            return None
    return path


def run(argv=None):
    """Launcher entry point: return an exit code, never raise on user input."""
    config = load_config()
    prefix = find_prefix(config)
    if prefix is None:
        prefix = choose_directory()
    if prefix is None:
        show_message(
            "Vortex не найден.\n\n"
            "Не удалось определить prefix Vortex. Ожидается каталог вида "
            "~/Windows/prefix (внутри pfx/)."
        )
        return 2

    prefix = Path(prefix).expanduser().resolve()
    if not is_prefix(prefix):
        show_message(f"Это не prefix Proton (нет pfx/drive_c/users):\n{prefix}")
        return 2

    vortex = vortex_exe(prefix)
    if not vortex.is_file():
        show_message(f"Vortex.exe не найден в prefix:\n{vortex}")
        return 2

    if str(prefix) != config.get("prefix"):
        try:
            config["prefix"] = str(prefix)
            save_config(config)
        except OSError as exc:
            print(f"OVLS: cannot save config: {exc}", file=sys.stderr)

    proton_path = _select_proton()
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
        show_message(f"Не удалось запустить Vortex:\n{exc}")
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
