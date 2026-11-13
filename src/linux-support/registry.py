"""Repair the Vortex uninstall registration inside a Wine/Proton prefix.

Vortex bundles the ``test-setup`` extension with the startup health-check
``uninstall-entry``. It opens
``HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\57979c68-f490-55b8-8fed-8b017a5af2fe``
and requires ``UninstallString`` to be a ``REG_SZ`` whose first token points at
the directory of the running Vortex application. Any failure is reported as
``No Vortex uninstall key`` (RU: ``Нет ключа удаления Vortex``) on every start.

The official NSIS installer normally writes that key. If its install section
never completes (for example it is interrupted while the bundled Visual C++
redistributable / .NET runtime is installed), the application files and
``Uninstall Vortex.exe`` are already on disk, but the registry entry and the
shortcuts were never written. This module repairs exactly that value.

The registry file is edited in place, surgically: only the target value inside
the target key is created or corrected. Every other byte of ``system.reg`` is
kept unchanged, a verified backup is written first, and the new file is
published with ``os.replace`` so a failure can never leave a half-written
registry behind.
"""

import hashlib
import ntpath
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\57979c68-f490-55b8-8fed-8b017a5af2fe"
)
VALUE_NAME = "UninstallString"

_SECTION_RE = re.compile(r"^\[(.*?)\]\s+\d+\s*$")
_TOKEN_RE = re.compile(r'\w+|"(?:\\.|[^"])+"')
_FILETIME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


class RegistryError(OSError):
    """The Vortex uninstall registration could not be read or repaired."""


# ---------------------------------------------------------------------------
# Windows path helpers
# ---------------------------------------------------------------------------

def _windows_path(prefix, host_path):
    """Translate a host path inside the prefix into a Windows path."""
    prefix = Path(prefix)
    pfx = prefix / "pfx"
    drive_c = pfx / "drive_c"
    host = Path(host_path).resolve()

    try:
        relative = host.relative_to(drive_c.resolve())
    except ValueError:
        pass
    else:
        return "C:\\" + str(relative).replace("/", "\\")

    dosdevices = pfx / "dosdevices"
    best = None
    if dosdevices.is_dir():
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


def find_vortex_install(prefix_path):
    """Locate Vortex inside the prefix without assuming a fixed directory.

    The official installer defaults to ``C:\\Program Files\\Vortex``, but the
    user may pick another directory. Known locations are checked first, then a
    shallow scan of ``drive_c`` keeps the lookup cheap on large prefixes.
    """
    prefix = Path(prefix_path).expanduser().resolve()
    drive_c = prefix / "pfx" / "drive_c"
    candidates = [
        drive_c / "Program Files" / "Vortex",
        drive_c / "Program Files (x86)" / "Vortex",
    ]
    if drive_c.is_dir():
        for exe in sorted(drive_c.glob("*/*/Vortex.exe")):
            candidates.append(exe.parent)

    seen = set()
    for directory in candidates:
        exe = directory / "Vortex.exe"
        if exe.is_symlink() or not exe.is_file():
            continue
        resolved = str(directory.resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        windows_dir = _windows_path(prefix, directory)
        if windows_dir is None:
            continue
        uninstaller = directory / "Uninstall Vortex.exe"
        if uninstaller.is_symlink() or not uninstaller.is_file():
            uninstaller = None
        return {
            "install_dir": str(directory),
            "exe": str(exe),
            "uninstaller": str(uninstaller) if uninstaller else None,
            "windows_install_dir": windows_dir,
            "uninstall_string": (
                f'"{windows_dir}\\{uninstaller.name}"' if uninstaller else None
            ),
        }
    return None


# ---------------------------------------------------------------------------
# Registry file parsing
# ---------------------------------------------------------------------------

def _read_registry(path):
    # surrogateescape keeps a byte-for-byte round trip for anything that is not
    # valid UTF-8, so untouched parts of the file are never re-encoded.
    return Path(path).read_bytes().decode("utf-8", "surrogateescape")


def _key_header(key):
    return "[" + key.replace("\\", "\\\\") + "]"


def _section_regex(key):
    return re.compile(
        r"^\[" + re.escape(_key_header(key)[1:-1]) + r"\]\s+\d+\s*$",
        re.IGNORECASE,
    )


def _find_section(lines, key):
    pattern = _section_regex(key)
    for index, line in enumerate(lines):
        if pattern.match(line.rstrip("\r\n")):
            end = index + 1
            while end < len(lines) and not _SECTION_RE.match(lines[end].rstrip("\r\n")):
                end += 1
            return index, end
    return None


def _find_value_line(lines, section, name):
    start, end = section
    prefix = f'"{name.lower()}"='
    for index in range(start + 1, end):
        if lines[index].lower().startswith(prefix):
            return index
    return None


def _decode_reg_string(text):
    """Decode a Wine registry string literal (``"..."``) into plain text."""
    text = text.lstrip()
    if not text.startswith('"'):
        return None
    out = []
    index = 1
    escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text):
                return None
            out.append(escapes.get(text[index], text[index]))
            index += 1
        elif char == '"':
            return "".join(out)
        else:
            out.append(char)
            index += 1
    return None


