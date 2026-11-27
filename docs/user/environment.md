# Supported Environment

OVLS runs on SteamOS/Linux and enables the official Windows version of Vortex to work through Proton.

## Supported components

| Component | Status | Notes |
|-----------|--------|-------|
| SteamOS 3.x | Supported | Desktop Mode (KDE Plasma) |
| SteamOS Game Mode | Not tested | |
| Proton-GE Latest | Normal in testing | Recommended choice |
| DW-Proton Latest | Normal in testing | Recommended choice |
| Proton 11.0 | Window and cursor issues observed in testing | |
| Proton Hotfix | Window and cursor issues observed in testing | |
| Proton Experimental | Window and cursor issues observed in testing | |
| Python 3 | Required | Needed to run OVLS |
| kdialog or zenity | Required for GUI | CLI works without either |

Both DW-Proton Latest and Proton-GE Latest are recommended for use with OVLS; both worked normally with Vortex in our tests. These compatibility recommendations are not requirements and are not used to detect the prefix. In automatic selection they form the preferred fallback group only when neither Steam nor the prefix records an installed tool; the order is described below.

## Proton selection

OVLS resolves the Proton tool for the Vortex prefix from `config/config.vdf → CompatToolMapping`; if there is no entry for that appid, it uses the tool recorded in `compatdata/<appid>/config_info`, provided that tool is installed. If neither source gives an installed tool, it prefers **DW-Proton** or **Proton-GE** (including "latest" builds); if neither is installed, it uses another installed Proton version. No specific version is required. GE-Proton is recognised by a case-insensitive directory name that starts with `GE-Proton` or contains `Proton-GE`.

### Tested Proton versions

The following results were observed during testing in SteamOS Desktop Mode (KDE Plasma):

| Proton version | Window display | Cursor coordinates |
|----------------|----------------|--------------------|
| Proton 11.0 | Issues observed | Offset |
| Proton Hotfix | Issues observed | Offset |
| Proton Experimental | Issues observed | Offset |
| Proton-GE Latest | Normal | Normal |
| DW-Proton Latest | Normal | Normal |

On Proton 11.0, Proton Hotfix, and Proton Experimental, the following were observed:

- KDE Plasma displays its own window control buttons (minimize, maximize, close).
- Vortex additionally displays its own Windows-style window control buttons.
- Both sets of buttons are present simultaneously; Vortex's buttons are mostly hidden under the white band at the top of the window.
- A white band appears at the top of the window.
- The Vortex interface is visually shifted downward.
- Cursor coordinates do not match actual click positions: input registers below the visible cursor position.
- These issues could not be resolved in testing.

On Proton-GE Latest and DW-Proton Latest, these issues were not observed in the same tests.

The cause of these differences has not been determined. The issues are not attributed to a specific component (Proton, Wine, KDE, or Vortex), and the results should not be assumed to apply to all systems.

## Input limitations in SteamOS Desktop Mode (KDE Plasma)

The following input issues have been observed in SteamOS Desktop Mode (KDE Plasma). They are distinct from the window rendering issues above.

| Category | Observation |
|----------|-------------|
| **Window rendering / mouse** | On Proton 11.0, Hotfix, Experimental: double window controls, white band, interface shift, cursor-click offset. Not observed on Proton-GE Latest / DW-Proton Latest. |
| **Keyboard focus & text input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Touch input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Controller** | R2/L2 work via SteamOS input handling. Other controller interactions not fully tested. |

### Notes on input issues

- OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom input handling, keyboard focus fixes, touch input handling, or Electron event processing.
- The interaction between Wine/Proton and Electron is a suspected direction of investigation, but the root cause has not been proven.
- No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.
- These issues are independent of the window rendering problems and should not be assumed to share a single cause.

## Finding your Proton versions

OVLS detects Proton installations from:

- Your Steam installation's `steamapps/common/` directory (directories containing "Proton" in the name)
- Your Steam `compatibilitytools.d` directory (files named `proton`)

You do not need to configure Proton manually: OVLS reads the tool selected in Steam. DW-Proton and Proton-GE are recommendations, not requirements.

## Prefix requirements

OVLS requires a Proton prefix that contains:

- `pfx/drive_c/users/` (a Windows users directory)
- `pfx/drive_c/Program Files/Vortex/Vortex.exe` (the official Vortex executable)

Prefix locations used automatically, in order:

1. The prefix you explicitly select
2. A previously selected prefix (saved in your OVLS config)
3. `~/Windows/prefix`
4. Every `<Steam root>/steamapps/compatdata/<appid>` prefix that actually contains Vortex, including roots in additional Steam libraries that are present and mounted. The appid is assigned by Steam and differs between users.

