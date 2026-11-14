"""Tests for src/linux-support/updater.py (GitHub Releases updater).

All network access is mocked. No real GitHub requests are made.
Temporary HOME/XDG trees are used; no real user directories are touched.

Crypto model (current): HTTPS + SHA-256 verification via SHA256SUMS only.
No GPG/signature/key dependencies are required.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import desktop  # noqa: E402
import updater  # noqa: E402


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class _FakeHeaders(dict):
    def get_content_charset(self):
        return self.get("charset", "utf-8")


class _FakeResponse:
    def __init__(self, data: bytes, headers=None):
        self._bio = io.BytesIO(data)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._bio.read(n)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_urlopen_factory(payload_by_url):
    """Return an opener callable mapping URL -> bytes/JSON/Response behavior."""

    def opener(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url not in payload_by_url:
            raise AssertionError(f"Unexpected URL: {url}")
        item = payload_by_url[url]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            data, headers = item
        else:
            data, headers = item, {}
        if isinstance(data, (dict, list)):
            data = json.dumps(data).encode("utf-8")
            headers = _FakeHeaders({**headers, "charset": "utf-8"})
        elif isinstance(data, str):
            data = data.encode("utf-8")
            headers = _FakeHeaders(headers)
        elif not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        if not isinstance(headers, _FakeHeaders):
            headers = _FakeHeaders(headers)
        return _FakeResponse(data, headers=headers)

    return opener


def _make_package_tree(root: Path, version: str = "0.2.0"):
    """Create a minimal valid standalone OVLS package tree."""
    pkg = root / "OVLS Installer"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "install.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(pkg / "install.sh", 0o755)
    (pkg / "README.md").write_text("readme\n", encoding="utf-8")
    ovls_dir = pkg / "ovls"
    ovls_dir.mkdir()
    (ovls_dir / "ovls.py").write_text("print('ovls')\n", encoding="utf-8")
    (ovls_dir / "core.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
    ext = ovls_dir / "extension"
    ext.mkdir()
    (ext / "index.js").write_text("// ext\n", encoding="utf-8")
    return pkg


def _make_tar_gz(archive_path: Path, members):
    """Create tar.gz from member specs."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for spec in members:
            kind = spec[0]
            if kind == "dir":
                ti = tarfile.TarInfo(name=spec[1])
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tar.addfile(ti)
            elif kind == "file":
                _, name, data, *rest = spec
                mode = rest[0] if rest else 0o644
                ti = tarfile.TarInfo(name=name)
                ti.size = len(data)
                ti.mode = mode
                tar.addfile(ti, io.BytesIO(data))
            elif kind == "symlink":
                _, name, linkname = spec
                ti = tarfile.TarInfo(name=name)
                ti.type = tarfile.SYMTYPE
                ti.linkname = linkname
                tar.addfile(ti)
            elif kind == "hardlink":
                _, name, linkname = spec
                ti = tarfile.TarInfo(name=name)
                ti.type = tarfile.LNKTYPE
                ti.linkname = linkname
                ti.size = 0
                tar.addfile(ti)
            elif kind == "fifo":
                _, name = spec
                ti = tarfile.TarInfo(name=name)
                ti.type = tarfile.FIFOTYPE
                tar.addfile(ti)
            elif kind == "chardev":
                _, name = spec
                ti = tarfile.TarInfo(name=name)
                ti.type = tarfile.CHRTYPE
                ti.devmajor = 1
                ti.devminor = 3
                tar.addfile(ti)
            elif kind == "raw":
                _, ti, fobj = spec
                tar.addfile(ti, fobj)
            elif kind == "dup":
                _, name, data = spec
                for _ in range(2):
                    ti = tarfile.TarInfo(name=name)
                    ti.size = len(data)
                    ti.mode = 0o644
                    tar.addfile(ti, io.BytesIO(data))
            else:
                raise ValueError(kind)
    return archive_path


def _make_valid_release_archive(temp_dir: Path, version: str = "0.2.0"):
    pkg = _make_package_tree(temp_dir / "src", version=version)
    archive = temp_dir / updater.INSTALLER_ASSET_NAME
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(pkg, arcname=pkg.name)
    return archive, pkg


class UpdaterTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-updater-", dir=str(HERE)))
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.state = self.root / "state"
        self.cache = self.root / "cache"
        self.home = self.root / "home"
        self.app = self.data / "ovls"
        self.app.mkdir(parents=True)

        self.env = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": str(self.data),
                "XDG_CONFIG_HOME": str(self.config),
                "XDG_STATE_HOME": str(self.state),
                "XDG_CACHE_HOME": str(self.cache),
                "HOME": str(self.home),
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.desktop_patch = mock.patch.object(desktop, "data_home", return_value=self.data)
        self.desktop_patch.start()
        self.addCleanup(self.desktop_patch.stop)

        self.state_patch = mock.patch.object(desktop, "state_home", return_value=self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

        self.config_patch = mock.patch.object(desktop, "config_home", return_value=self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _release_payload(self, version="0.2.0", tag="v0.2.0", size=None, assets=None):
        base = "https://example.invalid/releases/download/v0.2.0"
        if assets is None:
            assets = [
                {
                    "name": updater.INSTALLER_ASSET_NAME,
                    "browser_download_url": f"{base}/{updater.INSTALLER_ASSET_NAME}",
                    "size": size or 123,
                },
                {
                    "name": updater.CHECKSUMS_ASSET_NAME,
                    "browser_download_url": f"{base}/{updater.CHECKSUMS_ASSET_NAME}",
                    "size": 100,
                },
            ]
        return {
            "tag_name": tag,
            "name": tag,
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/Dimonchik-UA/Vortex-Linux-Support/releases/tag/" + tag,
            "assets": assets,
        }


class ApiSurfaceTests(UpdaterTestBase):
    def test_no_gpg_signature_api(self):
        self.assertFalse(hasattr(updater, "SIGNATURE_ASSET_NAME"))
        self.assertFalse(hasattr(updater, "PRODUCTION_PUBLIC_KEY"))
        self.assertFalse(hasattr(updater, "verify_signature"))
        self.assertFalse(hasattr(updater, "_run_gpg_verify"))
        # prepare_release_package must not accept public_key_text
        import inspect

        sig = inspect.signature(updater.prepare_release_package)
        self.assertNotIn("public_key_text", sig.parameters)


class VersionTests(UpdaterTestBase):
    def test_parse_and_normalize_versions(self):
        self.assertEqual(updater.parse_version("0.1.0"), (0, 1, 0))
        self.assertEqual(updater.parse_version("v0.1.0"), (0, 1, 0))
        self.assertEqual(updater.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("v1.2.3-beta"), (1, 2, 3))
        self.assertEqual(updater.parse_version("v1.2.3+build"), (1, 2, 3))
        self.assertIsNone(updater.parse_version("1.2"))
        self.assertIsNone(updater.parse_version("abc"))
        self.assertIsNone(updater.parse_version(None))
        self.assertEqual(updater.normalize_version("v01.2.3"), None)
        self.assertEqual(updater.normalize_version("v1.2.3-rc1"), "1.2.3")

    def test_compare_versions_semantic_not_lexicographic(self):
        self.assertEqual(updater.compare_versions("0.2.0", "0.10.0"), -1)
        self.assertEqual(updater.compare_versions("v0.10.0", "0.2.0"), 1)
        self.assertEqual(updater.compare_versions("1.0.0", "v1.0.0"), 0)
        self.assertIsNone(updater.compare_versions("1.0", "1.0.0"))

    def test_read_installed_version_prefers_installed_copy(self):
        with mock.patch.object(updater, "read_installed_version", return_value="0.1.5"):
            self.assertEqual(updater.current_local_version(), "0.1.5")


class CacheTests(UpdaterTestBase):
    def test_save_and_load_cache(self):
        path = self.state / "ovls" / "update-check.json"
        result = updater.UpdateCheckResult(
            status="up_to_date",
            current_version="0.1.0",
            latest_version="0.1.0",
            checked_at=1000.0,
        )
        updater.save_cache(result, path=path)
        loaded = updater.load_cache(path=path, now=1001.0, ttl=3600)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, "up_to_date")
        self.assertTrue(loaded.from_cache)

    def test_cache_ttl_expiry(self):
        path = self.state / "ovls" / "update-check.json"
        result = updater.UpdateCheckResult(status="unknown", checked_at=1000.0)
        updater.save_cache(result, path=path)
        self.assertIsNone(updater.load_cache(path=path, now=1000.0 + 3601, ttl=3600))
        self.assertIsNotNone(updater.load_cache(path=path, now=1000.0 + 3599, ttl=3600))


class ReleaseCheckTests(UpdaterTestBase):
    def test_check_for_updates_update_available(self):
        payload = self._release_payload(version="0.2.0", tag="v0.2.0")
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: payload})
        with mock.patch.object(updater, "current_local_version", return_value="0.1.0"):
            result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "update_available")
        self.assertEqual(result.latest_version, "0.2.0")
        self.assertEqual(result.current_version, "0.1.0")
        self.assertIsNone(result.error)
        self.assertFalse(hasattr(result, "signature_url") and getattr(result, "signature_url", None))

    def test_check_for_updates_up_to_date(self):
        payload = self._release_payload(version="0.1.0", tag="v0.1.0")
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: payload})
        with mock.patch.object(updater, "current_local_version", return_value="0.1.0"):
            result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "up_to_date")

    def test_check_for_updates_network_error_returns_unknown(self):
        import urllib.error

        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: urllib.error.URLError("dns")})
        with mock.patch.object(updater, "current_local_version", return_value="0.1.0"):
            result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "unknown")
        self.assertTrue(result.error)

    def test_check_for_updates_http_404_returns_unknown(self):
        import urllib.error

        err = urllib.error.HTTPError(updater.RELEASES_LATEST_URL, 404, "Not Found", {}, None)
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: err})
        result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "unknown")

    def test_check_for_updates_invalid_json_returns_unknown(self):
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: b"not json"})
        result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "unknown")

    def test_check_for_updates_missing_assets_returns_unknown(self):
        payload = self._release_payload()
        payload["assets"] = [{"name": "other.tar.gz", "browser_download_url": "https://x/y"}]
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: payload})
        result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "unknown")
        self.assertIn("Missing required", result.error or "")

    def test_check_for_updates_signature_asset_not_required(self):
        """Release without SHA256SUMS.asc must be accepted (no GPG dependency)."""
        payload = self._release_payload()
        # only installer + checksums, no signature asset
        self.assertFalse(any(a.get("name", "").endswith(".asc") for a in payload["assets"]))
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: payload})
        with mock.patch.object(updater, "current_local_version", return_value="0.1.0"):
            result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "update_available")
        self.assertEqual(result.latest_version, "0.2.0")

    def test_check_for_updates_prerelease_returns_unknown(self):
        payload = self._release_payload()
        payload["prerelease"] = True
        opener = _fake_urlopen_factory({updater.RELEASES_LATEST_URL: payload})
        result = updater.check_for_updates(opener=opener, force=True, cache_file=self.state / "c.json")
        self.assertEqual(result.status, "unknown")

    def test_check_uses_fresh_cache(self):
        cache_file = self.state / "c.json"
        cached = updater.UpdateCheckResult(
            status="up_to_date", current_version="0.1.0", latest_version="0.1.0", checked_at=time.time()
        )
        updater.save_cache(cached, path=cache_file)
        result = updater.check_for_updates(force=False, cache_file=cache_file, cache_ttl=3600)
        self.assertEqual(result.status, "up_to_date")
        self.assertTrue(result.from_cache)


