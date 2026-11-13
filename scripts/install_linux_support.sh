#!/usr/bin/env bash
# Host-side installer wrapper for OVLS Linux Support.
#
# Some sandboxes cannot write into a Wine/Proton prefix, so the actual
# installation must be started from the host user session with this script.
# It delegates to the package installer in `installer/install.sh`.
#
# Usage:
#   bash scripts/install_linux_support.sh                     # default prefix
#   bash scripts/install_linux_support.sh /path/to/compatdata # explicit prefix
#   DRY_RUN=1 bash scripts/install_linux_support.sh           # report only
#
# The prefix argument is the directory that contains `pfx/`, e.g.
#   /path/to/compatdata   ->   /path/to/compatdata/pfx/drive_c/...
#
# What it installs:
#   1. A code copy in ~/.local/share/ovls (autonomous package)
#   2. Vortex Extension `ovls-json-ipc-v2` into %APPDATA%\Vortex\plugins
#   3. Steam/config/libraryfolders.vdf inside the prefix (only if missing)
#   4. Vortex uninstall registration in the prefix registry (only if missing
#      or broken; a verified backup of system.reg is written first)
#   5. NXM handler: a desktop entry for x-scheme-handler/nxm and its
#      registration in the user's XDG config (uses `xdg-mime default`)
#
# It never modifies Vortex application files, game files, Vortex settings,
# mods, staging or other extensions, and it does not start Vortex.

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL="$ROOT/installer/install.sh"
PREFIX="${1:-}"

if [ ! -f "$INSTALL" ]; then
    echo "ERROR: install.sh not found at $INSTALL" >&2
    exit 1
fi
if [ -z "$PREFIX" ]; then
    echo "ERROR: PREFIX argument required" >&2
    exit 1
fi
if [ ! -d "$PREFIX/pfx/drive_c/users" ]; then
    echo "ERROR: '$PREFIX' is not a Proton prefix (missing pfx/drive_c/users)." >&2
    exit 1
fi

echo "== target prefix: $PREFIX"
echo "== installed copy: ${XDG_DATA_HOME:-$HOME/.local/share}/ovls"
echo "== backups:        ${XDG_DATA_HOME:-$HOME/.local/share}/ovls/backups"
echo

if [ "${DRY_RUN:-0}" = "1" ]; then
    echo "== dry run =="
    bash "$INSTALL" install --prefix "$PREFIX" --dry-run
    exit $?
fi

echo "== install =="
bash "$INSTALL" install --prefix "$PREFIX"
install_rc=$?
echo
if [ "$install_rc" -ne 0 ]; then
    echo "Installation failed (exit $install_rc)." >&2
    exit "$install_rc"
fi

echo "== verify =="
bash "$INSTALL" verify --prefix "$PREFIX"
verify_rc=$?
echo
if [ "$verify_rc" -eq 0 ]; then
    echo "OK: components installed and verified. Vortex was not started."
else
    echo "WARNING: verification needs attention (exit $verify_rc)." >&2
fi
exit "$verify_rc"
