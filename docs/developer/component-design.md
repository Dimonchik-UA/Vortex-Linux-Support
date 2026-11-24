# Component Design

This document describes the components of OVLS, their responsibilities, and how they relate to each other.

## Overview

OVLS consists of two main parts:

1. **External Linux Support** — Python code that runs outside Vortex.
2. **Vortex Extension** — JavaScript code loaded inside Vortex.

The current source layout uses `src/linux-support/` and `src/extension/`; the standalone installer repackages them as `ovls/` with the Extension in `ovls/extension/`. Historical `Development/Linux_Support/` and `Development/Phase9_JSON_IPC/extension/` paths are legacy fallbacks only: they are a frozen internal snapshot and are not a source for publication.

## Linux Support Components

### `core.py`
Configuration, logging, startup, and state management for the external component.

### `desktop.py`
Desktop integration for the NXM handler: creates the NXM handler wrapper (`ovls-nxm`), its `.desktop` file, registers the `nxm://` protocol handler, and restores the previous handler on uninstall.

### `diagnostics.py`
System diagnostics: collects Proton version, prefix state, Steam library information, filesystem checks.

### `extension_installer.py`
Installs and removes the Vortex Extension (`info.json`, `index.js`) into the Proton prefix plugin directory. Also generates `host.json` with the host mods directory (`modsRoot`) and, in v2, volume anchors from the real Steam libraries for staging folder detection.

### `filesystem.py`
Filesystem checks: case sensitivity, hardlink support, accessibility, free space.

### `installer.py`
Installs and verifies the OVLS components inside the Proton prefix: the Vortex Extension, the Vortex uninstall registry key, and `libraryfolders.vdf`. Does not stage or roll back the package copy.

### `ipc.py`
Legacy minimal IPC client for communication between external component and Vortex Extension. Uses flat layout with single request/response files. **Not used in production** — the production client is `external/client.py` with per-request files and session-scoped layout. Retained for backward compatibility and diagnostics only.

### `launcher.py`
Desktop-mode launcher: discovers Proton prefix, locates Vortex.exe, launches Vortex through Proton.

### `layout.py`
Path resolution: determines source tree and extension tree locations for both project checkout and standalone package layouts.

### `manager.py`
Graphical manager (kdialog/zenity): Install/Update, Verify, Uninstall, Change prefix actions.

### `ovls.py`
Command-line entry point: subcommands for status, discover, inspect-prefix, launch, nxm, launcher, manager, shortcuts, install-extension, remove-extension, install, verify, uninstall, package, check-fs, diagnostics, ipc, plus compatibility aliases `ping`, `get-state`, and `ipc-status` for `ipc ping`, `ipc get-state`, and `ipc status`.

### `package.py`
Autonomous package management: stages and verifies the new source tree, backs up managed files and restores them on failure, manages MANIFEST.sha256, handles transactional install/update/verify/uninstall, and calls `installer.py` for components inside the prefix.

### `proton.py`
Proton discovery and launch: finds installed Proton tools, resolves the prefix tool from Steam's `config.vdf` `CompatToolMapping` first, then from the tool recorded in `compatdata/<appid>/config_info` when the mapping entry is missing (used only if installed); the recommended-tool fallback (DW-Proton / Proton-GE, then any available Proton) applies only when neither source yields an installed tool. Never edits the Steam selection; no specific version is required.

### `registry.py`
Windows registry operations for Proton prefix: creates/repairs uninstall keys.

### `setup_ui.py`
Graphical installer UI for desktop mode.

### `shortcuts.py`
Desktop shortcut creation and management: creates three shortcuts with ownership markers:

- `ovls-vortex.desktop` — in applications menu **and** on desktop, Name: `Vortex`, Comment: `Official Vortex on Linux`, Categories: `Game;`, launches `ovls.py launcher`.
- `ovls-manager.desktop` — in applications menu only, Name: `OVLS Manager`, Comment: `OVLS Manager (Official Vortex Linux Support)`, Categories: `Utility;`, launches `ovls.py manager`.
- `ovls-nxm.desktop` — hidden protocol-handler entry (`NoDisplay=true`), not shown as a regular application, Name: `OVLS NXM Handler`, Categories: `Game;`, wraps `ovls.py nxm --prefix ... --uri "$1"` for NXM link handling.

### `steam.py`
Steam discovery: finds Steam root, library folders, installed games and Vortex prefixes under `steamapps/compatdata/*`.

## Extension Components

### `info.json`
Extension manifest: name (`ovls-json-ipc-v2`), version, description.

### `index.js`
Extension logic: initializes IPC context, handles JSON IPC requests (ping, getState), manages dedup records, handles ACK flow.

## Installer Components

### `install.sh`
Entry point shell script: locates `ovls.py` in the standalone/package layout (`ovls/ovls.py`) or the repository checkout (`src/linux-support/ovls.py`), with a legacy fallback to `Development/Linux_Support/ovls.py` (when present), and delegates to `package.py` subcommands.

### `OVLS Installer.desktop`
Desktop shortcut that launches `install.sh`.

### `installer/README.md`
Installer package README: installation instructions and component overview.

## Data Flow

```
External Linux Support (Python)
        ↕ JSON IPC (file-based)
Vortex Extension (JavaScript, inside Vortex)
        ↕
Official Vortex application
```

## Installation Layout

The standalone package (`dist/OVLS Installer/`, a generated build artifact) contains:

```
OVLS Installer/
├── install.sh
├── OVLS Installer.desktop
├── README.md
└── ovls/
    ├── *.py          (Python source files)
    └── extension/
        ├── index.js
        └── info.json
```

The installed copy (`~/.local/share/ovls/`) mirrors this layout plus `MANIFEST.sha256`, `VERSION`, and `backups/`.

**Note**: `host.json` is generated on install and describes the host mods directory for staging folder detection. In v2 it also contains deterministic `volumes` entries (`anchor` and per-volume `modsRoot`) derived from `libraryfolders.vdf` and the real filesystem mount point from `findmnt -T ... -no TARGET`. A library under PASSWD-home shares the home anchor only when both report the same mount; otherwise its own mount root is used. Mounts that cannot be determined or mapped through the prefix `z:` drive are skipped. Install and verify pass the currently installed `host.json` back to the generator so a still-registered but temporarily unavailable library keeps its known volume; real registration changes still change the bytes. It is removed with the Extension on uninstall.
