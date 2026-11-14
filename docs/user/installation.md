# Installing OVLS

OVLS (Vortex Linux Support) is distributed as a standalone installer folder called `OVLS Installer`. This page explains how to install it on SteamOS/Linux.

## What the installer package contains

After downloading or building the publication package, you will have a folder named `OVLS Installer`. Inside it:

| File | Purpose |
|------|---------|
| `install.sh` | Entry point script. Starts the OVLS package manager. |
| `OVLS Installer.desktop` | Desktop shortcut that launches `install.sh`. |
| `README.md` | Short readme for the installer package. |
| `ovls/` | OVLS Python code and Vortex Extension files. Contains `ovls.py` and subdirectories. |

**Do not copy `install.sh` or `OVLS Installer.desktop` separately.** They must stay inside the package folder together with `ovls/`.

## System requirements

- **SteamOS 3.x**, Desktop Mode (KDE Plasma)
- **Steam** installed
- **Official Vortex** installed in a Proton prefix
- **Proton-GE** (any version with "ge-proton" or "proton-ge" in the name, preferring "latest") — recommended
- **Proton 11.0** — fallback
- **Python 3**
- **kdialog** or **zenity** (required for graphical dialogs; the command-line interface works without them)

OVLS does not require root access.

## Step 1: Determine your Proton prefix

OVLS needs to know where your Proton prefix is. The prefix is the directory that contains a `pfx/` subdirectory. Common locations include:

- `~/Windows/prefix`
- `/path/to/your/prefix`
- A Steam compatdata directory under `~/.steam/root/steamapps/compatdata/`

You can choose any directory that contains a `pfx/` subdirectory with a Windows `drive_c` structure inside.

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

This starts the graphical installer menu (using kdialog or zenity). Choose **Install**.

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

OVLS installs to your user data directory (typically `~/.local/share/ovls/`). It creates:

- The OVLS Python code and Vortex Extension files inside the installed copy
- A `MANIFEST.sha256` file listing every file OVLS owns (used for verification and safe removal)
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
3. Run `bash install.sh` and choose **Update**.

The update installs code from the new package into your existing installation without touching your Vortex prefix, games, or mods.

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

Uninstall removes only OVLS-owned files: the installed copy, shortcuts, the NXM handler, and the installed OVLS Extension from the Vortex plugin directory in the selected prefix (only Extension files owned by OVLS). It does **not** remove:

- Vortex and its prefix
- Games, user mods, and staging folders
- User settings
- `backups/` in `~/.local/share/ovls`

## Directory layout

After installation, OVLS lives in your user data directory (typically `~/.local/share/ovls/`). The installed copy mirrors the package layout:

```
ovls/
  install.sh
  README.md
  VERSION
  MANIFEST.sha256
  ovls/*.py
  ovls/extension/*
  backups/<timestamp>/{app,extension,registry}
```

The `backups/` directory is kept when OVLS is uninstalled, so you can recover previous versions if needed.
