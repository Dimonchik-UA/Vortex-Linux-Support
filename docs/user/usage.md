# Using OVLS

This page covers day-to-day use of OVLS after installation: launching Vortex, using the OVLS Manager, and managing your prefix.

## Launching Vortex

OVLS installs a desktop shortcut (`ovls-vortex.desktop`) both in the applications menu and on the desktop. Name: `Vortex`. Comment: `Official Vortex on Linux`. Categories: `Game;`. When you launch Vortex through OVLS:

1. OVLS detects your Proton prefix (see below).
2. It launches the official Windows `Vortex.exe` through Proton-GE Latest (or DW-Proton Latest if Proton-GE is not available; Proton 11.0, Hotfix, and Experimental showed issues in testing).
3. The Vortex Extension inside Vortex loads and connects back to OVLS.

You do not need to pass `--proton` manually. OVLS selects Proton-GE Latest by default because Proton 11.0, Proton Hotfix, and Proton Experimental show double window controls, a white band, interface shift, and cursor-click offset in testing.

## Opening OVLS Manager

OVLS Manager is a graphical tool (using kdialog or zenity) for managing OVLS. You can open it from:

- The applications menu (look for **OVLS Manager**, in the `Utility;` category; Comment: `OVLS Manager (Official Vortex Linux Support)`)
- Running `bash install.sh` from the `OVLS Installer` folder (no arguments opens the graphical menu)

OVLS Manager provides four actions:

| Action | What it does |
|--------|--------------|
| **Install / Update** | Installs or updates the OVLS Extension, NXM handler, and shortcuts from the installed copy into your selected prefix. |
| **Verify** | Checks that the installed copy, Extension, NXM handler, and shortcuts are all present and correct. |
| **Uninstall** | Removes OVLS components (Extension, NXM handler, shortcuts). Vortex, mods, prefix, and user settings are not touched. |
| **Change prefix** | Switches the Proton prefix OVLS uses. Requires Vortex to be closed first. |

## Using OVLS Manager

### Install / Update

Use this action to (re)install the OVLS Extension, NXM handler, and shortcuts into your Proton prefix. OVLS Manager may check GitHub Releases for updates:
- If you decline the GitHub update, Manager can continue with a normal update from the already installed copy.
- If the GitHub update check is unavailable (for example, offline), Manager can use the normal update path from the already installed copy.
- If you confirm the GitHub update but download, SHA-256 verification, preparation, or installation fails, Manager reports the error; there is no automatic fallback to the normal update.

OVLS Manager will:

1. Check that your prefix is set and contains `Vortex.exe`.
2. Check that OVLS is installed in `~/.local/share/ovls/`.
3. Verify Vortex is not running.
4. Copy the Extension, register the NXM handler, and create shortcuts.

If Vortex is running, Manager will ask you to close it first.

### Verify

Use this action to check the health of your OVLS installation. Verify checks:

- The installed OVLS copy (all manifest files present and unchanged)
- Desktop shortcuts
- The NXM handler registration
- The Vortex Extension inside your prefix
- Steam library configuration

### Uninstall

Use this action to remove OVLS components. Uninstall removes only OVLS-owned files:

- The installed OVLS Extension from the Vortex plugin directory in the selected prefix (only Extension files owned by OVLS: `info.json` and `index.js`)
- The NXM handler
- Desktop shortcuts

Vortex, its prefix, user mods, staging folders, user settings, and `backups/` in `~/.local/share/ovls` are **not** removed.

After uninstalling, OVLS Manager closes because its installed copy is gone. To reinstall, run `bash install.sh install` from the `OVLS Installer` folder.

### Change prefix

Use this action to switch OVLS to a different Proton prefix. Requirements:

1. Vortex must be closed.
2. The new prefix must contain `pfx/drive_c/users` (a valid Proton prefix).
3. The prefix must contain `Vortex.exe` at `pfx/drive_c/Program Files/Vortex/Vortex.exe`.

After changing the prefix, run **Install / Update** to install OVLS components into the new prefix.

## Using OVLS from the command line

If you prefer the command line or do not have kdialog/zenity installed, you can use `install.sh` directly:

| Command | What it does |
|---------|--------------|
| `bash install.sh install --prefix /path/to/prefix` | Install OVLS components into a prefix |
| `bash install.sh update --prefix /path/to/prefix` | Update OVLS components in an existing prefix |
| `bash install.sh verify --prefix /path/to/prefix` | Verify the installation |
| `bash install.sh uninstall --prefix /path/to/prefix` | Remove OVLS components from a prefix |

If you omit `--prefix`, OVLS uses the remembered prefix from your config file (`~/.config/ovls/config.json`), or auto-detects from common locations.

## Prefix detection

OVLS looks for your Proton prefix in this order:

1. The prefix you explicitly provided (via command-line `--prefix` or Manager selection)
2. The prefix you previously selected (saved in `~/.config/ovls/config.json`)
3. `~/Windows/prefix`
4. `~/.steam/root/steamapps/compatdata/1712840` (Steam's Vortex compatdata)

A valid prefix must contain `pfx/drive_c/users`.

## Proton selection

OVLS prefers **Proton-GE** (any version with "ge-proton" or "proton-ge" in the name, preferring "latest") when launching Vortex, because Proton 11.0, Proton Hotfix, and Proton Experimental show double window controls, a white band, interface shift, and cursor-click offset in testing. Proton-GE and DW-Proton Latest did not show these issues in testing. If Proton-GE is not found, OVLS falls back to **Proton 11.0**, then to any other installed Proton version.

You do not need to pass `--proton` manually when launching Vortex through OVLS.

## Input limitations on Steam Deck

On Steam Deck in Desktop Mode (KDE Plasma), the following input limitations have been observed. They are separate from the window rendering issues described above.

| Category | Observation |
|----------|-------------|
| **Window rendering / mouse** | On Proton 11.0, Hotfix, Experimental: double window controls, white band, interface shift, cursor-click offset. Not observed on Proton-GE Latest / DW-Proton Latest. |
| **Keyboard focus & text input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Touch input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Controller** | R2/L2 work via SteamOS input handling. Other controller interactions not fully tested. |

OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom keyboard focus fixes, touch input handling, or Electron event processing. The root cause (suspected Wine/Proton–Electron interaction) has not been proven. No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.
