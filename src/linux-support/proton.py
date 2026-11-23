import os
import glob
import sys
import subprocess
import threading


# Log rotation constants for subprocess logs
LOG_MAX_BYTES = 1_000_000  # 1 MB
LOG_BACKUP_COUNT = 2       # 2 backups = ~3 MB total


class RotatingSubprocessLog:
    """File-like object that rotates at size limit, for subprocess stdout/stderr.

    Behaves like a regular file opened in 'ab' mode, but rotates when size
    exceeds LOG_MAX_BYTES, keeping LOG_BACKUP_COUNT backups.
    """

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        self._lock = threading.Lock()
        self._fh = None
        self._open()

    def _open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fh = open(self.path, "ab")

    def _rotate(self):
        """Rotate log file: log -> log.1, log.1 -> log.2, etc."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        # Remove oldest backup
        oldest = f"{self.path}.{LOG_BACKUP_COUNT}"
        if os.path.exists(oldest):
            try:
                os.remove(oldest)
            except OSError:
                pass
        # Shift existing backups
        for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
            src = f"{self.path}.{i}"
            dst = f"{self.path}.{i + 1}"
            if os.path.exists(src):
                try:
                    os.rename(src, dst)
                except OSError:
                    pass
        # Rotate current
        if os.path.exists(self.path):
            try:
                os.rename(self.path, f"{self.path}.1")
            except OSError:
                pass
        self._open()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self._lock:
            if self._fh is None:
                self._open()
            # Check size before write
            if self._fh.tell() + len(data) > LOG_MAX_BYTES:
                self._rotate()
            self._fh.write(data)

    def flush(self):
        with self._lock:
            if self._fh is not None:
                self._fh.flush()

    def close(self):
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def fileno(self):
        """Return file descriptor for subprocess.Popen."""
        with self._lock:
            if self._fh is None:
                self._open()
            return self._fh.fileno()


def find_proton_versions():
    steam_root = None
    import steam
    steam_root = steam.find_steam_root()
    if not steam_root:
        return []
    compat_dir = os.path.join(steam_root, "steamapps", "common")
    versions = []
    if os.path.isdir(compat_dir):
        for name in os.listdir(compat_dir):
            if "Proton" in name:
                versions.append(os.path.join(compat_dir, name))
    tools_dir = os.path.join(steam_root, "compatibilitytools.d")
    if os.path.isdir(tools_dir):
        for name in sorted(os.listdir(tools_dir)):
            path = os.path.join(tools_dir, name)
            if os.path.isfile(os.path.join(path, "proton")):
                versions.append(path)
    return versions


def is_ge_proton(proton_dir):
    name = os.path.basename(proton_dir).lower()
    return name.startswith("ge-proton") or "proton-ge" in name


def is_recommended_proton(proton_dir):
    """DW-Proton and Proton-GE are recommendations, never requirements."""
    name = os.path.basename(proton_dir).lower()
    return is_ge_proton(name) or "dw-proton" in name


def selected_proton(prefix=None):
    """Resolve the Proton tool Steam selected for ``prefix`` to an executable.

    Returns ``None`` when Steam has no mapping or the selected tool is not
    installed. Never changes the selection.
    """
    if not prefix:
        return None
    import steam
    name = steam.selected_proton_name(prefix)
    if not name:
        return None
    wanted = name.lower()
    for path in find_proton_versions():
        if os.path.basename(path).lower() == wanted:
            return os.path.join(path, "proton")
    root = steam.find_steam_root()
    if not root:
        return None
    for directory in ("compatibilitytools.d", os.path.join("steamapps", "common")):
        candidate = os.path.join(root, directory, name, "proton")
        if os.path.isfile(candidate):
            return candidate
    return None


def default_proton(prefix=None):
    """Return the Proton executable OVLS uses when the user did not pass one.

    The tool selected in Steam for this prefix wins. Otherwise a recommended
    tool (DW-Proton / Proton-GE) is preferred; no specific version is forced.
    """
    if prefix:
        chosen = selected_proton(prefix)
        if chosen:
            return chosen
    versions = find_proton_versions()
    recommended = [path for path in versions if is_recommended_proton(path)]
    if recommended:
        latest = [path for path in recommended if "latest" in os.path.basename(path).lower()]
        return os.path.join((latest or sorted(recommended))[-1], "proton")
    if versions:
        return os.path.join(sorted(versions)[0], "proton")
    raise ValueError("Proton not found; pass --proton explicitly")


def inspect_prefix(prefix_path):
    result = {
        "exists": os.path.isdir(prefix_path),
        "has_pfx": False,
        "has_drive_c": False,
        "has_vortex": False,
    }
    if not result["exists"]:
        return result
    pfx = os.path.join(prefix_path, "pfx")
    result["has_pfx"] = os.path.isdir(pfx)
    drive_c = os.path.join(pfx, "drive_c") if result["has_pfx"] else os.path.join(prefix_path, "drive_c")
    result["has_drive_c"] = os.path.isdir(drive_c)
    if result["has_drive_c"]:
        vortex_path = os.path.join(drive_c, "users", "steamuser", "AppData", "Roaming", "Vortex")
        result["has_vortex"] = os.path.isdir(vortex_path)
        result["vortex_path"] = vortex_path
    return result


def _prepare_launch(proton_path, vortex_path, prefix_path, steam_client_path=None, vortex_args=None):
    proton_path = os.path.abspath(os.path.expanduser(proton_path))
    vortex_path = os.path.abspath(os.path.expanduser(vortex_path))
    prefix_path = os.path.abspath(os.path.expanduser(prefix_path))

    if not os.path.isfile(proton_path):
        raise ValueError(f"Proton executable not found: {proton_path}")
    if not os.access(proton_path, os.X_OK):
        raise ValueError(f"Proton path is not executable: {proton_path}")
    if not os.path.isfile(vortex_path):
        raise ValueError(f"Vortex executable not found: {vortex_path}")

    prefix_info = inspect_prefix(prefix_path)
    if not prefix_info["exists"] or not prefix_info["has_pfx"]:
        raise ValueError(f"Not a Proton compatdata directory (expected pfx/): {prefix_path}")

    if steam_client_path:
        steam_client_path = os.path.abspath(os.path.expanduser(steam_client_path))
    else:
        steam_client_path = os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH")
        if not steam_client_path:
            import steam
            steam_client_path = steam.find_steam_root()
        if steam_client_path:
            steam_client_path = os.path.abspath(os.path.expanduser(steam_client_path))

    if not steam_client_path or not os.path.isdir(steam_client_path):
        raise ValueError(
            "Steam client directory not found; pass --steam-client or set "
            "STEAM_COMPAT_CLIENT_INSTALL_PATH"
        )

    env = os.environ.copy()
    env["STEAM_COMPAT_DATA_PATH"] = prefix_path
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = steam_client_path
    # Performance: async shader compilation, better D3D12, large address space for 32-bit Vortex
    env.setdefault("DXVK_ASYNC", "1")
    env.setdefault("VKD3D_CONFIG", "multi_queue")
    env.setdefault("WINE_LARGE_ADDRESS_AWARE", "1")

    command = [proton_path, "run", vortex_path]
    extra = list(vortex_args or [])
    # the `--` that separates `ovls.py launch ... -- <vortex args>` is consumed by
    # argparse only when a positional exists; argparse.REMAINDER keeps it, and it
    # would be forwarded into the Windows argv of Vortex.exe where it terminates
    # Chromium switch parsing (verified in vortex.log: argv ["Vortex.exe","--","..."]).
    if extra and extra[0] == "--":
        extra = extra[1:]
    command.extend(extra)
    return command, env


def launch_vortex(proton_path, vortex_path, prefix_path, steam_client_path=None, vortex_args=None):
    command, env = _prepare_launch(
        proton_path, vortex_path, prefix_path, steam_client_path, vortex_args
    )
    return subprocess.run(command, env=env, check=False).returncode


def spawn_vortex(
    proton_path,
    vortex_path,
    prefix_path,
    steam_client_path=None,
    vortex_args=None,
    log_path=None,
):
    """Start Vortex detached and return immediately.

    Used by the NXM handler: the desktop process must exit while Vortex keeps
    running. Electron's single-instance lock forwards the ``-d`` argument to an
    already running Vortex, so the same path works for first and repeated links.
    """
    command, env = _prepare_launch(
        proton_path, vortex_path, prefix_path, steam_client_path, vortex_args
    )
    log_handle = None
    resolved_log = None
    try:
        if log_path:
            resolved_log = os.path.abspath(os.path.expanduser(log_path))
            candidates = [resolved_log]
            fallback_name = "ovls-" + os.path.basename(resolved_log)
            for fallback in (os.environ.get("TMPDIR"), "/tmp"):
                if fallback:
                    candidates.append(os.path.join(fallback.rstrip("/"), fallback_name))
            for candidate in candidates:
                try:
                    os.makedirs(os.path.dirname(candidate) or ".", exist_ok=True)
                    log_handle = RotatingSubprocessLog(candidate)
                    resolved_log = candidate
                    break
                except OSError:
                    continue
            if log_handle is None:
                print(
                    "NXM handler: log disabled (state directory is read-only); "
                    "launching Vortex anyway",
                    file=sys.stderr,
                )
        stdout = log_handle if log_handle is not None else subprocess.DEVNULL
        process = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=os.path.expanduser("~"),
        )
    finally:
        if log_handle is not None:
            log_handle.close()
    return {"pid": process.pid, "log": resolved_log}
