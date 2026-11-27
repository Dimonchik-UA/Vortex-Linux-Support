import os
import platform


def collect_system_info():
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cwd": os.getcwd(),
    }


def collect_all(steam_root=None, prefix_path=None):
    import steam
    import proton
    import filesystem
    import registry

    if steam_root is None:
        steam_root = steam.find_steam_root()

    result = {
        "system": collect_system_info(),
        "steam_root": steam_root,
        "proton_versions": proton.find_proton_versions() if steam_root else [],
        "prefix": proton.inspect_prefix(prefix_path) if prefix_path else None,
    }

    if steam_root:
        result["games_count"] = len(steam.discover_games(steam_root))
        install = registry.find_vortex_install(prefix_path) if prefix_path else None
        result["vortex_install"] = install["install_dir"] if install else None
        result["case_sensitivity"] = filesystem.check_case_sensitivity(steam_root)
        result["hardlink"] = filesystem.check_hardlink(steam_root)

    return result