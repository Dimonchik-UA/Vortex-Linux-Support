![OVLS preview](docs/assets/ovls-preview.png)

# OVLS — Linux Support for Official Vortex

Vortex is a mod manager for PC games developed by Nexus Mods. OVLS (Vortex Linux Support) is an independent external layer that enables the official Windows version of Vortex to run on SteamOS/Linux through Proton, without modifying Vortex itself.

Repository: https://github.com/Dimonchik-UA/Vortex-Linux-Support

## What OVLS does

OVLS is not Vortex, and it does not change the official Vortex application files or Vortex user settings. It is a separate, installable layer that adds Linux/SteamOS integration around the official Vortex application; it installs and removes its own Extension separately:

- **Vortex launcher** (`ovls-vortex.desktop`): Launches the official Vortex through Proton. For the selected prefix OVLS first uses the compatibility tool from Steam's `config/config.vdf → CompatToolMapping`; if there is no entry for that appid, it uses the tool recorded in `compatdata/<appid>/config_info` (accepted only when that tool is installed). Only when neither source yields an installed tool does it prefer a recommended tool (DW-Proton or Proton-GE), then any other installed Proton. No specific Proton version is required; select the tool in Steam. `--proton` is an explicit override only for direct `ovls.py launch` and `ovls.py nxm` calls — the desktop shortcut, Manager and `install.sh` do not accept it. Proton-GE and DW-Proton Latest did not show window issues in testing; Proton 11.0, Hotfix, and Experimental did. Name: `Vortex`. Comment: `Official Vortex on Linux`. Installed in the applications menu **and** on the desktop. Categories: `Game;`.
- **OVLS Manager** (`ovls-manager.desktop`): Installs or updates the OVLS Extension in the selected Vortex prefix and registers the NXM handler and desktop shortcuts in your user XDG directories; also handles verify, uninstall, and change prefix. Name: `OVLS Manager`. Comment: `OVLS Manager (Official Vortex Linux Support)`. Installed in the applications menu only. Categories: `Utility;`.
- **NXM handler** (`ovls-nxm.desktop`): Registers the `nxm://` protocol handler so that “Download with Vortex” buttons on Nexus Mods open in the official Vortex running through Proton. Name: `OVLS NXM Handler`. Installed as a hidden protocol-handler entry (`NoDisplay=true`), not shown as a regular application. Categories: `Game;`.

OVLS installs three shortcuts:
- **Vortex shortcut** — on the desktop and in the applications menu.
- **OVLS Manager shortcut** — in the applications menu only.
- **NXM handler** — a protocol-handler entry, not a regular menu application.

The OVLS Manager appears in the `Utility;` category of your application menu.

## How OVLS works

OVLS uses a **hybrid architecture**:

- **External Linux Support**: A Python-based component that handles SteamOS/Proton/prefix/filesystem/NXM tasks outside of Vortex.
- **Vortex Extension**: A small extension loaded inside the official Vortex that handles operations requiring Vortex context (state reading, deployment-failure notifications, and the `SET_MOD_INSTALL_PATH` staging write).

The two components have no IPC channel; the Extension acts only through the Vortex API.

The official Vortex application files are never modified. OVLS adds its own Extension (including generated `host.json`), desktop shortcuts, an NXM handler, and launch automation around it; uninstall removes the OVLS-owned Extension files.

## Supported environment

OVLS has been tested on:

- **SteamOS 3.x, Desktop Mode (KDE Plasma)** — supported
- **SteamOS Game Mode** — not tested
- **DW-Proton Latest** — normal in testing, recommended for use with OVLS
- **Proton-GE Latest** — normal in testing, recommended for use with OVLS (case-insensitive name match: starts with `GE-Proton` or contains `Proton-GE`)
- **Proton 11.0** — window display and cursor offset issues observed in testing
- **Proton Hotfix** — window display and cursor offset issues observed in testing
- **Proton Experimental** — window display and cursor offset issues observed in testing
- **Python 3** — required
- **kdialog or zenity** — required for graphical dialogs; CLI works without them

Both DW-Proton Latest and Proton-GE Latest are recommended for use with OVLS; both worked normally with Vortex in our tests. These are compatibility recommendations, not requirements. They are preferred only as a fallback when neither Steam nor the prefix records an installed tool; a tool selected by the user is never replaced.

OVLS does not require root access.

## Project status

Core user flows (launch, Extension install/update, NXM, mod workflow) are implemented and tested. The staging fix for games on an additional library is implemented, but an automatic staging move on a physical SD card has not been verified in a live Vortex session; see [Staging folder](docs/user/staging.md) and [Known limitations](docs/user/limitations.md).

