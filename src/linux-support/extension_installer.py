import json
import os
import shutil
import tempfile
from pathlib import Path


EXTENSION_ID = "ovls-json-ipc-v2"
MANAGED_FILES = ("info.json", "index.js")
# Generated on install: describes the Windows view of the host mods directory so
# the extension can offer a same-drive staging folder. Removed with the rest.
HOST_FILE = "host.json"

# Vortex skips plugin directories whose name ends with ".installing" while it
# scans the plugin directory (renderer.js: /\.installing$/i.test(name)).
# Staging there keeps a leftover from an interrupted run invisible to Vortex.
STAGING_SUFFIX = ".installing"


def _resolve_plugin_directory(prefix_path):
    prefix = Path(prefix_path).expanduser().resolve(strict=True)
    pfx = prefix / "pfx"
    drive_c = pfx / "drive_c"
    users_dir = drive_c / "users"
    if not pfx.is_dir() or not users_dir.is_dir():
        raise ValueError(f"Not a Proton prefix with pfx/drive_c/users: {prefix}")

    profiles = [entry for entry in users_dir.iterdir() if entry.is_dir() and not entry.is_symlink()]
    profiles_with_vortex = [
        profile for profile in profiles
        if (profile / "AppData" / "Roaming" / "Vortex").is_dir()
    ]

    if len(profiles_with_vortex) == 1:
        profile = profiles_with_vortex[0]
    elif len(profiles_with_vortex) > 1:
        raise ValueError(f"Multiple Vortex user profiles found in prefix: {prefix}")
    else:
        steamuser = users_dir / "steamuser"
        if steamuser.is_dir() and not steamuser.is_symlink():
            profile = steamuser
        elif len(profiles) == 1:
            profile = profiles[0]
        else:
            raise ValueError(
                f"Cannot select a unique Windows user profile in prefix: {users_dir}"
            )

    vortex_dir = profile / "AppData" / "Roaming" / "Vortex"
    plugins_dir = vortex_dir / "plugins"
    resolved_plugins = plugins_dir.resolve(strict=False)
    if os.path.commonpath((str(prefix), str(resolved_plugins))) != str(prefix):
        raise ValueError(f"Vortex plugin directory escapes the selected prefix: {plugins_dir}")

    return vortex_dir, plugins_dir


# Public alias: the installer and verification code resolve the same paths.
resolve_plugin_directory = _resolve_plugin_directory


