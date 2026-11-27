# Lifecycle

This document describes the lifecycle of OVLS: installation, operation, update, and removal.

## Installation

OVLS is distributed as a standalone installer folder (`OVLS Installer`). The package contains:

- `install.sh` — entry point script
- `OVLS Installer.desktop` — desktop shortcut
- `README.md` — installer documentation
- `ovls/` — Python code and Vortex Extension files

### Installation Steps

1. Copy the `OVLS Installer` folder to any location
2. Close Vortex before installing
3. Run `bash install.sh` (graphical menu) or `bash install.sh install --prefix <prefix>` (command line)
4. Wait for completion

The installer copies OVLS code to `~/.local/share/ovls/` (autonomous copy), installs the Extension into the Proton prefix plugin directory, registers the NXM handler, and creates desktop shortcuts:

- `ovls-vortex.desktop` — applications menu **and** desktop, Name: `Vortex`, Comment: `Official Vortex on Linux`, Categories: `Game;`
- `ovls-manager.desktop` — applications menu, Name: `OVLS Manager`, Comment: `OVLS Manager (Official Vortex Linux Support)`, Categories: `Utility;`
- `ovls-nxm.desktop` — hidden protocol-handler entry (`NoDisplay=true`), not shown as a regular application, Name: `OVLS NXM Handler`, Categories: `Game;`


## Runtime Operation

When launched, OVLS:

1. Detects the Proton prefix (explicit argument, remembered prefix, or auto-detection)
2. Resolves the prefix Proton tool from Steam's `CompatToolMapping` first, then from the installed tool recorded in `compatdata/<appid>/config_info`; prefers DW-Proton / Proton-GE and then any Proton only if neither source gives an installed tool (no version is required. Proton-GE and DW-Proton Latest showed no window issues in testing; Proton 11.0, Hotfix, Experimental did), and launches the official Windows `Vortex.exe` through it
3. The Extension loads inside Vortex and periodically checks for the deployment-failure case
4. The Extension acts only through the Vortex API; there is no IPC channel to the external component

## Update

To update OVLS:

1. Obtain a new `OVLS Installer` folder
2. Run `bash install.sh` and choose **Install / Update**
3. The update installs code from the new package into the existing installation

Update is transactional: new files are staged first, previous files are backed up, and the previous state is restored on failure. Files removed from the package are deleted together with the directories they leave empty. Leftover data of the former JSON IPC channel is removed during install and update, once the new Extension is in place.

## Removal

Run `bash install.sh uninstall` or use OVLS Manager (Uninstall). Uninstall:

- Removes OVLS-owned files (installed copy, shortcuts, NXM handler)
- Removes Extension from the Proton prefix plugin directory (`info.json`, `index.js`)
- Removes leftover data of the former JSON IPC channel (`OVLS_IPC_V2` in the Vortex folder), keeping any other files there
- Does **not** remove: Vortex, prefix, games, mods, staging folders, user settings, `backups/` in `~/.local/share/ovls/`

After uninstall, OVLS Manager closes (its installed copy is removed). Reinstall with `bash install.sh install`.

## Extension Lifecycle

The Extension is loaded by Vortex on each launch and re-initialized if the renderer restarts. It performs staging checks and the staging-folder fix exclusively through the Vortex API. No IPC session, `sessionId`, ACK or IPC directory exists in OVLS 0.4.0; the former Stage 9 IPC lifecycle was removed together with the IPC channel.

## Version Compatibility

OVLS does not guarantee compatibility with future versions of Vortex. Changes to Vortex internals, extension API, or file layout may break OVLS. Users should check compatibility after Vortex updates.
