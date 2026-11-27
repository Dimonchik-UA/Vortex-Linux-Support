# Contributing to OVLS

Thank you for your interest in contributing to OVLS (Vortex Linux Support). This document describes how to contribute to the project.

## Development Environment

To contribute to OVLS, you will need:

- **Linux** (SteamOS 3.x Desktop Mode or compatible Linux distribution)
- **Python 3** — OVLS is written in Python 3; additional dependencies may apply, verify before contributing
- **Steam** and **official Vortex** installed in a Proton prefix
- **Proton-GE** (preferring "latest") for testing; **Proton 11.0** as fallback (Proton 11.0, Hotfix, Experimental showed window issues in testing)
- **kdialog** or **zenity** (for graphical dialogs; CLI works without either)
- **Git** — for version control of the project repository

## Project Structure

The project repository follows this structure:

```
OVLS/
├── README.md
├── CONTRIBUTING.md
├── src/
│   ├── linux-support/     (Python code: main OVLS components)
│   └── extension/         (JavaScript code: Vortex Extension)
├── installer/             (installer files: install.sh, desktop shortcut)
├── scripts/               (build and maintenance tools)
├── docs/                  (user and developer documentation)
└── tests/                 (test materials)
```

## Learning the Architecture

Before contributing, read the following documentation:

1. `docs/developer/architecture.md` — overall architecture and hybrid model
2. `docs/developer/component-design.md` — component responsibilities
3. `docs/developer/error-handling.md` — error handling and recovery
4. `docs/developer/lifecycle.md` — installation, operation, update, removal lifecycle

## Making Changes

1. Fork the repository (when public)
2. Create a feature branch from the main branch
3. Make your changes
4. Test your changes locally
5. Submit a pull request for review

## Testing

Before submitting changes:

- Run `bash install.sh verify` from the installer package to check installation integrity
- Test Vortex launch through OVLS
- Test Extension installation and removal
- Test NXM handler registration
- Verify that the official Vortex executable and settings are not modified by OVLS (the Extension is installed into Vortex's plugin directory)
- Test on multiple Proton versions (Proton-GE preferring "latest", Proton 11.0, Proton Hotfix, Proton Experimental) if possible

## Reporting Issues

When reporting bugs or requesting features, include:

- OVLS version (if applicable)
- SteamOS/Linux distribution and version
- Proton version
- Vortex version
- Steps to reproduce
- Expected vs. actual behavior
- Relevant log output (without secrets or personal data)

Do not include secrets, tokens, passwords, or personal absolute paths in issue reports.

## Documentation

When contributing code:

- Update corresponding documentation in `docs/developer/` if architecture or components change
- Update user documentation in `docs/user/` if user-facing behavior changes
- Keep documentation and code in sync
- Do not include personal absolute paths or internal project paths

## Safe Work with Vortex and Proton Prefix

- **Never modify official Vortex files** — OVLS only adds an extension to Vortex's plugin directory
- **Never modify game files or mods** — OVLS does not touch game content
- **Always close Vortex before installing or updating OVLS** — the installer will refuse to run while Vortex is active
- **Back up your prefix** before making changes — while OVLS is designed to be safe, prefix corruption can occur
- **Test on a secondary prefix first** — if possible, test changes on a test prefix before your main gaming prefix

## License

OVLS source code is licensed under the MIT License. Copyright (c) 2026 Dimonchik-UA. The full license text is in the `LICENSE` file.

The MIT License covers the OVLS source code in this repository only. Vortex is a separate project by Nexus Mods and is distributed under its own terms — it is not part of this repository and is not licensed under the MIT License. Any third-party components, if present, remain under their own licenses.

Repository: https://github.com/Dimonchik-UA/Vortex-Linux-Support

## Code Style

- Python: follow PEP 8 where practical
- JavaScript: follow standard JS conventions
- Shell scripts: use `#!/bin/bash` or `#!/usr/bin/env bash`
- Document all public functions and CLI commands
- Keep error messages user-friendly and actionable
