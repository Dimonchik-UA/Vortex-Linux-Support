import os
import configparser
from pathlib import Path


def find_steam_root():
    candidates = [
        os.path.expanduser("~/.local/share/Steam"),
        os.path.expanduser("~/.steam/steam"),
        os.path.expanduser("~/.steam/root"),
        "/home/deck/.local/share/Steam",
        "/home/deck/.steam/steam",
        "/home/deck/.steam/root",
    ]
    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, "steamapps")):
            return path
    return None


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


def find_vortex_install(steam_root):
    for game in discover_games(steam_root):
        # Vortex Steam app id is 1712840 in common setup, but keep generic.
        if game["appid"] == "1712840":
            return os.path.join(game["library"], "steamapps", "common", "Vortex")
    return None