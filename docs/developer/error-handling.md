# Error Handling

OVLS documents error categories and the IPC error codes emitted by its Vortex Extension. Recovery procedures are limited; several recovery mechanisms are recommended but not yet implemented.

## Conceptual Error Categories (documentation only)

These categories describe error domains in prose. They are **not** emitted as `response.error.code` values by the Extension.

| Category | Meaning |
|----------|---------|
| `SYSTEM_ERROR` | System-level error (file I/O, permissions, missing dependencies) |
| `VORTEX_UNAVAILABLE` | Vortex application is not running or not reachable |
| `IPC_ERROR` | IPC message exchange failure |
| `INVALID_STATE` | Nonsensical or inconsistent state |
| `UNSUPPORTED_OPERATION` | Requested operation is not supported |

## Extension IPC Error Codes

The following codes are actually emitted by the Extension in `response.error.code` (`src/extension/index.js`):

| Code | Meaning |
|------|---------|
| `INVALID_JSON` | Request file contains invalid JSON |
| `INVALID_REQUEST_ID` | Request ID is not a safe single path component |
| `CHECKSUM_MISMATCH` | Request checksum verification failed |
| `CHECKSUM_ERROR` | Checksum verification raised an error |
| `INVALID_REQUEST` | Request is not a JSON object |
| `UNSUPPORTED_PROTOCOL_VERSION` | Protocol version not supported |
| `MISSING_REQUEST_ID` | Request missing request ID |
| `MISSING_REQUEST_TYPE` | Request missing type field |
| `UNSAFE_STATE_PATH` | Requested state path not in allowlist |
| `UNKNOWN_REQUEST_TYPE` | Request type not recognized |
| `EXTENSION_ERROR` | Error raised while handling a valid request |
| `DUPLICATE_REQUEST` | Request ID has already completed and its result is not cached |

## Retry Policy

Retry behavior is limited. The following behavior is implemented:

- Invalid requests (malformed input, unknown request type) are not retried.
- When a component is unavailable, IPC continues with limited functionality.

No retry count, interval, or automatic retry procedure is specified or implemented beyond what the individual components implement.

## Recovery

### IPC Recovery After Failure

IPC communication is re-established after failure where possible. Component state recovery is recommended but not fully implemented.

### Dedup Recovery

On Extension restart there is no one-shot scan at init. `pollOnce()` re-scans the current session's `processing/` on every 2 s tick and handles any file found. Because a new `sessionId` is generated for each initialization, files left in a previous session directory are not recovered.

## Directory fsync Limitations

Under tested Proton 11.0, directory `fsync()` is unavailable (`EPERM`). File `fsync()` works. OVLS implements controlled degradation:

- `EPERM`/`ENOTSUP`/`EINVAL` → mandatory warning + limited guarantee; IPC continues
- `EIO`/`EISDIR`/unknown errors → propagated
- File fsync errors are not suppressed

Full durability against power loss is **not** claimed. Directory fsync unavailability has been observed under tested Proton 11.0; behavior under other Proton versions or configurations is not confirmed.

## Logging

OVLS logs to:
- `~/.local/share/ovls/ovls/ovls.log` — package manager operations
- `$XDG_STATE_HOME/ovls/launch.log` — launch attempts and Proton startup
- `<prefix>/pfx/drive_c/users/<user>/AppData/Roaming/Vortex/vortex.log` — Vortex application log (OVLS extension lines start with `OVLS9`)

Logs are intended not to contain secrets or sensitive data, but this has not been exhaustively verified across all code paths.

## Safety Rules

- Do not modify official Vortex
- Do not perform system operations from the Extension
- Use allowlist for Vortex data access
- Do not disclose secrets
- Log without sensitive data
