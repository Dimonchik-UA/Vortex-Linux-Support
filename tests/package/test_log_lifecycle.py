import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import logpump  # noqa: E402
import package  # noqa: E402
import proton  # noqa: E402


def fake_proton_script(marker):
    return (
        "#!/bin/sh\n"
        "i=0\n"
        "while [ $i -lt 300 ]; do\n"
        "  printf '%01000d\\\\n' 0\n"
        "  i=$((i+1))\n"
        "done\n"
        f"echo {marker}\n"
    )


def feed_pipe(data, chunk=4096):
    """Return (read_fd, thread) where the thread writes ``data`` and closes."""
    read_fd, write_fd = os.pipe()

    def writer():
        try:
            for offset in range(0, len(data), chunk):
                os.write(write_fd, data[offset:offset + chunk])
        finally:
            os.close(write_fd)

    thread = threading.Thread(target=writer)
    thread.start()
    return read_fd, thread


class LogPumpTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-logpump-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.log = self.root / "launch.log"

    def run_pump(self, data, max_bytes=1000, backups=2):
        read_fd, thread = feed_pipe(data)
        try:
            logpump.pump(str(self.log), max_bytes, backups, source=read_fd)
        finally:
            os.close(read_fd)
            thread.join()

    def test_long_stream_stays_within_fixed_file_set(self):
        data = b"".join(b"line %06d %s\n" % (n, b"x" * 80) for n in range(3000))
        self.run_pump(data)

        self.assertLessEqual(self.log.stat().st_size, 1000)
        self.assertEqual(Path(f"{self.log}.1").stat().st_size, 1000)
        self.assertEqual(Path(f"{self.log}.2").stat().st_size, 1000)
        self.assertFalse(Path(f"{self.log}.3").exists())
        retained = Path(f"{self.log}.2").read_bytes() + Path(f"{self.log}.1").read_bytes()
        retained += self.log.read_bytes()
        self.assertEqual(retained, data[-len(retained):])

    def test_restart_rotates_existing_large_file(self):
        self.log.write_bytes(b"A" * 1000)
        self.run_pump(b"B" * 300)

        self.assertEqual(self.log.read_bytes(), b"B" * 300)
        self.assertEqual(Path(f"{self.log}.1").read_bytes(), b"A" * 1000)
        self.assertFalse(Path(f"{self.log}.2").exists())

    def test_repeated_restarts_never_grow_past_backup_count(self):
        for run in range(6):
            self.run_pump(bytes([65 + run]) * 2500)
        names = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(names, ["launch.log", "launch.log.1", "launch.log.2"])

    def test_pump_never_writes_through_a_symlink(self):
        target = self.root / "victim.txt"
        target.write_text("keep me", encoding="utf-8")
        self.log.symlink_to(target)

        self.run_pump(b"noise " * 500)

        self.assertEqual(target.read_text(encoding="utf-8"), "keep me")

    def test_pump_drains_input_when_log_cannot_be_opened(self):
        blocked = self.root / "not-a-dir"
        blocked.write_text("x", encoding="utf-8")
        read_fd, thread = feed_pipe(b"z" * 20000)
        try:
            logpump.pump(str(blocked / "launch.log"), 1000, 2, source=read_fd)
        finally:
            os.close(read_fd)
            thread.join()

    def run_with_failed_rotation(self, data):
        """Rotate partially, fail to open the new active file, keep draining."""
        self.log.write_bytes(b"A" * 1000)
        Path(f"{self.log}.1").write_bytes(b"B" * 1000)
        read_fd, thread = feed_pipe(data)
        real_open = logpump._open
        calls = {"count": 0}

        def open_once(path):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated failure to open the new active log")
            return real_open(path)

        try:
            with mock.patch.object(logpump, "_open", side_effect=open_once):
                with mock.patch.object(
                    logpump.os,
                    "ftruncate",
                    side_effect=AssertionError("ftruncate must not be used"),
                ):
                    logpump.pump(str(self.log), 1000, 2, source=read_fd)
        finally:
            os.close(read_fd)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "pump stopped draining the pipe")
        self.assertEqual(calls["count"], 2)

    def test_partial_rotation_failure_preserves_rotated_files(self):
        self.run_with_failed_rotation(b"z" * 200_000)

        self.assertEqual(Path(f"{self.log}.1").read_bytes(), b"A" * 1000)
        self.assertEqual(Path(f"{self.log}.2").read_bytes(), b"B" * 1000)
        self.assertFalse(self.log.exists())
        for path in (self.log, Path(f"{self.log}.1"), Path(f"{self.log}.2")):
            if path.exists():
                self.assertLessEqual(path.stat().st_size, 1000)

    def test_rotation_failure_drains_pipe_without_ftruncate(self):
        self.run_with_failed_rotation(b"z" * 500_000)
        self.assertFalse(self.log.exists())


class SpawnVortexLogTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-spawn-log-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.processes = []
        self.addCleanup(self._stop_test_processes)
        self.proton = self.make_proton("END")
        self.vortex = self.root / "Vortex.exe"
        self.vortex.write_bytes(b"MZ")
        self.prefix = self.root / "compatdata" / "1"
        (self.prefix / "pfx").mkdir(parents=True)
        self.steam = self.root / "steam"
        self.steam.mkdir()

    def _stop_test_processes(self):
        for process in self.processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
        self.processes.clear()

    def make_proton(self, marker):
        path = self.root / f"proton-{marker}"
        path.write_text(fake_proton_script(marker), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def spawn(self, log_path, proton_path=None):
        real_popen = subprocess.Popen
        processes = self.processes

        def record(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with mock.patch.object(proton.subprocess, "Popen", record):
            return proton.spawn_vortex(
                str(proton_path or self.proton),
                str(self.vortex),
                str(self.prefix),
                steam_client_path=str(self.steam),
                log_path=str(log_path),
            )

    def wait_for_end(self, log, marker="END", timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if log.exists() and log.read_bytes().endswith(marker.encode() + b"\n"):
                return
            time.sleep(0.05)
        self.fail(f"child output did not reach {log}")

    def test_long_child_session_is_rotated_while_it_writes(self):
        log = self.root / "state" / "ovls" / "launch.log"
        with mock.patch.object(proton, "LOG_MAX_BYTES", 50_000):
            result = self.spawn(log)
            self.wait_for_end(log)

        self.assertEqual(result["log"], str(log))
        self.assertLessEqual(log.stat().st_size, 50_000)
        self.assertEqual(Path(f"{log}.1").stat().st_size, 50_000)
        self.assertEqual(Path(f"{log}.2").stat().st_size, 50_000)
        self.assertFalse(Path(f"{log}.3").exists())

    def test_relaunch_keeps_file_set_bounded(self):
        log = self.root / "data" / "ovls" / "nxm.log"
        with mock.patch.object(proton, "LOG_MAX_BYTES", 50_000):
            self.spawn(log)
            self.wait_for_end(log, "END")
            second = self.make_proton("END2")
            self.spawn(log, proton_path=second)
            self.wait_for_end(log, "END2")
        names = sorted(p.name for p in log.parent.iterdir())
        self.assertEqual(names, ["nxm.log", "nxm.log.1", "nxm.log.2"])

    def test_unwritable_log_directory_disables_log_without_tmp_fallback(self):
        tmp = self.root / "tmp"
        tmp.mkdir()
        blocked = self.root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TMPDIR": str(tmp)}):
            result = self.spawn(blocked / "launch.log")
        self.assertIsNone(result["log"])
        self.assertIsInstance(result["pid"], int)
        self.assertEqual(list(tmp.iterdir()), [])


class UserLogCleanupTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-logs-cleanup-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.data = self.root / "data"
        self.state = self.root / "state"
        self.env = mock.patch.dict(
            os.environ,
            {"XDG_DATA_HOME": str(self.data), "XDG_STATE_HOME": str(self.state)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_uninstall_removes_only_ovls_log_files(self):
        state_dir = self.state / "ovls"
        data_dir = self.data / "ovls"
        state_dir.mkdir(parents=True)
        data_dir.mkdir(parents=True)
        owned = [
            state_dir / "launch.log",
            state_dir / "launch.log.1",
            state_dir / "launch.log.2",
            data_dir / "nxm.log",
            data_dir / "nxm.log.1",
            data_dir / "nxm.log.2",
        ]
        foreign = [
            state_dir / "launch.log.bak",
            state_dir / "launch.log.3",
            state_dir / "notes.txt",
            data_dir / "nxm.log.old",
        ]
        for path in owned + foreign:
            path.write_text("x", encoding="utf-8")

        result = package.remove_user_logs()

        self.assertEqual(sorted(result["removed"]), sorted(str(p) for p in owned))
        for path in owned:
            self.assertFalse(path.exists())
        for path in foreign:
            self.assertTrue(path.exists())

    def test_code_copy_log_rotations_are_removed(self):
        root = self.root / "copy"
        root.mkdir()
        for name in ("ovls.log", "ovls.log.1", "ovls.log.2", "ovls.log.3", "keep.py"):
            (root / name).write_text("x", encoding="utf-8")

        package._remove_runtime_artifacts(root)

        self.assertEqual(
            sorted(p.name for p in root.iterdir()), ["keep.py", "ovls.log.3"]
        )



class CliBackupRetentionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ovls-cli-backups-", dir=str(HERE)))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_cli_stamp_is_pruned_with_package_snapshots(self):
        stamps = []
        for _ in range(5):
            stamp = package._stamp()
            stamps.append(stamp)
            (self.root / stamp / "extension").mkdir(parents=True)
            time.sleep(0.001)
        (self.root / "arbitrary-name").mkdir()

        package._cleanup_old_backups(self.root)

        kept = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(kept, sorted(stamps[-3:] + ["arbitrary-name"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
