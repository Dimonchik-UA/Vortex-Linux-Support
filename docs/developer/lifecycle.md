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
2. Launches the official Windows `Vortex.exe` through the Proton tool selected in Steam for the prefix (otherwise prefers DW-Proton / Proton-GE, then any Proton; no version is required. Proton-GE and DW-Proton Latest showed no window issues in testing; Proton 11.0, Hotfix, Experimental did)
3. The Extension loads inside Vortex and connects back to OVLS via JSON IPC
4. External component and Extension communicate via file-based JSON IPC

## Update

To update OVLS:

1. Obtain a new `OVLS Installer` folder
2. Run `bash install.sh` and choose **Install / Update**
3. The update installs code from the new package into the existing installation

Update is transactional: new files are staged first, previous files are backed up, and the previous state is restored on failure.

## Removal

Run `bash install.sh uninstall` or use OVLS Manager (Uninstall). Uninstall:

- Removes OVLS-owned files (installed copy, shortcuts, NXM handler)
- Removes Extension from the Proton prefix plugin directory (`info.json`, `index.js`)
- Does **not** remove: Vortex, prefix, games, mods, staging folders, user settings, `backups/` in `~/.local/share/ovls/`

After uninstall, OVLS Manager closes (its installed copy is removed). Reinstall with `bash install.sh install`.

## IPC Session Lifecycle

The IPC session lifecycle involves several distinct scenarios:

1. **First Extension load**: Extension loads inside Vortex and generates `sessionId` (UUID v4). Extension creates the IPC root plus `sessions/<sessionId>/` with `requests/`, `processing/`, `responses/`, and global `acks/`, `dedup/`, `processed/`, `quarantine/`, `client_state/`.
2. **Renderer re-initialization**: If the renderer process restarts (e.g., via `loadURL()`), the Extension re-initializes. Whether `sessionId` is preserved or regenerated across renderer restart is **not confirmed** (OQ-5 open question).
3. **New Vortex launch**: A new Vortex launch starts a new Extension instance. Whether `sessionId` persists across separate Vortex launches is **not confirmed**.

External component and Extension communicate via file-based JSON IPC during the session.

## IPC Session Termination

IPC sessions end when Vortex is closed or the Extension is unloaded. On next Vortex launch, a new Extension instance loads. Whether `sessionId` persists based on main process identity is **not confirmed** (OQ-5 open question).

ACK cleanup does not guarantee completion of all operations; it acknowledges receipt of a specific response.

## Version Compatibility

OVLS does not guarantee compatibility with future versions of Vortex. Changes to Vortex internals, extension API, or file layout may break OVLS. Users should check compatibility after Vortex updates.
