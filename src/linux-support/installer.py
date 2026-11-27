"""Installer for the OVLS Linux Support components inside a Proton prefix.

Installed components:
  * Vortex Extension ``ovls-json-ipc-v2`` in ``%APPDATA%\\Vortex\\plugins``.
  * ``Steam/config/libraryfolders.vdf`` inside the prefix, created from the
    host Steam libraries so that Vortex discovers installed games without a
    manual path entry. Existing files are never rewritten.
  * Vortex uninstall registration in the prefix registry, repaired only when
    the official installer left it missing or broken (see ``registry.py``).

The installer never touches the official Vortex application files, game files,
Vortex settings or other extensions, and it never moves mods or staging.
"""

import hashlib
import os
import tempfile
from pathlib import Path

import desktop
import extension_installer
import layout
import registry
import steam
import tempfiles

PROJECT_ROOT = layout.ROOT

VORTEX_EXE_REL = Path("pfx") / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
STEAM_DIR_REL = Path("pfx") / "drive_c" / "Program Files (x86)" / "Steam"
STEAM_CONFIG_REL = STEAM_DIR_REL / "config" / "libraryfolders.vdf"


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wine_path(prefix, host_path):
    """Translate a host path into a Wine path using the prefix dosdevices."""
    prefix = Path(prefix)
    host = Path(host_path).resolve()
    best = None
    dosdevices = prefix / "pfx" / "dosdevices"
    if not dosdevices.is_dir():
        return None
    for link in dosdevices.iterdir():
        if not link.name.endswith(":") or not link.is_symlink():
            continue
        target = Path(os.path.realpath(link))
        try:
            relative = host.relative_to(target)
        except ValueError:
            continue
        if best is None or len(str(target)) > len(str(best[1])):
            best = (link.name[0].upper(), target, relative)
    if best is None:
        return None
    drive, _target, relative = best
    tail = str(relative).replace("/", "\\")
    return f"{drive}:\\{tail}" if tail else f"{drive}:\\"


def _wine_drive(prefix, host_path):
    wine = _wine_path(prefix, host_path)
    return wine[:2] if wine else None


def _running_vortex(prefix):
    needle = str(prefix / "pfx")
    hits = []
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        cmdline = cmdline.replace("\0", " ").strip()
        if "Vortex.exe" in cmdline and needle in cmdline:
            hits.append({"pid": int(entry.name), "cmdline": cmdline[:200]})
    return hits


def _read_vdf_paths(vdf_path):
    paths = []
    text = vdf_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line.lower().startswith('"path"'):
            continue
        parts = [part for part in line.split('"') if part.strip()]
        if len(parts) >= 2:
            paths.append(parts[1].replace("\\\\", "/"))
    return paths


def _steam_libraries(steam_root=None):
    steam_root = steam_root or steam.find_steam_root()
    if not steam_root:
        return [], None
    libraries = []
    for library in steam.read_library_folders(steam_root):
        real = str(Path(library).resolve())
        if real not in libraries:
            libraries.append(real)
    return libraries, steam_root


def _steam_library_state(prefix):
    prefix = Path(prefix).expanduser().resolve()
    config = prefix / STEAM_CONFIG_REL
    state = {
        "steam_dir": str(prefix / STEAM_DIR_REL),
        "steam_installed": (prefix / STEAM_DIR_REL).is_dir(),
        "config": str(config),
        "config_present": config.is_file(),
        "libraries": [],
    }
    if state["config_present"]:
        try:
            state["libraries"] = _read_vdf_paths(config)
        except OSError:
            state["libraries"] = []
    return state


def _render_library_vdf(wine_paths):
    lines = ['"LibraryFolders"', "{"]
    for index, path in enumerate(wine_paths):
        lines.append(f'\t"{index}"')
        lines.append("\t{")
        lines.append(f'\t\t"path"\t\t"{path}"')
        lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def ensure_steam_library(prefix, steam_root=None, dry_run=False):
    """Create Steam/config/libraryfolders.vdf when it is missing.

    Existing files are left untouched: Vortex may have already written its own
    library list, and the installer must not overwrite user configuration.
    """
    prefix = Path(prefix).expanduser().resolve()
    state = _steam_library_state(prefix)
    if not state["steam_installed"]:
        state["action"] = "skipped_no_steam"
        return state
    if state["config_present"]:
        state["action"] = "present"
        return state

    libraries, host_root = _steam_libraries(steam_root)
    state["host_steam_root"] = host_root
    wine_paths = []
    for library in libraries:
        wine = _wine_path(prefix, library)
        if wine and wine not in wine_paths:
            wine_paths.append(wine)
    state["wine_libraries"] = wine_paths
    if not wine_paths:
        state["action"] = "skipped_no_libraries"
        return state

    state["action"] = "would_create" if dry_run else "created"
    if dry_run:
        return state

    config = prefix / STEAM_CONFIG_REL
    config.parent.mkdir(parents=True, exist_ok=True)
    tempfiles.cleanup_orphaned_files(config.parent, ".libraryfolders-")
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".libraryfolders-{os.getpid()}-", suffix=".tmp", dir=str(config.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_render_library_vdf(wine_paths))
        os.replace(temp_path, config)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    state["libraries"] = wine_paths
    return state


