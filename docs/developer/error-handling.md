# Error Handling

OVLS defines error classifications for its external Linux Support component and Vortex Extension. Recovery procedures are limited; several recovery mechanisms are recommended but not yet implemented.

## Error Classification

| Code | Meaning |
|------|---------|
| `SYSTEM_ERROR` | System-level error (file I/O, permissions, missing dependencies) |
| `VORTEX_UNAVAILABLE` | Vortex application is not running or not reachable |
| `IPC_ERROR` | IPC message exchange failure |
| `INVALID_STATE` | Nonsensical or inconsistent state |
| `UNSUPPORTED_OPERATION` | Requested operation is not supported |

The following IPC-specific error codes are returned by the Extension:

| Code | Meaning |
|------|---------|
| `INVALID_JSON` | Request file contains invalid JSON |
| `MISSING_REQUEST_ID` | Request missing request ID |
| `MISSING_REQUEST_TYPE` | Request missing type field |
| `UNSUPPORTED_PROTOCOL_VERSION` | Protocol version not supported |
| `UNKNOWN_REQUEST_TYPE` | Request type not recognized |
| `UNSAFE_STATE_PATH` | Requested state path not in allowlist |
| `STATE_PATH_NOT_ALLOWED` | State path explicitly denied |
| `IPC_STORAGE_LIMIT` | Storage limit exceeded |
| `RESPONSE_MISSING` | Expected response not found |
| `UNKNOWN_OUTCOME` | Outcome not recognized |

## Retry Policy

Retry behavior is limited. The following behavior is implemented:

- Invalid requests (malformed input, unknown request type) are not retried.
- When a component is unavailable, IPC continues with limited functionality.

No retry count, interval, or automatic retry procedure is specified or implemented beyond what the individual components implement.

## Recovery

### IPC Recovery After Failure

IPC communication is re-established after failure where possible. Component state recovery is recommended but not fully implemented.

### Dedup Recovery

On Extension restart, unfinished files in `processing/` are **not** automatically scanned at init. Recovery of unfinished processing files is recommended but not yet implemented.

## Directory fsync Limitations

Under tested Proton 11.0, directory `fsync()` is unavailable (`EPERM`). File `fsync()` works. OVLS implements controlled degradation:

- `EPERM`/`ENOTSUP`/`EINVAL` → mandatory warning + limited guarantee; IPC continues
- `EIO`/`EISDIR`/unknown errors → propagated
- File fsync errors are not suppressed

Full durability against power loss is **not** claimed. Directory fsync unavailability has been observed under tested Proton 11.0; behavior under other Proton versions or configurations is not confirmed.

## Logging

OVLS logs to:
- `~/.local/share/ovls/ovls.log` — package manager operations
- `$XDG_STATE_HOME/ovls/launch.log` — launch attempts and Proton startup
- `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log` — Vortex application log (OVLS extension lines start with `OVLS9`)

Logs are intended not to contain secrets or sensitive data, but this has not been exhaustively verified across all code paths.

## Safety Rules

- Do not modify official Vortex
- Do not perform system operations from the Extension
- Use allowlist for Vortex data access
- Do not disclose secrets
- Log without sensitive data
