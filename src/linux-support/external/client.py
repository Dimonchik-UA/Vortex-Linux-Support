#!/usr/bin/env python3
"""Stage 9 JSON IPC client — Step 1: per-request files and directories.

This is the Stage 9 client. It uses per-request files in separate directories.
Stage 8 prototype is preserved unchanged as a regression base.
"""

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path

MAX_SAFE_INTEGER = 9007199254740991
MIN_SAFE_INTEGER = -9007199254740991


def is_ascii_key(key):
    """Check if key contains only ASCII characters."""
    return all(ord(c) < 128 for c in key)


def validate_value(value):
    """Validate that value is allowed in canonical JSON."""
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        if value < MIN_SAFE_INTEGER or value > MAX_SAFE_INTEGER:
            raise ValueError(f"Integer out of safe range: {value}")
        return True
    if isinstance(value, str):
        return True
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_value(item)
        return True
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"Key must be string, got {type(k)}")
            if not is_ascii_key(k):
                raise ValueError(f"Non-ASCII key not allowed: {k}")
            validate_value(v)
        return True
    if value is None:
        return True
    raise ValueError(f"Unsupported type: {type(value)}")


def canonical_json(obj):
    """Produce canonical JSON string.

    Rules:
    - UTF-8
    - Compact (no spaces)
    - ASCII-only keys, sorted alphabetically
    - Unicode allowed in values
    - Only safe integers (no float, NaN, Infinity)
    - checksum field excluded by caller
    """
    validate_value(obj)
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def compute_checksum(obj):
    """Compute SHA-256 checksum of canonical JSON (checksum field excluded)."""
    obj_copy = {k: v for k, v in obj.items() if k != "checksum"}
    canonical = canonical_json(obj_copy)
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "sha256:" + h


def verify_checksum(obj, expected_checksum):
    """Verify checksum of an object (checksum field excluded)."""
    # Remove checksum field before computing
    obj_copy = {k: v for k, v in obj.items() if k != "checksum"}
    computed = compute_checksum(obj_copy)
    return computed == expected_checksum

PROTOCOL_VERSION = "1.0"
DEFAULT_IPC_DIR = None  # Set by environment or default
DEFAULT_RESPONSE_TIMEOUT = 10.0
DEFAULT_ACK_TIMEOUT = 10.0
POLL_INTERVAL = 0.1


def get_ipc_dir():
    """Return the Stage 9 IPC directory path."""
    if DEFAULT_IPC_DIR:
        return Path(DEFAULT_IPC_DIR)
    # Default: userData/OVLS_IPC_V2
    # In production this comes from Vortex; for testing use env var
    env = os.environ.get("OVLS_IPC_DIR")
    if env:
        return Path(env)
    return Path.home() / ".ovls_ipc_v2"


def is_safe_request_id(value):
    """Return True when value can be used as a single safe path component."""
    if not isinstance(value, str) or value == "":
        return False
    if len(value) > 200:
        return False
    if value in (".", ".."):
        return False
    if "/" in value or "\\" in value or "\x00" in value:
        return False
    return re.fullmatch(r"[A-Za-z0-9._-]+", value) is not None


def ensure_dirs(ipc_dir):
    """Create all Stage 9 IPC directories."""
    for subdir in ["requests", "processing", "responses", "acks", "processed", "quarantine", "client_state"]:
        (ipc_dir / subdir).mkdir(parents=True, exist_ok=True)


def _resolve_dirs(ipc_dir, session_dir=None):
    """Resolve requests/processing/responses directories.

    With session_dir=None the legacy flat layout under ipc_dir is used
    (kept for backward compatibility with existing tests). With an explicit
    session_dir the session-scoped layout is used; global directories
    (acks/, client_state/, ...) always live under ipc_dir.

    The explicit session_dir is a dependency-injection point for the
    integration harness. It does not define the production sessionId contract.
    """
    ipc_dir = Path(ipc_dir)
    if session_dir is None:
        ensure_dirs(ipc_dir)
        return ipc_dir / "requests", ipc_dir / "processing", ipc_dir / "responses"

    session_dir = Path(session_dir)
    for subdir in ["requests", "processing", "responses"]:
        (session_dir / subdir).mkdir(parents=True, exist_ok=True)
    for subdir in ["acks", "dedup", "processed", "quarantine", "client_state"]:
        (ipc_dir / subdir).mkdir(parents=True, exist_ok=True)
    return session_dir / "requests", session_dir / "processing", session_dir / "responses"