## Installation

OVLS is distributed as a standalone installer folder (`OVLS Installer`). Download the built package, copy it to any location, and run `install.sh`. Detailed installation instructions are included in the package itself ([installer/README.md](installer/README.md)).

Do not run the installer from inside the source checkout unless you are developing OVLS. The standalone package is the supported distribution format.

## Downloading and running the installer from GitHub Releases

1. Open the project releases page: https://github.com/Dimonchik-UA/Vortex-Linux-Support/releases
2. Download both release files:
   - `OVLS-Installer.tar.gz`
   - `SHA256SUMS`
3. Before running the installer, verify the archive integrity using `SHA256SUMS` (it contains the expected SHA-256 checksum for `OVLS-Installer.tar.gz`). For example, from the directory where both files are located:
   ```bash
   sha256sum -c SHA256SUMS
   ```
4. Extract `OVLS-Installer.tar.gz`. The archive contains the `OVLS Installer` folder.
5. Open the extracted `OVLS Installer` folder.
6. To run the installer graphically, double-click `OVLS Installer.desktop` inside that folder. On SteamOS/KDE, if the shortcut is marked as untrusted, select **Trust and Launch** (as documented in `installer/README.md`). Keep `OVLS Installer.desktop`, `install.sh`, and the `ovls/` directory together in the same folder.
7. If graphical launch is unavailable, open a terminal in the extracted `OVLS Installer` folder and run:
   ```bash
   bash install.sh
   ```
   (This is the documented package entry point; without arguments it starts the graphical installer UI when available. See `installer/README.md` and `installer/install.sh` for details and CLI options such as `install`, `update`, `verify`, and `uninstall`.)

## Documentation

- **User documentation**: [Installation](docs/user/installation.md), [Usage](docs/user/usage.md), [NXM integration](docs/user/nxm.md), [Staging folder](docs/user/staging.md), [Supported environment](docs/user/environment.md), [Diagnostics](docs/user/diagnostics.md), [Known limitations](docs/user/limitations.md).
- **Developer documentation**: [Architecture](docs/developer/architecture.md), [Component design](docs/developer/component-design.md), [Error handling](docs/developer/error-handling.md), [Lifecycle](docs/developer/lifecycle.md).
- **GitHub sections**: [Releases](https://github.com/Dimonchik-UA/Vortex-Linux-Support/releases), [Issues](https://github.com/Dimonchik-UA/Vortex-Linux-Support/issues), [package README](installer/README.md).

## Known limitations

- OVLS does not guarantee compatibility with all games or all versions of Vortex.
- Mod deployment requires staging and the game to be on the same filesystem volume; under Proton matching Wine drive letters (`Z:`) are not sufficient.
- OVLS can generate staging targets for games in additional Steam libraries (`host.json` v2), but an automatic staging move on a physical SD card has not been verified on SteamOS. Details: [Staging folder](docs/user/staging.md), [Known limitations](docs/user/limitations.md).
- Game Mode has not been tested.
- OVLS does not modify the official Vortex executable or Vortex user settings; it installs its own Extension into Vortex's plugin directory and removes it on uninstall. OVLS cannot guarantee behavior with future versions of Vortex internals.

## Input limitations in SteamOS Desktop Mode (KDE Plasma)

In SteamOS Desktop Mode (KDE Plasma), the following input limitations have been observed:

| Category | Observation |
|----------|-------------|
| **Window rendering / mouse** | On Proton 11.0, Hotfix, Experimental: double window controls, white band, interface shift, cursor-click offset. Not observed on Proton-GE Latest / DW-Proton Latest. |
| **Keyboard focus & text input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Touch input** | Does not work correctly in any Vortex text input field. Not limited to search/profile rename. No workaround in OVLS. |
| **Controller** | R2/L2 work via SteamOS input handling. Other controller interactions not fully tested. |

OVLS launches Vortex using standard `proton run vortex.exe` and does not implement custom input handling. The root cause (suspected Wine/Proton–Electron interaction) has not been proven. No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.

## Contributing

See `CONTRIBUTING.md` for how to propose changes and report issues.

## License

OVLS source code is licensed under the MIT License. Copyright (c) 2026 Dimonchik-UA. The full license text is in the `LICENSE` file.

The MIT License covers the OVLS source code in this repository only. Vortex is a separate project by Nexus Mods and is distributed under its own terms — it is not part of this repository and is not licensed under the MIT License. Any third-party components, if present, remain under their own licenses.

Repository: https://github.com/Dimonchik-UA/Vortex-Linux-Support
