"""Autonomous OVLS package: installed code copy, updates and safe removal.

The installed copy lives directly in ``$XDG_DATA_HOME/ovls`` and mirrors the
package layout::

    ovls/
      install.sh
      README.md
      VERSION
      MANIFEST.sha256
      ovls/*.py
      ovls/extension/*
      backups/<timestamp>/{app,extension,registry}

``MANIFEST.sha256`` lists every file owned by OVLS. Removal deletes only those
paths, rejects absolute or ``..`` entries and never follows symlinks, so user
files and ``backups/`` survive an uninstall.

Updates are transactional: the new tree is staged and verified first, the
previous managed files are copied to ``backups/<timestamp>/app`` and any error
restores the previous working copy instead of leaving a partially updated one.

Install and update also (re)create the desktop shortcuts; uninstall removes only
OVLS-owned shortcuts after the prefix integration is gone. Running
``install(update=True)`` from the installed copy refreshes the prefix components
and shortcuts without copying the package over itself.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import core
import desktop
import installer
import layout
import shortcuts
import steam

APP_DIR = Path("ovls")
SUPPORT_TREE = layout.CODE_TREE
EXTENSION_TREE = layout.EXTENSION_TREE
ENTRY_SCRIPT = "install.sh"
README_NAME = "README.md"
ICON_NAME = "ovls.png"
ICON_DIRNAME = "assets"
MANIFEST_NAME = "MANIFEST.sha256"
VERSION_NAME = "VERSION"
BACKUP_DIRNAME = "backups"
OLD_MANIFEST = "MANIFEST.sha256.old"
OLD_VERSION = "VERSION.old"
STEAM_COMPATDATA_APPID = "1712840"
OVLS_PY = layout.OVLS_PY


def app_root():
    return desktop.data_home() / APP_DIR


def backups_root():
    return app_root() / BACKUP_DIRNAME


def config_path():
    return desktop.config_home() / "ovls" / "config.json"


def load_prefix():
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("prefix")
    return str(value) if value else None


def save_prefix(prefix):
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["prefix"] = str(Path(prefix).expanduser().resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    _write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", 0o600)


def remove_config():
    path = config_path()
    try:
        path.unlink()
    except OSError:
        return
    try:
        path.parent.rmdir()
    except OSError:
        pass


def is_prefix(path):
    return (Path(path).expanduser() / "pfx" / "drive_c" / "users").is_dir()


def detect_prefix(explicit=None):
    """Return a usable Proton prefix or ``None``.

    Order: explicit argument, remembered prefix, known host locations, Steam
    ``compatdata``. Nothing is created or changed here.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    remembered = load_prefix()
    if remembered:
        candidates.append(remembered)
    candidates.append(Path.home() / "Windows" / "prefix")
    steam_root = steam.find_steam_root()
    if steam_root:
        candidates.append(Path(steam_root) / "steamapps" / "compatdata" / STEAM_COMPATDATA_APPID)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if is_prefix(path):
            return path.resolve()
    return None


