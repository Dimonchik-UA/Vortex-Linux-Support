import json
import os
import time
import uuid
from datetime import datetime


def atomic_write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def wait_for_status(ipc_dir, timeout=30.0):
    status_file = os.path.join(ipc_dir, "status.json")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(status_file):
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    status = json.load(f)
                if status.get("loaded") is True:
                    return status
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for Vortex extension status")


def send_request(ipc_dir, request, timeout=10.0):
    requests_dir = os.path.join(ipc_dir, "requests")
    responses_dir = os.path.join(ipc_dir, "responses")
    os.makedirs(requests_dir, exist_ok=True)
    os.makedirs(responses_dir, exist_ok=True)

    # Generate a unique request ID for correlation
    request_id = request.get("id") or str(uuid.uuid4())
    request["id"] = request_id

    request_file = os.path.join(requests_dir, f"request-{request_id}.json")
    response_file = os.path.join(responses_dir, f"response-{request_id}.json")

    # Use O_EXCL to atomically create the request file (prevents overwrites)
    request_fd = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(request_fd, "w", encoding="utf-8") as f:
            json.dump(request, f, ensure_ascii=False)
    except OSError:
        os.unlink(request_file)
        raise

    response_file = os.path.join(responses_dir, f"response-{request_id}.json")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(response_file):
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response = json.load(f)
            except (OSError, json.JSONDecodeError):
                time.sleep(0.25)
                continue
            # Verify response matches our request
            if response.get("id") == request_id:
                # Atomically remove response file to prevent reprocessing
                try:
                    os.unlink(response_file)
                except OSError:
                    pass
                return response
        time.sleep(0.25)
    raise TimeoutError("Timed out waiting for response to " + str(request_id))