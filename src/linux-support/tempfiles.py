"""Safe cleanup for explicitly named OVLS temporary files and directories."""

import errno
import os
import re
import shutil
from pathlib import Path


def process_is_alive(pid):
    """Treat unknown permission/runtime errors as alive; only ESRCH is proof of exit."""
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    except (OverflowError, ValueError):
        return True


STDLIB_RANDOM_TOKEN = r"[a-z0-9_]{8}"


def cleanup_orphaned_files(directory, prefix, suffix=".tmp", token=STDLIB_RANDOM_TOKEN):
    """Remove exact prefix/PID/random/suffix files only after the owner exits."""
    pattern = re.compile(
        re.escape(prefix) + r"(\d{1,10})-" + token + re.escape(suffix) + r"\Z"
    )
    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if not match:
            continue
        try:
            if entry.is_file() and not entry.is_symlink() and not process_is_alive(int(match.group(1))):
                entry.unlink()
        except OSError:
            pass


def cleanup_orphaned_dirs(directory, prefix, suffix="", token=STDLIB_RANDOM_TOKEN):
    """Remove exact prefix/PID/random/suffix directories only after owner exit."""
    pattern = re.compile(
        re.escape(prefix) + r"(\d{1,10})-" + token + re.escape(suffix) + r"\Z"
    )
    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if not match:
            continue
        try:
            if entry.is_dir() and not entry.is_symlink() and not process_is_alive(int(match.group(1))):
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            pass
