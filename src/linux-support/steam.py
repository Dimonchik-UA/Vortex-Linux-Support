import os
import configparser
from pathlib import Path


def find_steam_root():
    candidates = [
        os.path.expanduser("~/.local/share/Steam"),
        os.path.expanduser("~/.steam/steam"),
        os.path.expanduser("~/.steam/root"),
    ]
    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "steamapps")):
            return path
    return None


VORTEX_EXE_REL = Path("pfx") / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
VORTEX_USERS_REL = Path("pfx") / "drive_c" / "users"


def is_vortex_prefix(path):
    """A Proton prefix that actually contains the official Vortex."""
    path = Path(path).expanduser()
    return (path / VORTEX_USERS_REL).is_dir() and (path / VORTEX_EXE_REL).is_file()


def find_vortex_prefixes(steam_root=None):
    """Vortex prefixes under <steam_root>/steamapps/compatdata/* (read-only)."""
    root = steam_root or find_steam_root()
    if not root:
        return []
    compatdata = Path(root).expanduser() / "steamapps" / "compatdata"
    if not compatdata.is_dir():
        return []
    found = []
    try:
        entries = list(compatdata.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.is_dir() and is_vortex_prefix(entry):
            found.append(entry.resolve())
    return sorted(found)


def selected_proton_name(prefix=None, steam_root=None):
    """Return the Proton tool name Steam selected for this prefix's appid.

    Reads ``config/config.vdf`` (CompatToolMapping) read-only. Returns ``None``
    when the prefix has no appid or no mapping exists.
    """
    if not prefix:
        return None
    parts = Path(prefix).expanduser().resolve().parts
    appid = None
    for index, part in enumerate(parts):
        if part == "compatdata" and index + 1 < len(parts):
            appid = parts[index + 1]
            break
    if not appid:
        return None
    root = steam_root or find_steam_root()
    if not root:
        return None
    try:
        text = (Path(root) / "config" / "config.vdf").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    marker = text.find('"CompatToolMapping"')
    if marker < 0:
        return None
    chunk = text[marker:marker + 20000]
    import re
    mapping = re.search(r'"' + re.escape(appid) + r'"\s*\{([^}]*)\}', chunk)
    if not mapping:
        return None
    name = re.search(r'"name"\s+"([^"]+)"', mapping.group(1))
    return name.group(1) if name else None


def read_library_folders(steam_root):
    vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    libraries = [steam_root]
    if not os.path.exists(vdf_path):
        return libraries
    try:
        with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        # Minimal parser: collect quoted paths.
        import re
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            path = match.group(1).replace("\\\\", "/")
            if os.path.isdir(path) and path not in libraries:
                libraries.append(path)
    except OSError:
        pass
    return libraries


def discover_games(steam_root):
    games = []
    for library in read_library_folders(steam_root):
        steamapps = os.path.join(library, "steamapps")
        if not os.path.isdir(steamapps):
            continue
        for name in os.listdir(steamapps):
            if not name.startswith("appmanifest_") or not name.endswith(".acf"):
                continue
            appid = name[len("appmanifest_"):-len(".acf")]
            games.append({"appid": appid, "library": library})
    return games
