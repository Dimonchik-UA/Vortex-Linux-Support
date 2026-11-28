# Component Design

This document describes the components of OVLS, their responsibilities, and how they relate to each other.

## Overview

OVLS consists of two main parts:

1. **External Linux Support** — Python code that runs outside Vortex.
2. **Vortex Extension** — JavaScript code loaded inside Vortex.

The current source layout uses `src/linux-support/` and `src/extension/`; the standalone installer repackages them as `ovls/` with the Extension in `ovls/extension/`. `Development/Linux_Support/` and `Development/Phase9_JSON_IPC/extension/` belong to a frozen internal snapshot kept only as a compatibility fallback for old local checkouts. They are not part of this repository, are not a source for publication, and are never used when the current repository layout is present.

## Linux Support Components

### `core.py`
Shared constants (`APP_NAME`, `VERSION`, log rotation limits) and rotating-file logging (`setup_logging()`, `print_status()`) for the external component. It does not own user configuration or command dispatch.

### `desktop.py`
Desktop integration for the NXM handler: creates the NXM wrapper (`ovls-nxm`) in the OVLS data directory, the hidden `ovls-nxm.desktop` protocol-handler entry in the applications directory, registers `x-scheme-handler/nxm` via `xdg-mime default`, and attempts to restore the previous handler on uninstall only when its saved name is present, the corresponding `.desktop` file exists, and `xdg-mime` switches it back successfully. These NXM artifacts are managed by `desktop.py`, not by the shortcut ownership markers in `shortcuts.py`.

### `diagnostics.py`
System diagnostics: collects Proton version, prefix state, Steam library information, filesystem checks.

### `extension_installer.py`
Installs and removes the Vortex Extension (`info.json`, `index.js`) into the Proton prefix plugin directory. Also generates `host.json` with the host mods directory (`modsRoot`) and, in v2, volume anchors from the real Steam libraries for staging folder detection.

### `filesystem.py`
Filesystem checks: case sensitivity, hardlink support, and writability (`check_case_sensitivity`, `check_hardlink`, `check_writable`). There is no free-space check.

### `installer.py`
Installs and verifies the OVLS components inside the Proton prefix: the Vortex Extension, the Vortex uninstall registry key, and `libraryfolders.vdf`. Does not stage or roll back the package copy.

### `launcher.py`
Desktop-mode launcher: discovers Proton prefix, locates Vortex.exe, launches Vortex through Proton.

### `layout.py`
Path resolution: determines source tree and extension tree locations for both project checkout and standalone package layouts.

### `manager.py`
Graphical manager (kdialog/zenity): Install / Update, Select Vortex prefix (pfx), Verify, Paths & Folders, Uninstall actions.

### `ovls.py`
Command-line entry point: subcommands for status, discover, inspect-prefix, launch, nxm, launcher, manager, shortcuts, install-extension, remove-extension, install, verify, uninstall, package, check-fs, and diagnostics. The former `ipc` subcommand and its `ping`/`get-state`/`ipc-status` aliases were removed in OVLS 0.4.0.

### `package.py`
Autonomous package management: stages and verifies the new source tree, backs up managed files and restores them on failure, manages MANIFEST.sha256, handles transactional install/update/verify/uninstall, and calls `installer.py` for components inside the prefix.

### `proton.py`
Proton discovery and launch: finds installed Proton tools and resolves the selected tool to an executable. The read-only parsing of Steam's `config.vdf` `CompatToolMapping` and the `compatdata/<appid>/config_info` fallback lives in `steam.py` (`selected_proton_name()`). `proton.py` calls it from `selected_proton()`, then checks that the named tool is installed; the recommended-tool fallback (DW-Proton / Proton-GE, then any available Proton) applies only when no installed tool was selected. Never edits the Steam selection; no specific version is required.

### `registry.py`
Windows registry operations for Proton prefix: creates/repairs uninstall keys.

### `setup_ui.py`
Graphical installer UI for desktop mode.

### `shortcuts.py`
Desktop shortcut creation and management: creates two application entries with `X-OVLS-Managed=true` markers (their wrappers carry a generated-by-OVLS owner line):

