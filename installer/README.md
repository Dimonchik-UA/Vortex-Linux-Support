# OVLS Installer — Standalone Package

This folder contains everything needed to install OVLS Linux Support on a SteamOS/Linux system. Copy this folder to any location (for example `~/Downloads/OVLS`) and run `install.sh` to install OVLS.

## Package contents

| File | Purpose |
|------|---------|
| `install.sh` | Entry point. Starts the OVLS package manager (`ovls.py package ...`). |
| `OVLS Installer.desktop` | Desktop shortcut that launches `install.sh`. Double-click or run from terminal. |
| `README.md` | This file. |
| `ovls/` | OVLS Python code and Vortex Extension files. Contains `ovls.py` and subdirectories. |

Do not copy `install.sh` or `OVLS Installer.desktop` separately — they must remain inside this package folder together with `ovls/`.

## Shortcuts installed by OVLS

After installation, OVLS creates three shortcuts:

| Shortcut | Location | Purpose |
|----------|----------|---------|
| `ovls-vortex.desktop` | Applications menu **and** desktop | Launches the official Vortex through Proton. Name: `Vortex`. Comment: `Official Vortex on Linux`. Categories: `Game;`. |
| `ovls-manager.desktop` | Applications menu | Opens OVLS Manager for install/update/verify/uninstall and prefix changes. Name: `OVLS Manager`. Comment: `OVLS Manager (Official Vortex Linux Support)`. Categories: `Utility;`. |
| `ovls-nxm.desktop` | Protocol-handler entry (not shown as a regular application) | Registers the `nxm://` protocol handler so “Download with Vortex” buttons on Nexus Mods open in the official Vortex. Name: `OVLS NXM Handler`. Categories: `Game;`. |

The OVLS Manager appears in the `Utility;` category of your application menu.

## System requirements

- SteamOS 3.x, Desktop Mode (KDE Plasma)
- Steam installed
- Official Vortex installed in a Proton prefix
- **Proton-GE** (any version with "ge-proton" or "proton-ge" in the name, preferring "latest") — recommended
- **Proton 11.0** — fallback
- Python 3
- `kdialog` or `zenity` (for graphical dialogs; CLI works without them)

## Prepare before installing

1. Determine your Proton prefix — the directory that contains `pfx/`. For example: `/path/to/your/prefix`.
2. Make sure the prefix is writable.
3. Close Vortex before installing. The installer will refuse to proceed if Vortex is running.

## Installation

1. Copy the `OVLS Installer` folder to any location, for example `~/Downloads/OVLS`.
2. Open a terminal in that folder, or run:
   ```bash
   bash install.sh
   ```
   or double-click `OVLS Installer.desktop`. If KDE marks the shortcut as untrusted, select “Trust and Launch”.
3. When prompted, choose **Install**.
4. If asked for a prefix, provide the path to the directory containing `pfx/` (for example `/path/to/your/prefix`).
5. Wait for the success message.

## First launch

After installation, Vortex is available from the desktop and application menu. The OVLS Manager is also available from the application menu.

Vortex is launched through Proton-GE by default (preferring "latest" version). If Proton-GE is not found, OVLS falls back to Proton 11.0, then to any other installed Proton version. You do not need to pass `--proton` manually.

## Update

1. Obtain a new `OVLS Installer` folder (rebuild the package or unpack a new version).
2. Copy it to any location.
3. Run `install.sh` and choose **Update**.
4. The update installs code from the new package into the existing installation.

Alternatively, use the OVLS Manager from the application menu (Install / Update). It may check GitHub Releases for updates:
- If you decline the GitHub update, Manager can continue with a normal update from the already installed copy.
- If the GitHub update check is unavailable (for example, offline), Manager can use the normal update path from the already installed copy.
- If you confirm the GitHub update but download, SHA-256 verification, preparation, or installation fails, Manager reports the error; there is no automatic fallback to the normal update.

## Verify installation

Run:
```bash
bash install.sh verify
```

This checks the installed copy, extension, NXM handler, and shortcuts.

## Uninstallation

1. Run `install.sh` and choose **Uninstall**.
   Or use the OVLS Manager from the application menu.
2. Uninstall removes only OVLS files: the installed copy, shortcuts, and NXM handler.
3. The following are **not** removed:
   - Vortex and its prefix
   - Games, mods, and staging folders
   - User settings
   - `backups/` in `~/.local/share/ovls`

## Diagnostics

- Run `bash install.sh verify` to check the installed copy state, extension, NXM, and shortcuts.
- Vortex log: `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log`. OVLS extension lines start with `OVLS9`.
- If Vortex shows double window controls, a white band, interface shift, or cursor-click offset, run it through Proton-GE (preferring "latest") or DW-Proton Latest (OVLS selects Proton-GE by default).

## Known limitations

- OVLS does not modify the official Vortex executable or settings; it installs its own Extension into Vortex's plugin directory. OVLS cannot guarantee compatibility with future versions of Vortex internals.
- Mod deployment requires the staging folder to be on the same Wine disk as the game (e.g. `Z:` under Proton).
- Staging on SD cards and other non-home roots is not supported automatically.
- SteamOS Game Mode has not been tested. R2/L2 triggers work via SteamOS input handling; other controller interactions with Vortex UI elements have not been fully tested.

## Input limitations on Steam Deck

On Steam Deck in Desktop Mode (KDE Plasma), the following input limitations have been observed:

| Category | Observation |
|----------|-------------|
| **Window rendering / mouse** | On Proton 11.0, Hotfix, Experimental: double window controls, white band, interface shift, cursor-click offset. Not observed on Proton-GE Latest / DW-Proton Latest. |
| **Keyboard focus & text input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Touch input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Controller** | R2/L2 work via SteamOS input handling. Other controller interactions not fully tested. |

OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom input handling. The root cause (suspected Wine/Proton–Electron interaction) has not been proven. No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.

## License

OVLS source code is licensed under the MIT License. Copyright (c) 2026 Dimonchik-UA. The full license text is in the `LICENSE` file at the root of the repository.

The MIT License covers the OVLS source code in this repository only. Vortex is a separate project by Nexus Mods and is distributed under its own terms — it is not part of this repository and is not licensed under the MIT License. Any third-party components, if present, remain under their own licenses.

Repository: https://github.com/Dimonchik-UA/Vortex-Linux-Support
