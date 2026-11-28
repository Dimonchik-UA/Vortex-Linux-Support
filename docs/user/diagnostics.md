# Diagnostics

If OVLS is not working as expected, use these tools to diagnose the problem.

## Verify your installation

Run:

```bash
bash install.sh verify
```

This checks:

- **Prefix**: the selected prefix is accessible and contains Vortex.exe
- **OVLS version**: the version of the installed OVLS copy (`unknown` if unavailable)
- **Code copy**: all OVLS files are present and unchanged (verified against `MANIFEST.sha256`)
- **Shortcuts**: all OVLS desktop shortcuts exist and point to the correct files
- **Extension**: the Vortex Extension is installed in your prefix
- **Vortex uninstall key**: the Vortex uninstall key in the prefix is present and correct
- **NXM handler**: the `nxm://` protocol handler is registered correctly
- **Steam library**: shown in OVLS Manager only; the installer's Verify does not show this line

For most problems, run **Install / Update** from OVLS Manager or `bash install.sh update` to repair the installation. This is **not** guaranteed for the Extension `host.json` states described below.

## Check your prefix

OVLS Manager includes a prefix check as part of **Install / Update** and **Verify**. It verifies:

- The prefix directory exists
- The prefix contains `pfx/drive_c/users` (a valid Proton prefix structure)
- `Vortex.exe` exists at `pfx/drive_c/Program Files/Vortex/Vortex.exe`

If any check fails, OVLS Manager will show an error message explaining what is wrong.

## Extension `host.json` states

`verify` reports the Extension state as one of the following. The three exceptional `host.json` states — `stale_host`, `invalid_host_file`, and `host_map_unrepairable` — are **not** repaired by Install / Update; OVLS leaves the existing file untouched and shows an attention message instead of "Components updated". The `host_map_issues` value is a count of damaged volume entries, not a separate state.

| State | Meaning | What to do |
|-------|---------|------------|
| `unchanged` | Installed Extension and `host.json` match the generated files. | Nothing. |
| `drifted` / `missing_files` | A managed file differs from the generated version. | Run Install / Update; it rewrites only OVLS-owned files. |
| `host_map_issues` | Count of damaged entries in the saved v2 `volumes` map. Non-zero means at least one saved volume was invalid and was not reused. | Run Install / Update; valid volumes are kept and the damaged entries are rebuilt when Steam libraries are available. |
| `stale_host` | `host.json` exists but the prefix `z:` drive mapping (`pfx/dosdevices/z:`) is missing, so OVLS cannot describe host paths. | Restore the `z:` drive mapping in the prefix, then verify again. Install / Update will not overwrite or delete the existing file. Do not remove `host.json` unless you intend to lose the staging fix. |
| `invalid_host_file` | `host.json` is a directory or symlink instead of a regular file. | Remove or rename that object yourself after inspecting it. OVLS will not replace or delete it, and uninstall will skip it. |
| `host_map_unrepairable` | The installed v2 `volumes` map is damaged and cannot be rebuilt safely. A valid v2 map is preserved unchanged when Steam libraries are temporarily unavailable. | Repair or restore the Steam library registration, then verify again. OVLS will not downgrade the file to v1 or destroy the damage information. |

A valid v1 `host.json` (only `modsRoot`) is still supported. Install / Update may upgrade it to v2 when additional Steam libraries are registered, and may rewrite it in canonical form even when no v2 map is generated, so byte-for-byte preservation is not guaranteed for v1. Byte-for-byte preservation applies to a fully valid v2 map when the Steam root is temporarily unavailable. Structural damage to a v2 map is counted even when `z:` is missing, although `stale_host` remains the reported status in that case.

## Diagnostic logs

OVLS writes logs to the following locations:

| Log | Location | What it contains |
|-----|----------|------------------|
| Vortex log | `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log` | Vortex application log; OVLS extension lines start with `OVLS9` |
| OVLS launch log | `$XDG_STATE_HOME/ovls/launch.log` (typically `~/.local/state/ovls/launch.log`) | Vortex launch and Proton output |
| OVLS NXM log | `$XDG_DATA_HOME/ovls/nxm.log` (typically `~/.local/share/ovls/nxm.log`) | Vortex output for `nxm://` links |
| OVLS package log | `$XDG_DATA_HOME/ovls/ovls/ovls.log` (default `~/.local/share/ovls/ovls/ovls.log` when `XDG_DATA_HOME` is unset) | OVLS package manager log (currently no records) |

The OVLS launch and NXM logs are limited to 1 MB each, with two rotated copies (`launch.log.1`, `launch.log.2`, and the same for `nxm.log`). Older output is discarded automatically. Uninstall removes all OVLS logs and their rotated copies.

## Common problems and solutions

### Vortex window: double window controls, white band, shift, cursor offset

Observed on Proton 11.0, Proton Hotfix, and Proton Experimental in SteamOS Desktop Mode (KDE Plasma):

- KDE Plasma shows its window buttons; Vortex also shows its Windows-style buttons (both present, Vortex buttons mostly hidden under the white band at the top).
- White band at the top of the window.
- Vortex interface visually shifted downward.
- Cursor position does not match click coordinates (click registers below visible cursor).

**Solution:** Select DW-Proton or Proton-GE in Steam for the Vortex shortcut. For a direct `ovls.py launch` call, you can instead pass `--proton /path/to/proton`; the desktop shortcut and Manager do not accept that option. OVLS honours your selection; these variants did not show the issue in testing. The cause is not determined.

### "Mods can't be deployed."

This means your staging folder is on a different filesystem volume than your game; under Proton the Wine drive letters can be the same. **Solution:** Use the OVLS "Move staging folder" button in Vortex, or manually set your staging folder to the target volume for your Steam library.

### Vortex.exe not found in prefix

OVLS could not find `Vortex.exe` at `pfx/drive_c/Program Files/Vortex/Vortex.exe` in your selected prefix. OVLS accepts a prefix from the config, a known location, or the Steam `compatdata/*` scan in the main root and in mounted additional libraries only when both `pfx/drive_c/users/` and `Vortex.exe` are present. If nothing is found, Install offers a directory chooser; **Select Vortex prefix (pfx)** always opens it. **Solution:** Install the official Vortex by adding `vortex-setup-*.exe` as a non-Steam game and running it once with a Proton compatibility tool (DW-Proton or GE-Proton are recommended, but not required), or pass `--prefix /path/to/compatdata` explicitly. If another valid prefix is already installed, open **Select Vortex prefix (pfx)** and select its directory manually. **Select Vortex prefix (pfx)** opens a system dialog for manual directory selection and does not use the automatic list of found prefixes.

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

## Input limitations in SteamOS Desktop Mode (KDE Plasma)

The following input issues have been observed in SteamOS Desktop Mode (KDE Plasma). They are distinct from the window rendering issues above.

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
