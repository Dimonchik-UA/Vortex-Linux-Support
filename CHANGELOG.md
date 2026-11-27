# Changelog

This file records user-facing changes to OVLS. The newest version is listed first.

## 0.4.0

**Status:** prepared in the working tree; version `0.4.0` is not published yet. The source version is `0.4.0`.

Removes the experimental JSON IPC channel and stops uncontrolled log/backup growth. User-facing flows are unchanged.

### Fixed

- Launch and NXM logs are now rotated while Vortex runs. Previously rotation was declared but never happened, so the files could grow without limit. Each log keeps at most two rotated copies (1 MB each).
- The `/tmp` fallback for launch and NXM logs is removed. If the log directory cannot be written, Vortex starts without a log and a message is printed.
- Uninstall removes `ovls.log.1`, `ovls.log.2`, `launch.log*` and `nxm.log*` in addition to `ovls.log`.
- `mimeapps.list.ovls-backup-*` is written only when the file changes, and older copies are removed after a successful update. Identical copies are no longer created.
- The standalone `system.reg.ovls-backup` in the prefix is a single file that is overwritten on each repair, instead of a new file per run.
- `install --backup` uses the same timestamp format as snapshots, so its directories are removed by the retention limit.
- Prefix diagnostics use the same Windows profile resolver as Extension install/verify/uninstall instead of assuming `steamuser`; when several profiles contain Vortex, no profile is guessed (`has_vortex: false`).
- Package path protection validates every parent component of the lexical path under the install root, so a symlinked parent pointing to a different directory is rejected.
- Uninstall removes the OVLS icon only when the ownership marker is present. A foreign `ovls.png` without a valid marker is left untouched.

### Removed

- The experimental JSON IPC channel between the external component and the Vortex Extension, with its `ovls.py ipc ping|get-state|status` commands and the `ping`, `get-state` and `ipc-status` aliases. No user scenario depended on it; the Extension is kept for the staging fix.
- Unused imports (`subprocess` and `time` in `ovls.py`, `os` in `manager.py`).

### Changed

- Install, update and uninstall remove leftover data of the former IPC channel (`OVLS_IPC_V2` in the Vortex folder of the prefix). Other files in that folder are kept.
- Update removes the directories left empty after files are removed from the package.
- The duplicated `_zenity` and `_shortcut_notes` helpers in Manager are delegates to the single implementation in Setup UI.

## Unreleased

No unreleased changes.

## 0.3.0

Extends Vortex prefix discovery to all registered Steam libraries and prepares staging for games installed outside the home filesystem.

### Added

- Discovery of Vortex prefixes in every registered Steam library, not only the main Steam root. Libraries that are no longer registered are removed from the volume map. Temporarily unavailable libraries that remain registered reuse their saved volume mapping.
- Storage-type labels in the prefix list: `Internal storage`, `SD card`, and `Storage type unknown`. The label is derived from filesystem and device attributes, not from the directory name.
- `host.json` v2 with a volume map built from real filesystem mount points reported by `findmnt`. Games on different filesystems receive separate `modsRoot` values, and the Extension picks the most specific matching volume for staging.
- A safety guard in Uninstall: if the selected prefix contains Vortex but no OVLS Extension, the operation is blocked and the installed copy, shortcuts, NXM handler and settings are kept.
- Diagnostic states for stale, partially damaged and unrepairable `host.json` maps instead of silently reporting a healthy installation.

### Changed

- Manager uses the same validated prefix search for Install / Update and Uninstall; found prefixes are shown with their detected Proton version and storage type.
- `Change prefix` remains a manual directory chooser. It does not install or move the Extension automatically.
- Proton launch selection now resolves the tool recorded in the prefix's `config_info` when Steam has no `CompatToolMapping` entry, and uses it only if installed. The recommended-tool fallback (DW-Proton / Proton-GE, then any installed Proton) applies only when neither source yields an installed tool.
- Documentation now lists both DW-Proton Latest and Proton-GE Latest as recommended for OVLS (both worked normally with Vortex in our tests). Compatibility recommendations are stated separately from the automatic selection order, with no preference between the two. Test observations are attributed to SteamOS Desktop Mode (KDE Plasma).
- Staging is matched against real filesystem volumes rather than Wine drive letters, so games in additional Steam libraries can get a staging target on the game's own volume.
- `host.json` v1 stays compatible: without `volumes`, the previous behavior is unchanged and existing v1 bytes are preserved. A library that is unmounted but still registered reuses its saved volume; removing a registration is reported as a real change.

### Fixed

