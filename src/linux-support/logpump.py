"""Size-bounded log writer for the stdout/stderr of a detached Vortex launch.

Reads a pipe and appends it to ``path``. When the file reaches ``max_bytes`` it
is rotated to ``path.1`` ... ``path.<backups>``; the oldest copy is dropped. The
file set therefore stays bounded however long Proton keeps writing.

The process exits on EOF, i.e. when every writer has closed the pipe. It imports
nothing from OVLS and never stops draining its input: if the file cannot be
written, the bytes are discarded so the Vortex process never gets SIGPIPE.

Usage: ``python3 logpump.py <path> <max_bytes> <backups>``
"""

import os
import sys

CHUNK = 65536


def _open(path):
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)


def _rotate(path, backups):
    oldest = f"{path}.{backups}"
    if os.path.lexists(oldest):
        os.unlink(oldest)
    for index in range(backups - 1, 0, -1):
        if os.path.lexists(f"{path}.{index}"):
            os.replace(f"{path}.{index}", f"{path}.{index + 1}")
    if os.path.lexists(path):
        os.replace(path, f"{path}.1")


def _roll(fd, path, backups):
    """Return a fresh (fd, size), or (None, None) if logging must stop.

    A failed rotation leaves ``fd`` pointing at an already renamed file, so
    truncating it would corrupt a backup. Close it and stop writing instead;
    the caller keeps draining the pipe.
    """
    try:
        _rotate(path, backups)
        new_fd = _open(path)
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None, None
    os.close(fd)
    return new_fd, 0


def pump(path, max_bytes, backups, source=0):
    try:
        fd = _open(path)
        size = os.fstat(fd).st_size
    except OSError:
        fd, size = None, 0

    while True:
        try:
            chunk = os.read(source, CHUNK)
        except OSError:
            break
        if not chunk:
            break
        if fd is None:
            continue
        data = memoryview(chunk)
        while data:
            if size >= max_bytes:
                fd, size = _roll(fd, path, backups)
                if fd is None:
                    break
            try:
                written = os.write(fd, data[: max_bytes - size])
            except OSError:
                break
            if written <= 0:
                break
            size += written
            data = data[written:]

    if fd is not None:
        os.close(fd)


if __name__ == "__main__":
    pump(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