class ChecksumTests(UpdaterTestBase):
    def test_parse_and_verify_sha256(self):
        data = b"hello world"
        digest = _sha256_bytes(data)
        sums = f"{digest}  {updater.INSTALLER_ASSET_NAME}\n# comment\n"
        self.assertEqual(updater.expected_checksum_for(sums, updater.INSTALLER_ASSET_NAME), digest)
        f = self.root / "file.bin"
        f.write_bytes(data)
        updater.verify_sha256(f, digest)
        with self.assertRaises(updater.VerificationError):
            updater.verify_sha256(f, "0" * 64)

    def test_expected_checksum_missing(self):
        with self.assertRaises(updater.VerificationError):
            updater.expected_checksum_for("abcd  other.tar.gz\n", updater.INSTALLER_ASSET_NAME)


class SafeExtractionTests(UpdaterTestBase):
    def _extract(self, members, **kwargs):
        arc = self.root / "a.tar.gz"
        _make_tar_gz(arc, members)
        out = self.root / "out"
        out.mkdir(exist_ok=True)
        return updater._safe_extract_tar(arc, out, **kwargs)

    def test_valid_archive_extracts(self):
        out = self._extract(
            [
                ("dir", "OVLS Installer"),
                ("dir", "OVLS Installer/ovls"),
                ("file", "OVLS Installer/install.sh", b"#!/bin/sh\n", 0o755),
                ("file", "OVLS Installer/ovls/ovls.py", b"print(1)\n", 0o644),
                ("file", "OVLS Installer/ovls/core.py", b'VERSION="0.2.0"\n', 0o644),
            ]
        )
        self.assertTrue((out / "OVLS Installer" / "install.sh").is_file())

    def test_rejects_path_traversal(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("file", "../evil.txt", b"x", 0o644)])
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("file", "OVLS Installer/../../evil.txt", b"x", 0o644)])

    def test_rejects_absolute_path(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("file", "/etc/passwd", b"x", 0o644)])

    def test_rejects_symlink(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("symlink", "OVLS Installer/link", "/etc/passwd")])

    def test_rejects_hardlink(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract(
                [
                    ("file", "OVLS Installer/a.txt", b"x", 0o644),
                    ("hardlink", "OVLS Installer/b.txt", "OVLS Installer/a.txt"),
                ]
            )

    def test_rejects_fifo(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("fifo", "OVLS Installer/pipe")])

    def test_rejects_device(self):
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("chardev", "OVLS Installer/dev")])

    def test_rejects_duplicate_names(self):
        arc = self.root / "dup.tar.gz"
        _make_tar_gz(arc, [("dup", "OVLS Installer/x.txt", b"data")])
        out = self.root / "out"
        out.mkdir()
        with self.assertRaises(updater.PackageFormatError):
            updater._safe_extract_tar(arc, out)

    def test_rejects_excessive_extracted_size(self):
        data = b"a" * 1024
        with self.assertRaises(updater.PackageFormatError):
            self._extract([("file", "OVLS Installer/big.bin", data, 0o644)], max_total_bytes=512)

    def test_rejects_too_many_files(self):
        members = [("file", f"OVLS Installer/f{i}.txt", b"x", 0o644) for i in range(5)]
        with self.assertRaises(updater.PackageFormatError):
            self._extract(members, max_files=3)

    def test_corrupted_archive(self):
        arc = self.root / "bad.tar.gz"
        arc.write_bytes(b"not a tarball")
        out = self.root / "out"
        out.mkdir()
        with self.assertRaises(updater.PackageFormatError):
            updater._safe_extract_tar(arc, out)


