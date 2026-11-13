# Component Design

This document describes the components of OVLS, their responsibilities, and how they relate to each other.

## Overview

OVLS consists of two main parts:

1. **External Linux Support** — Python code that runs outside Vortex.
2. **Vortex Extension** — JavaScript code loaded inside Vortex.

The target repository structure places these under `src/linux-support/` and `src/extension/`. The current OVLS source layout uses `Development/Linux_Support/` and `Development/Phase9_JSON_IPC/extension/` respectively; these paths are project-internal and may differ from the public repository layout.

## Linux Support Components

### `core.py`
Configuration, logging, startup, and state management for the external component.

### `desktop.py`
Desktop integration for the NXM handler: creates the NXM handler wrapper (`ovls-nxm`), its `.desktop` file, registers the `nxm://` protocol handler, and restores the previous handler on uninstall.

### `diagnostics.py`
System diagnostics: collects Proton version, prefix state, Steam library information, filesystem checks.

### `extension_installer.py`
Installs and removes the Vortex Extension (`info.json`, `index.js`) into the Proton prefix plugin directory. Also generates `host.json` describing the host mods directory for staging folder detection.

### `filesystem.py`
Filesystem checks: case sensitivity, hardlink support, accessibility, free space.

### `installer.py`
Package installation, update, verification, and removal. Transactional: stages new files, backs up existing ones, restores on failure.

### `ipc.py`
JSON IPC client for communication between external component and Vortex Extension.

### `launcher.py`
Desktop-mode launcher: discovers Proton prefix, locates Vortex.exe, launches Vortex through Proton.

### `layout.py`
Path resolution: determines source tree and extension tree locations for both project checkout and standalone package layouts.

### `manager.py`
Graphical manager (kdialog/zenity): Install/Update, Verify, Uninstall, Change prefix actions.

### `ovls.py`
Command-line entry point: subcommands for status, discover, inspect-prefix, launch, nxm, launcher, manager, shortcuts, install-extension, remove-extension, install, verify, uninstall, package, check-fs, diagnostics, ipc.

### `package.py`
Autonomous package management: copies source tree to installed copy, manages MANIFEST.sha256, handles transactional install/update/verify/uninstall.

### `proton.py`
Proton discovery and launch: finds Proton installations, selects Proton-GE (preferring "latest"), falls back to Proton 11.0, then to any available Proton; launches Vortex through Proton.

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
Steam discovery: finds Steam root, library folders, installed games, Vortex installation.

## Extension Components

### `info.json`
Extension manifest: name (`ovls-json-ipc-v2`), version, description.

### `index.js`
Extension logic: initializes IPC context, handles JSON IPC requests (ping, getState), manages dedup records, handles ACK flow.

## Installer Components

### `install.sh`
Entry point shell script: locates `ovls.py` in supported layouts — standalone/package `ovls/ovls.py`, publication/repository checkout `src/linux-support/ovls.py`, or source/project checkout fallback `Development/Linux_Support/ovls.py` (when present) — and delegates to `package.py` subcommands.

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

**Note**: `host.json` is generated on install and describes the host mods directory for staging folder detection. It is removed with the Extension on uninstall.