def _encode_reg_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_value_line(line):
    _name, separator, raw = line.partition("=")
    if not separator:
        return None
    return _decode_reg_string(raw)


def _newline(text):
    return "\r\n" if "\r\n" in text else "\n"


def _render_text(text, expected):
    """Return ``text`` with only the target value created or corrected."""
    lines = text.splitlines(keepends=True)
    if not lines:
        raise RegistryError("Cannot repair an empty registry file")
    newline = _newline(text)
    value_line = f'"{VALUE_NAME}"={_encode_reg_string(expected)}{newline}'

    section = _find_section(lines, UNINSTALL_KEY)
    if section is not None:
        index = _find_value_line(lines, section, VALUE_NAME)
        if index is not None:
            lines[index] = value_line
        else:
            insert_at = section[0] + 1
            if insert_at < len(lines) and lines[insert_at].startswith("#time="):
                insert_at += 1
            lines.insert(insert_at, value_line)
        return "".join(lines)

    if not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    text = "".join(lines)
    if not text.endswith(newline + newline):
        text += newline if text.endswith(newline) else newline + newline
    return (
        text
        + f"{_key_header(UNINSTALL_KEY)} {int(time.time())}{newline}"
        + f"#time={format(int((time.time() + _FILETIME_EPOCH_OFFSET) * 10_000_000), 'x')}{newline}"
        + value_line
        + newline
    )


def _read_value_from_file(path):
    """Read only the target value; used to verify a completed write."""
    text = _read_registry(path)
    lines = text.splitlines(keepends=True)
    section = _find_section(lines, UNINSTALL_KEY)
    if section is None:
        return None
    index = _find_value_line(lines, section, VALUE_NAME)
    if index is None:
        return None
    return _parse_value_line(lines[index])


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------

def _normalise_windows_path(path):
    return ntpath.normpath(path.replace("/", "\\")).casefold()


def uninstall_target(value):
    """Return the executable referenced by the first token of a value."""
    if not value:
        return None
    match = _TOKEN_RE.match(value)
    if match is None:
        return None
    token = match.group(0)
    if token.startswith('"') and token.endswith('"'):
        token = token[1:-1].replace('\\"', '"')
    return token


def _value_matches(value, expected):
    actual_target = uninstall_target(value)
    expected_target = uninstall_target(expected)
    if actual_target is None or expected_target is None:
        return False
    return _normalise_windows_path(actual_target) == _normalise_windows_path(expected_target)


# ---------------------------------------------------------------------------
# State / repair
# ---------------------------------------------------------------------------