- `ovls-vortex.desktop` — in applications menu **and** on desktop, Name: `Vortex`, Comment: `Official Vortex on Linux`, Categories: `Game;`, launches `ovls.py launcher`.
- `ovls-manager.desktop` — in applications menu only, Name: `OVLS Manager`, Comment: `OVLS Manager (Official Vortex Linux Support)`, Categories: `Utility;`, launches `ovls.py manager`.

The hidden `ovls-nxm.desktop` protocol-handler entry belongs to `desktop.py`, not to this mechanism: it has no `X-OVLS-Managed=true` marker and is registered through `xdg-mime`.

### `steam.py`
Steam discovery: finds Steam root, library folders, installed games and Vortex prefixes under `steamapps/compatdata/*`.

### `updater.py`
GitHub Releases update support for Manager: checks the public `Dimonchik-UA/Vortex-Linux-Support` repository for releases, obtains and verifies the published `SHA256SUMS` for the installer archive, and caches update-check results in the XDG state directory (`$XDG_STATE_HOME/ovls/update-check.json`, with a TTL). Manager uses it to check for updates and to obtain the validated release information needed for an update; the installation itself is performed by `package.install()` called from `manager.py`, not by `updater.py`.

## Extension Components

### `info.json`
Extension manifest: id (`ovls-json-ipc-v2`, retained as the installed plugin identifier for compatibility), name, version, description.

### `index.js`
Extension logic: initializes the extension, detects the deployment-failure notification and offers/applies the staging-folder fix through the Vortex API. File-based JSON IPC was removed in OVLS 0.4.0.

## Installer Components

### `install.sh`
Entry point shell script: locates `ovls.py` in the standalone/package layout (`ovls/ovls.py`) or the current repository checkout (`src/linux-support/ovls.py`), then falls back to `Development/Linux_Support/ovls.py` only in a legacy local checkout outside this repository, and delegates to `package.py` subcommands. That fallback is retained solely so old local checkouts keep working; it does not make `Development/` an active or published source.

### `OVLS Installer.desktop`
Desktop shortcut that launches `install.sh`.

### `installer/README.md`
Installer package README: installation instructions and component overview.

## Data Flow

```
External Linux Support (Python)   — independent of —   Vortex Extension (JavaScript, inside Vortex)
                                                              ↕
                                                    Official Vortex application
```

There is no IPC, request/response, ACK or shared IPC directory between the two components.

## Installation Layout

The standalone package (`dist/OVLS Installer/`, a generated build artifact) contains:

```
OVLS Installer/
├── install.sh
├── README.md
├── OVLS Installer.desktop           (package-root launcher, not installed as a system shortcut)
├── OVLS - GitHub Repository.desktop (package-root launcher, not installed as a system shortcut)
└── ovls/
    ├── *.py          (Python source files)
    ├── assets/
    │   └── ovls.png
    └── extension/
        ├── index.js
        └── info.json
```

The build directory also contains the hidden `.ovls-build` marker, which is excluded from `OVLS-Installer.tar.gz`. `host.json` is not shipped: it is generated on install.

The managed installed copy (`$XDG_DATA_HOME/ovls`, default `~/.local/share/ovls/`) is built from the package sources above plus `MANIFEST.sha256` and `VERSION`, which are generated during installation. `MANIFEST.sha256` lists only the staged managed files; it does not list the additional files created by other mechanisms inside the same directory (`bin/`, `ovls.log`, `ovls-nxm`, `nxm-previous-default`) or the conditional `backups/<timestamp>/` tree.

External integration files are created separately: application entries in `$XDG_DATA_HOME/applications/`, the icon in `$XDG_DATA_HOME/icons/hicolor/256x256/apps/ovls.png`, settings in `$XDG_CONFIG_HOME/ovls/config.json`, the launch log in `$XDG_STATE_HOME/ovls/launch.log`, and the Extension in the selected Proton prefix.

**Note**: `host.json` is generated on install and describes the host mods directory for staging folder detection. In v2 it also contains deterministic `volumes` entries (`anchor` and per-volume `modsRoot`) derived from `libraryfolders.vdf` and the real filesystem mount point from `findmnt -T ... -no TARGET`. A library under PASSWD-home shares the home anchor only when both report the same mount; otherwise its own mount root is used. Mounts that cannot be determined or mapped through the prefix `z:` drive are skipped. Install and verify pass the currently installed `host.json` back to the generator so a still-registered but temporarily unavailable library keeps its known volume; real registration changes still change the bytes. It is removed with the Extension on uninstall.
