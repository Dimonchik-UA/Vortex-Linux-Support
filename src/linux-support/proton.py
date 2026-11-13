import os
import glob
import sys
import subprocess


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


def default_proton():
    """Return the Proton executable OVLS uses when the user does not choose one.

    Proton-GE is preferred because Proton 11.0 reproduces the window defect
    (outer KDE frame, white band, click offset) — see IS-053.
    """
    versions = find_proton_versions()
    ge = [path for path in versions if is_ge_proton(path)]
    if ge:
        latest = [path for path in ge if "latest" in os.path.basename(path).lower()]
        return os.path.join((latest or sorted(ge))[-1], "proton")
    preferred = [path for path in versions if "11.0" in os.path.basename(path)]
    if preferred:
        return os.path.join(preferred[0], "proton")
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
                    log_handle = open(candidate, "ab")
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