def _host_home():
    """Home directory of the desktop user, not whatever $HOME points at.

    Wrappers and sandboxes can redirect $HOME, while Vortex reaches the real
    home through the prefix ``z:`` drive. Prefer the passwd entry so both
    install and verify always describe the same directory.
    """
    try:
        import pwd

        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
        if pw_dir:
            return Path(pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    return Path.home()


def _wine_mods_root(prefix_path, home=None):
    """Describe the host mods directory as seen from inside the prefix.

    Returns a Windows path such as ``Z:\\home\\deck\\Vortex Mods`` or ``None``
    when the prefix has no ``z:`` mapping (then the extension stays passive).
    """
    home_dir = Path(home) if home is not None else _host_home()
    mods_root = (home_dir / "Vortex Mods").resolve(strict=False)

    prefix = Path(prefix_path).expanduser().resolve(strict=True)
    z_link = prefix / "pfx" / "dosdevices" / "z:"
    try:
        if not z_link.is_symlink():
            return None
        z_target = Path(os.readlink(z_link))
    except OSError:
        return None
    if not z_target.is_absolute():
        z_target = z_link.parent / z_target
    z_root = z_target.resolve(strict=False)

    try:
        relative = mods_root.relative_to(z_root)
    except ValueError:
        return None
    return "Z:\\" + str(relative).replace("/", "\\")


def _host_payload(prefix_path, home=None):
    """Deterministic host.json bytes shared by install and verify."""
    mods_root = _wine_mods_root(prefix_path, home=home)
    if mods_root is None:
        return None
    return (json.dumps({"modsRoot": mods_root}, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _read_source_package(source_path, prefix_path=None, home=None):
    source = Path(source_path).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"Extension source is not a directory: {source}")

    source_info = source / "info.json"
    source_index = source / "index.js"
    for package_file in (source_info, source_index):
        if package_file.is_symlink() or not package_file.is_file():
            raise ValueError(f"Required regular package file is missing: {package_file}")

    try:
        metadata = json.loads(source_info.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read Extension metadata: {exc}") from exc

    if not isinstance(metadata, dict) or metadata.get("id") != EXTENSION_ID:
        raise ValueError(f"Expected Extension id {EXTENSION_ID!r} in {source_info}")

    package_files = {name: (source / name).read_bytes() for name in MANAGED_FILES}
    if prefix_path is not None:
        host_bytes = _host_payload(prefix_path, home=home)
        if host_bytes is not None:
            package_files[HOST_FILE] = host_bytes
    return source, package_files


def install_extension(source_path, prefix_path, backup_dir=None, home=None):
    source, source_bytes = _read_source_package(source_path, prefix_path, home=home)

    vortex_dir, plugins_dir = _resolve_plugin_directory(prefix_path)
    target = plugins_dir / EXTENSION_ID
    if target.is_symlink():
        raise ValueError(f"Refusing to install through a symlinked Extension directory: {target}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Extension target exists and is not a directory: {target}")

    if target.is_dir() and all(
        (target / name).is_file()
        and not (target / name).is_symlink()
        and (target / name).read_bytes() == data
        for name, data in source_bytes.items()
    ):
        return {
            "status": "unchanged",
            "extension_id": EXTENSION_ID,
            "target": str(target),
            "files": list(source_bytes),
        }

    backups = []
    if backup_dir is not None:
        backup_dir = Path(backup_dir)
        for name, data in source_bytes.items():
            existing = target / name
            if existing.is_symlink() or not existing.is_file():
                continue
            if existing.read_bytes() == data:
                continue
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing, backup_dir / name)
            backups.append(str(backup_dir / name))

    plugins_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{EXTENSION_ID}-", suffix=STAGING_SUFFIX, dir=str(plugins_dir)))
    try:
        for name, data in source_bytes.items():
            (staging_dir / name).write_bytes(data)

        target.mkdir(parents=True, exist_ok=True)
        # Replace only the files owned by this Extension; leave all other
        # plugins and any additional files in this Extension directory intact.
        for name in source_bytes:
            os.replace(staging_dir / name, target / name)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return {
        "status": "installed",
        "extension_id": EXTENSION_ID,
        "target": str(target),
        "files": list(source_bytes),
        "source": str(source),
        "backups": backups,
    }


def remove_extension(prefix_path):
    """Remove only the files owned by this Extension.

    The Extension directory itself is removed only when it holds nothing else,
    so files added by the user into the managed directory are never deleted.
    Every neighbouring plugin is left untouched.
    """
    _vortex_dir, plugins_dir = _resolve_plugin_directory(prefix_path)
    target = plugins_dir / EXTENSION_ID
    if target.is_symlink():
        raise ValueError(f"Refusing to remove through a symlinked Extension directory: {target}")
    if not target.exists():
        return {
            "status": "not_installed",
            "extension_id": EXTENSION_ID,
            "target": str(target),
            "removed_files": [],
        }
    if not target.is_dir():
        raise ValueError(f"Extension target exists and is not a directory: {target}")

    removed = []
    for name in MANAGED_FILES + (HOST_FILE,):
        package_file = target / name
        if package_file.is_symlink() or not package_file.exists():
            continue
        package_file.unlink()
        removed.append(name)

    remaining = sorted(entry.name for entry in target.iterdir())
    directory_removed = False
    if not remaining:
        target.rmdir()
        directory_removed = True

    return {
        "status": "removed",
        "extension_id": EXTENSION_ID,
        "target": str(target),
        "removed_files": removed,
        "directory_removed": directory_removed,
        "remaining_files": remaining,
    }
