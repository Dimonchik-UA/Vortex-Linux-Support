# OVLS Installer — Standalone Package

This folder contains everything needed to install OVLS Linux Support on a SteamOS/Linux system. Copy this folder to any location (for example `~/Downloads/OVLS`) and run `install.sh` to install OVLS.

## Package contents

| File | Purpose |
|------|---------|
| `install.sh` | Entry point. Starts the OVLS package manager (`ovls.py package ...`). |
| `OVLS Installer.desktop` | Desktop shortcut that launches `install.sh`. Double-click or run from terminal. |
| `OVLS - GitHub Repository.desktop` | Shortcut that opens the project's GitHub repository. |
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
- **DW-Proton Latest and Proton-GE Latest** — both recommended for use with OVLS, not required (case-insensitive GE-Proton name match: starts with `GE-Proton` or contains `Proton-GE`); both worked normally with Vortex in our tests
- **Any other installed Proton** — works; OVLS honours the tool selected in Steam, or the installed tool recorded in `compatdata/<appid>/config_info` when there is no Steam selection
- Python 3
- `kdialog` or `zenity` (for graphical dialogs; CLI works without them)

## Prepare before installing

1. Install the official Vortex in a Proton prefix. The usual way is to add `vortex-setup-*.exe` to Steam as a non-Steam game, select a Proton compatibility tool (DW-Proton or GE-Proton are recommended) and run it once.
2. Make sure the prefix is writable.
3. Close Vortex before installing. The installer will refuse to proceed if Vortex is running.

Install searches for real Vortex installations: the saved prefix, the legacy location `~/Windows/prefix`, and every `<Steam root>/steamapps/compatdata/<appid>` that contains both `pfx/drive_c/users/` and `pfx/drive_c/Program Files/Vortex/Vortex.exe`, including roots in additional Steam libraries that are present and mounted. One match is used automatically; several matches show a list to choose from; if none is found, the graphical installer opens a directory chooser and validates the folder you pick. You can always pass `--prefix /path/to/compatdata` explicitly. `Change prefix` in the Manager is separate and always opens the chooser.

## Installation

1. Copy the `OVLS Installer` folder to any location, for example `~/Downloads/OVLS`.
2. Open a terminal in that folder, or run:
   ```bash
   bash install.sh
   ```
   or double-click `OVLS Installer.desktop`. If KDE marks the shortcut as untrusted, select “Trust and Launch”.
3. When prompted, choose **Install / Update**.
4. If several Vortex prefixes are found, choose the correct one from the list. On the command line, pass `--prefix /path/to/compatdata` instead.
5. Wait for the success message.

## First launch

After installation, Vortex is available from the desktop and application menu. The OVLS Manager is also available from the application menu.

OVLS launches Vortex through the Proton tool resolved for the prefix: the explicit Steam selection in `config/config.vdf → CompatToolMapping`, then the tool recorded in `compatdata/<appid>/config_info` when there is no mapping entry (only if that tool is installed). Only when neither source gives an installed Proton does it prefer DW-Proton or Proton-GE, then any other installed Proton. No specific version is required and you do not need to pass `--proton` manually.

## Update

1. Obtain a new `OVLS Installer` folder (rebuild the package or unpack a new version).
2. Copy it to any location.
3. Run `install.sh` and choose **Install / Update**.
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
2. Uninstall removes only OVLS-owned files: the installed copy, shortcuts, the NXM handler and its state file, the saved prefix setting, OVLS logs with their rotated copies, and the OVLS Extension (`info.json`, `index.js`, `host.json`) from the Vortex plugin directory. It also removes leftover data of the former JSON IPC channel (`OVLS_IPC_V2` in the Vortex folder) that older OVLS versions created; other files in that folder are kept.
3. If the selected prefix contains Vortex but no OVLS Extension, uninstall is blocked and all OVLS components are kept.
4. The following are **not** removed:
   - Vortex and its prefix
   - Games, mods, and staging folders
   - User settings
   - The Vortex uninstall registration and Steam's `config/libraryfolders.vdf`
   - Registry backups: `backups/<timestamp>/registry/` after a package install repair, or a standalone `system.reg.ovls-backup` in the prefix after a direct `ovls.py install` repair without `--backup`
   - A `mimeapps.list.ovls-backup-<UTC>` recovery copy in `~/.config/`, if the uninstall fallback created one
   - `backups/` in `~/.local/share/ovls`

The two registry backups come from different paths and are not guaranteed for every install. A package install passes a timestamped backup root, so `installer.py` places the registry copy in `backups/<timestamp>/registry/` only when `registry.ensure_uninstall_key()` actually creates or corrects the Vortex uninstall key. A direct `ovls.py install` passes no backup root unless `--backup` is given: without `--backup`, a repair writes `system.reg.ovls-backup` next to `system.reg` in the prefix; with `--backup`, the copy goes to `<backup-root>/<timestamp>/registry/`. No registry backup is made when the registration is already correct or on a dry run.

NXM handler installation does not create a `mimeapps.list` backup. A `mimeapps.list.ovls-backup-<UTC>` copy is made only by `desktop._remove_default_entry()` immediately before that fallback edits `mimeapps.list` directly during uninstall, and only when OVLS is the current default handler and restoring the previous handler did not succeed. If OVLS is not the current default, no backup is made. This copy is a recovery point: OVLS does not restore `mimeapps.list` from it automatically. Restoring the previous handler is a separate mechanism that uses the saved handler name, checks that its `.desktop` file exists, and switches the default with `xdg-mime`.

## Diagnostics

- Run `bash install.sh verify` to check the installed copy state, extension, NXM, and shortcuts.
- Vortex log: `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log`. OVLS extension lines start with `OVLS9`.
- If Vortex shows double window controls, a white band, interface shift, or cursor-click offset, select DW-Proton or Proton-GE for the Vortex shortcut in Steam. For a direct `ovls.py launch` call, `--proton` can override the tool. They are recommendations, not requirements.

## Known limitations

- OVLS does not modify the official Vortex executable or settings; it installs its own Extension into Vortex's plugin directory. OVLS cannot guarantee compatibility with future versions of Vortex internals.
- Mod deployment requires the staging folder to be on the same filesystem volume as the game; matching Wine drive letters (`Z:`) are not sufficient. OVLS can offer a staging target from `host.json` v2 for additional Steam libraries, but the automatic move on a physical SD card still needs verification on SteamOS.
- SteamOS Game Mode has not been tested. R2/L2 triggers work via SteamOS input handling; other controller interactions with Vortex UI elements have not been fully tested.

## Input limitations in SteamOS Desktop Mode (KDE Plasma)

In SteamOS Desktop Mode (KDE Plasma), the following input limitations have been observed:

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
