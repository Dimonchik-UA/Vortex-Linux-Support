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

import ast
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import core
import desktop
import extension_installer
import installer
import layout
import proton
import shortcuts
import steam
import tempfiles

APP_DIR = Path("ovls")
SUPPORT_TREE = layout.CODE_TREE
EXTENSION_TREE = layout.EXTENSION_TREE
ENTRY_SCRIPT = "install.sh"
README_NAME = "README.md"
ICON_DIRNAME = "assets"
MANIFEST_NAME = "MANIFEST.sha256"
VERSION_NAME = "VERSION"
BACKUP_DIRNAME = "backups"
OLD_MANIFEST = "MANIFEST.sha256.old"
OLD_VERSION = "VERSION.old"
OVLS_PY = layout.OVLS_PY

# Maximum number of backup snapshots to retain. Older backups are removed
# automatically during copy_tree() to prevent unbounded disk usage.
MAX_BACKUP_COUNT = 3

# Regex matching the _stamp() format: YYYY-MM-DD_HHMMSS_ffffff
_BACKUP_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_\d{6}$")


def app_root():
    return desktop.data_home() / APP_DIR


def backups_root():
    return app_root() / BACKUP_DIRNAME


def _cleanup_old_backups(root=None):
    """Remove oldest OVLS backup snapshots beyond MAX_BACKUP_COUNT.

    Only directories whose names match the _stamp() format
    (YYYY-MM-DD_HHMMSS_ffffff) are considered. Directories with
    arbitrary names, symlinks, and files are never touched.

    Backup directories are sorted lexicographically by name (which equals
    chronological order for the timestamp format). The newest
    MAX_BACKUP_COUNT directories are kept; the rest are removed.
    """
    root = Path(root) if root is not None else backups_root()
    if not root.is_dir():
        return
    try:
        dirs = sorted(
            (
                d
                for d in root.iterdir()
                if d.is_dir()
                and not d.is_symlink()
                and _BACKUP_STAMP_RE.match(d.name)
            ),
            reverse=True,
        )
    except OSError:
        return
    for old_dir in dirs[MAX_BACKUP_COUNT:]:
        shutil.rmtree(old_dir, ignore_errors=True)


def _cleanup_orphaned_staging_dirs(root=None):
    """Remove staging directories only when their tagged owner process is gone.

    Only directories matching the exact .ovls-stage-<pid>-<random> pattern are
    considered; untagged or active directories are left untouched.
    """
    root = Path(root) if root is not None else (app_root().parent)
    tempfiles.cleanup_orphaned_dirs(root, ".ovls-stage-")


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
    return steam.is_vortex_prefix(path)


