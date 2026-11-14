"""Tests for src/linux-support/external/client.py (IPC client pipeline).

All tests use temporary directories. No real Vortex/Extension/Proton runtime.
Network is never touched. Filesystem errors are simulated via mocks.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / "src" / "linux-support"))

import external.client as client  # noqa: E402


def _sha256_obj(obj):
    obj_copy = {k: v for k, v in obj.items() if k != "checksum"}
    canonical = json.dumps(obj_copy, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IpcClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ovls-ipc-client-"))
        self.ipc_dir = self.tmp / "ipc"
        self.ipc_dir.mkdir(parents=True, exist_ok=True)
        # ensure dirs
        client.ensure_dirs(self.ipc_dir)
        # session dir
        self.session_dir = self.tmp / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("requests", "processing", "responses"):
            (self.session_dir / sub).mkdir(parents=True, exist_ok=True)
        # global dirs
        for sub in ("acks", "dedup", "processed", "quarantine", "client_state"):
            (self.ipc_dir / sub).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # submit_request
    # ------------------------------------------------------------------
    def test_submit_request_creates_file_with_checksum(self):
        rid = client.submit_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir)
        self.assertIsInstance(rid, str)
        req_file = self.session_dir / "requests" / (rid + ".json")
        self.assertTrue(req_file.exists())
        data = json.loads(req_file.read_text(encoding="utf-8"))
        self.assertEqual(data["protocolVersion"], "1.0")
        self.assertEqual(data["id"], rid)
        self.assertEqual(data["type"], "ping")
        self.assertTrue(client.verify_checksum(data, data["checksum"]))

    def test_submit_request_rejects_unsafe_id(self):
        with self.assertRaises(ValueError):
            client.submit_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir,
                                  request_id="../evil")

    def test_submit_request_explicit_safe_id(self):
        rid = client.submit_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir,
                                    request_id="safe-id-1")
        self.assertEqual(rid, "safe-id-1")
        self.assertTrue((self.session_dir / "requests" / "safe-id-1.json").exists())

    # ------------------------------------------------------------------
    # wait_response
    # ------------------------------------------------------------------
    def test_wait_response_returns_matching_response(self):
        rid = "wait-test-1"
        # Write a response file that matches rid
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        resp_file = self.session_dir / "responses" / (rid + ".json")
        resp_file.write_text(json.dumps(resp), encoding="utf-8")
        result = client.wait_response(rid, ipc_dir=self.ipc_dir, session_dir=self.session_dir, timeout=1.0)
        self.assertEqual(result["requestId"], rid)
        self.assertEqual(result["status"], "success")

    def test_wait_response_ignores_mismatched_request_id(self):
        rid = "wait-test-2"
        # Write response with wrong requestId
        resp = {"protocolVersion": "1.0", "requestId": "other-id", "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        resp_file = self.session_dir / "responses" / (rid + ".json")
        resp_file.write_text(json.dumps(resp), encoding="utf-8")
        # Should timeout (no matching response)
        result = client.wait_response(rid, ipc_dir=self.ipc_dir, session_dir=self.session_dir, timeout=0.3)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "TIMEOUT")

    def test_wait_response_timeout(self):
        result = client.wait_response("nonexistent", ipc_dir=self.ipc_dir, session_dir=self.session_dir, timeout=0.2)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "TIMEOUT")

    def test_wait_response_unsafe_id(self):
        result = client.wait_response("../evil", ipc_dir=self.ipc_dir, session_dir=self.session_dir, timeout=0.2)
        self.assertIn("error", result)

    # ------------------------------------------------------------------
    # validate_response
    # ------------------------------------------------------------------
    def test_validate_response_ok(self):
        resp = {"protocolVersion": "1.0", "requestId": "r1", "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        ok, reason = client.validate_response(resp, "r1")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_validate_response_wrong_protocol(self):
        resp = {"protocolVersion": "0.9", "requestId": "r1", "status": "success"}
        ok, reason = client.validate_response(resp, "r1")
        self.assertFalse(ok)
        self.assertEqual(reason, "protocol_version")

    def test_validate_response_request_id_mismatch(self):
        resp = {"protocolVersion": "1.0", "requestId": "other", "status": "success"}
        ok, reason = client.validate_response(resp, "r1")
        self.assertFalse(ok)
        self.assertEqual(reason, "request_id_mismatch")

    def test_validate_response_missing_request_id(self):
        resp = {"protocolVersion": "1.0", "status": "success"}
        ok, reason = client.validate_response(resp, "r1")
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_request_id")

    def test_validate_response_checksum_mismatch(self):
        resp = {"protocolVersion": "1.0", "requestId": "r1", "status": "success", "checksum": "sha256:deadbeef"}
        ok, reason = client.validate_response(resp, "r1")
        self.assertFalse(ok)
        self.assertEqual(reason, "checksum_mismatch")

    def test_validate_response_not_object(self):
        ok, reason = client.validate_response("string", "r1")
        self.assertFalse(ok)
        self.assertEqual(reason, "not_an_object")

    # ------------------------------------------------------------------
    # save_and_send_ack
    # ------------------------------------------------------------------
    def test_save_and_send_ack_success(self):
        rid = "ack-test-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        result = client.save_and_send_ack(rid, resp, ipc_dir=self.ipc_dir)
        self.assertTrue(result["ok"])
        self.assertTrue(result["responseValid"])
        self.assertTrue(result["localSaved"])
        self.assertTrue(result["ackSent"])
        # local response saved
        local_file = self.ipc_dir / "client_state" / (rid + ".response.json")
        self.assertTrue(local_file.exists())
        # ack file written
        ack_file = self.ipc_dir / "acks" / (rid + ".ack.json")
        self.assertTrue(ack_file.exists())

    def test_save_and_send_ack_invalid_response(self):
        rid = "ack-test-2"
        resp = {"protocolVersion": "0.9", "requestId": rid, "status": "success"}
        result = client.save_and_send_ack(rid, resp, ipc_dir=self.ipc_dir)
        self.assertFalse(result["ok"])
        self.assertFalse(result["responseValid"])
        self.assertEqual(result["error"], "INVALID_RESPONSE")

    def test_save_and_send_ack_local_save_failure(self):
        rid = "ack-test-3"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        # Make client_state unwritable by mocking atomic_write_json to raise
        with mock.patch.object(client, "atomic_write_json", side_effect=OSError("disk full")):
            result = client.save_and_send_ack(rid, resp, ipc_dir=self.ipc_dir)
        self.assertFalse(result["ok"])
        self.assertTrue(result["responseValid"])
        self.assertFalse(result["localSaved"])
        self.assertEqual(result["error"], "LOCAL_SAVE_FAILED")

    # ------------------------------------------------------------------
    # poll_acked
    # ------------------------------------------------------------------
    def test_poll_acked_returns_true_when_marker_exists(self):
        rid = "poll-acked-1"
        acked_file = self.ipc_dir / "acks" / (rid + ".acked")
        acked_file.write_text(json.dumps({"ackedAt": "now"}), encoding="utf-8")
        result = client.poll_acked(rid, ipc_dir=self.ipc_dir, timeout=0.5)
        self.assertTrue(result)

    def test_poll_acked_returns_false_on_timeout(self):
        result = client.poll_acked("nonexistent", ipc_dir=self.ipc_dir, timeout=0.2)
        self.assertFalse(result)

    def test_poll_acked_unsafe_id(self):
        result = client.poll_acked("../evil", ipc_dir=self.ipc_dir, timeout=0.2)
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # save_ack_confirmed
    # ------------------------------------------------------------------
    def test_save_ack_confirmed_creates_file(self):
        rid = "confirm-1"
        ok = client.save_ack_confirmed(rid, ipc_dir=self.ipc_dir)
        self.assertTrue(ok)
        confirmed_file = self.ipc_dir / "client_state" / (rid + ".ack_confirmed.json")
        self.assertTrue(confirmed_file.exists())
        data = json.loads(confirmed_file.read_text(encoding="utf-8"))
        self.assertEqual(data["requestId"], rid)
        self.assertEqual(data["protocolVersion"], "1.0")

    def test_save_ack_confirmed_unsafe_id(self):
        ok = client.save_ack_confirmed("../evil", ipc_dir=self.ipc_dir)
        self.assertFalse(ok)

    # ------------------------------------------------------------------
    # confirm_ack
    # ------------------------------------------------------------------
    def test_confirm_ack_requires_acked_marker(self):
        rid = "confirm-ack-1"
        result = client.confirm_ack(rid, ipc_dir=self.ipc_dir)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ACK_NOT_CONFIRMED")

    def test_confirm_ack_success(self):
        rid = "confirm-ack-2"
        # Create .acked marker first
        acked_file = self.ipc_dir / "acks" / (rid + ".acked")
        acked_file.write_text(json.dumps({"ackedAt": "now"}), encoding="utf-8")
        result = client.confirm_ack(rid, ipc_dir=self.ipc_dir)
        self.assertTrue(result["ok"])
        self.assertTrue(result["ackConfirmed"])
        # ack_confirmed file created
        confirmed_file = self.ipc_dir / "client_state" / (rid + ".ack_confirmed.json")
        self.assertTrue(confirmed_file.exists())

    # ------------------------------------------------------------------
    # ack_response (full pipeline, no Extension)
    # ------------------------------------------------------------------
    def test_ack_response_timeout_when_no_acked(self):
        rid = "ack-resp-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        # prep succeeds (ACK file written, response saved locally), then
        # poll_acked times out waiting for the Extension's ACK_CONFIRMED marker.
        result = client.ack_response(rid, resp, ipc_dir=self.ipc_dir, ack_timeout=0.2)
        self.assertTrue(result["responseValid"])
        self.assertTrue(result["localSaved"])
        self.assertTrue(result["ackSent"])
        self.assertFalse(result["acked"])
        self.assertEqual(result["error"], "ACK_TIMEOUT")
        # Regression guard: on ACK_TIMEOUT the result must not report success.
        self.assertIs(result["ok"], False,
                      "ack_response must return ok=False on ACK_TIMEOUT")

    def test_ack_response_success_when_acked_marker_exists(self):
        rid = "ack-resp-success-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        # Simulate the Extension having polled and written the ACK_CONFIRMED
        # marker (.acked) before the client's wait expires.
        acked_file = self.ipc_dir / "acks" / (rid + ".acked")
        acked_file.write_text(json.dumps({"ackedAt": "now"}), encoding="utf-8")
        result = client.ack_response(rid, resp, ipc_dir=self.ipc_dir, ack_timeout=0.5)
        self.assertTrue(result["ok"])
        self.assertTrue(result["acked"])
        self.assertTrue(result["ackConfirmed"])
        self.assertIsNone(result["error"])
        confirmed_file = self.ipc_dir / "client_state" / (rid + ".ack_confirmed.json")
        self.assertTrue(confirmed_file.exists())

    def test_ack_response_confirm_save_failed(self):
        rid = "ack-resp-confirm-fail-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        # prep succeeds; Extension marker .acked exists so poll_acked returns True;
        # but save_ack_confirmed fails (e.g. filesystem error in client_state).
        acked_file = self.ipc_dir / "acks" / (rid + ".acked")
        acked_file.write_text(json.dumps({"ackedAt": "now"}), encoding="utf-8")
        with mock.patch.object(client, "save_ack_confirmed", return_value=False):
            result = client.ack_response(rid, resp, ipc_dir=self.ipc_dir, ack_timeout=0.5)
        self.assertTrue(result["responseValid"])
        self.assertTrue(result["localSaved"])
        self.assertTrue(result["ackSent"])
        self.assertTrue(result["acked"])
        self.assertFalse(result["ackConfirmed"])
        self.assertEqual(result["error"], "CONFIRM_SAVE_FAILED")
        # Regression guard: on CONFIRM_SAVE_FAILED the result must not report success.
        self.assertIs(result["ok"], False,
                      "ack_response must return ok=False on CONFIRM_SAVE_FAILED")

    # ------------------------------------------------------------------
    # receive_and_ack
    # ------------------------------------------------------------------
    def test_receive_and_ack_with_provided_response(self):
        rid = "recv-ack-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        # Create .acked marker so poll_acked succeeds
        acked_file = self.ipc_dir / "acks" / (rid + ".acked")
        acked_file.write_text(json.dumps({"ackedAt": "now"}), encoding="utf-8")
        response, confirmed = client.receive_and_ack(rid, ipc_dir=self.ipc_dir, response=resp, ack_timeout=0.5)
        self.assertEqual(response["requestId"], rid)
        self.assertTrue(confirmed)

    # ------------------------------------------------------------------
    # request_and_ack
    # ------------------------------------------------------------------
    def test_request_and_ack_timeout_no_response(self):
        result = client.request_and_ack("ping", {}, ipc_dir=self.ipc_dir,
                                       session_dir=self.session_dir, timeout=0.3, ack_timeout=0.2)
        self.assertIn("response", result)
        self.assertFalse(result["ok"])

    # ------------------------------------------------------------------
    # send_request (low-level)
    # ------------------------------------------------------------------
    def test_send_request_timeout(self):
        result = client.send_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir, timeout=0.2)
        self.assertIn("error", result)
        self.assertEqual(result["error"]["code"], "TIMEOUT")

    # ------------------------------------------------------------------
    # save_response_local
    # ------------------------------------------------------------------
    def test_save_response_local_success(self):
        rid = "save-local-1"
        resp = {"status": "ok"}
        ok = client.save_response_local(resp, rid, ipc_dir=self.ipc_dir)
        self.assertTrue(ok)
        local_file = self.ipc_dir / "client_state" / (rid + ".response.json")
        self.assertTrue(local_file.exists())

    def test_save_response_local_unsafe_id(self):
        ok = client.save_response_local({"status": "ok"}, "../evil", ipc_dir=self.ipc_dir)
        self.assertFalse(ok)

    # ------------------------------------------------------------------
    # send_ack
    # ------------------------------------------------------------------
    def test_send_ack_creates_file(self):
        rid = "send-ack-1"
        resp = {"protocolVersion": "1.0", "requestId": rid, "status": "success"}
        resp["checksum"] = _sha256_obj(resp)
        ok = client.send_ack(rid, resp, ipc_dir=self.ipc_dir)
        self.assertTrue(ok)
        ack_file = self.ipc_dir / "acks" / (rid + ".ack.json")
        self.assertTrue(ack_file.exists())
        data = json.loads(ack_file.read_text(encoding="utf-8"))
        self.assertEqual(data["requestId"], rid)

    def test_send_ack_unsafe_id(self):
        ok = client.send_ack("../evil", {"status": "ok"}, ipc_dir=self.ipc_dir)
        self.assertFalse(ok)

    # ------------------------------------------------------------------
    # is_safe_request_id
    # ------------------------------------------------------------------
    def test_is_safe_request_id(self):
        self.assertTrue(client.is_safe_request_id("abc-123"))
        self.assertTrue(client.is_safe_request_id("req.test"))
        self.assertFalse(client.is_safe_request_id(""))
        self.assertFalse(client.is_safe_request_id("../evil"))
        self.assertFalse(client.is_safe_request_id("a/b"))
        self.assertFalse(client.is_safe_request_id("."))

    # ------------------------------------------------------------------
    # check_status
    # ------------------------------------------------------------------
    def test_check_status_returns_none_when_missing(self):
        result = client.check_status(ipc_dir=self.ipc_dir)
        self.assertIsNone(result)

    def test_check_status_returns_data(self):
        status_file = self.ipc_dir / "status.json"
        status_file.write_text(json.dumps({"loaded": True}), encoding="utf-8")
        result = client.check_status(ipc_dir=self.ipc_dir)
        self.assertEqual(result, {"loaded": True})

    # ------------------------------------------------------------------
    # atomic_write_json
    # ------------------------------------------------------------------
    def test_atomic_write_json(self):
        target = self.tmp / "atomic.json"
        client.atomic_write_json(target, {"key": "value"})
        self.assertTrue(target.exists())
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data, {"key": "value"})

    # ------------------------------------------------------------------
    # Repeated requests (no reprocessing from client perspective)
    # ------------------------------------------------------------------
    def test_repeated_submit_same_id(self):
        # Client can submit same request_id twice (idempotent at client level)
        rid = "repeat-1"
        r1 = client.submit_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir, request_id=rid)
        r2 = client.submit_request("ping", {}, ipc_dir=self.ipc_dir, session_dir=self.session_dir, request_id=rid)
        self.assertEqual(r1, r2)
        # Only one file should exist
        req_file = self.session_dir / "requests" / (rid + ".json")
        self.assertTrue(req_file.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)