def read_state(prefix_path):
    """Report the current registration without modifying anything."""
    prefix = Path(prefix_path).expanduser().resolve()
    system_reg = prefix / "pfx" / "system.reg"
    vortex = find_vortex_install(prefix)
    state = {
        "prefix": str(prefix),
        "registry": str(system_reg),
        "key": UNINSTALL_KEY,
        "value_name": VALUE_NAME,
        "registry_present": system_reg.is_file(),
        "key_present": False,
        "value_present": False,
        "value": None,
        "expected": vortex["uninstall_string"] if vortex else None,
        "vortex_install_dir": vortex["install_dir"] if vortex else None,
        "status": "ok",
    }

    if not state["registry_present"]:
        state["status"] = "no_registry"
        return state

    lines = _read_registry(system_reg).splitlines(keepends=True)
    section = _find_section(lines, UNINSTALL_KEY)
    if section is not None:
        state["key_present"] = True
        index = _find_value_line(lines, section, VALUE_NAME)
        if index is not None:
            state["value_present"] = True
            state["value"] = _parse_value_line(lines[index])

    if vortex is None:
        state["status"] = "no_vortex"
    elif vortex["uninstall_string"] is None:
        state["status"] = "no_uninstaller"
    elif not state["key_present"]:
        state["status"] = "missing_key"
    elif not state["value_present"]:
        state["status"] = "missing_value"
    elif _value_matches(state["value"], vortex["uninstall_string"]):
        state["status"] = "ok"
    else:
        state["status"] = "mismatch"
    return state


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup(path, backup_dir=None):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(backup_dir) if backup_dir is not None else path.parent
    directory.mkdir(parents=True, exist_ok=True)
    base = f"{path.name}.ovls-backup-{stamp}"
    candidate = directory / base
    counter = 1
    while candidate.exists():
        candidate = directory / f"{base}-{counter}"
        counter += 1
    try:
        shutil.copy2(path, candidate)
    except OSError as exc:
        if candidate.exists():
            candidate.unlink()
        raise RegistryError(f"Cannot back up {path}: {exc}") from exc
    if _sha256(candidate) != _sha256(path):
        raise RegistryError(f"Backup verification failed: {candidate}")
    return str(candidate)


def _write_atomic(path, data):
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".system.reg-", suffix=".ovls.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        shutil.copymode(path, temp_path)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    try:
        dir_descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_descriptor)
    except OSError:
        # The rename is already visible; durability of the directory entry is
        # best effort on filesystems that reject directory fsync.
        pass
    finally:
        os.close(dir_descriptor)


def _restore(path, backup):
    data = Path(backup).read_bytes()
    _write_atomic(path, data)


def ensure_uninstall_key(prefix_path, dry_run=False, backup_dir=None):
    """Create or correct the Vortex uninstall registration.

    Returns a result dictionary. Nothing is written when the registration is
    already correct, when Vortex (or its uninstaller) is missing, or on a dry
    run. A failed backup, write or post-write verification raises
    :class:`RegistryError` after the previous registry content is restored.
    """
    prefix = Path(prefix_path).expanduser().resolve()
    state = read_state(prefix)
    result = {
        "status": state["status"],
        "action": "ok",
        "registry": state["registry"],
        "key": UNINSTALL_KEY,
        "value_name": VALUE_NAME,
        "value": state["value"],
        "expected": state["expected"],
        "backup": None,
    }

    if state["status"] == "ok":
        return result
    if state["status"] == "no_vortex":
        result["action"] = "skipped_no_vortex"
        result["message"] = "Vortex.exe not found in prefix; nothing to register"
        return result
    if state["status"] == "no_uninstaller":
        result["action"] = "skipped_no_uninstaller"
        result["message"] = (
            "Uninstall Vortex.exe is missing next to Vortex.exe; "
            "not registering a missing uninstaller"
        )
        return result
    if state["status"] == "no_registry":
        result["action"] = "skipped_no_registry"
        result["message"] = "pfx/system.reg not found; prefix has no Wine registry"
        return result

    expected = state["expected"]
    new_text = _render_text(_read_registry(state["registry"]), expected)
    if dry_run:
        result["action"] = (
            "would_create" if state["status"] == "missing_key" else "would_update"
        )
        result["expected"] = expected
        return result

    system_reg = Path(state["registry"])
    if system_reg.is_symlink():
        raise RegistryError(f"Refusing to rewrite a symlinked registry: {system_reg}")

    backup = _backup(system_reg, backup_dir)
    result["backup"] = backup
    try:
        _write_atomic(system_reg, new_text.encode("utf-8", "surrogateescape"))
        written = _read_value_from_file(system_reg)
        if not _value_matches(written, expected):
            raise RegistryError(
                f"Registry verification failed: expected {expected!r}, got {written!r}"
            )
    except OSError as exc:
        try:
            _restore(system_reg, backup)
        except OSError as restore_exc:
            raise RegistryError(
                f"Failed to register the Vortex uninstall key ({exc}); "
                f"restoring {system_reg} from {backup} also failed: {restore_exc}"
            ) from exc
        raise RegistryError(
            f"Failed to register the Vortex uninstall key: {exc}; "
            f"{system_reg} was restored from {backup}"
        ) from exc

    result.update(
        status="ok",
        action="created" if state["status"] == "missing_key" else "updated",
        value=written,
    )
    return result