def detect_prefix_candidates(explicit=None, include_generic=True):
    """Return all valid Vortex prefixes in priority order (read-only).

    Order: explicit argument, remembered prefix, known host locations, then a
    generic scan of Steam ``compatdata/*``. Nothing is created or changed here.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    remembered = load_prefix()
    if remembered:
        candidates.append(remembered)
    candidates.append(Path.home() / "Windows" / "prefix")
    steam_root = steam.find_steam_root()
    if steam_root and include_generic:
        candidates.extend(steam.find_vortex_prefixes(steam_root))
    found = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not is_prefix(path):
            continue
        try:
            path = path.resolve()
        except OSError:
            pass
        if path not in seen:
            seen.add(path)
            found.append(path)
    return found


def detect_prefix(explicit=None):
    """Return an explicit/saved prefix or the only discovered Vortex prefix.

    The generic ``compatdata/*`` scan is part of Install auto-detection: a
    candidate is accepted only when it contains both ``pfx/drive_c/users/`` and
    ``Vortex.exe``. Several matches stay ambiguous and are resolved by the
    user-facing prefix list, never by taking the first directory blindly.
    """
    candidates = detect_prefix_candidates(explicit)
    if not candidates:
        return None
    for chosen in (explicit, load_prefix()):
        if not chosen:
            continue
        path = Path(chosen).expanduser()
        try:
            path = path.resolve()
        except OSError:
            pass
        if path in candidates:
            return path
    return candidates[0] if len(candidates) == 1 else None


def prefix_search_message(explicit=None):
    """Explain how to proceed when no unique prefix could be selected."""
    candidates = detect_prefix_candidates(explicit)
    if len(candidates) > 1:
        lines = [
            "Proton prefix not found: more than one Vortex prefix was found.",
            "Pass --prefix /path/to/compatdata to choose one:",
        ]
        lines.extend(f"  {path}" for path in candidates)
        return "\n".join(lines)
    return (
        "Proton prefix not found. Vortex not found; add vortex-setup-*.exe as a "
        "non-Steam game in Steam, select a Proton compatibility tool (DW-Proton or "
        "GE-Proton are recommended) and run it once, or pass --prefix /path/to/compatdata."
    )


def _sha256(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_prefix = f".{path.name}-{os.getpid()}-"
    tempfiles.cleanup_orphaned_files(path.parent, f".{path.name}-")
    descriptor, temp_path = tempfile.mkstemp(
        prefix=temp_prefix, suffix=".tmp", dir=str(path.parent)
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
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _copy_file(source, destination):
    """Copy one file atomically inside the destination directory."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_prefix = f".{destination.name}-{os.getpid()}-"
    tempfiles.cleanup_orphaned_files(destination.parent, f".{destination.name}-")
    descriptor, temp_path = tempfile.mkstemp(
        prefix=temp_prefix, suffix=".tmp", dir=str(destination.parent)
    )
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _standalone_code_dir(root):
    """Return standalone package code dir (ovls/) if this root uses that layout."""
    candidate = Path(root) / layout.PACKAGE_CODE_TREE
    if (candidate / "ovls.py").is_file():
        return candidate
    return None


def _project_support_dir(root):
    """Return project-layout support dir if present."""
    root = Path(root)
    # Try the layout of the running copy first, then known fallbacks.
    for rel in (SUPPORT_TREE, Path("src") / "linux-support", Path("Development") / "Linux_Support"):
        candidate = root / rel
        if (candidate / "ovls.py").is_file():
            return candidate
    return None


def _source_layout_kind(root):
    root = Path(root).expanduser().resolve()
    if _standalone_code_dir(root) is not None:
        return "standalone"
    if _project_support_dir(root) is not None:
        return "project"
    return "unknown"


def _find_extension_dir(root):
    """Locate the OVLS extension directory within a package/installed root."""
    root = Path(root).expanduser().resolve()
    candidates = []
    standalone = _standalone_code_dir(root)
    if standalone is not None:
        candidates.append(standalone / "extension")
    support = _project_support_dir(root)
    if support is not None:
        # Project layouts keep extension as sibling of support (src/extension)
        # or inside the support dir in some layouts.
        candidates.append(support.parent / "extension")
        candidates.append(support / "extension")
    # Generic fallbacks (including installed-copy layouts)
    candidates.extend([
        root / EXTENSION_TREE,
        root / layout.PACKAGE_EXTENSION_TREE,
        root / "src" / "extension",
        root / "Development" / "Phase9_JSON_IPC" / "extension",
        root / "ovls" / "extension",
    ])
    seen = set()
    for candidate in candidates:
        try:
            key = str(candidate)
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir() and (candidate / "index.js").is_file() and (candidate / "info.json").is_file():
            return candidate
    return None


def _iter_sources(package_root):
    """Yield (installed-relative-path, source-path) for all managed files.

    Supports both project-layout and standalone-layout package roots. In both
    cases relative paths match the layout of the installed/autonomous copy.
    """
    root = Path(package_root).expanduser().resolve()
    kind = _source_layout_kind(root)

    if kind == "standalone":
        code_dir = _standalone_code_dir(root)
        if code_dir is not None and code_dir.is_dir():
            # Include all managed files under ovls/ (py, js, json, assets, etc.)
            for path in sorted(code_dir.rglob("*")):
                if "__pycache__" in path.parts or not path.is_file():
                    continue
                yield path.relative_to(root), path
    elif kind == "project":
        support = _project_support_dir(root)
        if support is not None and support.is_dir():
            for path in sorted(support.rglob("*.py")):
                if "__pycache__" not in path.parts:
                    yield path.relative_to(root), path
            # Include non-Python assets inside support dir (e.g. assets/ovls.png)
            for path in sorted(support.rglob("*")):
                if "__pycache__" in path.parts or not path.is_file():
                    continue
                if path.suffix == ".py":
                    continue
                # Keep only known asset locations inside the support tree.
                try:
                    rel_under_support = path.relative_to(support)
                except ValueError:
                    continue
                if rel_under_support.parts[:1] == (ICON_DIRNAME,):
                    yield path.relative_to(root), path
        extension = _find_extension_dir(root)
        if extension is not None:
            # Preserve the extension path relative to root when possible.
            try:
                ext_rel_base = extension.relative_to(root)
            except ValueError:
                ext_rel_base = EXTENSION_TREE
            for path in sorted(extension.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    try:
                        rel = ext_rel_base / path.relative_to(extension)
                    except ValueError:
                        rel = path.relative_to(root)
                    yield rel, path

    # Entry script and README live at the package root in both layouts.
    entry = root / ENTRY_SCRIPT
    if not entry.is_file():
        entry = root / "installer" / ENTRY_SCRIPT
    if entry.is_file():
        yield Path(ENTRY_SCRIPT), entry
    # Release packages must ship the installer guide, not the repository README.
    readme = root / "installer" / README_NAME
    if not readme.is_file():
        readme = root / README_NAME
    if readme.is_file():
        yield Path(README_NAME), readme


def _validate_source_set(sources, package_root):
    """Reject incomplete package sources that would produce a broken install.

    Standalone release packages must include the full runnable code set and
    extension; project-layout/minimal fixtures are accepted when they contain
    the entry module so existing package workflows keep working.
    """
    if not sources:
        raise ValueError(f"package has no files to install: {package_root}")
    rels = {str(Path(rel)).replace("\\", "/") for rel, _ in sources}
    has_ovls_py = any(r == "ovls.py" or r.endswith("/ovls.py") for r in rels)
    py_count = sum(1 for r in rels if r.endswith(".py"))
    has_extension = any(r.endswith("/extension/index.js") or r == "extension/index.js" for r in rels) and \
        any(r.endswith("/extension/info.json") or r == "extension/info.json" for r in rels)

    if not has_ovls_py:
        raise ValueError(
            f"package source is incomplete or unsupported layout: {package_root} "
            f"(ovls.py={has_ovls_py}, py_files={py_count}, extension={has_extension})"
        )

    kind = _source_layout_kind(package_root)
    if kind == "standalone":
        # Standalone release must install a complete, runnable OVLS copy.
        if py_count < 2 or not has_extension:
            raise ValueError(
                f"package source is incomplete or unsupported layout: {package_root} "
                f"(ovls.py={has_ovls_py}, py_files={py_count}, extension={has_extension})"
            )


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

    The manifest is the truth: the copy may have been installed from a
    repository checkout (``src/linux-support``) or from the standalone
    package (``ovls/``). Historical ``Development/`` paths are legacy
    fallbacks only, not an active source.
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
    # Resolving first rejects links whose final destination is outside root.
    try:
        root_real = Path(root_path).resolve(strict=True)
        target_real = (root_path / rel).resolve(strict=False)
        if os.path.commonpath([str(root_real), str(target_real)]) != str(root_real):
            return None
    except (OSError, ValueError):
        return None
    # A parent link must be rejected even when it resolves back inside root:
    # otherwise a manifest entry could reach a different managed file through
    # an internal alias. Check the original components, not the resolved path.
    candidate = root_path
    for part in rel.parts[:-1]:
        candidate = candidate / part
        if candidate.is_symlink():
            return None
    return Path(os.path.abspath(root_path / rel))


def _inside(target, root):
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _remove_path(target, root):
    # Always verify containment before deletion, including for symlinks.
    if not _inside(target, root):
        return False
    if target.is_symlink():
        target.unlink()
        return True
    if target.is_file():
        target.unlink()
        return True
    return False


def _prune_empty_dirs(directory, root):
    """Remove empty directories from ``directory`` up to, not including, ``root``."""
    directory = Path(directory)
    while directory != root and _inside(directory, root):
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def _version_from_core_text(text):
    """Read a single module-level VERSION string without executing the code."""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return None
    values = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets):
            continue
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            continue
        value = node.value.value.strip()
        if value:
            values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def _version_from_core_file(path):
    """Read VERSION from an installed/staged core.py without importing it."""
    text = _read_text(path)
    return _version_from_core_text(text) if text is not None else None


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
        try:
            current = _sha256(target)
        except OSError:
            modified.append(relative)
            continue
        if current != digest:
            modified.append(relative)
    # The installed core.py is the only version source; both project-layout
    # (src/linux-support/core.py) and standalone-layout (ovls/core.py) installs
    # are discovered through the manifest, without a second version constant.
    core_versions = set()
    for relative in sorted(entries):
        if Path(relative).name != "core.py":
            continue
        core_path = _safe_target(root, relative)
        if core_path is not None and core_path.is_file():
            value = _version_from_core_file(core_path)
            if value:
                core_versions.add(value)
    expected_version = next(iter(core_versions)) if len(core_versions) == 1 else None
    version_path = root / VERSION_NAME
    version_text = _read_text(version_path)
    if version_path.is_symlink():
        version_status = "unsafe"
    elif version_text is None:
        version_status = "missing"
    elif not version_text.strip():
        version_status = "empty"
    elif expected_version is None:
        version_status = "unknown"
    elif version_text.strip() == expected_version:
        version_status = "ok"
    else:
        version_status = "outdated"
    ok = bool(entries) and manifest.is_file() and version_status == "ok"
    ok = ok and not missing and not modified and not unsafe
    return {
        "status": "ok" if ok else "attention_required",
        "app": str(root),
        "manifest": str(manifest),
        "files": len(entries),
        "missing": missing,
        "modified": modified,
        "unsafe": unsafe,
        "version": version_text.strip() if version_text is not None else None,
        "expected_version": expected_version,
        "version_status": version_status,
    }


def _stamp():
    return datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")


def _mode(relative):
    return 0o755 if Path(relative).name in ("ovls.py", ENTRY_SCRIPT) else 0o644


def _version_from_staged_tree(staging, sources):
    """Read VERSION from the staged core.py, not from the running process."""
    for relative, _source in sources:
        path = staging / Path(relative)
        if path.name == "core.py" and path.is_file():
            return _version_from_core_file(path)
    return None


def copy_tree(package_root, stamp=None, dry_run=False):
    """Copy the package into ``app_root`` transactionally."""
    package = Path(package_root).expanduser().resolve()
    sources = list(_iter_sources(package))
    _validate_source_set(sources, package)
    root = app_root()
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".ovls-stage-{os.getpid()}-", dir=str(root.parent)
    ))
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
        version_path = root / VERSION_NAME
        version_is_symlink = version_path.is_symlink()
        old_version = _read_text(version_path)
        new_version = _version_from_staged_tree(staging, sources)
        if not new_version:
            raise ValueError(
                f"staged package has no readable VERSION in core.py: {package}"
            )
        stale = sorted(set(old) - set(staged))
        changed = sorted(rel for rel in staged if old.get(rel) != staged[rel])
        if (
            not changed
            and not stale
            and old_text is not None
            and not version_is_symlink
            and (old_version or "").strip() == new_version
        ):
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
        # Absence of VERSION.old is the marker that VERSION did not exist;
        # writing an empty string would turn "absent" into "empty" on rollback.
        if old_version is not None:
            _write_text(backup / OLD_VERSION, old_version)
        for relative in sorted(set(old) | set(changed) | set(stale)):
            target = _safe_target(root, relative)
            if target is None or target.is_symlink() or not target.is_file():
                continue
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(target, saved)

        try:
            copied_files = []
            for relative in changed + stale:
                if _safe_target(root, relative) is None:
                    raise ValueError(f"unsafe path in manifest or package: {relative}")
            for relative in changed:
                target = _safe_target(root, relative)
                _copy_file(staging / relative, target)
                copied_files.append(relative)
                os.chmod(target, _mode(relative))
            for relative in stale:
                target = _safe_target(root, relative)
                if target is not None:
                    _remove_path(target, root)
                    _prune_empty_dirs(target.parent, root)
            _write_text(root / MANIFEST_NAME, _manifest_text(staged))
            _write_text(root / VERSION_NAME, new_version + "\n")
        except Exception:
            _restore_app(root, backup, copied_files)
            raise
        _cleanup_old_backups()

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


def _restore_app(root, backup, copied_files=None):
    """Restore the previous managed tree from ``backup`` (best effort)."""
    backup = Path(backup)
    old_text = _read_text(backup / OLD_MANIFEST)
    if not old_text or not old_text.strip():
        for relative in copied_files or []:
            target = _safe_target(root, relative)
            if target is not None:
                _remove_path(target, root)
        remove_copy(root)
        return
    old = _parse_manifest(old_text)
    current = _read_manifest(root / MANIFEST_NAME)
    # First, remove any files that were copied during the failed update
    # but are not in the old manifest (orphan files from failed update).
    if copied_files is not None:
        for relative in copied_files:
            target = _safe_target(root, relative)
            if target is not None:
                _remove_path(target, root)
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
    backup_version = backup / OLD_VERSION
    version_file = root / VERSION_NAME
    if backup_version.is_file():
        _write_text(version_file, _read_text(backup_version) or "")
    elif version_file.exists() or version_file.is_symlink():
        version_file.unlink()


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


def _is_log_file(name, base):
    """True for ``base`` itself or one of its rotated copies ``base.1``…``base.N``."""
    if name == base:
        return True
    prefix = base + "."
    if not name.startswith(prefix):
        return False
    number = name[len(prefix):]
    return number.isdigit() and 1 <= int(number) <= proton.LOG_BACKUP_COUNT


def _remove_log_files(directory, base):
    removed = []
    if not directory.is_dir():
        return removed
    for entry in directory.iterdir():
        if not _is_log_file(entry.name, base):
            continue
        if entry.is_file() and not entry.is_symlink():
            try:
                entry.unlink()
                removed.append(str(entry))
            except OSError:
                pass
    return removed


def remove_user_logs():
    """Delete OVLS launch/NXM logs and their rotations outside the code copy.

    ``launch.log`` lives in the state home and ``nxm.log`` in the data home.
    Only exact OVLS names are removed; anything else in those directories is kept.
    """
    removed = []
    state_dir = desktop.state_home() / APP_DIR
    data_dir = desktop.data_home() / APP_DIR
    removed += _remove_log_files(state_dir, "launch.log")
    removed += _remove_log_files(data_dir, "nxm.log")
    for directory in (state_dir, data_dir):
        try:
            directory.rmdir()
        except OSError:
            pass
    return {"removed": removed}


def _remove_runtime_artifacts(root):
    """Delete only OVLS-generated runtime files (logs, pycache) from the copy."""
    root = Path(root)
    for current, _dirs, files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path != root and path.name == "__pycache__" and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
            continue
        for name in files:
            if not _is_log_file(name, "ovls.log"):
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
        raise ValueError(prefix_search_message(prefix))

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
        state = manifest_status()
        if state["version_status"] != "ok" and state["expected_version"]:
            if dry_run:
                copy_result["status"] = "would_update"
                copy_result["version_repair"] = {
                    "current": state["version"],
                    "expected": state["expected_version"],
                }
            else:
                _write_text(root / VERSION_NAME, state["expected_version"] + "\n")
    else:
        copy_result = copy_tree(package, stamp=stamp, dry_run=dry_run)
    package_ext = _find_extension_dir(package)
    if package_ext is None:
        package_ext = _find_extension_dir(root) or (package / EXTENSION_TREE)
    if dry_run:
        install_result = installer.install(
            str(target_prefix),
            str(package_ext),
            dry_run=True,
            with_desktop=False,
        )
        handler = desktop.install_handler(
            str(target_prefix),
            str(package),
            dry_run=True,
            ovls_py=str(installed_entry_point() if same else (root / OVLS_PY)),
        )
        return {
            "status": install_result.get("status", "ok"),
            "dry_run": True,
            "prefix": str(target_prefix),
            "copy": copy_result,
            "installation": install_result,
            "nxm_handler": handler,
            "shortcuts": shortcuts.install(dry_run=True),
        }

    if not dry_run:
        try:
            _cleanup_orphaned_staging_dirs(root.parent)
        except Exception:
            pass
    installed_ext = _find_extension_dir(root) or package_ext
    try:
        install_result = installer.install(
            str(target_prefix),
            str(installed_ext),
            backup_root=_backup_root(stamp),
            with_desktop=False,
        )
        handler = desktop.install_handler(
            str(target_prefix),
            str(package),
            ovls_py=str(installed_entry_point()),
        )
        state = manifest_status()
        if state["status"] != "ok":
            raise ValueError(
                "Installed copy failed post-install verification "
                f"({state['status']}, version={state['version']}, "
                f"expected={state['expected_version']}, "
                f"missing={state['missing']}, modified={state['modified']}, "
                f"unsafe={state['unsafe']})"
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
        "status": install_result.get("status", "ok"),
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
    extension_dir = _find_extension_dir(app_root()) or (app_root() / EXTENSION_TREE)
    if target_prefix is None:
        return {
            "status": "attention_required",
            "reason": "prefix_not_found",
            "message": prefix_search_message(prefix),
            "prefix": None,
            "copy": copy_state,
            "shortcuts": shortcut_state,
            "nxm_handler": {"wrapper": str(wrapper), "autonomous": autonomous},
        }
    try:
        installed = installer.verify(
            str(target_prefix),
            str(extension_dir),
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
                prefix_search_message(prefix)
                + " The OVLS code copy and NXM integration were kept."
            ),
            "copy_kept": True,
        }
    if not extension_installer.is_installed(target_prefix):
        return {
            "status": "blocked",
            "reason": "ovls_not_installed",
            "message": (
                "OVLS is not installed in this prefix: "
                f"{target_prefix}. The OVLS code copy, shortcuts, NXM handler "
                "and configuration were kept."
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
    logs_result = remove_user_logs()
    remove_config()
    return {
        "status": "ok",
        "prefix": str(target_prefix),
        "installation": installed,
        "shortcuts": shortcuts_result,
        "copy": copy_result,
        "logs": logs_result,
    }
