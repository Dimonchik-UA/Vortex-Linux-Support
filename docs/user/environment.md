# Supported Environment

OVLS runs on SteamOS/Linux and enables the official Windows version of Vortex to work through Proton.

## Supported components

| Component | Status | Notes |
|-----------|--------|-------|
| SteamOS 3.x | Supported | Desktop Mode (KDE Plasma) |
| SteamOS Game Mode | Not tested | |
| Proton-GE Latest | Normal in testing | Recommended choice |
| DW-Proton Latest | Normal in testing | |
| Proton 11.0 | Window and cursor issues observed in testing | |
| Proton Hotfix | Window and cursor issues observed in testing | |
| Proton Experimental | Window and cursor issues observed in testing | |
| Python 3 | Required | Needed to run OVLS |
| kdialog or zenity | Required for GUI | CLI works without either |

## Proton selection

OVLS uses the Proton tool selected in Steam for the Vortex prefix. If Steam has no selection, it prefers **DW-Proton** or **Proton-GE** (including "latest" builds); if neither is installed, it uses another installed Proton version. No specific version is required. GE-Proton is recognised by a case-insensitive directory name that starts with `GE-Proton` or contains `Proton-GE`.

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

## Input limitations on Steam Deck

The following input issues have been observed on Steam Deck in Desktop Mode (KDE Plasma). They are distinct from the window rendering issues above.

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
4. Every `<Steam root>/steamapps/compatdata/<appid>` prefix that actually contains Vortex. The appid is assigned by Steam and differs between users.

**Install / Update** uses this search: if exactly one Vortex is found, its prefix is used automatically; if several are found, you get a list to choose from; if none is found, OVLS says so and opens a directory chooser. The chosen path is validated before installation. **Change prefix** is separate: it always opens the directory chooser and never substitutes an automatically found prefix. Cancelling it leaves the saved configuration unchanged.

Both `pfx/drive_c/users/` and `Vortex.exe` are required: a directory that only has `users/` is not treated as a Vortex installation. Only the main Steam root is scanned; prefixes on additional Steam library drives are not covered. DW-Proton and GE-Proton are recommended for running Vortex, but they are not required and are not used to detect the prefix.

## Python requirements

OVLS is written in Python 3. No additional Python packages are required beyond the standard library.

## Graphical dialog requirements

OVLS Manager and other graphical dialogs use:

- **kdialog** (KDE Plasma dialog tool) — preferred on SteamOS
- **zenity** (GTK dialog tool) — fallback option

If neither is available, OVLS falls back to command-line output. The command-line interface (`bash install.sh install --prefix ...`) works without kdialog or zenity.

## Root access

OVLS does **not** require root access. All files are installed into your user data directory (typically `~/.local/share/ovls/`).

## What OVLS does not require

- OVLS does not modify the official Vortex executable or settings files; it installs its own Extension into Vortex's plugin directory.
- OVLS does not modify your game files or mods.
- OVLS does not modify your Steam installation.
- OVLS does not require network access beyond what Vortex itself uses.
