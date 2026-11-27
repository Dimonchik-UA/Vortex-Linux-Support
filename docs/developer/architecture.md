# OVLS Architecture

OVLS Linux Support (OVLS) is an independent external layer that enables the official Windows version of Vortex to run on SteamOS/Linux through Proton, without modifying Vortex itself.

## Architecture Overview

OVLS uses a **hybrid architecture** consisting of two logical components:

1. **External Linux Support** — a Python-based component that runs outside Vortex and handles SteamOS/Proton/prefix/filesystem/NXM tasks.
2. **Vortex Extension** — a small extension loaded inside the official Vortex application that handles operations requiring Vortex context (state reading, deployment-failure notifications, and the `SET_MOD_INSTALL_PATH` staging write).

The former JSON IPC channel was removed in OVLS 0.4.0: no user scenario depends on it. The components are independent; the Extension performs only internal Vortex operations.

OVLS does **not** modify official Vortex application files or Vortex user settings directly. It installs its own Extension (with generated `host.json`) into Vortex's plugin directory, can dispatch `SET_MOD_INSTALL_PATH` when you use the staging fix, and creates system shortcuts and NXM handlers around Vortex. Uninstall removes the OVLS-owned Extension files.

## Component Boundaries

### External Linux Support

Responsible for:

- Discovery of Steam, Proton, Vortex, and prefix
- Launching official `Vortex.exe` through Proton
- Checking availability, paths, rights, and filesystem limitations
- Separate installation, update, and removal of OVLS Extension
- Linux desktop integration, including registration and transmission of `nxm://`
- User configuration and system diagnostics

### Vortex Extension

Responsible for:

- Initialization
- Reading the Vortex state needed for staging detection
- Polling of Vortex state and notifications for the deployment-failure case (no event subscription)
- One permitted Vortex action: `SET_MOD_INSTALL_PATH` for the staging folder

The extension does **not** perform system-level operations.

## Design Principles

- **Non-invasive**: OVLS does not modify official Vortex executable files or Vortex user settings directly. It adds its own extension into Vortex's plugin directory and creates system shortcuts/handlers; OVLS-owned extension files are removed on uninstall.
- **Separation of concerns**: System-level tasks (Proton, prefix, filesystem) run outside Vortex; Vortex-context tasks run inside the extension.
- **Independence**: The external component and the Extension have no runtime communication channel. New Extension commands are not added without a concrete use case.
- **Safety**: The Extension reads only the Vortex state needed for staging detection. It does not expose confidential data and does not perform system operations.

## Relationship to Official Vortex

OVLS is not developed by, affiliated with, or endorsed by Nexus Mods or the official Vortex team. It is an independent project that adds Linux/SteamOS support around the official Windows version of Vortex. OVLS preserves the ability to receive official Vortex updates.