class PackageValidationTests(UpdaterTestBase):
    def test_detect_and_validate_package(self):
        pkg = _make_package_tree(self.root / "x", version="0.2.0")
        root = updater.detect_package_root(pkg.parent)
        self.assertEqual(root, pkg)
        updater.validate_package_root(pkg, expected_version="0.2.0")

    def test_version_mismatch_rejected(self):
        pkg = _make_package_tree(self.root / "x", version="0.1.0")
        with self.assertRaises(updater.PackageFormatError):
            updater.validate_package_root(pkg, expected_version="0.2.0")

    def test_invalid_structure_rejected(self):
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "install.sh").write_text("x", encoding="utf-8")
        with self.assertRaises(updater.PackageFormatError):
            updater.validate_package_root(bad, expected_version="0.2.0")


class PrepareReleaseTests(UpdaterTestBase):
    def _build_release(self, version="0.2.0", tamper_archive=False, tamper_checksums=False, bad_package_version=None):
        work = self.root / "work"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(exist_ok=True)
        archive, _pkg = _make_valid_release_archive(work, version=bad_package_version or version)
        original_bytes = archive.read_bytes()
        checksums = work / updater.CHECKSUMS_ASSET_NAME
        checksums.write_text(
            f"{_sha256_bytes(original_bytes)}  {archive.name}\n",
            encoding="utf-8",
        )
        if tamper_checksums:
            checksums.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")

        download_archive = bytearray(original_bytes)
        if tamper_archive and download_archive:
            download_archive[-1] ^= 0x01
        return {
            "archive": bytes(download_archive),
            "checksums": checksums.read_bytes(),
            "size": len(original_bytes),
        }

    def _update_info(self, version="0.2.0", size=None):
        base = "https://example.invalid/dl"
        return updater.UpdateCheckResult(
            status="update_available",
            current_version="0.1.0",
            latest_version=version,
            tag_name=f"v{version}",
            release_url="https://example.invalid/release",
            installer_url=f"{base}/{updater.INSTALLER_ASSET_NAME}",
            checksums_url=f"{base}/{updater.CHECKSUMS_ASSET_NAME}",
            installer_name=updater.INSTALLER_ASSET_NAME,
            installer_size=size,
            checked_at=time.time(),
        )

    def test_prepare_release_success(self):
        blobs = self._build_release(version="0.2.0")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        prepared = updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)
        try:
            self.assertEqual(prepared.version, "0.2.0")
            self.assertTrue(prepared.package_root.is_dir())
            self.assertTrue((prepared.package_root / "install.sh").is_file())
            self.assertTrue((prepared.package_root / "ovls" / "ovls.py").is_file())
            self.assertTrue(prepared.checksums_path.is_file())
            self.assertTrue(prepared.archive_path.is_file())
            self.assertFalse(hasattr(prepared, "signature_path"))
        finally:
            prepared.cleanup()
        self.assertFalse(prepared.temp_dir.exists())

    def test_prepare_release_success_without_gpg_installed(self):
        blobs = self._build_release(version="0.2.0")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        with mock.patch("shutil.which", return_value=None):
            prepared = updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)
            prepared.cleanup()

    def test_prepare_release_sha256_mismatch_cleans_up(self):
        blobs = self._build_release(version="0.2.0", tamper_archive=True)
        info = self._update_info(version="0.2.0", size=None)  # skip size pre-check
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        tmp_root = self.root / "tmp"
        with self.assertRaises(updater.VerificationError):
            updater.prepare_release_package(info, temp_root=tmp_root, opener=opener)
        self.assertFalse(any(tmp_root.iterdir())) if tmp_root.exists() else True

    def test_prepare_release_wrong_checksum_entry_rejected(self):
        blobs = self._build_release(version="0.2.0", tamper_checksums=True)
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        with self.assertRaises(updater.VerificationError):
            updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)

    def test_prepare_release_version_mismatch_rejected(self):
        blobs = self._build_release(version="0.2.0", bad_package_version="0.1.9")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        tmp_root = self.root / "tmp"
        with self.assertRaises(updater.PackageFormatError):
            updater.prepare_release_package(info, temp_root=tmp_root, opener=opener)
        self.assertFalse(any(tmp_root.iterdir())) if tmp_root.exists() else True

    def test_prepare_release_unsafe_tar_rejected(self):
        arc = self.root / updater.INSTALLER_ASSET_NAME
        _make_tar_gz(arc, [("file", "../evil.txt", b"x", 0o644)])
        checksums = self.root / "SHA256SUMS"
        checksums.write_text(f"{_sha256_file(arc)}  {arc.name}\n", encoding="utf-8")
        info = self._update_info(version="0.2.0", size=arc.stat().st_size)
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: checksums.read_bytes(),
                info.installer_url: arc.read_bytes(),
            }
        )
        with self.assertRaises(updater.PackageFormatError):
            updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)

    def test_prepare_does_not_call_package_install(self):
        blobs = self._build_release(version="0.2.0")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: blobs["archive"],
            }
        )
        with mock.patch("package.install") as install_mock:
            prepared = updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)
            prepared.cleanup()
        install_mock.assert_not_called()

    def test_prepare_does_not_request_signature_asset(self):
        """Only checksums and installer are fetched (no .asc)."""
        blobs = self._build_release(version="0.2.0")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        requested = []

        def opener(request, timeout=None):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            requested.append(url)
            return _fake_urlopen_factory(
                {
                    info.checksums_url: blobs["checksums"],
                    info.installer_url: blobs["archive"],
                }
            )(request, timeout=timeout)

        prepared = updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)
        prepared.cleanup()
        self.assertEqual(set(requested), {info.checksums_url, info.installer_url})
        self.assertFalse(any(url.endswith(".asc") for url in requested))

    def test_network_error_during_download(self):
        import urllib.error

        blobs = self._build_release(version="0.2.0")
        info = self._update_info(version="0.2.0", size=blobs["size"])
        opener = _fake_urlopen_factory(
            {
                info.checksums_url: blobs["checksums"],
                info.installer_url: urllib.error.URLError("timeout"),
            }
        )
        with self.assertRaises(updater.NetworkError):
            updater.prepare_release_package(info, temp_root=self.root / "tmp", opener=opener)

    def test_no_verified_package_available(self):
        info = updater.UpdateCheckResult(status="up_to_date", current_version="0.2.0")
        with self.assertRaises(updater.UpdaterError):
            updater.prepare_release_package(info, temp_root=self.root / "tmp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
