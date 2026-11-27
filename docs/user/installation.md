# Installing OVLS

OVLS (Vortex Linux Support) is distributed as a standalone installer folder called `OVLS Installer`. This page explains how to install it on SteamOS/Linux.

## What the installer package contains

After downloading or building the publication package, you will have a folder named `OVLS Installer`. Inside it:

| File | Purpose |
|------|---------|
| `install.sh` | Entry point script. Starts the OVLS package manager. |
| `OVLS Installer.desktop` | Desktop shortcut that launches `install.sh`. |
| `OVLS - GitHub Repository.desktop` | Shortcut that opens the project's GitHub repository. |
| `README.md` | Short readme for the installer package. |
| `ovls/` | OVLS Python code and Vortex Extension files. Contains `ovls.py` and subdirectories. |

**Do not copy `install.sh` or `OVLS Installer.desktop` separately.** They must stay inside the package folder together with `ovls/`.

## System requirements

- **SteamOS 3.x**, Desktop Mode (KDE Plasma)
- **Steam** installed
- **Official Vortex** installed in a Proton prefix
- **DW-Proton Latest and Proton-GE Latest** — both recommended for use with OVLS, not required (case-insensitive GE-Proton name match: starts with `GE-Proton` or contains `Proton-GE`); both worked normally with Vortex in our tests
- **Any other installed Proton** — works; OVLS first uses the tool selected in Steam via `config/config.vdf → CompatToolMapping`; if there is no entry for that appid, it uses the installed tool recorded in `compatdata/<appid>/config_info`. The fallback is applied only when neither source gives an installed tool.
- **Python 3**
- **kdialog** or **zenity** (required for graphical dialogs; the command-line interface works without them)

OVLS does not require root access.

## Step 1: Check your Proton prefix

The usual way to install official Vortex on SteamOS is to add `vortex-setup-*.exe` to Steam as a non-Steam game, select a Proton compatibility tool and run it once. Vortex is then installed to `C:\Program Files\Vortex` inside the prefix Steam created for that game. **DW-Proton and GE-Proton are recommended choices**; they are recommendations only, not a requirement of OVLS.

**Install / Update** in OVLS Installer and OVLS Manager searches for real Vortex installations: the saved prefix, `~/Windows/prefix`, and every `<Steam root>/steamapps/compatdata/<appid>` that contains both `pfx/drive_c/users/` and `pfx/drive_c/Program Files/Vortex/Vortex.exe`.

- One Vortex found: its prefix is selected and used automatically, no folder search is required.
- Several found: the graphical tools show a list of prefix paths and install only into the one you pick; cancelling aborts the installation.
- None found: OVLS explains how to install Vortex as a non-Steam game and opens a directory chooser so you can find the prefix yourself. On the command line, pass `--prefix /path/to/compatdata`. The chosen directory is validated before installation.
- **Change prefix** is a separate Manager action: it always opens the directory chooser, even when a valid prefix is configured, and cancelling it leaves the configuration unchanged.

Prefixes in additional Steam libraries (another disk or SD card) are scanned when those libraries are present and mounted. Staging targets for such libraries are included in `host.json` v2. If a library is temporarily unmounted, its existing staging target is retained only while it remains registered in Steam; removing the registration is detected by `verify`.

## Step 2: Close Vortex

Close the official Vortex application before installing or updating OVLS. The installer will refuse to modify components while Vortex is running.

## Step 3: Copy the package

Copy the entire `OVLS Installer` folder to any location on your system, for example:

```bash
cp -r "OVLS Installer" ~/Downloads/OVLS
```

## Step 4: Run the installer

Open a terminal in the `OVLS Installer` folder and run:

```bash
bash install.sh
```

This starts the graphical installer menu (using kdialog or zenity). Choose **Install / Update**.

Alternatively, you can run the command-line form:

```bash
bash install.sh install --prefix /path/to/your/prefix
```

Replace `/path/to/your/prefix` with the actual path to your Proton prefix directory (the one that contains `pfx/`).

## Step 5: Verify the installation

After installation completes, run:

```bash
bash install.sh verify
```

This checks that the OVLS copy, Vortex Extension, NXM handler, and desktop shortcuts are all correctly installed.

## What gets installed

The installed copy is placed in `$XDG_DATA_HOME/ovls` (default `~/.local/share/ovls/`). It contains the managed OVLS Python code, the bundled Vortex Extension files, and `MANIFEST.sha256` listing the files of the managed installed copy (used for verification and safe removal), plus `VERSION`. The manifest does not list other OVLS files that may physically reside in the same directory.

OVLS also creates integration files **outside** the installed copy:

- Desktop shortcuts for launching Vortex and opening OVLS Manager
- An NXM handler so `nxm://` links open in the official Vortex

OVLS does **not** modify the official Vortex executable, settings, game files, mods, or your staging directory. It installs its own Extension into Vortex's plugin directory.

### Shortcuts installed by OVLS

After installation, OVLS creates three shortcuts:

| Shortcut | Location | Purpose |
|----------|----------|---------|
| `ovls-vortex.desktop` | Applications menu **and** desktop | Launches the official Vortex through Proton. Name: `Vortex`. Comment: `Official Vortex on Linux`. Categories: `Game;`. |
| `ovls-manager.desktop` | Applications menu | Opens OVLS Manager for install/update/verify/uninstall and prefix changes. Name: `OVLS Manager`. Comment: `OVLS Manager (Official Vortex Linux Support)`. Categories: `Utility;`. |
| `ovls-nxm.desktop` | Protocol-handler entry (not shown as a regular application) | Registers the `nxm://` protocol handler so “Download with Vortex” buttons on Nexus Mods open in the official Vortex. Name: `OVLS NXM Handler`. Categories: `Game;`. |

