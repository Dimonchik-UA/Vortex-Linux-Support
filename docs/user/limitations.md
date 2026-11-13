# Known Limitations

OVLS is a work in progress. Before using it, please review the following limitations.

## No guarantee of compatibility with all games

OVLS does not guarantee that every game will work through Vortex on Linux. Some games have anti-cheat systems, launcher requirements, or other dependencies that may not function correctly under Proton. OVLS itself does not modify game files or game behavior — it only provides the Linux support layer around Vortex.

## No guarantee of compatibility with all Vortex versions

OVLS is tested against specific versions of the official Vortex application. Future versions of Vortex may change their internal structure, extension API, or file layout in ways that break OVLS. OVLS does not guarantee continued functionality after Vortex updates.

## Game Mode not tested

SteamOS Game Mode has not been tested by OVLS. The results described in this document were obtained in Desktop Mode (KDE Plasma). R2/L2 triggers work via SteamOS input handling; other controller interactions with Vortex UI elements have not been fully tested.

## Staging folder restrictions

Mod deployment requires the staging folder to be on the same Wine disk (drive letter) as the game installation. For example, if your game is installed on `Z:`, your staging folder must also be on `Z:`. OVLS offers to move the staging folder when Vortex reports deployment problems, but it does not automatically move an existing non-empty staging folder.

Staging on SD cards and other non-home roots is not supported automatically.

## OVLS is not Vortex

OVLS is not developed by, affiliated with, or endorsed by Nexus Mods or the official Vortex team. It is an independent project that adds Linux/SteamOS support around the official Windows version of Vortex.

## No guarantee of future compatibility

OVLS does not guarantee that it will continue to work with future versions of Vortex, SteamOS, Proton, or Steam. Changes to any of these components may require corresponding changes to OVLS.

## Extension limitations

The Vortex Extension loaded inside Vortex has limited capabilities:

- It can read Vortex state (within an allowlist)
- It can subscribe to Vortex events
- It can write a limited set of safe parameters
- It cannot perform system-level operations

Some advanced mod management features may not be available through the extension alone.

## Proton version limitations

The following results were observed during testing in SteamOS Desktop Mode (KDE Plasma):

- **Proton 11.0, Proton Hotfix, Proton Experimental**: KDE Plasma displays its window control buttons; Vortex additionally displays its Windows-style window buttons (both present simultaneously, Vortex buttons mostly hidden under the white band). White band at top, interface shifted down, cursor-click coordinate mismatch. These issues could not be resolved in testing.
- **Proton-GE Latest, DW-Proton Latest**: No such issues were observed in the same tests.

The cause of these differences has not been determined. The results should not be assumed to apply to all systems or all configurations. Other Proton versions may work but have not been tested. Report issues if you encounter them.

## Input limitations on Steam Deck

The following input issues were observed when running Vortex through Proton on Steam Deck in Desktop Mode (KDE Plasma). They are separate from the window rendering issues described above and may have different causes.

### A. Window rendering and mouse coordinates

Observed on Proton 11.0, Proton Hotfix, and Proton Experimental:

- KDE Plasma displays its window control buttons; Vortex additionally displays its Windows-style window buttons (both present simultaneously, Vortex buttons mostly hidden under the white band).
- White band at the top of the window.
- Vortex interface visually shifted downward.
- Cursor position does not match click coordinates (click registers below visible cursor).

These issues were not observed on Proton-GE Latest and DW-Proton Latest in the same tests.

### B. Keyboard focus and text input

- Keyboard focus and text input do not work correctly in **all** text input fields of Vortex.
- The problem is not limited to search or profile renaming; it affects all text input fields.
- OVLS does not include a mechanism to fix keyboard focus or text input handling.

### C. Touch input

- Touch input does not work correctly in **all** text input fields of Vortex.
- The problem is not limited to search or profile renaming; it affects all text input fields.
- OVLS does not include a mechanism to fix touch input handling.

### D. Controller input

- R2/L2 triggers work via SteamOS input handling.
- Other controller interactions with Vortex UI elements have not been fully tested.

### Notes on input issues

- OVLS launches Vortex using standard `proton run vortex.exe`. It does not implement custom input handling, keyboard focus fixes, touch input handling, or Electron event processing.
- The interaction between Wine/Proton and Electron is a suspected direction of investigation, but the root cause has not been proven.
- No fix is promised via Proton updates, Steam launch parameters, or OVLS updates.
- These issues are independent of the window rendering problems (category A) and should not be assumed to share a single cause.

## No automatic game detection for all games

Vortex discovers games through game extensions. If a game does not have a corresponding Vortex game extension installed, Vortex may not detect it or may not show mods for it. OVLS does not add game detection for games that lack Vortex extensions.