def installed_state(prefix, source):
    """Compare installed Extension files with the source package."""
    _vortex_dir, plugins_dir = extension_installer.resolve_plugin_directory(prefix)
    target = plugins_dir / extension_installer.EXTENSION_ID
    existing_host = extension_installer._read_installed_host(target)
    try:
        source_dir, source_bytes = extension_installer._read_source_package(
            source, prefix, existing_host=existing_host
        )
    except extension_installer.HostMapRepairError as exc:
        return {
            "extension_id": extension_installer.EXTENSION_ID,
            "target": str(target),
            "source": str(Path(source).expanduser()),
            "source_sha256": {},
            "installed_sha256": {},
            "missing": [],
            "drifted": [],
            "status": "host_map_unrepairable",
            "reason": str(exc),
            "host_map_issues": extension_installer._host_map_issues(existing_host, prefix),
        }
    result = {
        "extension_id": extension_installer.EXTENSION_ID,
        "target": str(target),
        "source": str(source_dir),
        "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()},
        "installed_sha256": {},
        "missing": [],
        "drifted": [],
        "host_map_issues": extension_installer._host_map_issues(existing_host, prefix),
    }
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        result["status"] = "invalid_target"
        return result
    if not target.is_dir():
        result["status"] = "not_installed"
        result["missing"] = list(source_bytes)
        return result

    host_path = target / extension_installer.HOST_FILE
    if (extension_installer.HOST_FILE not in source_bytes
            and (host_path.is_symlink() or host_path.exists())):
        result["status"] = "stale_host"
        result["stale_host"] = str(host_path)
        return result
    if host_path.is_symlink() or (host_path.exists() and not host_path.is_file()):
        result["status"] = "invalid_host_file"
        result["invalid_host_file"] = str(host_path)
        return result

    for name in source_bytes:
        path = target / name
        if path.is_symlink() or not path.is_file():
            result["missing"].append(name)
            continue
        digest = _sha256(path)
        result["installed_sha256"][name] = digest
        if digest != result["source_sha256"][name]:
            result["drifted"].append(name)

    if result["missing"]:
        result["status"] = "missing_files"
    elif result["drifted"]:
        result["status"] = "drifted"
    else:
        result["status"] = "unchanged"
    return result


def _proton_state(prefix):
    import proton
    versions = []
    for directory in proton.find_proton_versions():
        version_file = Path(directory) / "version"
        version = None
        if version_file.is_file():
            try:
                version = version_file.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                version = None
        versions.append({"path": directory, "version": version})
    return {
        "versions": versions,
        "has_11_0": any("proton-11.0" in (entry["version"] or "") for entry in versions),
    }


