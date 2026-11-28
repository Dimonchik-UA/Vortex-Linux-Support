# Using OVLS

This page covers day-to-day use of OVLS after installation: launching Vortex, using the OVLS Manager, and managing your prefix.

## Launching Vortex

OVLS installs a desktop shortcut (`ovls-vortex.desktop`) both in the applications menu and on the desktop. Name: `Vortex`. Comment: `Official Vortex on Linux`. Categories: `Game;`. When you launch Vortex through OVLS:

1. OVLS detects your Proton prefix (see below).
2. It launches the official Windows `Vortex.exe` through the Proton tool resolved for the Vortex prefix: the explicit Steam selection in `config/config.vdf → CompatToolMapping` first, then — when there is no entry for that appid — the tool recorded in `compatdata/<appid>/config_info`. A tool found there is used only if it is installed (its directory contains `proton`); OVLS only reads these Steam files. Only if neither source yields an installed Proton does it prefer a recommended tool (DW-Proton or Proton-GE), then any other installed Proton. No specific Proton version is required. There is no separate DW-Proton branch: it is one of the recommended names used only in this last fallback.
3. The Vortex Extension loads inside Vortex. It checks the staging folder and applies the staging fix through Vortex's internal API (`SET_MOD_INSTALL_PATH`); there is no IPC channel or connection back to OVLS.

You do not need to pass `--proton` manually: OVLS uses the Proton selected in Steam for this prefix, or the installed tool recorded in the prefix when Steam has no selection. The `--proton` option is available only for direct `ovls.py launch` and `ovls.py nxm` calls; the desktop shortcut, Manager and `install.sh` do not accept it. DW-Proton Latest and Proton-GE Latest are the compatibility recommendations — both worked normally with Vortex in our tests — and the automatic choice uses them only when no installed tool can be resolved from Steam. Proton 11.0, Proton Hotfix, and Proton Experimental show double window controls, a white band, interface shift, and cursor-click offset in testing, but OVLS will still launch with them.

## Opening OVLS Manager

OVLS Manager is a graphical tool (using kdialog or zenity) for managing OVLS. You can open it from:

- The applications menu (look for **OVLS Manager**, in the `Utility;` category; Comment: `OVLS Manager (Official Vortex Linux Support)`)

Running `bash install.sh` from the `OVLS Installer` folder without arguments opens the standalone **OVLS Installer** menu, not OVLS Manager. The two graphical tools are separate; see [Installation](installation.md).

OVLS Manager provides five actions:

| Action | What it does |
|--------|--------------|
| **Install / Update** | Installs or updates the OVLS Extension in the selected Vortex prefix and registers the NXM handler and desktop shortcuts in your user XDG directories. |
| **Select Vortex prefix (pfx)** | Opens a manual directory chooser to switch the Proton prefix OVLS uses. If a prefix is already configured, Vortex in that prefix must be closed first. |
| **Verify** | Checks that the installed copy, Extension, NXM handler, shortcuts, and Steam library configuration are all present and correct, and shows the installed OVLS version. |
| **Paths & Folders** | Opens the folder submenu with the prefix folder, discovered games and their Mods folders, and Mod Downloads Folder. |
| **Uninstall** | Removes OVLS components (Extension, NXM handler, shortcuts). Vortex, mods, prefix, and user settings are not touched. |

## Using OVLS Manager

### Install / Update

Use this action to (re)install the OVLS Extension in the selected Proton prefix and register the NXM handler and desktop shortcuts in your user XDG directories. OVLS Manager may check GitHub Releases for updates:
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

