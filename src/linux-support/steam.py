import os
import subprocess
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
    """Vortex prefixes under Steam ``compatdata/*`` for every library (read-only).

    Steam may register additional libraries on other disks or SD cards in
    ``libraryfolders.vdf``. Scan each of them, but never leave the Steam library
    layout and never scan arbitrary directories.
    """
    root = steam_root or find_steam_root()
    if not root:
        return []
    libraries = read_library_folders(root)
    found = []
    seen_libraries = set()
    seen_prefixes = set()
    for library in libraries:
        try:
            library_path = Path(library).expanduser()
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if not library_path.is_dir():
            continue
        try:
            library_key = str(library_path.resolve())
        except OSError:
            library_key = str(library_path)
        if library_key in seen_libraries:
            continue
        seen_libraries.add(library_key)

        compatdata = library_path / "steamapps" / "compatdata"
        if not compatdata.is_dir():
            continue
        try:
            entries = list(compatdata.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir() or not is_vortex_prefix(entry):
                    continue
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved in seen_prefixes:
                continue
            seen_prefixes.add(resolved)
            found.append(resolved)
    return sorted(found)


def prefix_library(prefix, steam_root=None):
    """Return the Steam library root containing ``prefix``, if it is known.

    The result is derived from ``read_library_folders()``; arbitrary directories
    outside registered Steam libraries are not accepted.
    """
    if not prefix:
        return None
    root = steam_root or find_steam_root()
    if not root:
        return None
    try:
        prefix_path = Path(prefix).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        root_path = Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        root_path = Path(root).expanduser()

    for library in read_library_folders(root_path):
        try:
            library_path = Path(library).expanduser()
            try:
                library_path = library_path.resolve()
            except OSError:
                pass
            if not library_path.is_dir():
                continue
            compatdata = library_path / "steamapps" / "compatdata"
            try:
                prefix_path.relative_to(compatdata)
            except ValueError:
                continue
            return library_path
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return None


_STORAGE_SD = "SD card"
_STORAGE_INTERNAL = "Internal storage"
_STORAGE_UNKNOWN = "Storage type unknown"

_BLOCK_ROOT = "/sys/class/block"
_MMC_DEVICES_ROOT = "/sys/bus/mmc/devices"


def _findmnt_field(path, field):
    """Return one ``findmnt`` field for the filesystem containing ``path``.

    ``-v`` (``--nofsroot``) suppresses the ``[/subvolume]`` suffix that findmnt
    otherwise prints for btrfs subvolumes and bind mounts.
    """
    try:
        result = subprocess.run(
            ["findmnt", "-v", "-T", str(path), "-no", field],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def _findmnt_source(path):
    """Return the ``findmnt`` SOURCE for the filesystem containing ``path``."""
    return _findmnt_field(path, "SOURCE")


def findmnt_target(path):
    """Mount point (TARGET) of the filesystem containing ``path``, or "".

    Used to identify the real volume root of a Steam library. An empty result
    means the mount point cannot be determined and must never be guessed.
    """
    return _findmnt_field(path, "TARGET")


def _block_device_name(source):
    """Return a kernel block device name for a ``findmnt`` SOURCE value."""
    source = (source or "").strip()
    if source.startswith("/dev/"):
        # Defensive fallback for a btrfs/bind suffix if -v was unavailable.
        # Malformed bracketed sources are rejected, never guessed.
        if "[" in source or "]" in source:
            marker = source.find("[/")
            if marker < 0 or not source.endswith("]"):
                return ""
            source = source[:marker]
        if not source.startswith("/dev/") or len(source) == len("/dev/"):
            return ""
        # /dev/mapper/... resolves to dm-N; a missing device node keeps its
        # basename, which is harmless for classification.
        return os.path.basename(os.path.realpath(source)) or ""
    if source and "/" not in source:
        return source
    return ""


def _mmc_card_type(block_name):
    """Read the kernel ``type`` of the MMC card backing a block device.

    A partition such as ``mmcblk0p1`` lives below the card device that owns
    the ``type`` attribute, so both are resolved through sysfs. No mount
    path, Wine drive letter or device-name guess is involved.
    """
    if not block_name:
        return None
    block_path = os.path.join(_BLOCK_ROOT, block_name)
    if not os.path.exists(block_path):
        return None
    try:
        block_real = os.path.realpath(block_path)
        cards = sorted(Path(_MMC_DEVICES_ROOT).iterdir())
    except OSError:
        return None
    for card in cards:
        try:
            card_real = os.path.realpath(card)
        except OSError:
            continue
        if block_real != card_real and not block_real.startswith(card_real + os.sep):
            continue
        try:
            return (card / "type").read_text(encoding="utf-8").strip().upper()
        except OSError:
            return None
    return None


def _udev_id_path(name):
    """Return udev's ``ID_PATH`` for a block device, if available."""
    if not name:
        return ""
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", f"--path={_BLOCK_ROOT}/{name}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("ID_PATH="):
            return line.split("=", 1)[1].strip()
    return ""


def _internal_nvme(id_path):
    """Confirm a PCI-attached (non-USB) NVMe device from udev's ID_PATH."""
    lowered = (id_path or "").casefold()
    return lowered.startswith("pci-") and "nvme" in lowered and "usb" not in lowered


def _classify_storage_device(block_name, id_path=""):
    """Classify storage by the kernel MMC card type and internal NVMe path.

    ``ID_PATH`` is used only to confirm internal NVMe. An SD card is never
    inferred from a mount path, a device name or an ``mmc`` substring.
    """
    card_type = _mmc_card_type(block_name)
    if card_type == "SD":
        return _STORAGE_SD
    if card_type == "MMC":
        return _STORAGE_INTERNAL
    if _internal_nvme(id_path):
        return _STORAGE_INTERNAL
    return _STORAGE_UNKNOWN


def storage_device_type(path):
    """Determine the physical storage type for the filesystem containing path.

    The Prefix path is mapped to a block device with ``findmnt -T``. SD and
    eMMC are identified by the kernel MMC card ``type`` attribute; internal
    NVMe is confirmed by udev ``ID_PATH``. Everything else stays unknown.
    """
    block_name = _block_device_name(_findmnt_source(path))
    if not block_name:
        return _STORAGE_UNKNOWN
    return _classify_storage_device(block_name, _udev_id_path(block_name))



def prefix_location(prefix, steam_root=None):
    """Return the compact physical storage type for a Prefix."""
    return storage_device_type(prefix)

def proton_display_name(name):
    """Return a compact Proton label for UI display."""
    if not name:
        return "Proton not detected"
    lowered = str(name).casefold()
    if "experimental" in lowered:
        return "Proton Experimental"
    if "proton 11" in lowered or "proton-11" in lowered or "proton 11.0" in lowered:
        return "Proton 11.0"
    if "ge-proton" in lowered or "proton-ge" in lowered or "proton ge" in lowered:
        return "Proton-GE Latest"
    if "hotfix" in lowered:
        return "Proton Hotfix"
    return str(name)


def prefix_display_label(prefix, steam_root=None):
    """Return the agreed compact label for a Prefix."""
    root = steam_root or find_steam_root()
    proton = proton_display_name(selected_proton_name(prefix, root))
    location = prefix_location(prefix, root)
    return f"{proton} — {location}"
def _config_info_proton_name(prefix, root):
    """Read the Proton tool Steam recorded in compatdata/<appid>/config_info.

    Steam writes the tool that actually created the prefix there even when
    ``CompatToolMapping`` has no entry (common for non-Steam shortcuts that use
    Steam's default tool). Only the installed tool directory name is used.
    """
    try:
        lines = (Path(prefix) / "config_info").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None
    root_path = Path(root).expanduser().resolve()
    for line in lines:
        text = line.strip()
        if not text.startswith(str(root_path)):
            continue
        try:
            parts = Path(text).relative_to(root_path).parts
        except ValueError:
            continue
        for marker in ("common", "compatibilitytools.d"):
            if marker not in parts:
                continue
            index = parts.index(marker)
            if index + 1 >= len(parts) or parts[index + 1] == "files":
                continue
            name = parts[index + 1]
            tool_path = root_path.joinpath(*parts[:index + 2])
            if (tool_path / "proton").is_file():
                return name
    return None


def selected_proton_name(prefix=None, steam_root=None):
    """Return the Proton tool actually selected for this prefix's appid.

    Steam's explicit selection lives in ``config/config.vdf``
    (``CompatToolMapping``). When that entry is missing, the tool recorded in
    the prefix's ``config_info`` is used. Both sources are read-only.
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
        text = ""
    if text:
        marker = text.find('"CompatToolMapping"')
        if marker >= 0:
            chunk = text[marker:marker + 20000]
            import re
            mapping = re.search(r'"' + re.escape(appid) + r'"\s*\{([^}]*)\}', chunk)
            if mapping:
                name = re.search(r'"name"\s+"([^"]+)"', mapping.group(1))
                if name:
                    return name.group(1)
    return _config_info_proton_name(prefix, root)


def read_library_folders(steam_root, require_exists=True):
    """Registered Steam libraries, optionally including unavailable ones.

    The default keeps the historical behaviour of returning only existing
    directories. ``require_exists=False`` is used by host.json generation so a
    temporarily unmounted volume can be recognised from the registration and
    matched against a previously generated volume instead of being guessed.
    """
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
            if (not require_exists or os.path.isdir(path)) and path not in libraries:
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