def deploy_state(prefix, steam_root=None):
    """Report staging folders versus the Wine drive of the game libraries.

    Read-only: the installer never moves staging or changes Vortex settings.
    """
    vortex_dir, _plugins_dir = extension_installer.resolve_plugin_directory(prefix)
    libraries, _root = _steam_libraries(steam_root)
    library_drives = sorted({drive for drive in (_wine_drive(prefix, lib) for lib in libraries) if drive})
    staging = []
    for entry in sorted(vortex_dir.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        mods = entry / "mods"
        if not mods.is_dir():
            continue
        drive = _wine_drive(prefix, mods)
        staging.append({
            "game_id": entry.name,
            "staging": _wine_path(prefix, mods),
            "drive": drive,
            "matches_library_drive": drive in library_drives if drive else None,
        })
    return {"library_drives": library_drives, "staging": staging}


def detect(prefix_path):
    prefix = Path(prefix_path).expanduser().resolve(strict=True)
    result = {
        "prefix": str(prefix),
        "is_proton_prefix": (prefix / "pfx" / "drive_c" / "users").is_dir(),
    }
    if not result["is_proton_prefix"]:
        return result
    vortex_exe = prefix / VORTEX_EXE_REL
    result["vortex_exe"] = str(vortex_exe) if vortex_exe.is_file() else None
    result["vortex_installed"] = vortex_exe.is_file()
    vortex_dir, plugins_dir = extension_installer.resolve_plugin_directory(prefix)
    result["vortex_user_data"] = str(vortex_dir)
    result["plugins_dir"] = str(plugins_dir)
    result["plugins_dir_writable"] = os.access(plugins_dir, os.W_OK)
    result["other_extensions"] = sorted(
        entry.name for entry in plugins_dir.iterdir()
        if entry.name != extension_installer.EXTENSION_ID
    ) if plugins_dir.is_dir() else []
    result["running_vortex"] = _running_vortex(prefix)
    return result


def install(
    prefix_path,
    source,
    fix_steam_library=True,
    backup_root=None,
    dry_run=False,
    force=False,
    with_desktop=False,
    applications_dir=None,
    project_root=None,
    ovls_py=None,
):
    info = detect(prefix_path)
    if not info["is_proton_prefix"]:
        raise ValueError(f"Not a valid Vortex prefix (missing pfx/drive_c/users): {prefix_path}")
    if not info["vortex_installed"]:
        raise ValueError(f"Vortex.exe not found in prefix: {info['prefix']}")
    if info["running_vortex"] and not force:
        raise ValueError(
            "Vortex is running for this prefix; close it before installing "
            f"(pids: {', '.join(str(hit['pid']) for hit in info['running_vortex'])})"
        )

    # Repair the Vortex uninstall registration first: it is the step that may
    # abort the install, and a failure must not leave other components changed.
    registry_backup_dir = (
        Path(backup_root).parent / "registry" if backup_root is not None else None
    )
    registry_result = registry.ensure_uninstall_key(
        prefix_path, dry_run=dry_run, backup_dir=registry_backup_dir
    )

    steam_state = (
        ensure_steam_library(prefix_path, dry_run=dry_run)
        if fix_steam_library
        else {"action": "disabled"}
    )

    before = installed_state(prefix_path, source)
    no_install_statuses = (
        "stale_host", "invalid_host_file", "host_map_unrepairable", "invalid_target"
    )
    if dry_run:
        # Do not promise an action that the real install would refuse.
        preview = before["status"]
        extension_result = {
            "status": (
                preview if preview in no_install_statuses
                else "unchanged" if preview == "unchanged" else "would_install"
            ),
            "extension_id": before["extension_id"],
            "target": before["target"],
        }
    else:
        extension_result = extension_installer.install_extension(
            source, prefix_path, backup_dir=backup_root
        )
        extension_result["previous_status"] = before["status"]

    desktop_result = {"status": "disabled"}
    if with_desktop:
        desktop_result = desktop.install_handler(
            prefix_path,
            project_root or PROJECT_ROOT,
            dry_run=dry_run,
            applications_dir=applications_dir,
            ovls_py=ovls_py,
        )

    extension_ok = extension_result["status"] in ("installed", "unchanged", "would_install")
    # Old IPC data is removed only once the new Extension is in place: otherwise
    # the previous Extension is still active and would recreate it.
    legacy_ipc = (
        extension_installer.remove_legacy_ipc_data(prefix_path, dry_run=dry_run)
        if extension_ok
        else {"status": "kept", "reason": "Extension was not updated"}
    )
    return {
        "status": "ok" if extension_ok else "attention_required",
        "dry_run": dry_run,
        "installation": info,
        "vortex_registry": registry_result,
        "extension": extension_result,
        "legacy_ipc": legacy_ipc,
        "steam_library": steam_state,
        "nxm_handler": desktop_result,
        "proton": _proton_state(prefix_path),
        "deploy": deploy_state(prefix_path),
    }


def verify(prefix_path, source, with_desktop=False, applications_dir=None):
    info = detect(prefix_path)
    if not info["is_proton_prefix"]:
        raise ValueError(f"Not a valid Vortex prefix (missing pfx/drive_c/users): {prefix_path}")
    state = installed_state(prefix_path, source)
    steam_state = _steam_library_state(prefix_path)
    registry_state = registry.read_state(prefix_path)
    desktop_state = (
        desktop.handler_state(applications_dir=applications_dir)
        if with_desktop
        else {"status": "disabled"}
    )
    ok = (
        info["vortex_installed"]
        and state["status"] == "unchanged"
        and steam_state["config_present"]
        and registry_state["status"] == "ok"
        and not info["running_vortex"]
        and (not with_desktop or desktop_state["status"] == "ok")
    )
    return {
        "status": "ok" if ok else "attention_required",
        "installation": info,
        "vortex_registry": registry_state,
        "extension": state,
        "steam_library": steam_state,
        "nxm_handler": desktop_state,
        "proton": _proton_state(prefix_path),
        "deploy": deploy_state(prefix_path),
    }


def uninstall(prefix_path, with_desktop=False, applications_dir=None):
    info = detect(prefix_path)
    if not info["is_proton_prefix"]:
        raise ValueError(f"Not a valid Vortex prefix (missing pfx/drive_c/users): {prefix_path}")
    if info["running_vortex"]:
        raise ValueError("Vortex is running for this prefix; close it before uninstalling")
    removed = extension_installer.remove_extension(prefix_path)
    legacy_ipc = extension_installer.remove_legacy_ipc_data(prefix_path)
    desktop_result = (
        desktop.remove_handler(applications_dir=applications_dir)
        if with_desktop
        else {"status": "disabled"}
    )
    return {
        "status": "ok",
        "installation": info,
        "extension": removed,
        "legacy_ipc": legacy_ipc,
        "nxm_handler": desktop_result,
        "steam_library": _steam_library_state(prefix_path),
        "vortex_registry": {"status": "kept", "key": registry.UNINSTALL_KEY},
        "note": (
            "Steam/config/libraryfolders.vdf is kept: it is Steam configuration, "
            "not an OVLS component. The Vortex uninstall registration is kept as "
            "well: it belongs to Vortex and is not owned by Linux Support."
        ),
    }
