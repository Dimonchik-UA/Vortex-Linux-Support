"""Read-only tests for the Vortex state.v2 (LevelDB) reader.

All fixtures are synthetic LevelDB files built in a temporary directory. Real
Prefixes, user data and external test fixtures are never read or modified.
"""

import hashlib
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))  # noqa: E402

import extension_installer  # noqa: E402
import installer  # noqa: E402
import registry  # noqa: E402
import state_v2  # noqa: E402

MAGIC = struct.pack("<Q", 0xDB4775248B80FB57)
COMPARATOR = b"leveldb.BytewiseComparator"


# Independent fixture builders ------------------------------------------------

def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def sized(value):
    return varint(len(value)) + value


_CRC_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ (0x82F63B78 if _c & 1 else 0)
    _CRC_TABLE.append(_c)


def crc32c(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def masked_crc(data):
    rotated = ((crc32c(data) >> 15) | (crc32c(data) << 17)) & 0xFFFFFFFF
    return (rotated + 0xA282EAD8) & 0xFFFFFFFF


def physical(payload, kind=1):
    head = bytes([kind]) + payload
    return struct.pack("<I", masked_crc(head)) + struct.pack("<H", len(payload)) + head


def block(entries):
    out = bytearray()
    restarts = []
    for key, value in entries:
        restarts.append(len(out))
        out.extend(varint(0) + varint(len(key)) + varint(len(value)) + key + value)
    for offset in restarts:
        out.extend(struct.pack("<I", offset))
    out.extend(struct.pack("<I", len(restarts)))
    return bytes(out)


def snappy_compress(data):
    out = bytearray(varint(len(data)))
    for pos in range(0, len(data), 60):
        chunk = data[pos:pos + 60]
        out.append(((len(chunk) - 1) << 2) & 0xFF)
        out.extend(chunk)
    return bytes(out)


def internal_key(key, sequence, kind=1):
    return key + struct.pack("<Q", (sequence << 8) | kind)


def table(entries, compression=0):
    data = block(entries)
    body = snappy_compress(data) if compression == 1 else data
    meta = block([])
    index = block([
        (
            internal_key(entries[-1][0], 0) if entries else internal_key(b"z", 0),
            varint(0) + varint(len(body)),
        )
    ])
    meta_at = len(body) + 5
    index_at = meta_at + len(meta) + 5
    trailer = lambda chunk, kind: bytes([kind]) + struct.pack("<I", masked_crc(chunk + bytes([kind])))
    footer = varint(meta_at) + varint(len(meta)) + varint(index_at) + varint(len(index))
    footer += b"\x00" * (40 - len(footer)) + MAGIC
    return b"".join((
        body + trailer(body, compression),
        meta + trailer(meta, 0),
        index + trailer(index, 0),
        footer,
    ))


def edit(log_number=None, next_file=None, last_seq=None, prev=None,
         live=(), deleted=(), comparator=COMPARATOR, compact=()):
    out = bytearray()
    if comparator is not None:
        out.extend(b"\x01" + sized(comparator))
    if log_number is not None:
        out.extend(b"\x02" + varint(log_number))
    if next_file is not None:
        out.extend(b"\x03" + varint(next_file))
    if last_seq is not None:
        out.extend(b"\x04" + varint(last_seq))
    if prev is not None:
        out.extend(b"\x09" + varint(prev))
    for level, key in compact:
        out.extend(b"\x05" + varint(level) + sized(key))
    for number in deleted:
        out.extend(b"\x06" + varint(0) + varint(number))
    for number in live:
        out.extend(b"\x07" + varint(0) + varint(number) + varint(1)
                   + sized(b"a") + sized(b"z"))
    return bytes(out)


def batch(sequence, operations):
    """operations: (key, value) pairs; value None means deletion."""
    out = bytearray(struct.pack("<QI", sequence, len(operations)))
    for key, value in operations:
        if value is None:
            out.extend(b"\x00" + sized(key))
        else:
            out.extend(b"\x01" + sized(key) + sized(value))
    return bytes(out)


class StateCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-state-v2-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.prefix = self.root / "prefix"
        self.vortex = (
            self.prefix / "pfx" / "drive_c" / "users" / "steamuser"
            / "AppData" / "Roaming" / "Vortex"
        )
        (self.vortex / "plugins").mkdir(parents=True)
        self.db = self.vortex / "state.v2"
        self.db.mkdir()

    def current(self, name="MANIFEST-000001", newline=True):
        (self.db / "CURRENT").write_bytes(
            name.encode("ascii") + (b"\n" if newline else b"")
        )

    def manifest(self, edits, name="MANIFEST-000001"):
        (self.db / name).write_bytes(b"".join(physical(item) for item in edits))

    def add_table(self, number, entries, compression=0):
        (self.db / f"{number:06d}.ldb").write_bytes(table(entries, compression))

    def add_wal(self, number, payloads):
        (self.db / f"{number:06d}.log").write_bytes(
            b"".join(physical(payload) for payload in payloads)
        )

    def minimal(self, *, last_seq=0, log_number=1, prev=None, table_number=1):
        self.current()
        self.add_table(table_number, [(internal_key(b"a", 0), b"old")])
        kwargs = dict(log_number=log_number, next_file=9, last_seq=last_seq,
                      live=(table_number,))
        if prev is not None:
            kwargs["prev"] = prev
        self.manifest([edit(**kwargs)])

    def realistic(self):
        """Build a synthetic multi-file snapshot with Vortex-like keys.

        File numbers mirror a rotated database so the tests still exercise the
        named-file paths (MANIFEST-000017, 000019.log, compressed SSTables).
        """
        self.current("MANIFEST-000017")
        self.add_table(16, [
            (internal_key(b"persistent###changelogs", 0), b'"[]"'),
            (internal_key(
                b"settings###gameMode###discovered###batmanarkhamknight###path", 0),
             b'"Z:\\\\games\\\\batmanarkhamknight"'),
        ], compression=1)
        self.add_table(18, [
            (internal_key(b"persistent###downloads###files###seed", 0), b'"seed"'),
        ], compression=1)

        operations = []
        for game in ("batmanarkhamknight", "falloutnv"):
            operations.append((
                f"settings###gameMode###discovered###{game}###path".encode(),
                f'"Z:\\\\games\\\\{game}"'.encode(),
            ))
        for index in range(110):
            operations.append((
                f"app###mod-{index:03d}".encode(),
                f'"value-{index:03d}"'.encode(),
            ))
        for index in range(20):
            operations.append((
                f"persistent###downloads###files###{index:03d}".encode(),
                b'"archive"',
            ))

        self.manifest(
            [edit(log_number=19, next_file=20, last_seq=0, live=(16, 18))],
            name="MANIFEST-000017",
        )
        self.add_wal(19, [batch(1, operations)])

    def fingerprint(self):
        result = {}
        for path in sorted(self.db.iterdir()):
            stat = path.stat()  # capture atime before hashing the file
            digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_NOATIME", 0)
            try:
                fd = os.open(path, flags)
            except OSError:
                fd = os.open(path, os.O_RDONLY)
            with os.fdopen(fd, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            result[path.name] = (
                stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino,
                digest.hexdigest(), stat.st_atime_ns,
            )
        return result

    def read(self):
        return state_v2.read_state(self.prefix)


class CrcAndSnappyTest(unittest.TestCase):
    def test_crc32c_known_vector(self):
        self.assertEqual(crc32c(b"123456789"), 0xE3069283)
        self.assertEqual(state_v2.crc32c(b"123456789"), 0xE3069283)

    def test_masked_crc_matches_manifest_record(self):
        # Fixed manifest comparator record with an independently recorded CRC.
        data = physical(COMPARATOR)
        stored = struct.unpack("<I", data[:4])[0]
        length = struct.unpack("<H", data[4:6])[0]
        self.assertEqual(stored, 0x16717B8)
        self.assertEqual(length, len(COMPARATOR))
        self.assertEqual(masked_crc(data[6:7] + data[7:7 + length]), stored)
        self.assertNotEqual(crc32c(data[4:6] + data[6:7] + data[7:7 + length]), stored)

    def test_module_log_record_encoder_roundtrip(self):
        payload = b"round-trip payload"
        encoded = state_v2._encode_log_record(payload)
        self.assertEqual(list(state_v2._log_records(encoded)), [payload])
        self.assertEqual(len(encoded), 7 + len(payload))

    def test_snappy_literal_and_copy_vectors(self):
        self.assertEqual(state_v2._snappy_decompress(b"\x00"), b"")
        self.assertEqual(state_v2._snappy_decompress(b"\x03\x08abc"), b"abc")
        self.assertEqual(state_v2._snappy_decompress(b"\x08\x0cabcd\x01\x04"), b"abcdabcd")

    def test_snappy_rejects_truncated_and_invalid_copy(self):
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._snappy_decompress(b"\x05\x08abc")
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._snappy_decompress(b"\x04\x08abc\x01\x09")

    def test_snappy_sstable_index_block(self):
        index = block([
            (internal_key(b"persistent###changelogs", 0), varint(0) + varint(12)),
            (internal_key(b"zzz", 0), varint(0) + varint(3)),
        ])
        decoded = state_v2._snappy_decompress(snappy_compress(index))
        self.assertEqual(decoded, index)
        self.assertEqual(struct.unpack("<I", decoded[-4:])[0], 2)
        self.assertIn(b"persistent###changelogs", decoded)


class ManifestTest(StateCase):
    def test_current_with_and_without_newline(self):
        for newline in (True, False):
            with self.subTest(newline=newline):
                self.current("MANIFEST-000007", newline=newline)
                self.assertEqual(state_v2._parse_current(
                    (self.db / "CURRENT").read_bytes()), "MANIFEST-000007")

    def test_current_rejects_bad_names(self):
        for content in (b"MANIFEST-7\n", b"MANIFEST-000001\nMANIFEST-000002\n",
                        b"", b"MANIFEST-000001x\n"):
            with self.subTest(content=content):
                with self.assertRaises(state_v2.StateV2Error):
                    state_v2._parse_current(content)

    def test_current_pointing_to_missing_manifest_is_an_error(self):
        self.current("MANIFEST-000099")
        with self.assertRaises(OSError):
            self.read()

    def test_current_rejects_non_ascii(self):
        self.assertEqual(state_v2._parse_current(b"MANIFEST-000001\n"),
                         "MANIFEST-000001")
        with self.assertRaisesRegex(state_v2.StateV2Error, "non-ASCII"):
            state_v2._parse_current(b"MANIFEST-\xff00001\n")

    def test_read_state_reports_non_ascii_current(self):
        self.current("MANIFEST-000001")
        (self.db / "CURRENT").write_bytes(b"MANIFEST-\xff00001\n")
        with self.assertRaisesRegex(state_v2.StateV2Error, "non-ASCII") as caught:
            self.read()
        self.assertIsInstance(caught.exception, state_v2.StateV2Error)
        self.assertNotIsInstance(caught.exception, UnicodeDecodeError)

    def test_multiple_version_edits_and_all_tags(self):
        edits = [
            edit(log_number=7, next_file=11, last_seq=100, live=(9,)),
            edit(comparator=None, prev=5, compact=[(0, b"x")],
                 live=(10,), deleted=(9,)),
        ]
        self.manifest(edits)
        parsed = state_v2._parse_manifest((self.db / "MANIFEST-000001").read_bytes())
        self.assertEqual(parsed["log_number"], 7)
        self.assertEqual(parsed["next_file_number"], 11)
        self.assertEqual(parsed["last_sequence"], 100)
        self.assertEqual(parsed["prev_log_number"], 5)
        self.assertEqual(list(parsed["live"]), [10])

    def test_required_fields_and_live_tables(self):
        for edits in (
            [edit(log_number=1, next_file=2, live=(1,))],
            [edit(log_number=1, last_seq=3, live=(1,))],
            [edit(next_file=2, last_seq=3, live=(1,))],
            [edit(log_number=1, next_file=2, last_seq=3)],
            [edit(log_number=1, next_file=2, last_seq=3, live=(1,)),
             edit(comparator=None, deleted=(1,))],
        ):
            with self.subTest(edits=edits):
                self.manifest(edits)
                with self.assertRaises(state_v2.StateV2Error):
                    state_v2._parse_manifest(
                        (self.db / "MANIFEST-000001").read_bytes())

    def test_corrupt_and_truncated_manifest(self):
        good = physical(edit(log_number=1, next_file=2, last_seq=3, live=(1,)))
        self.db.joinpath("MANIFEST-000001").write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._parse_manifest((self.db / "MANIFEST-000001").read_bytes())
        self.db.joinpath("MANIFEST-000001").write_bytes(good[:10])
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._parse_manifest((self.db / "MANIFEST-000001").read_bytes())

    def test_unsupported_comparator(self):
        self.manifest([edit(log_number=1, next_file=2, last_seq=3,
                            live=(1,), comparator=b"other")])
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._parse_manifest((self.db / "MANIFEST-000001").read_bytes())

    def test_only_manifest_named_in_current_is_used(self):
        self.current("MANIFEST-000002")
        self.manifest([edit(log_number=1, next_file=2, last_seq=3, live=(1,))])
        self.manifest([edit(log_number=8, next_file=9, last_seq=10, live=(2,))],
                      name="MANIFEST-000002")
        self.add_table(1, [(internal_key(b"a", 1), b"one")])
        self.add_table(2, [(internal_key(b"b", 2), b"two")])
        identity, parsed, wals = state_v2._snapshot_identity(
            self.db, set())
        self.assertEqual(parsed["log_number"], 8)
        self.assertEqual([name for name, _ in wals], [])
        self.assertIn("MANIFEST-000002", identity)


class SstableTest(StateCase):
    def test_uncompressed_table_entries(self):
        raw = table([
            (internal_key(b"a", 3), b"value-a"),
            (internal_key(b"b", 4), b"value-b"),
        ])
        items = state_v2._read_table(raw)
        self.assertEqual(
            [(k, s, t, v) for k, s, t, v in items],
            [(b"a", 3, 1, b"value-a"), (b"b", 4, 1, b"value-b")],
        )

    def test_snappy_table_entries(self):
        raw = table([(internal_key(b"key", 9), b"payload" * 12)], compression=1)
        self.assertEqual(state_v2._read_table(raw),
                         [(b"key", 9, 1, b"payload" * 12)])

    def test_tombstone_entry_has_no_value(self):
        raw = table([(internal_key(b"gone", 2, 0), b"")])
        self.assertEqual(state_v2._read_table(raw), [(b"gone", 2, 0, None)])

    def test_bad_footer_crc_and_compression(self):
        good = table([(internal_key(b"a", 1), b"v")])
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._read_table(good[:-1] + bytes([good[-1] ^ 1]))
        broken = bytearray(good)
        broken[-49] = 2  # metaindex block trailer compression type
        with self.assertRaises(state_v2.StateV2Error):
            state_v2._read_table(bytes(broken))

    def test_unsupported_compression_reaches_compression_branch(self):
        entries = [(internal_key(b"a", 1), b"value")]
        raw = bytearray(table(entries, compression=1))
        body_len = len(snappy_compress(block(entries)))
        self.assertEqual(raw[body_len], 1)
        raw[body_len] = 2
        raw[body_len + 1:body_len + 5] = struct.pack(
            "<I", masked_crc(bytes(raw[:body_len]) + b"\x02"))
        with self.assertRaisesRegex(state_v2.StateV2Error,
                                    "unsupported SSTable compression"):
            state_v2._read_table(bytes(raw))

    def test_missing_live_table_is_explicit_error(self):
        self.current()
        self.manifest([edit(log_number=1, next_file=2, last_seq=0, live=(7,))])
        with self.assertRaises(state_v2.StateV2Error):
            self.read()

    def test_tables_are_read_without_change(self):
        self.realistic()
        values = self.read()
        self.assertIn(b"settings###gameMode###discovered###batmanarkhamknight###path", values)
        self.assertTrue(any(key.startswith(b"persistent###downloads###files###")
                            for key in values))


class WalTest(StateCase):
    def test_full_records_batches_sequence_and_tombstones(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [
            batch(1, [(b"a", b"new"), (b"b", b"keep")]),
            batch(3, [(b"a", None)]),
        ])
        self.assertEqual(self.read(), {b"b": b"keep"})

    def test_fragmented_first_middle_last(self):
        self.minimal(last_seq=0)
        payload = batch(1, [(b"frag", b"assembled")])
        parts = [payload[:12], payload[12:24], payload[24:]]
        raw = b"".join((physical(parts[0], 2), physical(parts[1], 3),
                        physical(parts[2], 4)))
        self.db.joinpath("000001.log").write_bytes(raw)
        self.assertEqual(self.read()[b"frag"], b"assembled")

    def test_middle_and_last_without_first_are_ignored(self):
        self.minimal(last_seq=0)
        payload = batch(1, [(b"dropped", b"x")])
        self.db.joinpath("000001.log").write_bytes(
            physical(payload[:12], 3) + physical(payload[12:], 4))
        self.assertNotIn(b"dropped", self.read())

    def test_type_zero_and_incomplete_tail_are_ignored(self):
        self.minimal(last_seq=0)
        payload = batch(1, [(b"ok", b"yes")])
        good = physical(payload)
        self.db.joinpath("000001.log").write_bytes(
            good + physical(b"", 0) + good[:4] + good[4:9])
        self.assertEqual(self.read()[b"ok"], b"yes")

    def test_wal_crc_error_is_reported(self):
        self.minimal(last_seq=0)
        good = physical(batch(1, [(b"key", b"value")]))
        self.db.joinpath("000001.log").write_bytes(
            good[:-4] + bytes([good[-4] ^ 1]) + good[-3:])
        with self.assertRaises(state_v2.StateV2Error):
            self.read()

    def test_empty_wal_is_allowed(self):
        self.minimal(last_seq=0)
        self.db.joinpath("000001.log").write_bytes(b"")
        self.assertEqual(self.read(), {b"a": b"old"})

    def test_sequence_continuity_violation_in_basic_set(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(2, [(b"bad", b"x")])])
        with self.assertRaises(state_v2.StateV2Error):
            self.read()

    def test_last_sequence_is_only_a_lower_bound(self):
        self.minimal(last_seq=100)
        self.add_wal(1, [batch(101, [(b"a", b"from-wal")])])
        self.assertEqual(self.read()[b"a"], b"from-wal")

    def test_block_padding_of_one_to_six_bytes(self):
        for padding in range(7):
            with self.subTest(padding=padding):
                self.minimal(last_seq=0)
                value = b"x" * (32741 - padding)
                first = physical(batch(1, [(b"pad", value)]))
                self.assertEqual(len(first), 32768 - padding)
                self.db.joinpath("000001.log").write_bytes(
                    first + b"\x00" * padding
                    + physical(batch(2, [(b"after", b"ok")])))
                values = self.read()
                self.assertEqual(values[b"pad"], value)
                self.assertEqual(values[b"after"], b"ok")

    def test_fragments_across_block_boundaries(self):
        for kinds in ((2, 4), (2, 3, 4)):
            with self.subTest(kinds=kinds):
                self.minimal(last_seq=0)
                payload = batch(1, [(b"frag", b"y" * 32760)])
                parts = [payload[:32761]]
                remaining = payload[32761:]
                if len(kinds) == 3:
                    parts.extend((remaining[:10], remaining[10:]))
                else:
                    parts.append(remaining)
                raw = b"".join(physical(part, kind)
                               for part, kind in zip(parts, kinds))
                self.assertEqual(len(physical(parts[0], kinds[0])), 32768)
                self.db.joinpath("000001.log").write_bytes(raw)
                self.assertEqual(self.read()[b"frag"], b"y" * 32760)

    def test_record_crossing_block_boundary_is_an_error(self):
        self.minimal(last_seq=0)
        first = physical(batch(1, [(b"first", b"x" * 32732)]))
        self.assertEqual(len(first), 32761)
        fake_header = struct.pack("<I", 0) + struct.pack("<H", 100) + b"\x01"
        self.db.joinpath("000001.log").write_bytes(first + fake_header + b"\x00" * 200)
        with self.assertRaisesRegex(state_v2.StateV2Error, "block boundary"):
            self.read()

    def test_bad_length_in_full_block_does_not_lose_next_block(self):
        self.minimal(last_seq=0)
        first = batch(1, [(b"first", b"one")])
        second = batch(2, [(b"second", b"two")])
        bogus = struct.pack("<I", 0) + struct.pack("<H", 0xFFFF) + b"\x01"
        raw = (physical(first) + bogus).ljust(32768, b"\x00") + physical(second)
        self.db.joinpath("000001.log").write_bytes(raw)

        records = []
        with self.assertRaisesRegex(state_v2.StateV2Error,
                                    "corrupted log record length"):
            for record in state_v2._log_records(raw):
                records.append(record)
        self.assertEqual(records, [first, second])
        with self.assertRaisesRegex(state_v2.StateV2Error,
                                    "corrupted log record length"):
            self.read()

    def test_new_record_after_unfinished_fragment_replaces_it(self):
        for kinds in ((2, 1), (2, 2, 4)):
            with self.subTest(kinds=kinds):
                self.minimal(last_seq=0)
                lost = batch(1, [(b"lost", b"discarded")])
                kept = batch(1, [(b"kept", b"recovered")])
                parts = [physical(lost[:10], 2)]
                if kinds == (2, 1):
                    parts.append(physical(kept, 1))
                else:
                    parts.extend((physical(kept[:12], 2),
                                  physical(kept[12:], 4)))
                self.db.joinpath("000001.log").write_bytes(b"".join(parts))
                self.assertEqual(
                    self.read(), {b"a": b"old", b"kept": b"recovered"})

    def test_empty_first_then_last_is_assembled(self):
        self.minimal(last_seq=0)
        value = b"x" * 32733
        first_payload = batch(1, [(b"seed", value)])
        self.assertEqual(len(first_payload), 32754)  # leaves exactly 7 bytes
        kept = batch(2, [(b"kept", b"from-empty-first")])
        self.db.joinpath("000001.log").write_bytes(
            physical(first_payload) + physical(b"", 2) + physical(kept, 4))
        values = self.read()
        self.assertEqual(values[b"seed"], value)
        self.assertEqual(values[b"kept"], b"from-empty-first")

    def test_zero_type_drops_unfinished_fragment_before_next_record(self):
        self.minimal(last_seq=0)
        lost = batch(1, [(b"lost", b"before-padding")])
        kept = batch(1, [(b"kept", b"after-padding")])
        raw = (physical(lost[:10], 2) + physical(b"", 0)).ljust(32768, b"\x00")
        raw += physical(lost[10:], 4) + physical(kept, 1)
        self.db.joinpath("000001.log").write_bytes(raw)
        values = self.read()
        self.assertNotIn(b"lost", values)
        self.assertEqual(values[b"kept"], b"after-padding")

    def test_zero_type_with_payload_checks_crc_then_fails(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                self.minimal(last_seq=0)
                good = physical(batch(1, [(b"ok", b"yes")]))
                zero = physical(b"unexpected", 0)
                if corrupt:
                    zero = zero[:-1] + bytes([zero[-1] ^ 1])
                self.db.joinpath("000001.log").write_bytes(good + zero)
                if corrupt:
                    with self.assertRaisesRegex(state_v2.StateV2Error, "CRC32C"):
                        self.read()
                else:
                    with self.assertRaisesRegex(state_v2.StateV2Error, "zero-type"):
                        self.read()

    def test_unfinished_first_and_middle_drop_whole_logical_record(self):
        for fragments in (1, 2):
            with self.subTest(fragments=fragments):
                self.minimal(last_seq=0)
                ok = physical(batch(1, [(b"ok", b"yes")]))
                payload = batch(2, [(b"lost", b"nope")])
                raw = ok + physical(payload[:10], 2)
                if fragments == 2:
                    raw += physical(payload[10:20], 3)
                self.db.joinpath("000001.log").write_bytes(raw)
                values = self.read()
                self.assertEqual(values[b"ok"], b"yes")
                self.assertNotIn(b"lost", values)

    def test_equal_sequence_conflict_is_explicit_error(self):
        self.current()
        self.add_table(1, [(internal_key(b"same", 1), b"from-table")])
        self.manifest([edit(log_number=1, next_file=9, last_seq=0, live=(1,))])
        self.add_wal(1, [batch(1, [(b"same", b"from-wal")])])
        with self.assertRaisesRegex(state_v2.StateV2Error,
                                    "conflicting values at sequence 1"):
            self.read()

    def test_conflict_is_resolved_on_retry_with_changed_wal(self):
        self.current()
        self.add_table(1, [(internal_key(b"same", 1), b"from-table")])
        self.manifest([edit(log_number=1, next_file=9, last_seq=0, live=(1,))])
        self.add_wal(1, [batch(1, [(b"same", b"from-wal")])])
        original = state_v2._read_bytes
        state = {"fixed": False}

        def fixing(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.suffix == ".log" and not state["fixed"]:
                state["fixed"] = True
                path.write_bytes(physical(batch(1, [(b"other", b"ok")])))
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=fixing):
            values = self.read()
        self.assertTrue(state["fixed"])
        self.assertEqual(values[b"same"], b"from-table")
        self.assertEqual(values[b"other"], b"ok")


class RotationTest(StateCase):
    def test_two_wals_and_lagging_log_number(self):
        self.current()
        self.add_table(1, [(internal_key(b"seed", 1), b"seed")])
        self.manifest([edit(log_number=1, next_file=9, last_seq=0, live=(1,))])
        self.add_wal(1, [batch(1, [(b"one", b"1")])])
        self.add_wal(2, [batch(2, [(b"two", b"2")])])
        self.add_wal(3, [batch(3, [(b"three", b"3")])])
        values = self.read()
        self.assertEqual(values[b"one"], b"1")
        self.assertEqual(values[b"two"], b"2")
        self.assertEqual(values[b"three"], b"3")

    def test_newest_wal_empty(self):
        self.minimal(last_seq=1)
        self.add_wal(1, [batch(2, [(b"a", b"fresh")])])
        self.add_wal(2, [])
        self.assertEqual(self.read()[b"a"], b"fresh")

    def test_old_wal_below_log_number_is_not_applied(self):
        self.current()
        self.add_table(1, [(internal_key(b"a", 1), b"old")])
        self.manifest([edit(log_number=2, next_file=9, last_seq=1, live=(1,))])
        self.add_wal(1, [batch(1, [(b"stale-only", b"do-not-resurrect")])])
        self.add_wal(2, [batch(2, [(b"a", b"new")])])
        values = self.read()
        self.assertNotIn(b"stale-only", values)
        self.assertEqual(values[b"a"], b"new")

    def test_prev_log_number_merged_without_continuity_check(self):
        self.current()
        self.add_table(1, [(internal_key(b"seed", 1), b"seed")])
        self.manifest([edit(log_number=3, next_file=9, last_seq=10,
                            prev=1, live=(1,))])
        self.add_wal(1, [batch(1, [(b"legacy", b"old-value")])])
        self.add_wal(3, [batch(11, [(b"a", b"current")])])
        values = self.read()
        self.assertEqual(values[b"legacy"], b"old-value")
        self.assertEqual(values[b"a"], b"current")

    def test_noncontiguous_prev_wal_does_not_break_basic_set(self):
        self.minimal(last_seq=10, prev=1, log_number=2)
        self.add_wal(1, [batch(4, [(b"legacy", b"v")])])
        self.add_wal(2, [])  # basic WAL with no records
        self.assertEqual(self.read()[b"legacy"], b"v")

    def test_prev_log_number_above_log_number_is_not_checked_for_continuity(self):
        self.current()
        self.add_table(1, [(internal_key(b"seed", 1), b"seed")])
        self.manifest([edit(log_number=2, next_file=9, last_seq=10,
                            prev=3, live=(1,))])
        self.add_wal(2, [batch(11, [(b"current", b"value")])])
        self.add_wal(3, [batch(50, [(b"legacy", b"old-value")])])
        values = self.read()
        self.assertEqual(values[b"current"], b"value")
        self.assertEqual(values[b"legacy"], b"old-value")

    def test_rotation_without_version_edit(self):
        self.current()
        self.add_table(1, [(internal_key(b"seed", 1), b"seed")])
        self.manifest([edit(log_number=1, next_file=9, last_seq=0, live=(1,))])
        self.add_wal(1, [batch(1, [(b"a", b"1")])])
        self.add_wal(2, [batch(2, [(b"a", b"2")])])
        self.add_wal(3, [batch(3, [(b"a", b"3")])])  # no manifest update
        self.assertEqual(self.read()[b"a"], b"3")


class SnapshotTest(StateCase):
    def test_read_does_not_change_database_files(self):
        self.realistic()
        before = self.fingerprint()
        metadata = {}
        values = self.read_state_with_metadata(metadata)
        after = self.fingerprint()
        self.assertGreater(len(values), 100)
        self.assertEqual(set(before), set(after))
        for name in before:
            self.assertEqual(before[name][:5], after[name][:5], name)
            if str(self.db / name) in metadata["noatime_files"]:
                self.assertEqual(before[name][5], after[name][5], name)

    def read_state_with_metadata(self, metadata):
        return state_v2.read_state(self.prefix, metadata=metadata)

    def test_noatime_applied_to_all_read_files(self):
        self.realistic()
        metadata = {}
        self.read_state_with_metadata(metadata)
        applied = metadata["noatime_files"]
        self.assertTrue(applied)
        self.assertTrue(any(item.endswith("000019.log") for item in applied))
        self.assertTrue(any(item.endswith("MANIFEST-000017") for item in applied))

    def test_fallback_to_plain_o_rdonly(self):
        self.realistic()
        fake_os = types.SimpleNamespace(
            O_RDONLY=os.O_RDONLY, O_NOATIME=0, open=os.open, fdopen=os.fdopen)
        metadata = {}
        with mock.patch.object(state_v2, "os", fake_os):
            values = self.read_state_with_metadata(metadata)
        self.assertTrue(values)
        self.assertEqual(metadata["noatime_files"], frozenset())

    def test_o_noatime_failure_falls_back(self):
        self.realistic()
        real_open = os.open

        def fake_open(path, flags, *args, **kwargs):
            if flags & os.O_NOATIME:
                raise PermissionError("O_NOATIME refused")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=fake_open):
            values = self.read()
        self.assertTrue(values)

    def test_wal_change_during_read_is_retried(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        original = state_v2._read_bytes
        state = {"append": True}

        def appending(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.suffix == ".log" and state["append"]:
                state["append"] = False
                with open(path, "ab") as handle:
                    handle.write(b"\x00" * 10)
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=appending):
            values = self.read()
        self.assertEqual(values[b"a"], b"fresh")

    def test_continuous_change_exhausts_retries(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        original = state_v2._read_bytes

        def appending(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.suffix == ".log":
                with open(path, "ab") as handle:
                    handle.write(b"\x00" * 10)
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=appending):
            with self.assertRaises(state_v2.StateV2Error):
                self.read()

    def test_manifest_append_and_new_wal_are_detected(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        original = state_v2._read_bytes
        state = {"done": False}

        def changing(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if not state["done"]:
                state["done"] = True
                with open(path.parent / "000002.log", "wb") as handle:
                    handle.write(b"")
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=changing):
            values = self.read()
        self.assertEqual(values[b"a"], b"fresh")

    def test_manifest_append_is_detected(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        original = state_v2._read_bytes
        state = {"appended": False}

        def appending(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.name == "MANIFEST-000001" and not state["appended"]:
                state["appended"] = True
                with open(path, "ab") as handle:
                    handle.write(physical(edit(comparator=None)))
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=appending):
            values = self.read()
        self.assertEqual(values[b"a"], b"fresh")
        self.assertTrue(state["appended"])

    def test_current_swap_is_detected(self):
        self.current("MANIFEST-000001")
        payload = edit(log_number=1, next_file=9, last_seq=0, live=(1,))
        self.manifest([payload])
        self.manifest([payload], name="MANIFEST-000002")
        self.add_table(1, [(internal_key(b"a", 1), b"v")])
        original = state_v2._read_bytes
        state = {"swapped": False}

        def swapping(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.name == "CURRENT" and not state["swapped"]:
                state["swapped"] = True
                path.write_bytes(b"MANIFEST-000002\n")
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=swapping):
            self.assertEqual(self.read()[b"a"], b"v")

    def test_oserror_while_stating_a_wal_is_retried(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        real_stat = Path.stat
        state = {"fail": True}

        def flaky_stat(path, *args, **kwargs):
            if state["fail"] and path.suffix == ".log":
                state["fail"] = False
                raise FileNotFoundError(str(path))
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(state_v2.Path, "stat", flaky_stat):
            values = self.read()
        self.assertFalse(state["fail"])
        self.assertEqual(values[b"a"], b"fresh")

    def test_persistent_oserror_exhausts_retries_with_snapshot_error(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        attempts = {"count": 0}

        def denied(*_args, **_kwargs):
            attempts["count"] += 1
            raise PermissionError("access denied")

        with mock.patch.object(state_v2, "_read_bytes", side_effect=denied):
            with self.assertRaises(state_v2.StateV2Error) as caught:
                self.read()
        self.assertEqual(attempts["count"], state_v2.MAX_ATTEMPTS)
        self.assertIn("snapshot", str(caught.exception))
        self.assertIn("access denied", str(caught.exception))

    def test_valid_wal_append_during_read_is_retried(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [(b"a", b"fresh")])])
        original = state_v2._read_bytes
        state = {"done": False}

        def appending(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.suffix == ".log" and not state["done"]:
                state["done"] = True
                with open(path, "ab") as handle:
                    handle.write(physical(batch(2, [(b"appended", b"yes")])))
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=appending):
            values = self.read()
        self.assertTrue(state["done"])
        self.assertEqual(values[b"a"], b"fresh")
        self.assertEqual(values[b"appended"], b"yes")

    def test_sstable_replacement_during_read_is_retried(self):
        self.minimal(last_seq=0)
        original = state_v2._read_bytes
        state = {"done": False}

        def replacing(path, noatime_files, *, size=None):
            data = original(path, noatime_files, size=size)
            if path.suffix == ".ldb" and not state["done"]:
                state["done"] = True
                path.write_bytes(table([
                    (internal_key(b"a", 0), b"replaced-with-a-longer-value")]))
            return data

        with mock.patch.object(state_v2, "_read_bytes", side_effect=replacing):
            values = self.read()
        self.assertTrue(state["done"])
        self.assertEqual(values[b"a"], b"replaced-with-a-longer-value")

    def test_missing_state_directory(self):
        shutil.rmtree(self.db)
        with self.assertRaises(state_v2.StateV2Error):
            self.read()

    def test_missing_setting_is_not_invented(self):
        self.minimal(last_seq=0)
        self.assertNotIn(b"settings###downloads###path", self.read())


class StateValuesTest(StateCase):
    def test_discovered_games_from_synthetic_snapshot(self):
        self.realistic()
        values = self.read()
        games = {
            key.split(b"###")[3].decode()
            for key in values
            if key.startswith(b"settings###gameMode###discovered###")
            and key.endswith(b"###path")
        }
        self.assertIn("batmanarkhamknight", games)
        self.assertIn("falloutnv", games)
        self.assertTrue(all(isinstance(values[k], bytes) for k in values))

    def test_second_synthetic_snapshot_is_read(self):
        self.realistic()
        values = self.read()
        self.assertGreater(len(values), 20)
        self.assertTrue(any(key.startswith(b"app###") for key in values))
        self.assertTrue(all(isinstance(value, bytes) for value in values.values()))

    def test_user_staging_path_and_custom_downloads_path(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [
            (b"settings###mods###installPath###game1", b'"Z:\\\\mods\\\\game1"'),
            (b"settings###downloads###path", b'"C:\\\\users\\\\steamuser\\\\Downloads2"'),
        ])])
        values = self.read()
        self.assertEqual(values[b"settings###mods###installPath###game1"],
                         b'"Z:\\\\mods\\\\game1"')
        self.assertEqual(values[b"settings###downloads###path"],
                         b'"C:\\\\users\\\\steamuser\\\\Downloads2"')

    def test_standard_downloads_path_value(self):
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, [
            (b"settings###downloads###path", b'"C:\\\\users\\\\steamuser\\\\Downloads"'),
        ])])
        self.assertEqual(self.read()[b"settings###downloads###path"],
                         b'"C:\\\\users\\\\steamuser\\\\Downloads"')

    def test_windows_paths_use_existing_helpers(self):
        target = self.root / "host"
        target.mkdir()
        dosdevices = self.prefix / "pfx" / "dosdevices"
        dosdevices.mkdir(parents=True)
        (dosdevices / "z:").symlink_to(target)
        z_root = extension_installer._z_root(self.prefix)
        self.assertEqual(z_root, target.resolve())
        self.assertEqual(
            extension_installer._host_path_from_wine(r"Z:\home\deck\Downloads", z_root),
            target / "home" / "deck" / "Downloads",
        )
        self.assertEqual(
            extension_installer._host_path_from_wine("Z:/home/deck/Downloads", z_root),
            target / "home" / "deck" / "Downloads",
        )
        self.assertIsNone(
            extension_installer._host_path_from_wine(r"C:\users\steamuser", z_root))
        self.assertEqual(installer._wine_path(self.prefix, target / "abc"), "Z:\\abc")
        self.assertEqual(registry._normalise_windows_path("C:/Users/SteamUser"),
                         "c:\\users\\steamuser")

    def test_windows_c_drive_uses_prefix_drive_c(self):
        dosdevices = self.prefix / "pfx" / "dosdevices"
        dosdevices.mkdir(parents=True)
        (dosdevices / "c:").symlink_to("../drive_c")
        host_path = (self.prefix / "pfx" / "drive_c" / "users"
                     / "steamuser" / "Downloads")
        wine_path = installer._wine_path(self.prefix, host_path)
        self.assertEqual(wine_path, r"C:\users\steamuser\Downloads")
        self.assertEqual(
            registry._normalise_windows_path(r"C:\Users\SteamUser\Downloads"),
            r"c:\users\steamuser\downloads")

    def test_staging_path_for_every_discovered_game(self):
        games = ("game-one", "game-two")
        discovered = [
            (f"settings###gameMode###discovered###{game}###path".encode(),
             f'"Z:\\\\games\\\\{game}"'.encode())
            for game in games
        ]
        staging = [
            (f"settings###mods###installPath###{game}".encode(),
             f'"Z:\\\\mods\\\\{game}"'.encode())
            for game in games
        ]
        self.minimal(last_seq=0)
        self.add_wal(1, [batch(1, discovered + staging)])
        values = self.read()
        found = {
            key.split(b"###")[3].decode()
            for key in values
            if key.startswith(b"settings###gameMode###discovered###")
            and key.endswith(b"###path")
        }
        self.assertEqual(found, set(games))
        for game in games:
            self.assertEqual(
                values[f"settings###mods###installPath###{game}".encode()],
                f'"Z:\\\\mods\\\\{game}"'.encode())


if __name__ == "__main__":
    unittest.main(verbosity=2)