**Install / Update** uses this search: if exactly one Vortex is found, its prefix is used automatically; if several are found, you get a list to choose from; if none is found, OVLS says so and opens a directory chooser. The chosen path is validated before installation. **Change prefix** is separate: it always opens the directory chooser and never substitutes an automatically found prefix. Cancelling it leaves the saved configuration unchanged.

Both `pfx/drive_c/users/` and `Vortex.exe` are required: a directory that only has `users/` is not treated as a Vortex installation. Additional Steam libraries are scanned when they are present and mounted; an unmounted library cannot be searched until its filesystem is available. DW-Proton and GE-Proton are recommended for running Vortex, but they are not required and are not used to detect the prefix.

When several prefixes are shown, each is labelled with the detected Proton version and storage type: `Internal storage`, `SD card`, or `Storage type unknown`. The label comes from the filesystem device (kernel MMC type for SD/eMMC, udev data for internal NVMe), not from the directory name. A real SD card has not been verified in the test environment; USB card readers and unknown devices are reported as `Storage type unknown`.

## Python requirements

OVLS is written in Python 3. No additional Python packages are required beyond the standard library.

## Graphical dialog requirements

OVLS Manager and other graphical dialogs use:

- **kdialog** (KDE Plasma dialog tool) — preferred on SteamOS
- **zenity** (GTK dialog tool) — fallback option

If neither is available, OVLS falls back to command-line output. The command-line interface (`bash install.sh install --prefix ...`) works without kdialog or zenity.

## Root access

OVLS does **not** require root access. Its files are spread across several user-owned locations; the XDG variables use the defaults shown in parentheses when unset.

- **Installed OVLS files** — `$XDG_DATA_HOME/ovls` (default `~/.local/share/ovls/`): the managed copy whose file list is recorded in `MANIFEST.sha256` — the Python code, `ovls/extension/`, `ovls/assets/ovls.png`, `install.sh` and `README.md`. The manifest lists only these managed files, not every OVLS file physically located in `$XDG_DATA_HOME/ovls`. `MANIFEST.sha256` and `VERSION` are generated in the same directory during installation. Additional files described below also live there, but are not part of the managed copy.
- **User settings** — `$XDG_CONFIG_HOME/ovls/config.json` (default `~/.config/ovls/config.json`) stores the selected Proton prefix.
- **State** — `$XDG_STATE_HOME/ovls/` (default `~/.local/state/ovls/`) holds `launch.log`.
- **Logs** — `ovls.log` is written next to the installed `ovls.py` (for the release package, `$XDG_DATA_HOME/ovls/ovls/ovls.log`), `launch.log` in `$XDG_STATE_HOME/ovls/`, and `nxm.log` in `$XDG_DATA_HOME/ovls/`. Each keeps two rotated copies.
- **Shortcuts and NXM integration** — application entries in `$XDG_DATA_HOME/applications/` (`ovls-vortex.desktop`, `ovls-manager.desktop`, `ovls-nxm.desktop`), wrappers in `$XDG_DATA_HOME/ovls/bin/`, the NXM wrapper `ovls-nxm` and `nxm-previous-default` in `$XDG_DATA_HOME/ovls/`, and the icon in `$XDG_DATA_HOME/icons/hicolor/256x256/apps/ovls.png`. The wrappers and NXM files are physically inside the data directory but are not listed in `MANIFEST.sha256`. The desktop copy of `ovls-vortex.desktop` goes to `XDG_DESKTOP_DIR` (default `~/Desktop`).
- **Extension** — inside the selected Proton prefix at `<prefix>/pfx/drive_c/users/<profile>/AppData/Roaming/Vortex/plugins/ovls-json-ipc-v2/` (`info.json`, `index.js` and generated `host.json`).
- **Backups** — `$XDG_DATA_HOME/ovls/backups/<timestamp>/` may contain `app/`, `extension/` and `registry/`; these subdirectories are conditional, are not guaranteed after every installation, and may exist at the same time.

## What OVLS does not require

- OVLS does not modify the official Vortex executable or settings files; it installs its own Extension into Vortex's plugin directory.
- OVLS does not modify your game files or mods.
- OVLS does not modify the host Steam installation; it may create Steam/config/libraryfolders.vdf inside the Proton prefix if that file is missing.
- Core install, launch, and NXM handling do not require network access. OVLS Manager optionally checks GitHub Releases in the background and, when you confirm an update, downloads and verifies the release archive over HTTPS.
