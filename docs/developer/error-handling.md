# Error Handling

OVLS documents error categories for its external component and Vortex Extension. The Extension reports staging-fix problems through Vortex notifications and logs. There is no IPC error channel in OVLS 0.4.0: the former Stage 9 IPC error codes and ACK/dedup recovery were removed with the IPC channel.

## Error Categories

| Category | Meaning |
|----------|---------|
| `SYSTEM_ERROR` | System-level error (file I/O, permissions, missing dependencies) |
| `VORTEX_UNAVAILABLE` | Vortex application is not running or not reachable |
| `INVALID_STATE` | Nonsensical or inconsistent state |
| `UNSUPPORTED_OPERATION` | Requested operation is not supported |
| `STAGING_FIX_ERROR` | Staging-folder check or `SET_MOD_INSTALL_PATH` write failed |

These categories describe error domains in prose; they are not a wire protocol.

## Retry Policy

No retry count, interval, or automatic retry procedure is specified. The Extension performs its staging check on the existing poll interval; a failed staging write is reported through a Vortex notification and the log.

## Recovery

Component state recovery is not implemented. The Extension re-evaluates the current Vortex state on each staging check, so a transient failure does not require a manual reset.

## Logging

OVLS writes three log files of its own. Each is limited to a 1 MB current file and two rotated copies (`.1`, `.2`), so it never exceeds about 3 MB. On rollover the current file becomes `.1`, `.1` becomes `.2`, and the previous `.2` is deleted; older output is discarded automatically rather than kept as extra history:
- `~/.local/share/ovls/ovls/ovls.log` — package manager log (rotated; no records are written at present)
- `$XDG_STATE_HOME/ovls/launch.log` — Vortex launch and Proton output, written by the launcher
- `$XDG_DATA_HOME/ovls/nxm.log` — Vortex output for `nxm://` links

Proton output goes to a detached `logpump.py` process that rotates the file while Vortex is running. It exits when Vortex closes its output. There is no fallback to `/tmp`: if the log directory cannot be written, Vortex is started without a log. In that case `proton.spawn_vortex()` prints `NXM handler: log disabled (log directory is not writable); launching Vortex anyway` to stderr; the launcher does not show a separate dialog for this message.

Uninstall removes `ovls.log`, `launch.log`, `nxm.log` and their rotated copies. Other files in those directories are kept.

`<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log` belongs to Vortex; OVLS extension lines start with `OVLS9`.

Logs are intended not to contain secrets or sensitive data, but this has not been exhaustively verified across all code paths.

## Safety Rules

- Do not modify official Vortex
- Do not perform system operations from the Extension
- Read only the Vortex state needed for the staging fix
- Do not disclose secrets
- Log without sensitive data
