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

Registration uses `xdg-mime default`. If no previous `nxm://` handler existed, uninstall removes only OVLS's entry from your `mimeapps.list` and leaves everything else intact.

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

If OVLS was previously your default `nxm://` handler, uninstall restores your previous default handler. If OVLS was not the default, it simply removes its entry from `mimeapps.list`.

## Known limitations

- OVLS registers `nxm://` only for your desktop user. It does not modify system-wide handler configuration.
- If another application later registers itself as the default `nxm://` handler, OVLS will no longer receive links. Re-run **Install / Update** to re-register.
- OVLS does not modify the official Vortex application files; it installs its own Extension into Vortex's plugin directory and provides an external link handler that Vortex communicates with.