def _sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _copy_file(source, destination):
    """Copy one file atomically inside the destination directory."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _iter_sources(package_root):
    root = Path(package_root).expanduser().resolve()
    support = root / SUPPORT_TREE
    if support.is_dir():
        for path in sorted(support.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path.relative_to(root), path
    extension = root / EXTENSION_TREE
    if extension.is_dir():
        for path in sorted(extension.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path.relative_to(root), path
    entry = root / ENTRY_SCRIPT
    if not entry.is_file():
        entry = root / "installer" / ENTRY_SCRIPT
    if entry.is_file():
        yield Path(ENTRY_SCRIPT), entry
    readme = root / README_NAME
    if readme.is_file():
        yield Path(README_NAME), readme
    icon = root / SUPPORT_TREE / ICON_DIRNAME / ICON_NAME
    if icon.is_file():
        yield Path(SUPPORT_TREE) / ICON_DIRNAME / ICON_NAME, icon


def _parse_manifest(text):
    entries = {}
    for line in (text or "").splitlines():
        digest, _, relative = line.partition("  ")
        if digest and relative:
            entries[relative.strip()] = digest.strip()
    return entries


def _read_manifest(path=None):
    return _parse_manifest(_read_text(path or app_root() / MANIFEST_NAME))


def installed_entry_point():
    """Return ``ovls.py`` of the installed copy.

    The manifest is the truth: the copy may have been installed from this
    checkout (``Development/Linux_Support``) or from the standalone package
    (``ovls/``). The module layout is only a fallback.
    """
    root = app_root()
    for relative in _read_manifest():
        if relative == "ovls.py" or relative.endswith("/ovls.py"):
            return root / relative
    return root / OVLS_PY


def _manifest_text(entries):
    return "".join(f"{entries[key]}  {key}\n" for key in sorted(entries))


def _safe_target(root, relative):
    """Return the delete/copy target inside ``root`` or ``None``.

    Rejects absolute paths, empty paths and ``..``; also rejects any symlinked
    parent directory so a manifest entry cannot escape through a link.
    """
    root_path = Path(root)
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(part == ".." for part in rel.parts):
        return None
    root_abs = os.path.abspath(root_path)
    target_abs = os.path.abspath(root_path / rel)
    try:
        if os.path.commonpath([root_abs, target_abs]) != root_abs:
            return None
    except ValueError:
        return None
    parent = Path(target_abs).parent
    root_lexical = Path(root_abs)
    while parent != root_lexical:
        if parent.is_symlink():
            return None
        parent = parent.parent
    return Path(target_abs)


def _inside(target, root):
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _remove_path(target, root):
    if target.is_symlink():
        target.unlink()
        return True
    if target.is_file():
        if not _inside(target, root):
            return False
        target.unlink()
        return True
    return False


def manifest_status():
    root = app_root()
    manifest = root / MANIFEST_NAME
    entries = _read_manifest(manifest)
    missing, modified, unsafe = [], [], []
    for relative, digest in entries.items():
        target = _safe_target(root, relative)
        if target is None:
            unsafe.append(relative)
            continue
        if target.is_symlink() or not target.is_file():
            missing.append(relative)
            continue
        if _sha256(target) != digest:
            modified.append(relative)
    ok = bool(entries) and manifest.is_file() and (root / VERSION_NAME).is_file()
    ok = ok and not missing and not modified and not unsafe
    return {
        "status": "ok" if ok else "attention_required",
        "app": str(root),
        "manifest": str(manifest),
        "files": len(entries),
        "missing": missing,
        "modified": modified,
        "unsafe": unsafe,
    }


def _stamp():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")


def _mode(relative):
    return 0o755 if Path(relative).name in ("ovls.py", ENTRY_SCRIPT) else 0o644


def copy_tree(package_root, stamp=None, dry_run=False):
    """Copy the package into ``app_root`` transactionally."""
    package = Path(package_root).expanduser().resolve()
    sources = list(_iter_sources(package))
    if not sources:
        raise ValueError(f"package has no files to install: {package}")
    root = app_root()
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ovls-stage-", dir=str(root.parent)))
    try:
        staged = {}
        for relative, source in sources:
            relative = Path(relative)
            destination = staging / relative
            _copy_file(source, destination)
            os.chmod(destination, _mode(relative))
            staged[str(relative)] = _sha256(destination)

        old_text = _read_text(root / MANIFEST_NAME)
        old = _parse_manifest(old_text)
        old_version = _read_text(root / VERSION_NAME)
        stale = sorted(set(old) - set(staged))
        changed = sorted(rel for rel in staged if old.get(rel) != staged[rel])
        if not changed and not stale and old_text is not None:
            return {
                "status": "unchanged",
                "app": str(root),
                "files": len(staged),
                "backup": None,
                "previous": True,
            }
        if dry_run:
            return {
                "status": "would_update" if old else "would_install",
                "app": str(root),
                "files": len(staged),
                "changed": changed,
                "stale": stale,
                "backup": None,
                "previous": old_text is not None,
            }

        stamp = stamp or _stamp()
        backup = backups_root() / stamp / "app"
        backup.mkdir(parents=True, exist_ok=True)
        _write_text(backup / OLD_MANIFEST, old_text or "")
        _write_text(backup / OLD_VERSION, old_version or "")
        for relative in sorted(set(old) | set(changed) | set(stale)):
            target = _safe_target(root, relative)
            if target is None or target.is_symlink() or not target.is_file():
                continue
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(target, saved)

        try:
            for relative in changed + stale:
                if _safe_target(root, relative) is None:
                    raise ValueError(f"unsafe path in manifest or package: {relative}")
            for relative in changed:
                target = _safe_target(root, relative)
                _copy_file(staging / relative, target)
                os.chmod(target, _mode(relative))
            for relative in stale:
                target = _safe_target(root, relative)
                if target is not None:
                    _remove_path(target, root)
            _write_text(root / MANIFEST_NAME, _manifest_text(staged))
            _write_text(root / VERSION_NAME, core.VERSION + "\n")
        except Exception:
            _restore_app(root, backup)
            raise
        return {
            "status": "updated" if old else "installed",
            "app": str(root),
            "files": len(staged),
            "changed": changed,
            "stale": stale,
            "backup": str(backup),
            "previous": bool(old_text),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _restore_app(root, backup):
    """Restore the previous managed tree from ``backup`` (best effort)."""
    backup = Path(backup)
    old_text = _read_text(backup / OLD_MANIFEST)
    if not old_text or not old_text.strip():
        remove_copy(root)
        return
    old = _parse_manifest(old_text)
    current = _read_manifest(root / MANIFEST_NAME)
    for relative in sorted(set(current) - set(old)):
        target = _safe_target(root, relative)
        if target is not None:
            _remove_path(target, root)
    for relative in sorted(old):
        saved = backup / relative
        if not saved.is_file() or saved.is_symlink():
            continue
        target = _safe_target(root, relative)
        if target is None:
            continue
        _copy_file(saved, target)
        os.chmod(target, _mode(relative))
    _write_text(root / MANIFEST_NAME, old_text)
    old_version = _read_text(backup / OLD_VERSION)
    if old_version is not None:
        _write_text(root / VERSION_NAME, old_version)


def remove_copy(root=None):
    """Delete only manifest files; keep user files and ``backups/``."""
    root = Path(root) if root is not None else app_root()
    entries = _read_manifest(root / MANIFEST_NAME)
    removed, skipped = [], []
    for relative in sorted(entries):
        target = _safe_target(root, relative)
        if target is None:
            skipped.append(relative)
            continue
        try:
            if _remove_path(target, root):
                removed.append(relative)
            elif target.exists() or target.is_symlink():
                skipped.append(relative)
        except OSError:
            skipped.append(relative)
    for name in (MANIFEST_NAME, VERSION_NAME):
        try:
            (root / name).unlink()
        except OSError:
            pass
    _remove_runtime_artifacts(root)
    if root.is_dir():
        for current, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
            path = Path(current)
            if path == root:
                continue
            try:
                path.rmdir()
            except OSError:
                pass
    return {
        "status": "ok",
        "app": str(root),
        "removed": removed,
        "skipped": skipped,
        "app_removed": not root.exists(),
        "backups_kept": backups_root().is_dir(),
    }


def _remove_runtime_artifacts(root):
    """Delete only OVLS-generated runtime files (log, pycache) from the copy."""
    root = Path(root)
    for current, _dirs, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path != root and path.name == "__pycache__" and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
            continue
        for name in files:
            if name != "ovls.log":
                continue
            target = path / name
            if target.is_file() and not target.is_symlink():
                try:
                    target.unlink()
                except OSError:
                    pass


def _backup_root(stamp):
    return str(backups_root() / stamp / "extension")


def install(package_root=None, prefix=None, dry_run=False, update=False):
    package = Path(package_root).expanduser().resolve() if package_root else layout.ROOT
    root = app_root()
    try:
        same = root.exists() and package == root.resolve()
    except OSError:
        same = False
    if same and not update:
        raise ValueError(
            "Run install from the package directory, not from the installed copy; "
            "use update to refresh the installed components"
        )
    if update and not (root / MANIFEST_NAME).is_file():
        raise ValueError("OVLS is not installed; run install first")
    target_prefix = detect_prefix(prefix)
    if target_prefix is None:
        raise ValueError(
            "Proton prefix not found. Pass --prefix /path/to/compatdata "
            "(the directory containing pfx/)"
        )

    stamp = _stamp()
    if same:
        # Self-refresh from the installed copy: never copy the package over
        # itself and never start staging. Only the prefix integration and the
        # shortcuts are re-applied.
        copy_result = {
            "status": "unchanged",
            "app": str(root),
            "files": len(_read_manifest()),
            "backup": None,
            "previous": True,
            "refresh": True,
        }
    else:
        copy_result = copy_tree(package, stamp=stamp, dry_run=dry_run)
    if dry_run:
        install_result = installer.install(
            str(target_prefix),
            str(package / EXTENSION_TREE),
            dry_run=True,
            with_desktop=False,
        )
        handler = desktop.install_handler(
            str(target_prefix),
            str(package),
            dry_run=True,
            ovls_py=str(root / OVLS_PY),
        )
        return {
            "status": "ok",
            "dry_run": True,
            "prefix": str(target_prefix),
            "copy": copy_result,
            "installation": install_result,
            "nxm_handler": handler,
            "shortcuts": shortcuts.install(dry_run=True),
        }

    try:
        install_result = installer.install(
            str(target_prefix),
            str(root / EXTENSION_TREE),
            backup_root=_backup_root(stamp),
            with_desktop=False,
        )
        handler = desktop.install_handler(
            str(target_prefix),
            str(package),
            ovls_py=str(root / OVLS_PY),
        )
    except Exception:
        if not same:
            if copy_result.get("backup"):
                _restore_app(root, Path(copy_result["backup"]))
            elif not copy_result.get("previous"):
                remove_copy(root)
        raise
    try:
        shortcut_result = shortcuts.install()
    except OSError as exc:
        shortcut_result = {
            "status": "attention_required",
            "reason": str(exc),
            "installed_code": str(root / OVLS_PY),
            "written": [],
            "would_write": [],
            "skipped": [],
            "shortcuts": [],
        }
    save_prefix(target_prefix)
    return {
        "status": "ok",
        "prefix": str(target_prefix),
        "copy": copy_result,
        "installation": install_result,
        "nxm_handler": handler,
        "shortcuts": shortcut_result,
    }


def verify(prefix=None):
    target_prefix = detect_prefix(prefix)
    copy_state = manifest_status()
    shortcut_state = shortcuts.status()
    wrapper = desktop._wrapper_path()
    wrapper_text = _read_text(wrapper) or ""
    autonomous = str(installed_entry_point()) in wrapper_text
    if target_prefix is None:
        return {
            "status": "attention_required",
            "reason": "prefix_not_found",
            "prefix": None,
            "copy": copy_state,
            "shortcuts": shortcut_state,
            "nxm_handler": {"wrapper": str(wrapper), "autonomous": autonomous},
        }
    try:
        installed = installer.verify(
            str(target_prefix),
            str(app_root() / EXTENSION_TREE),
            with_desktop=True,
        )
    except (OSError, ValueError) as exc:
        return {
            "status": "attention_required",
            "reason": str(exc),
            "prefix": str(target_prefix),
            "copy": copy_state,
            "shortcuts": shortcut_state,
            "nxm_handler": {"wrapper": str(wrapper), "autonomous": autonomous},
        }
    ok = (
        installed["status"] == "ok"
        and copy_state["status"] == "ok"
        and shortcut_state["status"] == "ok"
        and autonomous
    )
    return {
        "status": "ok" if ok else "attention_required",
        "prefix": str(target_prefix),
        "copy": copy_state,
        "shortcuts": shortcut_state,
        "nxm_handler": {"wrapper": str(wrapper), "autonomous": autonomous},
        "installation": installed,
    }


def uninstall(prefix=None):
    target_prefix = detect_prefix(prefix)
    if target_prefix is None:
        return {
            "status": "blocked",
            "reason": "prefix_not_found",
            "message": (
                "Proton prefix not found. Pass --prefix /path/to/compatdata; "
                "the OVLS code copy and NXM integration were kept."
            ),
            "copy_kept": True,
        }
    try:
        installed = installer.uninstall(str(target_prefix), with_desktop=True)
    except (OSError, ValueError) as exc:
        return {
            "status": "blocked",
            "reason": "uninstall_failed",
            "message": str(exc),
            "copy_kept": True,
        }
    shortcuts_result = shortcuts.remove()
    copy_result = remove_copy()
    remove_config()
    return {
        "status": "ok",
        "prefix": str(target_prefix),
        "installation": installed,
        "shortcuts": shortcuts_result,
        "copy": copy_result,
    }
