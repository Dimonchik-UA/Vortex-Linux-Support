# Diagnostics

If OVLS is not working as expected, use these tools to diagnose the problem.

## Verify your installation

Run:

```bash
bash install.sh verify
```

This checks:

- **Installed copy**: All OVLS files are present and unchanged (verified against `MANIFEST.sha256`)
- **Desktop shortcuts**: All OVLS shortcuts exist and point to the correct files
- **NXM handler**: The `nxm://` protocol handler is registered correctly
- **Vortex Extension**: The extension is installed in your Proton prefix
- **Prefix**: Your Proton prefix is accessible and contains Vortex.exe

If verify reports any problem, run **Install / Update** from OVLS Manager or `bash install.sh update` to repair the installation.

## Check your prefix

OVLS Manager includes a prefix check as part of **Install / Update** and **Verify**. It verifies:

- The prefix directory exists
- The prefix contains `pfx/drive_c/users` (a valid Proton prefix structure)
- `Vortex.exe` exists at `pfx/drive_c/Program Files/Vortex/Vortex.exe`

If any check fails, OVLS Manager will show an error message explaining what is wrong.

## Diagnostic logs

OVLS writes logs to the following locations:

| Log | Location | What it contains |
|-----|----------|------------------|
| Vortex log | `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log` | Vortex application log; OVLS extension lines start with `OVLS9` |
| OVLS launch log | `$XDG_STATE_HOME/ovls/launch.log` (typically `~/.local/state/ovls/launch.log`) | Launch attempts and Proton startup |
| OVLS package log | `~/.local/share/ovls/ovls/ovls.log` | OVLS package manager operations |

## Common problems and solutions

### Vortex window: double window controls, white band, shift, cursor offset

Observed on Proton 11.0, Proton Hotfix, and Proton Experimental in SteamOS Desktop Mode (KDE Plasma):

- KDE Plasma shows its window buttons; Vortex also shows its Windows-style buttons (both present, Vortex buttons mostly hidden under the white band at the top).
- White band at the top of the window.
- Vortex interface visually shifted downward.
- Cursor position does not match click coordinates (click registers below visible cursor).

**Solution:** Select DW-Proton or Proton-GE in Steam for the Vortex shortcut, or pass `--proton /path/to/proton` to OVLS. OVLS honours your selection; these variants did not show the issue in testing. The cause is not determined.

### "Mods can't be deployed."

This means your staging folder is on a different Wine disk than your game. **Solution:** Use the OVLS "Move staging folder" button in Vortex, or manually set your staging folder to the same drive as your game.

### Vortex.exe not found in prefix

OVLS could not find `Vortex.exe` at `pfx/drive_c/Program Files/Vortex/Vortex.exe` in your selected prefix. OVLS accepts a prefix from the config, a known location, or the Steam `compatdata/*` scan only when both `pfx/drive_c/users/` and `Vortex.exe` are present. If nothing is found, Install offers a directory chooser; **Change prefix** always opens it. **Solution:** Install the official Vortex by adding `vortex-setup-*.exe` as a non-Steam game and running it once with a Proton compatibility tool (DW-Proton or GE-Proton are recommended, but not required), or pass `--prefix /path/to/compatdata` explicitly. If another valid prefix is already installed, open **Change prefix** and select its directory manually. **Change prefix** opens a system dialog for manual directory selection and does not use the automatic list of found prefixes.

### NXM links do not open in Vortex

The NXM handler may not be registered. **Solution:** Run **Install / Update** from OVLS Manager or `bash install.sh install --prefix <your-prefix>` to re-register the handler.

### OVLS Manager will not open

OVLS Manager requires kdialog or zenity for graphical dialogs. If neither is installed, use the command line instead:

```bash
bash install.sh install --prefix /path/to/your/prefix
bash install.sh verify --prefix /path/to/your/prefix
bash install.sh uninstall --prefix /path/to/your/prefix
```

### No Proton found

OVLS could not find any Proton installation. **Solution:** Install a Proton compatibility tool through Steam (DW-Proton or Proton-GE is recommended), select it for the Vortex shortcut, then re-run the installer.

## Input limitations on Steam Deck

The following input issues have been observed on Steam Deck in Desktop Mode (KDE Plasma). They are distinct from the window rendering issues above.

### Keyboard focus and text input not working

- Text input does not work correctly in any Vortex input field.
- Not limited to search or profile renaming.
- No workaround available in OVLS.

### Touch input not working

- Touch input does not work correctly in any Vortex input field.
- Not limited to search or profile renaming.
- No workaround available in OVLS.

### Controller input

- R2/L2 triggers work via SteamOS input handling.
- Other controller interactions with Vortex UI have not been fully tested.

### Notes on input issues

- OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom input handling.
- The cause is suspected to involve Wine/Proton and Electron interaction, but has not been proven.
- No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.
