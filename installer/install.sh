#!/usr/bin/env bash
# OVLS Linux Support installer entry point.
#
# Copies the package into ~/.local/share/ovls, installs the Vortex Extension
# and the NXM handler inside the Steam Proton prefix, and remembers the prefix.
#
# Usage:
#   bash install.sh                               # graphical installer
#   bash install.sh install                       # default or remembered prefix
#   bash install.sh install --prefix /path/compatdata
#   bash install.sh update [--prefix ...]         # update an existing copy
#   bash install.sh verify
#   bash install.sh uninstall
#
# The prefix argument is the directory that contains `pfx/`, e.g.
#   ~/Windows/prefix   ->   ~/Windows/prefix/pfx/drive_c/...
#
# The official Vortex application, the prefix contents, games, mods and user
# settings are never modified by this entry point.

set -u

BASE="$(cd "$(dirname "$0")" && pwd)"

# The standalone package keeps its code in ovls/, the GitHub checkout in
# src/linux-support/. Development/Linux_Support is a legacy fallback only.
if [ -f "$BASE/ovls/ovls.py" ]; then
    OVLS="$BASE/ovls/ovls.py"
elif [ -f "$BASE/src/linux-support/ovls.py" ]; then
    OVLS="$BASE/src/linux-support/ovls.py"
else
    OVLS="$BASE/Development/Linux_Support/ovls.py"
fi

if [ ! -f "$OVLS" ]; then
    echo "ERROR: ovls.py not found next to install.sh (looked in ovls/," >&2
    echo "       src/linux-support/ and legacy Development/Linux_Support/)." >&2
    echo "       This folder is not a complete OVLS package: it needs the" >&2
    echo "       ovls/ directory (or src/linux-support/) with the code." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    exec python3 "$OVLS" package ui
fi

exec python3 "$OVLS" package "$@"
