import os
import sys
import subprocess


# Each launch log keeps at most LOG_BACKUP_COUNT rotated copies next to the
# current file, so a log never exceeds (LOG_BACKUP_COUNT + 1) * LOG_MAX_BYTES.
LOG_MAX_BYTES = 1_000_000  # 1 MB
LOG_BACKUP_COUNT = 2       # 2 backups = ~3 MB total per log
LOGPUMP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logpump.py")


def _start_log_pump(log_path):
    """Start a detached logpump for ``log_path`` and return the pipe's write end.

    Proton writes straight into the pipe, so rotation has to happen in a
    process that outlives the Vortex launch; ``logpump`` exits on EOF.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    descriptor = os.open(
        log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    os.close(descriptor)
    read_fd, write_fd = os.pipe()
    try:
        subprocess.Popen(
            [
                sys.executable,
                LOGPUMP_PATH,
                log_path,
                str(LOG_MAX_BYTES),
                str(LOG_BACKUP_COUNT),
            ],
            stdin=read_fd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except BaseException:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    return write_fd


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
        # Use the installer's profile resolver instead of assuming the Windows
        # account is named steamuser. An ambiguous prefix is reported as
        # "no Vortex profile" rather than guessing one of the candidates.
        import extension_installer
        try:
            vortex_dir, _plugins_dir = extension_installer.resolve_plugin_directory(prefix_path)
        except (OSError, ValueError):
            result["vortex_path"] = None
        else:
            result["vortex_path"] = str(vortex_dir)
            result["has_vortex"] = vortex_dir.is_dir()
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
    write_fd = None
    resolved_log = None
    try:
        if log_path:
            resolved_log = os.path.abspath(os.path.expanduser(log_path))
            try:
                write_fd = _start_log_pump(resolved_log)
            except OSError:
                resolved_log = None
                print(
                    "NXM handler: log disabled (log directory is not writable); "
                    "launching Vortex anyway",
                    file=sys.stderr,
                )
        stdout = write_fd if write_fd is not None else subprocess.DEVNULL
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
        if write_fd is not None:
            os.close(write_fd)
    return {"pid": process.pid, "log": resolved_log}