def atomic_write_json(file_path, data):
    """Atomically write JSON to file (tmp + fsync + rename).

    Uses a unique temporary file name to avoid conflicts.
    Cleans up the temporary file on any error.
    Does not damage the existing target file on failure.
    """
    tmp = str(file_path) + "." + str(os.getpid()) + "." + str(int(time.time() * 1000)) + "." + secrets.token_hex(8) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(file_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate_response(response, request_id):
    """Validate a response before it may be saved or ACKed.

    Returns (ok, reason). Checks protocolVersion, requestId and, when present,
    the checksum. A response is never ACKed when validation fails.
    """
    if not isinstance(response, dict):
        return False, "not_an_object"
    if response.get("protocolVersion") != PROTOCOL_VERSION:
        return False, "protocol_version"
    resp_request_id = response.get("requestId")
    if not isinstance(resp_request_id, str) or resp_request_id == "":
        return False, "missing_request_id"
    if resp_request_id != str(request_id):
        return False, "request_id_mismatch"
    if "checksum" in response:
        try:
            if not verify_checksum(response, response["checksum"]):
                return False, "checksum_mismatch"
        except (TypeError, ValueError, KeyError):
            return False, "checksum_error"
    return True, "ok"


def submit_request(request_type, payload=None, ipc_dir=None, session_dir=None, request_id=None):
    """Write a request file and return its requestId (no waiting).

    request_id is normally generated here. Callers may supply one explicitly
    (the integration harness does) but it must be a safe path component.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    if request_id is None:
        request_id = str(uuid.uuid4())
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        raise ValueError(f"Unsafe request id: {request_id!r}")

    requests_dir, _, _ = _resolve_dirs(ipc_dir, session_dir)
    request = {
        "protocolVersion": PROTOCOL_VERSION,
        "id": request_id,
        "type": request_type,
        "payload": payload or {},
    }
    request["checksum"] = compute_checksum({k: v for k, v in request.items() if k != "checksum"})
    atomic_write_json(requests_dir / (request_id + ".json"), request)
    return request_id


def wait_response(request_id, ipc_dir=None, timeout=DEFAULT_RESPONSE_TIMEOUT, session_dir=None):
    """Wait until a response for request_id exists and matches it."""
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return {"status": "error", "error": {"code": "INVALID_REQUEST_ID", "message": "Unsafe request id"}}

    _, _, responses_dir = _resolve_dirs(ipc_dir, session_dir)
    response_file = responses_dir / (request_id + ".json")
    start = time.time()
    while time.time() - start < timeout:
        if response_file.exists():
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response = json.load(f)
                if isinstance(response, dict) and response.get("requestId") == request_id:
                    return response
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(POLL_INTERVAL)

    return {"status": "error", "error": {"code": "TIMEOUT", "message": "No response received"}}


def send_request(request_type, payload=None, ipc_dir=None, timeout=DEFAULT_RESPONSE_TIMEOUT, session_dir=None):
    """Send a request and wait for its response.

    This is the low-level half of the pipeline. Use request_and_ack() or
    receive_and_ack() to run the complete ACK protocol.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = submit_request(request_type, payload, ipc_dir, session_dir)
    return wait_response(request_id, ipc_dir, timeout, session_dir)


def save_response_local(response, request_id, ipc_dir=None):
    """Save a local copy of the response to client_state/<requestId>.response.json.

    Returns True on success, False on failure. Does not send ACK.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return False
    client_state_dir = Path(ipc_dir) / "client_state"
    client_state_dir.mkdir(parents=True, exist_ok=True)
    local_file = client_state_dir / (request_id + ".response.json")
    try:
        atomic_write_json(local_file, response)
        return True
    except (OSError, IOError, ValueError):
        return False


def poll_acked(request_id, ipc_dir=None, timeout=DEFAULT_ACK_TIMEOUT):
    """P0.4 step 4: poll acks/<requestId>.acked until present.

    Returns True if .acked marker exists, False on timeout. The timeout is
    always the caller-provided value; there is no hidden constant.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return False
    acked_file = Path(ipc_dir) / "acks" / (request_id + ".acked")
    start = time.time()
    while time.time() - start < timeout:
        if acked_file.exists():
            return True
        time.sleep(POLL_INTERVAL)
    return False


def save_ack_confirmed(request_id, ipc_dir=None):
    """P0.4 step 4: create client_state/<requestId>.ack_confirmed.json.

    The client writes "confirmedAt"; the Extension uses it as
    ackConfirmedAt (Specification v2 §6.1 step 17).

    Returns True on success, False on failure.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return False
    client_state_dir = Path(ipc_dir) / "client_state"
    client_state_dir.mkdir(parents=True, exist_ok=True)
    confirmed_file = client_state_dir / (request_id + ".ack_confirmed.json")
    confirmed = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "confirmedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        atomic_write_json(confirmed_file, confirmed)
        return True
    except (OSError, IOError, ValueError):
        return False


def send_ack(request_id, response, ipc_dir=None):
    """Send ACK after successful local save of response.

    ACK format:
    {
        "protocolVersion": "1.0",
        "requestId": <request_id>,
        "receivedAt": <ISO timestamp>,
        "checksum": "sha256:..."
    }

    Returns True on success, False on failure.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return False
    ensure_dirs(Path(ipc_dir))

    received_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ack = {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "receivedAt": received_at,
    }
    ack["checksum"] = compute_checksum({k: v for k, v in ack.items() if k != "checksum"})

    ack_file = Path(ipc_dir) / "acks" / (request_id + ".ack.json")
    try:
        atomic_write_json(ack_file, ack)
        return True
    except (OSError, IOError, ValueError):
        return False


def save_and_send_ack(request_id, response, ipc_dir=None):
    """Validate response, save it locally, then send ACK. No waiting.

    Order is mandatory: validation -> local save -> ACK. If any step fails,
    the ACK is not sent. Returns a result dict.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    result = {"ok": False, "error": None, "responseValid": False, "localSaved": False, "ackSent": False}
    valid, reason = validate_response(response, request_id)
    if not valid:
        result["error"] = "INVALID_RESPONSE"
        result["reason"] = reason
        return result
    result["responseValid"] = True
    if not save_response_local(response, request_id, ipc_dir):
        result["error"] = "LOCAL_SAVE_FAILED"
        return result
    result["localSaved"] = True
    if not send_ack(request_id, response, ipc_dir):
        result["error"] = "ACK_SEND_FAILED"
        return result
    result["ackSent"] = True
    result["ok"] = True
    return result


def confirm_ack(request_id, ipc_dir=None):
    """Create ACK_CONFIRMED only after acks/<id>.acked exists.

    Returns a result dict. This is the stepwise variant used by the
    integration harness; the Extension must process the ACK between
    send_ack() and confirm_ack().
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = str(request_id)
    if not is_safe_request_id(request_id):
        return {"ok": False, "error": "INVALID_REQUEST_ID", "ackConfirmed": False}
    acked_file = Path(ipc_dir) / "acks" / (request_id + ".acked")
    if not acked_file.exists():
        return {"ok": False, "error": "ACK_NOT_CONFIRMED", "ackConfirmed": False}
    confirmed = save_ack_confirmed(request_id, ipc_dir)
    return {
        "ok": confirmed,
        "error": None if confirmed else "CONFIRM_SAVE_FAILED",
        "ackConfirmed": confirmed,
    }


def ack_response(request_id, response, ipc_dir=None, ack_timeout=DEFAULT_ACK_TIMEOUT):
    """Run validation -> local save -> ACK -> wait .acked -> ACK_CONFIRMED.

    The Extension must poll concurrently for this blocking variant to finish.
    Returns a result dict with the exact pipeline outcome.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    result = {
        "requestId": str(request_id),
        "ok": False,
        "error": None,
        "responseValid": False,
        "localSaved": False,
        "ackSent": False,
        "acked": False,
        "ackConfirmed": False,
    }
    prep = save_and_send_ack(request_id, response, ipc_dir)
    result.update(prep)
    if not prep.get("ok"):
        return result

    if not poll_acked(request_id, ipc_dir, timeout=ack_timeout):
        result["error"] = "ACK_TIMEOUT"
        return result
    result["acked"] = True

    if not save_ack_confirmed(request_id, ipc_dir):
        result["error"] = "CONFIRM_SAVE_FAILED"
        return result
    result["ackConfirmed"] = True
    result["ok"] = True
    return result


def receive_and_ack(request_id, ipc_dir=None, timeout=DEFAULT_RESPONSE_TIMEOUT,
                    response=None, ack_timeout=DEFAULT_ACK_TIMEOUT, session_dir=None):
    """Receive response, validate, save locally, then run the ACK protocol.

    If `response` is supplied, it is used as-is: the response is NOT awaited
    a second time. This removes the old send_request/receive_and_ack gap.

    Returns (response, ack_confirmed) for backward compatibility.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    if response is None:
        response = wait_response(request_id, ipc_dir, timeout, session_dir)

    result = ack_response(request_id, response, ipc_dir, ack_timeout=ack_timeout)
    return (response, bool(result.get("ackConfirmed")))


def request_and_ack(request_type, payload=None, ipc_dir=None,
                    timeout=DEFAULT_RESPONSE_TIMEOUT, ack_timeout=DEFAULT_ACK_TIMEOUT,
                    session_dir=None, request_id=None):
    """Complete client pipeline: request -> response -> save -> ACK -> .acked -> ACK_CONFIRMED.

    Returns a result dict that includes the response and the pipeline status.
    """
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    request_id = submit_request(request_type, payload, ipc_dir, session_dir, request_id=request_id)
    response = wait_response(request_id, ipc_dir, timeout, session_dir)
    result = ack_response(request_id, response, ipc_dir, ack_timeout=ack_timeout)
    result["response"] = response
    return result


def check_status(ipc_dir=None):
    """Check extension status."""
    if ipc_dir is None:
        ipc_dir = get_ipc_dir()
    status_file = ipc_dir / "status.json"
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: client.py <ping|getState> [path]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "ping":
        request_type = "ping"
        payload = {}
    elif cmd == "getState":
        request_type = "getState"
        payload = {"path": sys.argv[2].split(".") if len(sys.argv) > 2 else ["settings"]}
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    result = request_and_ack(request_type, payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)