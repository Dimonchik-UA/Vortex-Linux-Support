import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import steam
import tempfiles


EXTENSION_ID = "ovls-json-ipc-v2"
MANAGED_FILES = ("info.json", "index.js")
# Generated on install: describes the Windows view of the host mods directory so
# the extension can offer a same-drive staging folder. Removed with the rest.
HOST_FILE = "host.json"

# Vortex skips plugin directories whose name ends with ".installing" while it
# scans the plugin directory (renderer.js: /\.installing$/i.test(name)).
# Staging there keeps a leftover from an interrupted run invisible to Vortex.
STAGING_SUFFIX = ".installing"


class HostMapRepairError(ValueError):
    """A damaged v2 volume map cannot be rebuilt without losing information."""


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


def _z_root(prefix_path):
    """Host directory mapped to the prefix ``z:`` drive, or None."""
    prefix = Path(prefix_path).expanduser().resolve(strict=True)
    z_link = prefix / "pfx" / "dosdevices" / "z:"
    try:
        if not z_link.is_symlink():
            return None
        z_target = Path(os.readlink(z_link))
        if not z_target.is_absolute():
            z_target = z_link.parent / z_target
        return z_target.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _wine_path(host_path, z_root):
    """Return the Windows view of ``host_path`` through ``z_root``, or None."""
    try:
        relative = Path(host_path).resolve(strict=False).relative_to(z_root)
    except (OSError, RuntimeError, ValueError):
        return None
    text = str(relative)
    if text == ".":
        return "Z:\\"
    return "Z:\\" + text.replace("/", "\\")


