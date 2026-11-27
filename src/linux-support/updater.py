"""GitHub Releases updater for OVLS (public repository, no authentication).

This module discovers the latest stable release of
`Dimonchik-UA/Vortex-Linux-Support`, verifies release artifacts, and prepares
a validated package directory that can be passed to ``package.install(...)``.

Security model
--------------
1. Release metadata and assets are fetched over HTTPS from the public GitHub
   repository.
2. The downloaded installer archive must match the SHA-256 value published in
   the release checksums asset (``SHA256SUMS``).
3. The archive is extracted with strict safety checks (path traversal,
   symlinks/hardlinks, special files, size limits, duplicate names).
4. The extracted package version must match the release tag/version.

Digital signatures, GPG, and public/private keys are not used. Integrity of the
downloaded archive is verified by SHA-256 before extraction/installation.

Nothing is installed until all checks pass. No downloaded file is executed
during verification. Temporary files are removed on both success and failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import core
import desktop
import tempfiles

# ---------------------------------------------------------------------------
# Public configuration
# ---------------------------------------------------------------------------

REPO_OWNER = "Dimonchik-UA/Vortex-Linux-Support".split("/")[0]
REPO_NAME = "Dimonchik-UA/Vortex-Linux-Support".split("/")[1]
REPO_SLUG = f"{REPO_OWNER}/{REPO_NAME}"
API_BASE = "https://api.github.com"
RELEASES_LATEST_URL = f"{API_BASE}/repos/{REPO_SLUG}/releases/latest"

# Expected release asset names (fixed contract)
INSTALLER_ASSET_NAME = "OVLS-Installer.tar.gz"
CHECKSUMS_ASSET_NAME = "SHA256SUMS"

# Cache
CACHE_FILENAME = "update-check.json"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# Network limits
DEFAULT_TIMEOUT_SECONDS = 10
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024  # 512 MiB download limit
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024  # 1 GiB total extracted content
MAX_EXTRACTED_FILES = 20_000
MAX_ARCHIVE_MEMBERS = 20_000

# Version/tag format: vX.Y.Z or X.Y.Z with optional pre-release/build ignored here
_VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:[-+].*)?$"
)




# ---------------------------------------------------------------------------
# Errors and result types
# ---------------------------------------------------------------------------


class UpdaterError(Exception):
    """Base error for updater failures."""


class NetworkError(UpdaterError):
    """Network/HTTP failures while contacting GitHub or downloading assets."""


class ReleaseNotFoundError(UpdaterError):
    """No suitable release or required assets were found."""


class VerificationError(UpdaterError):
    """Integrity or authenticity verification failed."""


class PackageFormatError(UpdaterError):
    """Downloaded/extracted package is malformed or unsafe."""


@dataclass
class UpdateCheckResult:
    status: str  # "up_to_date" | "update_available" | "unknown"
    current_version: Optional[str] = None
    latest_version: Optional[str] = None
    tag_name: Optional[str] = None
    release_url: Optional[str] = None
    installer_url: Optional[str] = None
    checksums_url: Optional[str] = None
    installer_name: Optional[str] = None
    installer_size: Optional[int] = None
    checked_at: Optional[float] = None
    error: Optional[str] = None
    from_cache: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "tag_name": self.tag_name,
            "release_url": self.release_url,
            "installer_url": self.installer_url,
            "checksums_url": self.checksums_url,
            "installer_name": self.installer_name,
            "installer_size": self.installer_size,
            "checked_at": self.checked_at,
            "error": self.error,
            "from_cache": self.from_cache,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UpdateCheckResult":
        return cls(
            status=str(data.get("status") or "unknown"),
            current_version=data.get("current_version"),
            latest_version=data.get("latest_version"),
            tag_name=data.get("tag_name"),
            release_url=data.get("release_url"),
            installer_url=data.get("installer_url"),
            checksums_url=data.get("checksums_url"),
            installer_name=data.get("installer_name"),
            installer_size=data.get("installer_size"),
            checked_at=data.get("checked_at"),
            error=data.get("error"),
            from_cache=bool(data.get("from_cache", False)),
        )


@dataclass
class PreparedRelease:
    """Validated release package ready for ``package.install``."""

    package_root: Path
    version: str
    tag_name: str
    temp_dir: Path
    archive_path: Path
    checksums_path: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def parse_version(value: Optional[str]) -> Optional[tuple[int, int, int]]:
    """Parse ``X.Y.Z`` or ``vX.Y.Z`` into a comparable tuple.

    Pre-release/build suffixes are ignored for comparison purposes. Returns
    ``None`` for invalid or missing values.
    """
    if value is None:
        return None
    text = str(value).strip()
    match = _VERSION_RE.match(text)
    if not match:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def normalize_version(value: Optional[str]) -> Optional[str]:
    parsed = parse_version(value)
    if parsed is None:
        return None
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"


def compare_versions(left: Optional[str], right: Optional[str]) -> Optional[int]:
    """Compare two versions.

    Returns -1 if left < right, 0 if equal, 1 if left > right, or ``None`` if
    either version cannot be parsed.
    """
    lpar = parse_version(left)
    rpar = parse_version(right)
    if lpar is None or rpar is None:
        return None
    if lpar < rpar:
        return -1
    if lpar > rpar:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Local version / cache helpers
# ---------------------------------------------------------------------------


def _state_dir() -> Path:
    return desktop.state_home() / "ovls"


def cache_path() -> Path:
    return _state_dir() / CACHE_FILENAME


def read_installed_version() -> Optional[str]:
    """Return the installed OVLS version from the installed copy, if present."""
    try:
        import package  # local import to avoid heavy module side effects

        version_file = package.app_root() / package.VERSION_NAME
        text = version_file.read_text(encoding="utf-8", errors="replace").strip()
        normalized = normalize_version(text)
        return normalized or (text if text else None)
    except Exception:
        return None


def current_local_version() -> Optional[str]:
    """Best-effort current local version (installed copy preferred)."""
    return read_installed_version() or normalize_version(core.VERSION) or core.VERSION


def load_cache(path: Optional[Path] = None, now: Optional[float] = None, ttl: int = DEFAULT_CACHE_TTL_SECONDS) -> Optional[UpdateCheckResult]:
    path = Path(path) if path is not None else cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    result = UpdateCheckResult.from_dict(data)
    result.from_cache = True
    if now is None:
        now = time.time()
    checked_at = result.checked_at
    if not isinstance(checked_at, (int, float)):
        return None
    if ttl >= 0 and (now - float(checked_at)) > ttl:
        return None
    return result


def save_cache(result: UpdateCheckResult, path: Optional[Path] = None) -> None:
    path = Path(path) if path is not None else cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result.to_dict())
    payload["from_cache"] = False
    tempfiles.cleanup_orphaned_files(path.parent, ".update-check-")
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".update-check-{os.getpid()}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def invalidate_cache(path: Optional[Path] = None) -> None:
    """Remove the cached update check so the next check fetches fresh data."""
    path = Path(path) if path is not None else cache_path()
    path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_get_json(url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS, opener: Optional[Callable] = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"OVLS-Updater/{core.VERSION}",
        },
        method="GET",
    )
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise NetworkError(f"Timeout while requesting {url}") from exc
    except Exception as exc:
        raise NetworkError(f"Failed to fetch {url}: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise NetworkError(f"Invalid JSON from {url}") from exc


def _download_to_file(
    url: str,
    destination: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    expected_size: Optional[int] = None,
    opener: Optional[Callable] = None,
) -> tuple[int, str]:
    """Download URL to destination file, returning (size, sha256_hex).

    The hash is computed over the bytes actually written.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"OVLS-Updater/{core.VERSION}"},
        method="GET",
    )
    sha = hashlib.sha256()
    size = 0
    tempfiles.cleanup_orphaned_files(destination.parent, ".download-")
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".download-{os.getpid()}-", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(tmp_fd)
    try:
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response, open(tmp_name, "wb") as handle:
            # Best-effort early size guard from headers
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                declared = int(length)
                if expected_size is not None and declared != expected_size:
                    raise VerificationError(
                        f"Declared size mismatch for {destination.name}: expected {expected_size}, got {declared}"
                    )
                if declared > max_bytes:
                    raise VerificationError(
                        f"Download too large: {declared} bytes exceeds limit {max_bytes}"
                    )
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise VerificationError(
                        f"Download exceeds maximum allowed size ({max_bytes} bytes)"
                    )
                sha.update(chunk)
                handle.write(chunk)
        if expected_size is not None and size != expected_size:
            raise VerificationError(
                f"Downloaded size mismatch for {destination.name}: expected {expected_size}, got {size}"
            )
        os.replace(tmp_name, destination)
        return size, sha.hexdigest()
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"HTTP {exc.code} while downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"Network error while downloading {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise NetworkError(f"Timeout while downloading {url}") from exc
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Release parsing
# ---------------------------------------------------------------------------


def _select_asset(assets: Any, name: str) -> Optional[dict]:
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name:
            return asset
    return None


def parse_latest_release(release: Mapping[str, Any]) -> dict:
    """Extract required metadata from a GitHub release payload."""
    if not isinstance(release, Mapping):
        raise ReleaseNotFoundError("Invalid release payload")
    if release.get("draft"):
        raise ReleaseNotFoundError("Latest release is a draft")
    if release.get("prerelease"):
        raise ReleaseNotFoundError("Latest release is a prerelease")

    tag_name = release.get("tag_name")
    latest_version = normalize_version(tag_name) or normalize_version(release.get("name"))
    if not latest_version:
        raise ReleaseNotFoundError("Release has no parseable version/tag")

    assets = release.get("assets") or []
    installer = _select_asset(assets, INSTALLER_ASSET_NAME)
    checksums = _select_asset(assets, CHECKSUMS_ASSET_NAME)

    missing = [
        name
        for name, asset in (
            (INSTALLER_ASSET_NAME, installer),
            (CHECKSUMS_ASSET_NAME, checksums),
        )
        if asset is None
    ]
    if missing:
        raise ReleaseNotFoundError("Missing required release assets: " + ", ".join(missing))

    def _url(asset: dict) -> str:
        url = asset.get("browser_download_url")
        if not url:
            raise ReleaseNotFoundError(f"Asset {asset.get('name')} has no download URL")
        return str(url)

    def _size(asset: dict) -> Optional[int]:
        size = asset.get("size")
        return int(size) if isinstance(size, int) and size >= 0 else None

    return {
        "tag_name": str(tag_name) if tag_name is not None else None,
        "latest_version": latest_version,
        "release_url": str(release.get("html_url") or ""),
        "installer_url": _url(installer),
        "checksums_url": _url(checksums),
        "installer_name": str(installer.get("name") or INSTALLER_ASSET_NAME),
        "installer_size": _size(installer),
    }


# ---------------------------------------------------------------------------
# Checksum verification
# ---------------------------------------------------------------------------


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse SHA256SUMS content into {filename: hex_digest}.

    Supports common formats:
      <hash>  <filename>
      <hash> *<filename>
    """
    results: dict[str, str] = {}
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # hash + whitespace + optional mode char + filename
        match = re.match(r"^([A-Fa-f0-9]{64})[\s*]+(.+)$", line)
        if not match:
            continue
        digest = match.group(1).lower()
        filename = match.group(2).strip()
        # Remove leading path components if present (defensive)
        filename = Path(filename).name
        if filename:
            results[filename] = digest
    return results


def expected_checksum_for(checksums_text: str, filename: str) -> str:
    mapping = parse_sha256sums(checksums_text)
    digest = mapping.get(filename) or mapping.get(Path(filename).name)
    if not digest:
        raise VerificationError(f"No SHA-256 checksum found for {filename}")
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise VerificationError(f"Invalid SHA-256 checksum format for {filename}")
    return digest


def verify_sha256(file_path: Path, expected_hex: str) -> None:
    expected = str(expected_hex).strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise VerificationError("Invalid expected SHA-256 value")
    sha = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != expected:
        raise VerificationError(
            f"SHA-256 mismatch for {Path(file_path).name}: expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Safe extraction and package validation
# ---------------------------------------------------------------------------


def _is_within_directory(base: Path, target: Path) -> bool:
    base_real = base.resolve()
    target_real = target.resolve()
    try:
        return os.path.commonpath([str(base_real), str(target_real)]) == str(base_real)
    except ValueError:
        return False


def _safe_extract_tar(
    archive_path: Path,
    dest_dir: Path,
    max_total_bytes: int = MAX_EXTRACTED_BYTES,
    max_files: int = MAX_EXTRACTED_FILES,
    max_members: int = MAX_ARCHIVE_MEMBERS,
) -> Path:
    """Safely extract a .tar.gz archive into dest_dir and return extract root."""
    archive_path = Path(archive_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > max_members:
                raise PackageFormatError("Archive contains too many members")

            total_size = 0
            file_count = 0
            seen_names: set[str] = set()

            for member in members:
                name = member.name
                if not name:
                    raise PackageFormatError("Archive contains an empty member name")
                if name in seen_names:
                    raise PackageFormatError(f"Archive contains duplicate member: {name}")
                seen_names.add(name)

                # Normalize and reject unsafe names
                if name.startswith("/") or name.startswith("\\"):
                    raise PackageFormatError(f"Archive contains absolute path: {name}")
                # Reject Windows drive-like paths defensively
                if re.match(r"^[A-Za-z]:", name):
                    raise PackageFormatError(f"Archive contains drive path: {name}")
                parts = Path(name).parts
                if any(part == ".." for part in parts):
                    raise PackageFormatError(f"Archive contains path traversal: {name}")

                # Disallow links and special files entirely
                if member.issym() or member.islnk():
                    raise PackageFormatError(f"Archive contains link entry: {name}")
                if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                    raise PackageFormatError(f"Archive contains special device entry: {name}")
                if not (member.isdir() or member.isfile()):
                    raise PackageFormatError(f"Archive contains unsupported entry type: {name}")

                target_path = (dest_dir / name).resolve()
                if not _is_within_directory(dest_dir, target_path):
                    raise PackageFormatError(f"Archive extraction escapes destination: {name}")

                # Prevent writing through pre-existing symlinked parent components
                parent = dest_dir / Path(name).parent
                cur = dest_dir
                for part in Path(name).parent.parts:
                    cur = cur / part
                    if cur.exists() and cur.is_symlink():
                        raise PackageFormatError(f"Archive path traverses symlink: {name}")

                if member.isfile():
                    file_count += 1
                    if file_count > max_files:
                        raise PackageFormatError("Archive contains too many files")
                    if member.size < 0:
                        raise PackageFormatError(f"Archive contains invalid file size: {name}")
                    total_size += member.size
                    if total_size > max_total_bytes:
                        raise PackageFormatError("Archive extracted size exceeds limit")

            # Extract member-by-member after validation
            for member in members:
                target_path = (dest_dir / member.name).resolve()
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                # Ensure parent exists and is a safe directory
                target_path.parent.mkdir(parents=True, exist_ok=True)
                # Extra guard right before extraction
                if target_path.parent.is_symlink():
                    raise PackageFormatError(f"Refusing to extract through symlink: {member.name}")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise PackageFormatError(f"Cannot extract archive member: {member.name}")
                # Write atomically within destination
                tempfiles.cleanup_orphaned_files(target_path.parent, ".extract-")
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".extract-{os.getpid()}-", suffix=".tmp", dir=str(target_path.parent)
                )
                try:
                    with os.fdopen(fd, "wb") as out, extracted:
                        remaining = member.size
                        while remaining > 0:
                            chunk = extracted.read(min(DOWNLOAD_CHUNK_SIZE, remaining))
                            if not chunk:
                                break
                            out.write(chunk)
                            remaining -= len(chunk)
                        if remaining != 0:
                            raise PackageFormatError(f"Archive member size mismatch: {member.name}")
                    # Preserve reasonable permissions (no exec by default except handled later)
                    mode = member.mode & 0o777
                    if mode == 0:
                        mode = 0o644
                    os.chmod(tmp_name, mode)
                    os.replace(tmp_name, target_path)
                finally:
                    try:
                        if os.path.exists(tmp_name):
                            os.unlink(tmp_name)
                    except OSError:
                        pass
    except tarfile.TarError as exc:
        raise PackageFormatError(f"Failed to read archive: {exc}") from exc

    return dest_dir


def detect_package_root(extracted_dir: Path) -> Path:
    """Find the OVLS package root inside an extracted release directory."""
    extracted_dir = Path(extracted_dir)
    # Common case: archive contains a single top-level "OVLS Installer" directory
    direct_candidates = [
        extracted_dir,
        extracted_dir / "OVLS Installer",
        extracted_dir / "OVLS-Installer",
    ]
    for candidate in direct_candidates:
        if _looks_like_package_root(candidate):
            return candidate

    # Fallback: search one level deep
    try:
        children = [p for p in extracted_dir.iterdir() if p.is_dir()]
    except OSError:
        children = []
    for child in children:
        if _looks_like_package_root(child):
            return child
    raise PackageFormatError("Could not locate a valid OVLS package in extracted archive")


def _looks_like_package_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    install_sh = path / "install.sh"
    if not install_sh.is_file():
        return False
    # Accept either standalone layout (ovls/ovls.py) or repo-like layouts
    candidates = [
        path / "ovls" / "ovls.py",
        path / "src" / "linux-support" / "ovls.py",
        path / "Development" / "Linux_Support" / "ovls.py",
    ]
    return any(c.is_file() for c in candidates)


def read_package_version(package_root: Path) -> Optional[str]:
    """Read OVLS version from a package source tree."""
    package_root = Path(package_root)
    # Prefer core.py as authoritative for the code in the package
    core_candidates = [
        package_root / "ovls" / "core.py",
        package_root / "src" / "linux-support" / "core.py",
        package_root / "Development" / "Linux_Support" / "core.py",
    ]
    for core_path in core_candidates:
        try:
            text = core_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'^\s*VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if m:
            return normalize_version(m.group(1)) or m.group(1).strip()
    # Fallback: VERSION file in package root
    for vf in (package_root / "VERSION", package_root / "ovls" / "VERSION"):
        try:
            text = vf.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return normalize_version(text) or text
    return None


def validate_package_root(package_root: Path, expected_version: Optional[str] = None) -> Path:
    package_root = Path(package_root)
    if not _looks_like_package_root(package_root):
        raise PackageFormatError(f"Invalid OVLS package structure: {package_root}")

    # Ensure key files exist and are regular files (not symlinks)
    install_sh = package_root / "install.sh"
    if install_sh.is_symlink() or not install_sh.is_file():
        raise PackageFormatError("Package install.sh is missing or unsafe")

    # Locate code dir
    code_dirs = [
        package_root / "ovls",
        package_root / "src" / "linux-support",
        package_root / "Development" / "Linux_Support",
    ]
    code_dir = next((d for d in code_dirs if (d / "ovls.py").is_file()), None)
    if code_dir is None:
        raise PackageFormatError("Package does not contain ovls.py")
    if (code_dir / "ovls.py").is_symlink():
        raise PackageFormatError("Package ovls.py is unsafe")

    version = read_package_version(package_root)
    if expected_version:
        nv_expected = normalize_version(expected_version) or expected_version
        nv_actual = normalize_version(version) or version
        if nv_actual != nv_expected:
            raise PackageFormatError(
                f"Package version mismatch: expected {nv_expected}, got {nv_actual or 'unknown'}"
            )
    elif version is None:
        raise PackageFormatError("Cannot determine package version")

    return package_root


# ---------------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------------


def check_for_updates(
    repo: str = REPO_SLUG,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
    force: bool = False,
    now: Optional[float] = None,
    cache_file: Optional[Path] = None,
    opener: Optional[Callable] = None,
) -> UpdateCheckResult:
    """Check GitHub Releases for a newer stable OVLS version.

    On network/API errors returns status ``unknown`` (never "no updates").
    Uses XDG state cache unless ``force`` is True.
    """
    if now is None:
        now = time.time()
    cache_file = Path(cache_file) if cache_file is not None else cache_path()

    if not force:
        cached = load_cache(cache_file, now=now, ttl=cache_ttl)
        if cached is not None:
            return cached

    current_version = current_local_version()
    owner, name = (repo.split("/", 1) + [None])[:2]
    if not owner or not name:
        result = UpdateCheckResult(
            status="unknown",
            current_version=current_version,
            checked_at=now,
            error="Invalid repository slug",
        )
        save_cache(result, cache_file)
        return result

    url = f"{API_BASE}/repos/{owner}/{name}/releases/latest"
    try:
        payload = _http_get_json(url, timeout=timeout, opener=opener)
        meta = parse_latest_release(payload)
        latest = meta["latest_version"]
        cmp = compare_versions(current_version, latest)
        if cmp is None:
            status = "unknown"
        elif cmp < 0:
            status = "update_available"
        else:
            status = "up_to_date"
        result = UpdateCheckResult(
            status=status,
            current_version=current_version,
            latest_version=latest,
            tag_name=meta.get("tag_name"),
            release_url=meta.get("release_url"),
            installer_url=meta.get("installer_url"),
            checksums_url=meta.get("checksums_url"),
            installer_name=meta.get("installer_name"),
            installer_size=meta.get("installer_size"),
            checked_at=now,
            error=None,
            from_cache=False,
        )
    except (NetworkError, ReleaseNotFoundError, UpdaterError) as exc:
        result = UpdateCheckResult(
            status="unknown",
            current_version=current_version,
            checked_at=now,
            error=str(exc),
            from_cache=False,
        )
    except Exception as exc:
        result = UpdateCheckResult(
            status="unknown",
            current_version=current_version,
            checked_at=now,
            error=str(exc),
            from_cache=False,
        )

    try:
        save_cache(result, cache_file)
    except Exception:
        pass
    return result


def _cleanup_orphaned_update_dirs(base_tmp=None):
    """Remove OVLS update directories only when their tagged owner is gone."""
    search_dir = Path(base_tmp) if base_tmp is not None else Path(tempfile.gettempdir())
    tempfiles.cleanup_orphaned_dirs(search_dir, "ovls-update-")


def prepare_release_package(
    update_info: Optional[UpdateCheckResult] = None,
    temp_root: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Optional[Callable] = None,
) -> PreparedRelease:
    """Download, verify, and extract a release package for installation.

    ``update_info`` should come from ``check_for_updates()``. If omitted, a
    fresh check is performed. The function never calls ``package.install()``.

    Verification flow:
      1. Download ``SHA256SUMS``.
      2. Download installer archive and verify SHA-256 against ``SHA256SUMS``
         (and expected size when provided) before extraction.
      3. Safely extract archive and validate package structure/version.
    """
    if update_info is None:
        update_info = check_for_updates(timeout=timeout, opener=opener)

    # Clean up orphaned temp directories from previous crashed runs.
    try:
        _cleanup_orphaned_update_dirs(temp_root)
    except Exception:
        pass

    if update_info.status != "update_available":
        # Safer to require update_available or explicit URLs+version.
        if not (
            update_info.installer_url
            and update_info.checksums_url
            and update_info.latest_version
        ):
            raise UpdaterError("No verified update package is available to prepare")

    for attr in ("installer_url", "checksums_url", "installer_name", "latest_version", "tag_name"):
        if not getattr(update_info, attr):
            raise ReleaseNotFoundError(f"Update metadata missing required field: {attr}")

    base_tmp = Path(temp_root) if temp_root is not None else None
    if base_tmp is not None:
        base_tmp.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(
        prefix=f"ovls-update-{os.getpid()}-", dir=str(base_tmp) if base_tmp else None
    ))
    try:
        archive_path = temp_dir / str(update_info.installer_name)
        checksums_path = temp_dir / CHECKSUMS_ASSET_NAME
        extract_dir = temp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        # 1) Download checksums (small)
        _download_to_file(
            update_info.checksums_url,
            checksums_path,
            timeout=timeout,
            max_bytes=10 * 1024 * 1024,
            opener=opener,
        )

        # 2) Download archive and verify hash/size BEFORE extraction/install
        expected_size = update_info.installer_size
        size, actual_hash = _download_to_file(
            update_info.installer_url,
            archive_path,
            timeout=timeout,
            max_bytes=MAX_ARCHIVE_BYTES,
            expected_size=expected_size,
            opener=opener,
        )
        checksums_text = checksums_path.read_text(encoding="utf-8", errors="replace")
        expected_hash = expected_checksum_for(checksums_text, archive_path.name)
        if actual_hash.lower() != expected_hash.lower():
            raise VerificationError(
                f"SHA-256 mismatch for {archive_path.name}: expected {expected_hash}, got {actual_hash}"
            )
        # Redundant explicit verify (stream hash already computed) for clarity/safety
        verify_sha256(archive_path, expected_hash)

        # 3) Safe extraction (only after checksum/size verification)
        _safe_extract_tar(archive_path, extract_dir)

        # 4) Locate and validate package
        package_root = detect_package_root(extract_dir)
        expected_version = normalize_version(update_info.latest_version) or update_info.latest_version
        validate_package_root(package_root, expected_version=expected_version)

        return PreparedRelease(
            package_root=package_root,
            version=str(expected_version),
            tag_name=str(update_info.tag_name),
            temp_dir=temp_dir,
            archive_path=archive_path,
            checksums_path=checksums_path,
        )
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
