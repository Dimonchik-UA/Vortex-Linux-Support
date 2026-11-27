# NXM Integration

OVLS registers an `nxm://` protocol handler so that clicking **"Download with Vortex"** buttons on Nexus Mods opens the link in the official Vortex running through Proton.

## How NXM works

When you click an `nxm://` link on Nexus Mods:

1. Your desktop environment opens the OVLS NXM handler (`ovls-nxm.desktop`).
2. The handler runs `ovls.py nxm --prefix <your-prefix> --uri "nxm://..."`.
3. OVLS launches or focuses the official Vortex and passes the download request to it.
4. Vortex downloads and installs the mod through its normal Windows flow.

## How OVLS registers NXM

OVLS installs three files:

| File | Location | Purpose |
|------|----------|---------|
| `ovls-nxm` | `~/.local/share/ovls/ovls-nxm` | Wrapper script that runs `ovls.py nxm --prefix ... --uri "$1"` |
| `ovls-nxm.desktop` | `~/.local/share/applications/ovls-nxm.desktop` | Desktop entry declaring the `nxm://` protocol handler (hidden from the applications menu; `NoDisplay=true`) |
| `nxm-previous-default` | `~/.local/share/ovls/nxm-previous-default` | Remembers your previous `nxm://` handler so OVLS can restore it on uninstall |

Registration uses `xdg-mime default` and does not create a `mimeapps.list` backup. A `mimeapps.list.ovls-backup-<UTC>` copy is made only by the uninstall fallback, when OVLS is the current default handler and restoring the previous handler did not succeed; if OVLS is not the current default, no backup is made. This copy is a recovery point: OVLS does not restore `mimeapps.list` from it automatically. If no previous `nxm://` handler existed, uninstall removes only OVLS's entry from your `mimeapps.list` and leaves everything else intact.

## Installing or updating the NXM handler

The NXM handler is installed automatically when you run **Install / Update** in OVLS Manager or `bash install.sh install`. It is also updated when you change your Proton prefix.

## Verifying NXM

Run:

```bash
bash install.sh verify
```

The verification output includes the NXM handler status. If it shows anything other than `ok`, re-run **Install / Update** from OVLS Manager.

## Uninstalling the NXM handler

Running **Uninstall** in OVLS Manager or `bash install.sh uninstall` removes:

- `~/.local/share/ovls/ovls-nxm` (the wrapper script)
- `~/.local/share/applications/ovls-nxm.desktop` (the desktop entry)
- `~/.local/share/ovls/nxm-previous-default` (the state file)

If OVLS was previously your default `nxm://` handler, uninstall tries to restore it, but only if its name is still saved in `nxm-previous-default`, the corresponding `.desktop` file exists, and switching back with `xdg-mime` succeeds. If any of those conditions fails, OVLS simply removes its own entry from `mimeapps.list` and does not guarantee that the previous handler is restored. If OVLS was not the default, it also just removes its entry. One `mimeapps.list.ovls-backup-*` copy is kept as a recovery point.

## Known limitations

- OVLS registers `nxm://` only for your desktop user. It does not modify system-wide handler configuration.
- If another application later registers itself as the default `nxm://` handler, OVLS will no longer receive links. Re-run **Install / Update** to re-register.
- OVLS does not modify the official Vortex application files; it installs its own Extension into Vortex's plugin directory and provides an external desktop handler that receives an `nxm://` link and launches Vortex with it. The OVLS Extension is not involved in passing NXM links, and there is no two-way IPC channel between the Python side and the Extension.