- The selected prefix
- The installed OVLS copy (all manifest files present and unchanged) and its version
- Desktop shortcuts
- The NXM handler registration
- The Vortex Extension inside your prefix
- The Vortex uninstall key
- Steam library configuration (shown in Manager only; the installer's Verify does not show it)

The result includes an `OVLS version:` line with the version of the installed OVLS copy. If no version is available, it shows `OVLS version: unknown`.

### Paths & Folders

This menu contains three actions and `⬅️ Back`:

| Action | What it does |
|--------|--------------|
| **Open Vortex prefix folder** | Opens the `pfx` folder of the currently selected Vortex prefix in your file manager. |
| **Games & Mods folders** | Opens the discovered-game list and the per-game folder submenu. See below. |
| **Mod Downloads Folder** | Opens the Vortex mod downloads folder directly in your file manager. No chooser or information dialog is shown. See below. |

The Games & Mods folders and Mod Downloads Folder actions read only the Vortex state (`state.v2`) of the currently selected prefix through the read-only OVLS reader. They never change the prefix, never write Vortex settings, and do not use `settings.json`. If no unique prefix is selected, or `state.v2` cannot be read, Manager shows an error and stays in the same menu.

`⬅️ Back` and Cancel move up exactly one level: game submenu → game list → `Paths & Folders` → main menu. Cancel in the main menu closes OVLS Manager. Opening a folder does not change the current level.

Submenus display a fixed, non-selectable label with the current section name (`Paths & Folders`, `Games & Mods folders`, or the selected game) above the items. The main menu has no label, and the window title stays `OVLS`.

### Games & Mods folders

OVLS reads the games Vortex has already discovered from `state.v2` in the current prefix. The game list shows one `🎮 <game name>` entry per game and `⬅️ Back`; technical game IDs are not shown. Game names come from the name Vortex stored for the discovered game, or from the matching game extension's `registerGame()`/`registerGameStub()` name. If Vortex has no reliable name, the entry is shown as `🎮 Игра без названия (N)` and still opens that game's folders. If no discovered games are present, it shows `Обнаруженные игры не найдены` and returns to `Paths & Folders`.

Selecting a game opens a submenu with `🧩 Open mods staging folder`, `📁 Open game folder` and `⬅️ Back`:

- **Open mods staging folder** opens the staging folder Vortex uses for this game. If Vortex has an explicit `settings.mods.installPath[gameId]`, that path is used exactly as before, including its error messages. Only when that setting is absent does Manager check Vortex's standard `<userData>/<gameId>/mods` folder. The standard folder is opened only if it exists, stays inside `userData`, and contains a regular `__vortex_staging_folder` JSON marker whose `game` and `instance` match the selected prefix's `state.v2`. This is not the `Mods` folder inside the game directory.
- **Open game folder** opens the installed game folder from `settings.gameMode.discovered[gameId].path`, the same source Vortex uses for its Open Game Folder action.

Both paths are converted from Windows form to a Linux host path, checked to be existing directories, and opened with `xdg-open`. An invalid explicit staging setting is never replaced by the standard path; a missing or unverifiable standard staging folder is reported as an error. No other defaults are substituted, and no Vortex file, marker, setting or mod is changed. If `xdg-open` fails, Manager shows a clear message.

### Mod Downloads Folder

OVLS reads `settings.downloads.path` from `state.v2` in the current selected prefix. Vortex template tokens are expanded before conversion to a Linux path:

- `{USERDATA}` is the actual Windows `userData` path inside the selected Proton prefix.
- `{USERNAME}` is the current desktop user name, determined dynamically; it is not a fixed `steamuser` value.
- Relative and drive-relative values are resolved against that `userData` path, following Vortex behavior. Paths that leave the mapped drive are rejected.

Manager opens the resolved folder directly in the file manager with `xdg-open`. No directory chooser and no `Downloads folder: ...` information dialog are shown; nothing is saved and Vortex settings are not changed. If the setting is missing, Manager opens Vortex's standard `<userData>/downloads` folder. If the setting is invalid, cannot be converted, or if the resulting directory does not exist, Manager shows a clear error, does not call `xdg-open` and does not substitute another path.

### Uninstall

Use this action to remove OVLS components. Uninstall removes only OVLS-owned files:

- The installed OVLS code copy, including `MANIFEST.sha256`, `VERSION`, and the saved prefix setting (`~/.config/ovls/config.json`)
- The installed OVLS Extension from the Vortex plugin directory in the selected prefix (only OVLS-owned files: `info.json`, `index.js`, and generated `host.json`)
- The NXM handler and its state file
- Desktop shortcuts
- OVLS logs and their rotated copies (`ovls.log`, `launch.log`, `nxm.log`, `.1`, `.2`)
- Leftover data of the former JSON IPC channel (`OVLS_IPC_V2` in the Vortex folder of the selected prefix), if an older OVLS version created it; other files in that folder are kept

Vortex, its prefix, user mods, staging folders, user settings, and `backups/` in `~/.local/share/ovls` are **not** removed.

Uninstall uses the saved valid prefix without asking again. If no valid prefix is configured, OVLS applies the same search as Install / Update and asks you to choose only when several Vortex prefixes are found. If the chosen prefix contains Vortex but no OVLS Extension, uninstall is blocked and the installed copy, shortcuts, NXM handler, and settings are kept.

After uninstalling, OVLS Manager closes because its installed copy is gone. To reinstall, run `bash install.sh install` from the `OVLS Installer` folder.

### Select Vortex prefix (pfx)

Use this action to switch OVLS to a different Proton prefix. It is separate from Install: it always opens a manual directory chooser (kdialog/zenity), not a list of automatically found prefixes. A prefix found automatically is used only as the chooser's starting folder; it never replaces your manual choice. It only changes the selected prefix; it does not install components there automatically. Requirements:

1. If a prefix is already configured, Vortex in that prefix must be closed. The check is limited to that prefix; OVLS does not detect Vortex running from another prefix.
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

If you omit `--prefix`, OVLS uses the remembered prefix from your config file (`~/.config/ovls/config.json`), a known host location, or a single Vortex found by the Steam prefix scan. If several valid prefixes are found, the command line does not guess: pass `--prefix /path/to/compatdata` explicitly.

## Prefix detection

For automatic selection, OVLS checks a valid Vortex prefix in this order:

1. The prefix you explicitly provided (via command-line `--prefix` or Manager selection)
2. The prefix you previously selected (saved in `~/.config/ovls/config.json`)
3. `~/Windows/prefix`
4. Every `<Steam root>/steamapps/compatdata/<appid>` that actually contains Vortex, including roots in additional Steam libraries that are present and mounted (the appid is assigned by Steam and differs between users)

A valid prefix must contain both `pfx/drive_c/users/` and `pfx/drive_c/Program Files/Vortex/Vortex.exe`.

- **One Vortex found:** its prefix is used automatically, without a dialog.
- **Several found:** the graphical Installer and Manager list the prefix paths, labelled with the detected Proton tool and storage type (`Internal storage`, `SD card`, or `Storage type unknown`), and install only into the one you select. Cancelling aborts the operation.
- **None found:** OVLS says Vortex was not found, offers a directory chooser, and validates the folder you pick. The CLI requires `--prefix /path/to/compatdata`.

**Select Vortex prefix (pfx)** in the Manager is a separate action: it always opens the directory chooser, never replaces your choice with an automatically found prefix (a found prefix is only the chooser's starting folder), and leaves the saved configuration untouched if you cancel.

Prefixes in additional Steam libraries can be detected when those libraries are present and mounted. A temporarily unmounted library does not make an installed prefix invalid on disk, but Install / Update may not be able to rebuild its staging target until the library is mounted again; see [Staging folder](staging.md) and [Diagnostics](diagnostics.md).

DW-Proton Latest and Proton-GE Latest are both recommended for use with OVLS; both worked normally with Vortex in our tests. Neither is required, and neither is used to detect the prefix. In the automatic fallback they form the preferred group and names containing `latest` are preferred; this does not replace a tool selected in Steam. GE-Proton is recognised by a case-insensitive directory name that starts with `GE-Proton` or contains `Proton-GE`.

## Proton selection

OVLS resolves the launch tool in this order: the explicit Steam selection for the prefix in `config/config.vdf → CompatToolMapping`; if that entry is absent, the tool recorded in `compatdata/<appid>/config_info` — the tool Steam used to create the prefix. A tool from `config_info` is accepted only when it is installed (its directory contains `proton`), and OVLS only reads these Steam files. Only when neither source gives an installed tool does OVLS prefer **DW-Proton** or **Proton-GE** (including "latest" builds), then any other installed Proton; it never blocks the launch for this reason. Proton 11.0, Proton Hotfix, and Proton Experimental show double window controls, a white band, interface shift, and cursor-click offset in testing but are still allowed. GE-Proton matching is case-insensitive and requires a directory name starting with `GE-Proton` or containing `Proton-GE`. For direct CLI calls, `ovls.py launch` and `ovls.py nxm` also accept `--proton` to override the choice; the desktop shortcut and Manager do not expose it.

You do not need to pass `--proton` manually when launching Vortex through OVLS.

## Input limitations in SteamOS Desktop Mode (KDE Plasma)

In SteamOS Desktop Mode (KDE Plasma), the following input limitations have been observed. They are separate from the window rendering issues described above.

| Category | Observation |
|----------|-------------|
| **Window rendering / mouse** | On Proton 11.0, Hotfix, Experimental: double window controls, white band, interface shift, cursor-click offset. Not observed on Proton-GE Latest / DW-Proton Latest. |
| **Keyboard focus & text input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Touch input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Controller** | R2/L2 work via SteamOS input handling. Other controller interactions not fully tested. |

OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom keyboard focus fixes, touch input handling, or Electron event processing. The root cause (suspected Wine/Proton–Electron interaction) has not been proven. No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.