- A leftover mount directory from an SD card no longer produces a false staging drift; the most specific saved anchor is reused.
- A stale `host.json` without the `z:` mapping is reported as stale instead of being treated as unchanged or silently overwritten.
- Partially damaged `volumes` entries no longer discard the valid ones and are counted as map issues; an unrepairable v2 map is never silently downgraded to v1.
- A fully valid v2 map is preserved byte-for-byte when the Steam root is temporarily unavailable.
- Prefix labels now show the Proton tool actually used for the prefix: the explicit Steam selection, or the installed tool from the prefix's `config_info` when there is no mapping entry. A truly unknown tool is labelled `Proton not detected` in English instead of the old Russian placeholder; no hard-coded appid-to-Proton mapping was added.

**Not yet verified:** the full staging scenario on a physical SD card in a live Vortex session (Fix appears, staging moves to the SD volume, mod deploys). The implementation is ready; generation, reading, preservation and long-anchor selection were checked in tests and on a synthetic loop volume, but a real SD card still needs verification on SteamOS.

## 0.2.2

Corrects a misleading error message when re-checking for updates after a successful installation.

### Fixed

- False "no internet" error. Manager no longer reports a connection error when the installed version is already up to date; it now correctly states that OVLS is already up to date.

## 0.2.1

Corrects problems in the update mechanism discovered after the 0.2.0 release.

### Fixed

- Cache invalidation after successful update. Manager no longer offers an update that has already been installed.
- Strict staged-version validation. Reject packages without a readable staged version instead of falling back to the running process version.
- Post-install manifest verification with rollback when verification fails.
- Prevent background update checks from restoring stale update cache after a successful installation.

## 0.2.0

OVLS finds real Vortex installations in Steam prefixes, keeps manual prefix selection in `Change prefix`, and no longer forces a Proton version.

### Added

- Discovery of Vortex prefixes under `<Steam root>/steamapps/compatdata/`. Any appid is checked; a prefix is accepted only when it contains both `pfx/drive_c/users/` and `pfx/drive_c/Program Files/Vortex/Vortex.exe`. Install uses this scan automatically.
- A clear explanation when no Vortex is found, including how to install it as a non-Steam game or pass `--prefix`.

### Changed

- **Install / Update** finds Vortex automatically: one match is used without a dialog, several matches are shown as a list to choose from, and no match offers a directory chooser with validation of the picked folder.
- **Change prefix** in the Manager remains a separate manual action: it always opens the directory chooser, never substitutes an automatically found prefix, and leaves the configuration unchanged when cancelled.
- Prefix validation requires both `pfx/drive_c/users/` and `Vortex.exe`. Diagnostics no longer infer a Vortex installation from a game appid; that field is filled only from an explicitly supplied prefix.
- Proton selection honours the compatibility tool selected in Steam for the Vortex prefix (`config.vdf` `CompatToolMapping`). Without a Steam selection, OVLS prefers DW-Proton or Proton-GE (case-insensitive name starting with `GE-Proton` or containing `Proton-GE`), then any installed Proton. No version is mandatory: install and launch are never blocked or cancelled over Proton, `--proton` still overrides the choice.
- Graphical interface and error messages are in English.

### Fixed

- Prefix-detection documentation matches the Install auto-search and separate manual `Change prefix` rules.
- Prefix validation messages in the graphical interface name both required locations instead of only the prefix directory.

## 0.1.0

Initial OVLS version: an independent Linux support layer for the official Windows Vortex on SteamOS/Linux.

### Added

- Runs the official Vortex through Proton without modifying Vortex files or settings.
- Hybrid architecture: an external Linux component for SteamOS, Proton, prefix, filesystem, and NXM tasks, plus a small Vortex Extension for operations that require Vortex context, connected through file-based JSON IPC.
- Desktop integration: a `Vortex` shortcut on the desktop and in the applications menu, an `OVLS Manager` shortcut in the applications menu, and an `nxm://` handler so “Download with Vortex” links open in the official Vortex.
- Launcher with automatic Proton selection: Proton-GE Latest when available, then Proton 11.0 and other installed Proton versions.
- OVLS Manager with Install / Update, Verify, Uninstall, and Change prefix actions, result and error messages, and a guard that requires Vortex to be closed before changing the prefix.
- Support for the normal Vortex mod workflow, including installing mods and deploying them with Symlink Deployment when staging is on the same Wine disk as the game.
- Automatic staging-folder setup for games that require staging on the same Wine disk, plus fixes for Vortex game detection and the missing uninstall-key warning in the prefix.
- Standalone `OVLS Installer` package with a graphical installer (`install.sh` / `OVLS Installer.desktop`) and `install`, `update`, `verify`, and `uninstall` command-line operations, with backups on update and manifest-based integrity checks.
- User documentation for installation, usage, NXM, staging, diagnostics, and the supported environment matrix.
