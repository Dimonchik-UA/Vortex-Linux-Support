"""Read-only reader for Vortex ``state.v2`` (LevelDB on disk).

The reader never opens LevelDB, DuckDB or the database LOCK.  It reads CURRENT,
the selected MANIFEST, live SSTables and all relevant WAL files as plain files,
then merges internal keys by sequence.  No file in ``state.v2`` is written.

Public entry point: :func:`read_state(prefix)`.  Path templates and Windows path
conversion belong to the calling code, not to this module.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from pathlib import Path

import extension_installer

BLOCK_SIZE = 32768
FOOTER_MAGIC = struct.pack("<Q", 0xDB4775248B80FB57)
COMPARATOR = b"leveldb.BytewiseComparator"
MANIFEST_RE = re.compile(r"^MANIFEST-(\d{6})$")
MAX_ATTEMPTS = 4


class StateV2Error(OSError):
    """The state.v2 snapshot could not be read consistently."""


def crc32c(data: bytes) -> int:
    """CRC32C with polynomial 0x82F63B78 (same as LevelDB)."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def mask_crc32c(crc: int) -> int:
    """Storage mask LevelDB applies to the CRC of a log record."""
    rotated = ((crc >> 15) | (crc << 17)) & 0xFFFFFFFF
    return (rotated + 0xA282EAD8) & 0xFFFFFFFF


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise StateV2Error("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _sized(data: bytes, pos: int) -> tuple[bytes, int]:
    size, pos = _varint(data, pos)
    end = pos + size
    if end > len(data):
        raise StateV2Error("truncated length-prefixed value")
    return data[pos:end], end


def _snappy_decompress(data: bytes) -> bytes:
    """Decode raw Snappy (as used by LevelDB blocks)."""
    expected, pos = _varint(data, 0)
    out = bytearray()
    while pos < len(data) and len(out) < expected:
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length < 60:
                count = length + 1
            else:
                extra = length - 59
                if pos + extra > len(data):
                    raise StateV2Error("truncated Snappy literal length")
                count = int.from_bytes(data[pos:pos + extra], "little") + 1
                pos += extra
            if pos + count > len(data):
                raise StateV2Error("truncated Snappy literal")
            out.extend(data[pos:pos + count])
            pos += count
            continue
        if kind == 1:
            count = 4 + ((tag >> 2) & 7)
            if pos >= len(data):
                raise StateV2Error("truncated Snappy copy")
            offset = ((tag & 0xE0) << 3) | data[pos]
            pos += 1
        elif kind == 2:
            count = 1 + (tag >> 2)
            if pos + 2 > len(data):
                raise StateV2Error("truncated Snappy copy")
            offset = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            count = 1 + (tag >> 2)
            if pos + 4 > len(data):
                raise StateV2Error("truncated Snappy copy")
            offset = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        if offset == 0 or offset > len(out):
            raise StateV2Error("invalid Snappy copy offset")
        for _ in range(count):
            out.append(out[-offset])
    if len(out) != expected:
        raise StateV2Error("Snappy size mismatch")
    return bytes(out)


def _log_records(data: bytes):
    """Yield logical records from a LevelDB log (WAL or MANIFEST).

    A physical record never crosses a 32 KiB block boundary.  LevelDB fills a
    short block tail with 1-6 raw zero bytes instead of a header, so those
    bytes are skipped without parsing.  A bad length in a full block drops the
    rest of that block (as upstream does) but marks the snapshot as damaged.
    """
    pos = 0
    fragment = bytearray()
    in_fragment = False
    damaged = False
    while pos < len(data):
        block_end = min((pos // BLOCK_SIZE + 1) * BLOCK_SIZE, len(data))
        if block_end - pos < 7:
            pos = block_end  # short zero padding at the end of a block
            continue
        header = data[pos:pos + 7]
        length = struct.unpack("<H", header[4:6])[0]
        if pos + 7 + length > block_end:
            if block_end == len(data) and len(data) % BLOCK_SIZE != 0:
                break  # truncated header or payload at EOF: incomplete tail
            damaged = True
            fragment.clear()
            in_fragment = False
            pos = block_end  # upstream drops the rest of the 32 KiB buffer
            continue
        kind = header[6]
        stored = struct.unpack("<I", header[:4])[0]
        if kind == 0 and length == 0:
            # Upstream returns kBadRecord here; an unfinished logical record
            # is dropped together with its scratch buffer.
            fragment.clear()
            in_fragment = False
            pos = block_end  # zero-type record: skip the rest of the block
            continue
        payload = data[pos + 7:pos + 7 + length]
        if mask_crc32c(crc32c(header[6:7] + payload)) != stored:
            raise StateV2Error("CRC32C mismatch in log record")
        if kind == 0:
            raise StateV2Error("invalid zero-type log record with payload")
        pos += 7 + length
        if kind == 1:
            fragment.clear()  # abandon an unfinished record, as LevelDB does
            in_fragment = False
            yield payload
        elif kind == 2:
            # FIRST may have an empty payload: track the state separately so
            # that the following MIDDLE/LAST are not mistaken for orphans.
            fragment = bytearray(payload)
            in_fragment = True
        elif kind == 3:
            if in_fragment:
                fragment.extend(payload)
            # MIDDLE without FIRST has no owner: drop it.
        elif kind == 4:
            if not in_fragment:
                continue  # LAST without FIRST: drop it
            fragment.extend(payload)
            yield bytes(fragment)
            fragment.clear()
            in_fragment = False
        else:
            raise StateV2Error(f"unknown log record type: {kind}")
    # An unfinished FIRST/MIDDLE at EOF is dropped together with its record.
    if damaged:
        raise StateV2Error(
            "corrupted log record length crossing a 32 KiB block boundary"
        )


def _encode_log_record(payload: bytes) -> bytes:
    """Encode one FULL physical record (used by tests and fixtures)."""
    body = b"\x01" + payload  # type byte is part of the CRC input
    checksum = mask_crc32c(crc32c(body))
    return struct.pack("<I", checksum) + struct.pack("<H", len(payload)) + body


def _parse_current(data: bytes) -> str:
    try:
        text = data.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise StateV2Error("CURRENT contains non-ASCII bytes") from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or MANIFEST_RE.match(lines[0]) is None:
        raise StateV2Error("CURRENT does not contain a single MANIFEST-NNNNNN name")
    return lines[0]


def _parse_manifest(data: bytes) -> dict:
    log_number = None
    next_file_number = None
    last_sequence = None
    prev_log_number = 0
    comparator = None
    live = {}
    for payload in _log_records(data):
        pos = 0
        while pos < len(payload):
            tag, pos = _varint(payload, pos)
            if tag == 1:
                comparator, pos = _sized(payload, pos)
            elif tag == 2:
                log_number, pos = _varint(payload, pos)
            elif tag == 3:
                next_file_number, pos = _varint(payload, pos)
            elif tag == 4:
                last_sequence, pos = _varint(payload, pos)
            elif tag == 5:
                _level, pos = _varint(payload, pos)
                _key, pos = _sized(payload, pos)
            elif tag == 6:
                _level, pos = _varint(payload, pos)
                number, pos = _varint(payload, pos)
                live.pop(number, None)
            elif tag == 7:
                _level, pos = _varint(payload, pos)
                number, pos = _varint(payload, pos)
                size, pos = _varint(payload, pos)
                smallest, pos = _sized(payload, pos)
                largest, pos = _sized(payload, pos)
                live[number] = {
                    "size": size,
                    "smallest": smallest,
                    "largest": largest,
                }
            elif tag == 9:
                prev_log_number, pos = _varint(payload, pos)
            else:
                raise StateV2Error(f"unknown VersionEdit tag: {tag}")
    if comparator is not None and comparator != COMPARATOR:
        raise StateV2Error(f"unsupported comparator: {comparator!r}")
    if log_number is None or next_file_number is None or last_sequence is None:
        raise StateV2Error("MANIFEST misses log_number/next_file_number/last_sequence")
    if not live:
        raise StateV2Error("MANIFEST has no live SSTables")
    return {
        "log_number": log_number,
        "next_file_number": next_file_number,
        "last_sequence": last_sequence,
        "prev_log_number": prev_log_number,
        "live": live,
    }


def _read_block(raw: bytes) -> bytes:
    """Decode one block: block handle size excludes the 5-byte trailer."""
    if len(raw) < 5:
        raise StateV2Error("truncated block trailer")
    body = raw[:-5]
    kind = raw[-5]
    expected = struct.unpack("<I", raw[-4:])[0]
    if mask_crc32c(crc32c(body + raw[-5:-4])) != expected:
        raise StateV2Error("CRC32C mismatch in SSTable block")
    if kind == 0:
        return body
    if kind == 1:
        return _snappy_decompress(body)
    raise StateV2Error(f"unsupported SSTable compression: {kind}")


def _block_entries(block: bytes):
    if len(block) < 4:
        raise StateV2Error("truncated SSTable restart array")
    restart_count = struct.unpack("<I", block[-4:])[0]
    entry_end = len(block) - 4 - 4 * restart_count
    if entry_end < 0:
        raise StateV2Error("malformed SSTable restart array")
    pos = 0
    key = b""
    while pos < entry_end:
        shared, pos = _varint(block, pos)
        non_shared, pos = _varint(block, pos)
        value_len, pos = _varint(block, pos)
        if shared > len(key) or pos + non_shared + value_len > entry_end:
            raise StateV2Error("malformed SSTable block entry")
        key = key[:shared] + block[pos:pos + non_shared]
        pos += non_shared
        value = block[pos:pos + value_len]
        pos += value_len
        yield key, value


def _internal_key(key: bytes) -> tuple[bytes, int, int]:
    if len(key) < 8:
        raise StateV2Error("internal key is shorter than 8 bytes")
    packed = struct.unpack("<Q", key[-8:])[0]
    sequence = packed >> 8
    kind = packed & 0xFF
    if kind not in (0, 1):
        raise StateV2Error(f"unknown internal key type: {kind}")
    return key[:-8], sequence, kind


def _read_table(data: bytes) -> list[tuple[bytes, int, int, bytes | None]]:
    if len(data) < len(FOOTER_MAGIC) + 16 or data[-8:] != FOOTER_MAGIC:
        raise StateV2Error("invalid SSTable footer")
    footer = data[-48:-8]
    meta_offset, pos = _varint(footer, 0)
    meta_size, pos = _varint(footer, pos)
    index_offset, pos = _varint(footer, pos)
    index_size, pos = _varint(footer, pos)

    def block_at(offset: int, size: int) -> bytes:
        end = offset + size + 5  # data block plus its trailer
        if offset < 0 or size < 0 or end > len(data) - 48:
            raise StateV2Error("SSTable block handle is outside the file")
        return _read_block(data[offset:end])

    meta = block_at(meta_offset, meta_size)
    for _key, _value in _block_entries(meta):
        pass  # metaindex is validated, but filters are not needed for values

    index = block_at(index_offset, index_size)
    entries = []
    for _separator, handle in _block_entries(index):
        offset, hpos = _varint(handle, 0)
        size, hpos = _varint(handle, hpos)
        if hpos != len(handle):
            raise StateV2Error("malformed SSTable index handle")
        for internal, value in _block_entries(block_at(offset, size)):
            user_key, sequence, kind = _internal_key(internal)
            entries.append((user_key, sequence, kind, value if kind == 1 else None))
    return entries


def _parse_batch(record: bytes) -> tuple[int, int, list[tuple[bytes, int, int, bytes | None]]]:
    if len(record) < 12:
        raise StateV2Error("WAL record is shorter than a WriteBatch header")
    sequence = struct.unpack("<Q", record[:8])[0]
    count = struct.unpack("<I", record[8:12])[0]
    pos = 12
    entries = []
    for index in range(count):
        if pos >= len(record):
            raise StateV2Error("truncated WriteBatch")
        tag = record[pos]
        pos += 1
        item_sequence = sequence + index
        if tag == 1:
            key, pos = _sized(record, pos)
            value, pos = _sized(record, pos)
            kind = 1
        elif tag == 0:
            key, pos = _sized(record, pos)
            value = None
            kind = 0
        elif tag == 2:
            _cf, pos = _varint(record, pos)
            _key, pos = _sized(record, pos)
            continue  # column-family descriptor carries no value
        else:
            raise StateV2Error(f"unknown WriteBatch tag: {tag}")
        entries.append((key, item_sequence, kind, value))
    if pos != len(record):
        raise StateV2Error("trailing bytes in WriteBatch")
    return sequence, count, entries


def _read_bytes(path: Path, noatime_files: set[str], *, size: int | None = None) -> bytes:
    flags = os.O_RDONLY
    flag = getattr(os, "O_NOATIME", 0)
    if flag:
        try:
            fd = os.open(path, flags | flag)
            noatime_files.add(str(path))
        except OSError:
            fd = os.open(path, flags)
    else:
        fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        return handle.read() if size is None else handle.read(size)


def _snapshot_identity(db_dir: Path, noatime_files: set[str]) -> tuple[tuple, dict, list[tuple[Path, int]]]:
    current = _read_bytes(db_dir / "CURRENT", noatime_files)
    manifest_name = _parse_current(current)
    manifest_path = db_dir / manifest_name
    manifest_bytes = _read_bytes(manifest_path, noatime_files)
    manifest = _parse_manifest(manifest_bytes)
    identity = [
        current,
        manifest_name,
        hashlib.sha256(manifest_bytes).hexdigest(),
    ]
    live = []
    for number in sorted(manifest["live"]):
        path = db_dir / f"{number:06d}.ldb"
        try:
            stat = path.stat()
        except OSError as exc:
            raise StateV2Error(f"live SSTable is missing: {path.name}") from exc
        live.append((path.name, stat.st_size, stat.st_mtime_ns, stat.st_ino))
    identity.append(tuple(live))

    wals = []
    for path in sorted(db_dir.glob("*.log")):
        match = re.fullmatch(r"(\d{6})\.log", path.name)
        if match is None:
            continue
        number = int(match.group(1))
        is_prev = manifest["prev_log_number"] != 0 and number == manifest["prev_log_number"]
        if number >= manifest["log_number"] or is_prev:
            wals.append((path.name, path.stat().st_size))
    identity.append(tuple(wals))
    return tuple(identity), manifest, [(db_dir / name, size) for name, size in wals]


def _merge(entries: list[tuple[bytes, int, int, bytes | None]]) -> dict[bytes, bytes]:
    chosen: dict[bytes, tuple[int, int, bytes | None]] = {}
    for key, sequence, kind, value in entries:
        current = chosen.get(key)
        if current is None or sequence > current[0]:
            chosen[key] = (sequence, kind, value)
        elif sequence == current[0] and (kind, value) != (current[1], current[2]):
            raise StateV2Error(f"conflicting values at sequence {sequence}")
    return {key: item[2] for key, item in chosen.items() if item[1] == 1}


def _read_once(db_dir: Path) -> tuple[dict[bytes, bytes], set[str]]:
    noatime_files: set[str] = set()
    before, manifest, wals = _snapshot_identity(db_dir, noatime_files)

    entries: list[tuple[bytes, int, int, bytes | None]] = []
    for number in sorted(manifest["live"]):
        path = db_dir / f"{number:06d}.ldb"
        entries.extend(_read_table(_read_bytes(path, noatime_files)))

    expected_sequence = manifest["last_sequence"] + 1
    for path, size in wals:
        number = int(path.stem)
        is_prev = (manifest["prev_log_number"] != 0
                   and number == manifest["prev_log_number"])
        records = _log_records(_read_bytes(path, noatime_files, size=size))
        for record in records:
            sequence, count, batch_entries = _parse_batch(record)
            if not is_prev:
                if sequence != expected_sequence:
                    raise StateV2Error(
                        f"non-contiguous WAL sequence in {path.name}: "
                        f"expected {expected_sequence}, got {sequence}"
                    )
                expected_sequence += count
            entries.extend(batch_entries)

    values = _merge(entries)
    after, _manifest, _wals = _snapshot_identity(db_dir, noatime_files)
    if before != after:
        raise StateV2Error("state.v2 changed while reading")
    return values, noatime_files


def read_state(prefix, *, metadata: dict | None = None) -> dict[bytes, bytes]:
    """Return the latest value for each user key in the selected Prefix.

    ``prefix`` is a Vortex Proton Prefix; ``userData`` is resolved through
    ``extension_installer.resolve_plugin_directory()``.  The Prefix is never
    saved, changed or searched for.  When ``metadata`` is supplied, the set of
    files for which ``O_NOATIME`` was actually applied is stored under
    ``noatime_files``.  Atime invariance is only meaningful for those files.
    """
    vortex_dir, _plugins_dir = extension_installer.resolve_plugin_directory(prefix)
    db_dir = Path(vortex_dir) / "state.v2"
    if not db_dir.is_dir():
        raise StateV2Error(f"state.v2 not found in {vortex_dir}")

    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            values, applied = _read_once(db_dir)
        except OSError as exc:
            last_error = exc
            continue
        if metadata is not None:
            metadata["noatime_files"] = frozenset(applied)
        return values
    if isinstance(last_error, StateV2Error):
        raise last_error
    raise StateV2Error(
        f"could not obtain a consistent state.v2 snapshot: {last_error}"
    ) from last_error
