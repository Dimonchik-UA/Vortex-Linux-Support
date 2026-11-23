# Changelog

This file records user-facing changes to OVLS. The newest version is listed first.

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
