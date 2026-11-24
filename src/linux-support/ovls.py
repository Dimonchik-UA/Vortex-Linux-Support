#!/usr/bin/env python3
import argparse
import json
import os
import sys
import subprocess
import time
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import core
import layout
import steam
import proton
import filesystem
import diagnostics
import extension_installer
import installer
import desktop
import package
import launcher
import manager
import setup_ui
import shortcuts

DEFAULT_EXTENSION = str(layout.ROOT / layout.EXTENSION_TREE)

LOG_PATH = os.path.join(BASE_DIR, "ovls.log")
logger = core.setup_logging(LOG_PATH)

# IPC cleanup: remove stale files older than 30 days
IPC_TTL_DAYS = 30
IPC_TTL_SECONDS = IPC_TTL_DAYS * 86400
IPC_CLEANUP_DIRS = ("dedup", "processed", "client_state")


def cleanup_ipc_stale_files(prefix_path: str):
    """Remove IPC files older than TTL days.
    
    Called at startup to prevent unbounded growth of dedup/, processed/, client_state/.
    """
    ipc_root = os.path.join(prefix_path, "drive_c", "users", "steamuser", "AppData", "Roaming", "Vortex", "OVLS_IPC_V2")
    if not os.path.isdir(ipc_root):
        return
    cutoff = time.time() - IPC_TTL_SECONDS
    for subdir in IPC_CLEANUP_DIRS:
        d = os.path.join(ipc_root, subdir)
        if not os.path.isdir(d):
            continue
        for fname in os.listdir(d):
            fpath = os.path.join(d, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except OSError:
                pass


def cmd_status(args):
    steam_root = steam.find_steam_root()
    info = {
        "app": core.APP_NAME,
        "version": core.VERSION,
        "steam_root": steam_root,
        "proton_versions": proton.find_proton_versions() if steam_root else [],
    }
    core.print_status(info)


def cmd_discover(args):
    steam_root = steam.find_steam_root()
    if not steam_root:
        print("Steam not found")
        return 1
    games = steam.discover_games(steam_root)
    print(json.dumps({"steam_root": steam_root, "games": games}, indent=2, ensure_ascii=False))
    return 0


def cmd_inspect_prefix(args):
    result = proton.inspect_prefix(args.prefix)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_launch(args):
    try:
        # Default Electron args for better Wine performance; can be overridden via OVLS_VORTEX_ARGS
        if os.environ.get("OVLS_VORTEX_ARGS"):
            extra_args = os.environ["OVLS_VORTEX_ARGS"].split()
        else:
            extra_args = ["--disable-gpu"]
        if args.vortex_args:
            extra_args.extend(args.vortex_args)
        return proton.launch_vortex(
            args.proton or _default_proton(args.prefix),
            args.vortex,
            args.prefix,
            steam_client_path=args.steam_client,
            vortex_args=extra_args,
        )
    except (OSError, ValueError) as exc:
        print(f"Launch failed: {exc}", file=sys.stderr)
        return 2


def _default_proton(prefix=None):
    return proton.default_proton(prefix)


def cmd_nxm(args):
    uri = (args.uri or "").strip()
    if not uri.lower().startswith("nxm://"):
        print("NXM handler: expected an nxm:// URL", file=sys.stderr)
        return 2
    prefix = Path(args.prefix).expanduser()
    vortex = args.vortex or str(
        prefix / "pfx" / "drive_c" / "Program Files" / "Vortex" / "Vortex.exe"
    )
    try:
        proton_path = args.proton or _default_proton(prefix)
        # Default Electron args for better Wine performance; can be overridden via OVLS_VORTEX_ARGS
        if os.environ.get("OVLS_VORTEX_ARGS"):
            extra_args = ["-d", uri] + os.environ["OVLS_VORTEX_ARGS"].split()
        else:
            extra_args = ["-d", uri, "--disable-gpu"]
        result = proton.spawn_vortex(
            proton_path,
            vortex,
            str(prefix),
            steam_client_path=args.steam_client,
            vortex_args=extra_args,
            log_path=str(desktop.data_home() / "ovls" / "nxm.log"),
        )
    except (OSError, ValueError) as exc:
        print(f"NXM handler failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "launched", "uri": uri, **result}, indent=2, ensure_ascii=False))
    return 0


def cmd_launcher(args):
    return launcher.run()


def cmd_manager(args):
    return manager.run()


def cmd_shortcuts(args):
    try:
        if args.shortcuts_command == "install":
            result = shortcuts.install(
                dry_run=args.dry_run,
                applications=args.apps_dir,
                desktop_path=args.desktop_dir,
            )
        elif args.shortcuts_command == "remove":
            result = shortcuts.remove(
                applications=args.apps_dir,
                desktop_path=args.desktop_dir,
            )
        else:
            result = shortcuts.status(
                applications=args.apps_dir,
                desktop_path=args.desktop_dir,
            )
    except OSError as exc:
        print(f"Shortcuts failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "ok" else 1


def cmd_install_extension(args):
    try:
        result = extension_installer.install_extension(args.source, args.prefix)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") in ("installed", "unchanged") else 1
    except (OSError, ValueError) as exc:
        print(f"Extension installation failed: {exc}", file=sys.stderr)
        return 2


def cmd_remove_extension(args):
    try:
        result = extension_installer.remove_extension(args.prefix)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Extension removal failed: {exc}", file=sys.stderr)
        return 2


def cmd_install(args):
    backup_root = None
    if args.backup:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_root = os.path.join(args.backup_root, stamp, "extension")
    try:
        result = installer.install(
            args.prefix,
            args.source,
            fix_steam_library=not args.no_steam_library,
            backup_root=backup_root,
            dry_run=args.dry_run,
            force=args.force,
            with_desktop=not args.no_desktop,
            applications_dir=args.desktop_dir,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        registry_state = result.get("vortex_registry", {})
        print(
            f"Vortex uninstall key: {registry_state.get('action', 'unknown')}"
            + (f" (backup: {registry_state['backup']})" if registry_state.get("backup") else ""),
            file=sys.stderr,
        )
        handler = result.get("nxm_handler", {})
        print(f"NXM handler: {handler.get('status', 'unknown')}", file=sys.stderr)
        return 0 if result.get("status") == "ok" else 1
    except (OSError, ValueError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 2


def cmd_verify(args):
    try:
        result = installer.verify(
            args.prefix,
            args.source,
            with_desktop=not args.no_desktop,
            applications_dir=args.desktop_dir,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        registry_state = result.get("vortex_registry", {})
        print(
            f"Vortex uninstall key: {registry_state.get('status', 'unknown')}",
            file=sys.stderr,
        )
        handler = result.get("nxm_handler", {})
        print(f"NXM handler: {handler.get('status', 'unknown')}", file=sys.stderr)
        return 0 if result["status"] == "ok" else 1
    except (OSError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 2


def cmd_uninstall(args):
    try:
        result = installer.uninstall(
            args.prefix,
            with_desktop=not args.no_desktop,
            applications_dir=args.desktop_dir,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        handler = result.get("nxm_handler", {})
        print(f"NXM handler: {handler.get('status', 'unknown')}", file=sys.stderr)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Uninstallation failed: {exc}", file=sys.stderr)
        return 2


def cmd_package(args):
    if args.package_command == "ui":
        return setup_ui.run()
    try:
        if args.package_command in ("install", "update"):
            result = package.install(
                prefix=args.prefix,
                dry_run=args.dry_run,
                update=args.package_command == "update",
            )
        elif args.package_command == "verify":
            result = package.verify(prefix=args.prefix)
        else:
            result = package.uninstall(prefix=args.prefix)
    except (OSError, ValueError) as exc:
        print(f"Package {args.package_command} failed: {exc}", file=sys.stderr)
        return 2
    if not args.prefix and result.get("reason") == "prefix_not_found":
        print(result.get("message", ""), file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in (
        "ok", "unchanged", "would_install", "would_update"
    ) else 1


def cmd_check_fs(args):
    path = args.path
    result = {
        "case_sensitivity": filesystem.check_case_sensitivity(path),
        "hardlink": filesystem.check_hardlink(path),
        "writable": filesystem.check_writable(path),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_diagnostics(args):
    steam_root = steam.find_steam_root()
    prefix = args.prefix
    result = diagnostics.collect_all(steam_root, prefix)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_ping(args):
    try:
        # Check if session directory is provided (required for IPC operations)
        if args.session_dir is None:
            print("Error: --session-dir parameter is required for ipc ping command", file=sys.stderr)
            return 2
        
        from pathlib import Path
        from external.client import request_and_ack
        result = request_and_ack(
            "ping",
            {},
            ipc_dir=Path(args.ipc_dir),
            timeout=args.timeout,
            session_dir=Path(args.session_dir),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        print(f"Ping failed: {exc}", file=sys.stderr)
        return 2


def cmd_get_state(args):
    try:
        # Check if session directory is provided (required for IPC operations)
        if args.session_dir is None:
            print("Error: --session-dir parameter is required for ipc get-state command", file=sys.stderr)
            return 2
        
        from pathlib import Path
        from external.client import request_and_ack
        # Handle path as string or list
        if isinstance(args.path, str):
            path_list = args.path.split(".") if "." in args.path else ["settings"]
        else:
            path_list = args.path
        result = request_and_ack(
            "getState",
            {"path": path_list},
            ipc_dir=Path(args.ipc_dir),
            timeout=args.timeout,
            session_dir=Path(args.session_dir),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 2
    except Exception as exc:
        print(f"Get state failed: {exc}", file=sys.stderr)
        return 2


def cmd_ipc_status(args):
    try:
        from pathlib import Path
        from external.client import check_status
        status = check_status(Path(args.ipc_dir))
        print(json.dumps({"ok": status is not None, "status": status}, indent=2, ensure_ascii=False))
        return 0 if status is not None else 2
    except Exception as exc:
        print(f"IPC status failed: {exc}", file=sys.stderr)
        return 2


def main():
    parser = argparse.ArgumentParser(description=core.APP_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("discover")

    p = sub.add_parser("inspect-prefix")
    p.add_argument("prefix")

    p = sub.add_parser("launch", help="Launch Vortex through Proton")
    p.add_argument(
        "--proton",
        default=None,
        help="Path to the Proton executable (defaults to the Proton selected in Steam when available)",
    )
    p.add_argument("--vortex", required=True, help="Path to Vortex.exe inside the prefix")
    p.add_argument("--prefix", required=True, help="Proton compatdata directory containing pfx/")
    p.add_argument(
        "--steam-client",
        default=None,
        help="Steam client directory (defaults to STEAM_COMPAT_CLIENT_INSTALL_PATH or discovery)",
    )
    p.add_argument("vortex_args", nargs=argparse.REMAINDER, help="Arguments for Vortex, after --")

    sub.add_parser("launcher", help="Desktop launcher: find the prefix and start Vortex")
    sub.add_parser("manager", help="Graphical manager (kdialog, zenity fallback)")

    p = sub.add_parser("shortcuts", help="Desktop shortcuts for the installed OVLS copy")
    shortcuts_sub = p.add_subparsers(dest="shortcuts_command", required=True)
    for name in ("install", "remove", "status"):
        sp = shortcuts_sub.add_parser(name)
        sp.add_argument(
            "--desktop-dir",
            default=None,
            help="Desktop directory override (defaults to XDG_DESKTOP_DIR/xdg-user-dir)",
        )
        sp.add_argument(
            "--apps-dir",
            default=None,
            help="Applications directory override (defaults to XDG data home)",
        )
        if name == "install":
            sp.add_argument(
                "--dry-run",
                action="store_true",
                help="Report what would be written without touching the desktop",
            )

    p = sub.add_parser("nxm", help="Open an nxm:// URL in the official Vortex (NXM handler)")
    p.add_argument("--prefix", required=True, help="Proton compatdata directory containing pfx/")
    p.add_argument("--uri", required=True, help="nxm:// URL received from the desktop handler")
    p.add_argument("--proton", default=None, help="Proton executable (defaults to the Proton selected in Steam when available)")
    p.add_argument("--vortex", default=None, help="Vortex.exe path (defaults to the prefix install)")
    p.add_argument(
        "--steam-client",
        default=None,
        help="Steam client directory (defaults to STEAM_COMPAT_CLIENT_INSTALL_PATH or discovery)",
    )

    p = sub.add_parser("install-extension", help="Install or update the OVLS Stage 9 Extension")
    p.add_argument(
        "--prefix",
        required=True,
        help="Proton compatdata directory containing pfx/",
    )
    p.add_argument(
        "--source",
        default=DEFAULT_EXTENSION,
        help="Extension package directory (defaults to the in-project Stage 9 package)",
    )

    p = sub.add_parser("remove-extension", help="Remove the managed OVLS Stage 9 Extension")
    p.add_argument(
        "--prefix",
        required=True,
        help="Proton compatdata directory containing pfx/",
    )

    p = sub.add_parser("install", help="Install OVLS Linux Support components into a Proton prefix")
    p.add_argument("--prefix", required=True, help="Proton compatdata directory containing pfx/")
    p.add_argument(
        "--source",
        default=DEFAULT_EXTENSION,
        help="Extension package directory (defaults to the in-project Stage 9 package)",
    )
    p.add_argument(
        "--no-steam-library",
        action="store_true",
        help="Do not create Steam/config/libraryfolders.vdf in the prefix",
    )
    p.add_argument(
        "--backup",
        action="store_true",
        help="Back up extension files and system.reg before they are overwritten",
    )
    p.add_argument(
        "--backup-root",
        default=str(desktop.data_home() / "ovls" / "backups"),
        help="Backup root (backups go to <root>/<timestamp>/extension)",
    )
    p.add_argument("--dry-run", action="store_true", help="Report actions without writing")
    p.add_argument("--force", action="store_true", help="Install even if Vortex is running")
    p.add_argument(
        "--no-desktop",
        action="store_true",
        help="Do not install the nxm:// desktop handler",
    )
    p.add_argument(
        "--desktop-dir",
        default=None,
        help="Applications directory for the NXM handler (defaults to XDG data home)",
    )

    p = sub.add_parser("verify", help="Verify installed OVLS components and prefix compatibility")
    p.add_argument("--prefix", required=True, help="Proton compatdata directory containing pfx/")
    p.add_argument(
        "--source",
        default=DEFAULT_EXTENSION,
        help="Extension package directory (defaults to the in-project Stage 9 package)",
    )
    p.add_argument(
        "--no-desktop",
        action="store_true",
        help="Do not check the nxm:// desktop handler",
    )
    p.add_argument(
        "--desktop-dir",
        default=None,
        help="Applications directory for the NXM handler (defaults to XDG data home)",
    )

    p = sub.add_parser(
        "uninstall",
        help="Remove OVLS Extension and NXM handler, keep Steam config and Vortex registration",
    )
    p.add_argument("--prefix", required=True, help="Proton compatdata directory containing pfx/")
    p.add_argument(
        "--no-desktop",
        action="store_true",
        help="Keep the nxm:// desktop handler installed",
    )
    p.add_argument(
        "--desktop-dir",
        default=None,
        help="Applications directory for the NXM handler (defaults to XDG data home)",
    )

    p = sub.add_parser(
        "package",
        help="Install, update, verify or remove the autonomous OVLS package",
    )
    package_sub = p.add_subparsers(dest="package_command", required=True)
    for name in ("install", "update", "verify", "uninstall"):
        sp = package_sub.add_parser(name)
        sp.add_argument(
            "--prefix",
            default=None,
            help="Proton compatdata directory (defaults to the remembered or discovered prefix)",
        )
        if name in ("install", "update"):
            sp.add_argument("--dry-run", action="store_true", help="Report actions without writing")
    package_sub.add_parser("ui", help="Open the graphical installer (Desktop Mode)")

    p = sub.add_parser("check-fs")
    p.add_argument("path")

    p = sub.add_parser("diagnostics")
    p.add_argument("--prefix", default=None)

    # IPC commands group
    ipc_parser = sub.add_parser("ipc", help="Stage 9 JSON IPC operations")
    ipc_sub = ipc_parser.add_subparsers(dest="ipc_command", required=True)

    # ping command under ipc - session-dir is required for ping
    p_ping = ipc_sub.add_parser("ping", help="Send ping request to Stage 9 Extension")
    p_ping.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")
    p_ping.add_argument("--session-dir", required=True, help="Stage 9 session directory (required for ping)")
    p_ping.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")

    # get-state command under ipc - session-dir is required for get-state
    p_get_state = ipc_sub.add_parser("get-state", help="Retrieve state data from Stage 9 Extension")
    p_get_state.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")
    p_get_state.add_argument("--session-dir", required=True, help="Stage 9 session directory (required for get-state)")
    p_get_state.add_argument("--path", default="settings", help="State path (dot notation, e.g., 'settings.profile.game')")
    p_get_state.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")

    # status command under ipc - only requires IPC directory
    p_status = ipc_sub.add_parser("status", help="Check Stage 9 Extension status")
    p_status.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")

    # Backward compatibility aliases (session-dir now required for consistency)
    p_old_ping = sub.add_parser("ping", help="Alias for ipc ping (maintained for compatibility)")
    p_old_ping.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")
    p_old_ping.add_argument("--session-dir", required=True, help="Stage 9 session directory (required for ping)")
    p_old_ping.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")

    p_old_get_state = sub.add_parser("get-state", help="Alias for ipc get-state (maintained for compatibility)")
    p_old_get_state.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")
    p_old_get_state.add_argument("--session-dir", required=True, help="Stage 9 session directory (required for get-state)")
    p_old_get_state.add_argument("--path", default="settings", help="State path (dot notation, e.g., 'settings.profile.game')")
    p_old_get_state.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")

    p_old_ipc_status = sub.add_parser("ipc-status", help="Alias for ipc status (maintained for compatibility)")
    p_old_ipc_status.add_argument("--ipc-dir", required=True, help="Stage 9 IPC directory")

    args = parser.parse_args()

    # IPC cleanup on startup for commands that operate on a prefix
    if hasattr(args, "prefix") and args.prefix:
        cleanup_ipc_stale_files(args.prefix)

    # Route to appropriate handler
    if hasattr(args, 'ipc_command'):
        # Handle ipc subcommand
        if args.ipc_command == "ping":
            return cmd_ping(args)
        elif args.ipc_command == "get-state":
            return cmd_get_state(args)
        elif args.ipc_command == "status":
            return cmd_ipc_status(args)
    else:
        # Handle legacy commands (backward compatibility)
        if args.command == "ping":
            return cmd_ping(args)
        elif args.command == "get-state":
            return cmd_get_state(args)
        elif args.command == "ipc-status":
            return cmd_ipc_status(args)
        else:
            # Handle other legacy commands
            handlers = {
                "status": cmd_status,
                "discover": cmd_discover,
                "inspect-prefix": cmd_inspect_prefix,
                "launch": cmd_launch,
                "nxm": cmd_nxm,
                "launcher": cmd_launcher,
                "manager": cmd_manager,
                "shortcuts": cmd_shortcuts,
                "install-extension": cmd_install_extension,
                "remove-extension": cmd_remove_extension,
                "install": cmd_install,
                "verify": cmd_verify,
                "uninstall": cmd_uninstall,
                "package": cmd_package,
                "check-fs": cmd_check_fs,
                "diagnostics": cmd_diagnostics,
            }
            return handlers.get(args.command, lambda x: 1)(args) or 0


if __name__ == "__main__":
    sys.exit(main())