The OVLS Manager appears in the `Utility;` category of your application menu.

## Updating OVLS

To update OVLS to a new version:

1. Obtain a new `OVLS Installer` folder (rebuild the package or unpack a new release).
2. Copy it to any location.
3. Run `bash install.sh` and choose **Install / Update**.

The update refreshes the OVLS copy and its components. Inside the Vortex prefix it only touches OVLS-owned Extension files, repairs the Vortex uninstall key when missing or broken, adds `libraryfolders.vdf` if missing, and removes leftover `OVLS_IPC_V2` data created by older OVLS versions as legacy cleanup — not as an active IPC channel. Games, mods, and Vortex settings are not changed.

Alternatively, use the OVLS Manager from the application menu (Install / Update). It may check GitHub Releases for updates:
- If you decline the GitHub update, Manager can continue with a normal update from the already installed copy.
- If the GitHub update check is unavailable (for example, offline), Manager can use the normal update path from the already installed copy.
- If you confirm the GitHub update but download, SHA-256 verification, preparation, or installation fails, Manager reports the error; there is no automatic fallback to the normal update.

## Uninstalling OVLS

Run:

```bash
bash install.sh uninstall
```

Or use the OVLS Manager from the application menu (Uninstall).

Uninstall removes only OVLS-owned files: the installed copy, shortcuts, the NXM handler, and the installed OVLS Extension from the Vortex plugin directory in the selected prefix (only Extension files owned by OVLS), and leftover data of the former JSON IPC channel (`OVLS_IPC_V2` in the Vortex folder) created by older OVLS versions; other files in that folder are kept. It does **not** remove:

- Vortex and its prefix
- Games, user mods, and staging folders
- User settings
- `backups/` in `~/.local/share/ovls`

Uninstall also removes the OVLS launch and NXM logs with their rotated copies (`launch.log`, `nxm.log`, `.1`, `.2`).

## Directory layout

After installation, the **managed installed copy** is in `$XDG_DATA_HOME/ovls` (default `~/.local/share/ovls/`). Its layout is:

```
$XDG_DATA_HOME/ovls/
  install.sh
  README.md
  VERSION
  MANIFEST.sha256
  ovls/*.py
  ovls/assets/ovls.png
  ovls/extension/{index.js,info.json}
```

`VERSION` and `MANIFEST.sha256` are generated during installation; every other path in this block comes from package staging.

### Additional files under the OVLS data directory

These files live inside `$XDG_DATA_HOME/ovls`, but are created by separate mechanisms and are not part of the managed copy in `MANIFEST.sha256`:

- `$XDG_DATA_HOME/ovls/bin/ovls-vortex` — wrapper that launches the official Vortex through Proton.
- `$XDG_DATA_HOME/ovls/bin/ovls-manager` — wrapper that opens OVLS Manager.
- `$XDG_DATA_HOME/ovls/ovls.log` — OVLS log written next to the installed `ovls.py` (the path shown is the one used by the release package).
- `$XDG_DATA_HOME/ovls/ovls-nxm` — wrapper that passes an `nxm://` URI to Vortex.
- `$XDG_DATA_HOME/ovls/nxm-previous-default` — saved previous `nxm://` handler; restoration on uninstall is possible only when the conditions described in the NXM section (`docs/user/nxm.md`) are met.

### Integration files outside the OVLS data directory

- `$XDG_DATA_HOME/applications/ovls-vortex.desktop` (applications menu; also copied to `XDG_DESKTOP_DIR`, default `~/Desktop`)
- `$XDG_DATA_HOME/applications/ovls-manager.desktop` (applications menu only)
- `$XDG_DATA_HOME/applications/ovls-nxm.desktop` (hidden NXM protocol-handler entry)
- `$XDG_DATA_HOME/icons/hicolor/256x256/apps/ovls.png` (OVLS icon)
- `$XDG_CONFIG_HOME/ovls/config.json` (selected Proton prefix; default `~/.config/ovls/config.json`)
- `$XDG_CONFIG_HOME/mimeapps.list` (NXM handler registration)
- `$XDG_STATE_HOME/ovls/launch.log` (default `~/.local/state/ovls/launch.log`) and `$XDG_DATA_HOME/ovls/nxm.log`
- `<prefix>/pfx/drive_c/users/<profile>/AppData/Roaming/Vortex/plugins/ovls-json-ipc-v2/{info.json,index.js,host.json}` (Extension inside the selected Proton prefix; `host.json` is generated on install)

### Backups

Backup copies are stored separately from the managed installed copy:

```
$XDG_DATA_HOME/ovls/backups/<timestamp>/
  app/         (when the installed copy receives changed files)
  extension/   (only when Extension files are backed up)
  registry/    (only when the Vortex registry is repaired)
```

These subdirectories are conditional: none is guaranteed after every installation, and more than one can exist in the same snapshot.

The `backups/` directory is kept when OVLS is uninstalled, so you can recover previous versions if needed. It keeps the three most recent snapshots; older ones are removed automatically once `copy_tree()` has completed successfully, not necessarily after the whole install or update finishes. The separate CLI command `ovls.py install --backup` uses the same retention limit. `install.sh` does not accept that flag: `package install`/`update` creates a snapshot only when the installed copy actually receives changed files; `dry_run`, `unchanged`, and self-refresh from the installed copy do not create one.
