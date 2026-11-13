import json
import os
import time
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
    request_file = os.path.join(requests_dir, "request.json")
    response_file = os.path.join(responses_dir, "response.json")

    # Clean only the single response file for this minimal client.
    try:
        if os.path.exists(response_file):
            os.remove(response_file)
    except OSError:
        pass

    atomic_write_json(request_file, request)

    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(response_file):
            with open(response_file, "r", encoding="utf-8") as f:
                response = json.load(f)
            try:
                os.remove(response_file)
            except OSError:
                pass
            return response
        time.sleep(0.25)
    raise TimeoutError("Timed out waiting for response to " + str(request.get("id")))