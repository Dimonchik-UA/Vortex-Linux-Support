# Staging Folder

Some games require mods to be deployed from a **staging folder** located on the same Wine disk as the game itself. If staging is on a different disk, Vortex may report **"Mods can't be deployed."**

OVLS helps by offering to move your staging folder to the correct location.

## What is staging?

When you download a mod in Vortex, it is first unpacked into a staging folder. Vortex then deploys the mod files from staging into the game's folder. For some games (such as Cyberpunk 2077), mods can only be deployed if the staging folder is on the same Wine disk (drive letter) as the game installation.

## How OVLS helps

If Vortex reports that mods cannot be deployed, OVLS offers a **"Move staging folder"** button in its notification. Clicking it:

1. Creates a new staging folder on the same Wine disk as your game.
2. Updates Vortex's staging setting to point to the new location.

OVLS does not move your existing staging folder automatically — it offers the fix only when staging is empty or when you explicitly request it.

## Staging folder location

Under Proton/Wine, drives are mapped as follows:

- `Z:` typically maps to your Linux home directory
- `C:` is typically the Windows system drive inside the prefix
- Other letters may map to other drives depending on your prefix configuration

For Cyberpunk 2077 and similar games, both the game and the staging folder should be on the same drive letter (typically `Z:` if the game is installed in your Linux home directory).

## Limitations

- OVLS does not automatically move an existing non-empty staging folder. The move is offered only when staging is empty or when you explicitly trigger it.
- Staging on SD cards and other non-home roots is not supported automatically.
- SteamOS Game Mode has not been tested. R2/L2 triggers work via SteamOS input handling; other controller interactions with Vortex UI elements have not been fully tested.

## Troubleshooting staging

If Vortex reports **"Mods can't be deployed."**:

1. Check where your game is installed (which Wine drive letter).
2. Open Vortex and look for the OVLS notification offering to move the staging folder.
3. Click **Move staging folder**.
4. Restart Vortex if needed and try deploying mods again.

If the problem persists, ensure your game and staging folder are on the same Wine disk, and that the staging folder is writable.