def _mount_point(path):
    """Real filesystem mount point containing ``path``, or None."""
    target = steam.findmnt_target(str(path))
    if not target:
        return None
    try:
        return Path(target).resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _host_path_from_wine(value, z_root):
    """Reverse a generated ``Z:\\...`` path to a host path, or None."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().replace("/", "\\")
    if not normalized.lower().startswith("z:\\"):
        return None
    relative = normalized[3:].replace("\\", "/").lstrip("/")
    try:
        return (z_root / relative).resolve(strict=False) if relative else z_root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _path_under_host(path, anchor):
    """Directory-boundary check for host paths (no partial-name matches)."""
    try:
        path.relative_to(anchor)
        return True
    except ValueError:
        return False


def _existing_volumes(existing_host, z_root):
    """Return ``(valid_volumes, issue_count)`` from an installed host.json.

    A malformed entry is skipped instead of discarding the whole map, but the
    number of issues is returned so verify can report that the saved map was
    damaged. Structural damage is counted even without a ``z:`` mapping;
    path-level validation additionally needs ``z_root``. A v1 file without
    ``volumes`` is not an issue.
    """
    if not existing_host:
        return [], 0
    try:
        raw = existing_host.decode("utf-8") if isinstance(existing_host, bytes) else existing_host
        parsed = json.loads(raw)
    except (AttributeError, UnicodeError, TypeError, ValueError):
        return [], 1
    if not isinstance(parsed, dict):
        return [], 1
    if "volumes" not in parsed:
        return [], 0
    volumes = parsed.get("volumes")
    if not isinstance(volumes, list) or not volumes:
        return [], 1
    issues = sum(1 for volume in volumes if not isinstance(volume, dict))
    if z_root is None:
        return [], issues
    result = []
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        anchor_host = _host_path_from_wine(volume.get("anchor"), z_root)
        mods_host = _host_path_from_wine(volume.get("modsRoot"), z_root)
        expected_mods = None
        if anchor_host is not None:
            try:
                expected_mods = (anchor_host / "Vortex Mods").resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                pass
        generated_anchor = _wine_path(anchor_host, z_root) if anchor_host is not None else None
        existing_anchor = volume.get("anchor")
        if (anchor_host is None or mods_host is None or expected_mods is None
                or mods_host != expected_mods
                or generated_anchor is None or not isinstance(existing_anchor, str)
                or generated_anchor.lower() != existing_anchor.strip().lower().replace("/", "\\")):
            issues += 1
            continue
        result.append((anchor_host, volume))
    return result, issues


def _host_map_issues(existing_host, prefix_path):
    """Number of damaged entries in an installed host.json volume map."""
    return _existing_volumes(existing_host, _z_root(prefix_path))[1]


def _had_volumes_map(existing_host):
    """True when the installed host.json already used the v2 volume map."""
    if not existing_host:
        return False
    try:
        raw = existing_host.decode("utf-8") if isinstance(existing_host, bytes) else existing_host
        parsed = json.loads(raw)
    except (AttributeError, UnicodeError, TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and "volumes" in parsed


def _reuse_existing_volume(existing, library_path):
    """Most specific saved volume whose anchor contains ``library_path``."""
    matches = [entry for entry in existing if _path_under_host(library_path, entry[0])]
    if not matches:
        return None
    return max(matches, key=lambda entry: len(str(entry[0])))[1]


def _wine_mods_root(prefix_path, home=None, z_root=None):
    """Describe the host home mods directory as seen from inside the prefix."""
    home_dir = Path(home) if home is not None else _host_home()
    mods_root = (home_dir / "Vortex Mods").resolve(strict=False)
    if z_root is None:
        z_root = _z_root(prefix_path)
    if z_root is None:
        return None
    return _wine_path(mods_root, z_root)


def _steam_root_for_home(home):
    """Find the real Steam root under the same home used for modsRoot."""
    base = Path(home)
    for relative in (".local/share/Steam", ".steam/steam", ".steam/root"):
        candidate = base / relative
        if candidate.is_dir() and (candidate / "steamapps").is_dir():
            return candidate
    return None


def _host_volumes(prefix_path, home=None, steam_root=None, z_root=None, existing_host=None):
    """Deterministic staging volumes for registered Steam libraries.

    The anchor is the real filesystem mount point reported by ``findmnt``,
    never ``library_path.parent``. A library inside PASSWD-home shares the home
    anchor only when both are on the same filesystem. A library inside home but
    on another filesystem gets its own mount root. Unavailable libraries are
    reused only from a matching volume in the installed host.json; otherwise
    they are skipped instead of guessed.
    """
    home_dir = Path(home) if home is not None else _host_home()
    if z_root is None:
        z_root = _z_root(prefix_path)
    if z_root is None:
        return []
    root = Path(steam_root) if steam_root is not None else _steam_root_for_home(home_dir)
    if root is None:
        return []
    try:
        home_resolved = home_dir.resolve(strict=False)
    except (OSError, RuntimeError):
        return []
    home_mount = _mount_point(home_resolved)

    existing, _issues = _existing_volumes(existing_host, z_root)
    volumes = []
    seen = set()
    for library in steam.read_library_folders(str(root), require_exists=False):
        try:
            library_path = Path(library).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue

        if library_path.is_dir():
            library_mount = _mount_point(library_path)
            entry = _reuse_existing_volume(existing, library_path)
            if library_mount is not None and entry is not None:
                saved_anchor = _host_path_from_wine(entry.get("anchor"), z_root)
                if (saved_anchor is not None and saved_anchor != library_mount
                        and _path_under_host(saved_anchor, library_mount)):
                    library_mount = None
            if library_mount is None:
                anchor = entry.get("anchor") if entry is not None else None
                if isinstance(anchor, str) and anchor not in seen:
                    seen.add(anchor)
                    volumes.append(entry)
                continue
            if (home_mount is not None and _path_under_host(library_path, home_resolved)
                    and library_mount == home_mount):
                anchor_host = home_resolved
                mods_dir = home_resolved / "Vortex Mods"
            else:
                anchor_host = library_mount
                mods_dir = library_mount / "Vortex Mods"
            anchor = _wine_path(anchor_host, z_root)
            mods_root = _wine_path(mods_dir, z_root)
            if anchor is None or mods_root is None:
                continue
            entry = {"anchor": anchor, "modsRoot": mods_root}
        else:
            entry = _reuse_existing_volume(existing, library_path)
            anchor = entry.get("anchor") if entry is not None else None
            if not isinstance(anchor, str):
                continue

        if anchor in seen:
            continue
        seen.add(anchor)
        volumes.append(entry)

    volumes.sort(key=lambda entry: entry["anchor"])
    return volumes


def _host_payload(prefix_path, home=None, steam_root=None, existing_host=None, was_v2=False):
    """Deterministic host.json bytes shared by install and verify.

    v2 adds ``volumes``; without it the bytes are exactly the old v1 payload.
    ``existing_host`` preserves only still-registered but currently unavailable
    volumes so an unmounted SD card cannot create a false drift. A damaged v2
    map that cannot be rebuilt must never be silently downgraded to v1.
    """
    z_root = _z_root(prefix_path)
    mods_root = _wine_mods_root(prefix_path, home=home, z_root=z_root)
    if mods_root is None:
        return None
    payload = {"modsRoot": mods_root}
    volumes = _host_volumes(
        prefix_path, home=home, steam_root=steam_root, z_root=z_root,
        existing_host=existing_host,
    )
    if volumes:
        payload["volumes"] = volumes
    elif was_v2:
        # No Steam root means the map cannot be rebuilt, but a fully valid
        # installed v2 map must survive unchanged instead of being reported as
        # unrepairable or silently downgraded to v1.
        preserved, preserved_issues = _existing_volumes(existing_host, z_root)
        saved = None
        if preserved and preserved_issues == 0:
            try:
                raw = (existing_host.decode("utf-8") if isinstance(existing_host, bytes)
                       else existing_host)
                saved = json.loads(raw)
            except (AttributeError, UnicodeError, TypeError, ValueError):
                saved = None
        if (isinstance(saved, dict) and saved.get("modsRoot") == mods_root
                and saved.get("volumes") == [entry for _anchor, entry in preserved]):
            return (existing_host if isinstance(existing_host, bytes)
                    else (json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        raise HostMapRepairError(
            "Cannot rebuild the installed host.json v2 volume map safely; "
            "the existing file was left unchanged"
        )
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _read_installed_host(target):
    """Bytes of an already installed host.json, if it is a regular file."""
    host = Path(target) / HOST_FILE
    try:
        if host.is_symlink() or not host.is_file():
            return None
        return host.read_bytes()
    except OSError:
        return None


def _read_source_package(source_path, prefix_path=None, home=None, steam_root=None, existing_host=None):
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
        host_bytes = _host_payload(
            prefix_path, home=home, steam_root=steam_root, existing_host=existing_host,
            was_v2=_had_volumes_map(existing_host),
        )
        if host_bytes is not None:
            package_files[HOST_FILE] = host_bytes
    return source, package_files


def install_extension(source_path, prefix_path, backup_dir=None, home=None, steam_root=None):
    vortex_dir, plugins_dir = _resolve_plugin_directory(prefix_path)
    target = plugins_dir / EXTENSION_ID
    if target.is_symlink():
        raise ValueError(f"Refusing to install through a symlinked Extension directory: {target}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"Extension target exists and is not a directory: {target}")

    existing_host = _read_installed_host(target)
    try:
        source, source_bytes = _read_source_package(
            source_path, prefix_path, home=home, steam_root=steam_root,
            existing_host=existing_host,
        )
    except HostMapRepairError as exc:
        return {
            "status": "host_map_unrepairable",
            "extension_id": EXTENSION_ID,
            "target": str(target),
            "reason": str(exc),
            "host_map_issues": _host_map_issues(existing_host, prefix_path),
        }
    host_map_issues = _host_map_issues(existing_host, prefix_path)
    host_path = target / HOST_FILE
    stale_host = (
        HOST_FILE not in source_bytes
        and (host_path.is_symlink() or host_path.exists())
    )

    if target.is_dir() and stale_host:
        return {
            "status": "stale_host",
            "extension_id": EXTENSION_ID,
            "target": str(target),
            "stale_host": str(host_path),
            "files": list(source_bytes),
        }

    # A directory, FIFO or symlink at a managed name must be reported, never
    # replaced partially or silently skipped.
    if target.is_dir() and (host_path.is_symlink() or (host_path.exists() and not host_path.is_file())):
        return {
            "status": "invalid_host_file",
            "extension_id": EXTENSION_ID,
            "target": str(target),
            "invalid_host_file": str(host_path),
        }

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
            "host_map_issues": host_map_issues,
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
    tempfiles.cleanup_orphaned_dirs(
        plugins_dir, f".{EXTENSION_ID}-", STAGING_SUFFIX
    )
    staging_dir = Path(tempfile.mkdtemp(
        prefix=f".{EXTENSION_ID}-{os.getpid()}-",
        suffix=STAGING_SUFFIX,
        dir=str(plugins_dir),
    ))
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
        "host_map_issues_repaired": host_map_issues,
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
    skipped = []
    for name in MANAGED_FILES + (HOST_FILE,):
        package_file = target / name
        if package_file.is_symlink() or not package_file.exists():
            continue
        if not package_file.is_file():
            skipped.append(name)
            continue
        package_file.unlink()
        removed.append(name)

    remaining = sorted(entry.name for entry in target.iterdir())
    directory_removed = False
    if not remaining and not skipped:
        target.rmdir()
        directory_removed = True

    return {
        "status": "removed",
        "extension_id": EXTENSION_ID,
        "target": str(target),
        "removed_files": removed,
        "skipped_files": skipped,
        "directory_removed": directory_removed,
        "remaining_files": remaining,
    }


# Data written by the removed Stage 9 JSON IPC (OVLS 0.4.0) under Vortex's
# userData directory. Only these exact names are removed.
LEGACY_IPC_DIR = "OVLS_IPC_V2"
LEGACY_IPC_ENTRIES = (
    "sessions", "requests", "processing", "responses", "acks", "dedup",
    "processed", "quarantine", "client_state", "status.json", "processed_ids.json",
)
LEGACY_IPC_TEMP = re.compile(r"^(status|processed_ids)\.json\..+\.tmp$")


def remove_legacy_ipc_data(prefix_path, dry_run=False):
    """Delete the leftovers of the removed IPC channel from one prefix.

    Removes only the fixed names listed above and matching temp files in the
    OVLS_IPC_V2 directory. Foreign files are never touched; the directory
    itself is removed only when it becomes empty. Idempotent: an absent
    directory is reported as ``absent``.
    """
    try:
        vortex_dir, _plugins_dir = _resolve_plugin_directory(prefix_path)
    except (OSError, ValueError) as exc:
        return {"status": "unresolved", "reason": str(exc)}

    root = vortex_dir / LEGACY_IPC_DIR
    if not root.is_symlink() and not root.exists():
        return {"status": "absent", "target": str(root), "removed": []}
    if root.is_symlink() or not root.is_dir():
        return {
            "status": "skipped", "target": str(root),
            "reason": "not a real directory; left untouched",
        }

    try:
        names = sorted(entry.name for entry in root.iterdir())
    except OSError as exc:
        return {"status": "error", "target": str(root), "reason": str(exc), "removed": []}

    owned = [
        name for name in names
        if name in LEGACY_IPC_ENTRIES or LEGACY_IPC_TEMP.match(name)
    ]
    removed = []
    try:
        for name in owned:
            path = root / name
            if not dry_run:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            removed.append(name)
        remaining = [name for name in names if name not in removed]
        directory_removed = False
        if not remaining and not dry_run:
            root.rmdir()
            directory_removed = True
    except OSError as exc:
        return {
            "status": "error", "target": str(root), "reason": str(exc),
            "removed": removed,
        }

    return {
        "status": "would_remove" if dry_run else ("removed" if removed or directory_removed else "absent"),
        "target": str(root),
        "removed": removed,
        "kept_foreign": remaining,
        "directory_removed": directory_removed if not dry_run else False,
    }


def is_installed(prefix_path):
    """Return True when this Extension is installed in the given prefix.

    Non-mutating: used by ``package.uninstall`` to refuse removing global
    OVLS components from a prefix that never had OVLS installed. A symlinked
    or non-directory target counts as not installed, mirroring
    ``remove_extension``.
    """
    try:
        _vortex_dir, plugins_dir = _resolve_plugin_directory(prefix_path)
    except (OSError, ValueError):
        return False
    target = plugins_dir / EXTENSION_ID
    if target.is_symlink() or not target.is_dir():
        return False
    return (target / "info.json").is_file()
