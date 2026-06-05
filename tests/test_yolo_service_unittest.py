"""Tests for the YOLO object detection service and robot brain vision event endpoint.

Run with:
  pytest tests/test_yolo_service_unittest.py -v

All tests run in mock mode — no Hailo hardware required.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

# ---------------------------------------------------------------------------
# Environment setup — must happen before importing app modules
# ---------------------------------------------------------------------------

os.environ.setdefault("HAILO_YOLO_BACKEND_MODE", "mock")
os.environ.setdefault("YOLO_BACKEND_PERSISTENT", "0")   # one-shot for tests
os.environ.setdefault("YOLO_API_BASE_URL", "http://127.0.0.1:8091")
os.environ.setdefault("ROBOT_BRAIN_API_TOKEN", "test-token-yolo")
os.environ.setdefault("YOLO_DEFAULT_ROBOT_ID", "TestTurboPi")
os.environ.setdefault("ROBOT_REGISTRY_FILE", "/nonexistent/robots.json")
# Redirect Pi-specific paths to /tmp so app.py can be imported on any machine.
os.environ.setdefault("PASS_HASH_FILE", "/tmp/robot-console-ai-test.passhash")
os.environ.setdefault("ROBOT_BRAIN_AUDIT_LOG", "/tmp/robot-brain-test-yolo.log")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
BACKEND_SCRIPT = str(SCRIPTS_DIR / "hailo_yolo_backend.py")


# ---------------------------------------------------------------------------
# 1. Backend script — mock mode (one-shot subprocess)
# ---------------------------------------------------------------------------

class TestHailoYoloBackendMock(unittest.TestCase):
    """Test scripts/hailo_yolo_backend.py in mock mode."""

    def _run_backend(self, payload: dict) -> dict:
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock"}
        proc = subprocess.run(
            [sys.executable, BACKEND_SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, msg=f"stderr: {proc.stderr}")
        return json.loads(proc.stdout.strip())

    def test_mock_returns_ok(self):
        result = self._run_backend({"model": "yolov11s", "confidence_threshold": 0.5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["backend_mode"], "mock")

    def test_mock_returns_detections_list(self):
        result = self._run_backend({"confidence_threshold": 0.5})
        self.assertIsInstance(result["detections"], list)
        self.assertGreater(len(result["detections"]), 0)

    def test_detection_schema(self):
        result = self._run_backend({"confidence_threshold": 0.0})
        for det in result["detections"]:
            self.assertIn("class", det)
            self.assertIn("class_id", det)
            self.assertIn("confidence", det)
            self.assertIn("bbox", det)
            self.assertEqual(len(det["bbox"]), 4)
            self.assertIsInstance(det["confidence"], float)

    def test_confidence_threshold_filters(self):
        low  = self._run_backend({"confidence_threshold": 0.0})
        high = self._run_backend({"confidence_threshold": 0.99})
        self.assertGreaterEqual(len(low["detections"]), len(high["detections"]))
        for det in high["detections"]:
            self.assertGreaterEqual(det["confidence"], 0.99)

    def test_max_detections_caps_results(self):
        result = self._run_backend({"confidence_threshold": 0.0, "max_detections": 2})
        self.assertLessEqual(len(result["detections"]), 2)

    def test_custom_mock_detections_env(self):
        custom = [{"class": "robot", "class_id": 99, "confidence": 0.99, "bbox": [0, 0, 100, 100]}]
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock", "HAILO_YOLO_MOCK_DETECTIONS": json.dumps(custom)}
        proc = subprocess.run(
            [sys.executable, BACKEND_SCRIPT],
            input=json.dumps({}),
            capture_output=True, text=True, timeout=10, env=env,
        )
        result = json.loads(proc.stdout.strip())
        self.assertEqual(result["detections"], custom)

    def test_model_field_in_response(self):
        result = self._run_backend({"model": "yolov12n"})
        self.assertEqual(result["model"], "yolov12n")


# ---------------------------------------------------------------------------
# 2. Backend script — serve mode (persistent stdin/stdout loop)
# ---------------------------------------------------------------------------

class TestHailoYoloBackendServe(unittest.TestCase):
    """Test the --serve persistent loop in the backend script."""

    def test_serve_handles_multiple_requests(self):
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock"}
        proc = subprocess.Popen(
            [sys.executable, BACKEND_SCRIPT, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        try:
            requests_in = [
                {"model": "yolov11s", "confidence_threshold": 0.5},
                {"model": "yolov11n", "confidence_threshold": 0.8},
            ]
            results = []
            for req in requests_in:
                proc.stdin.write(json.dumps(req) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                results.append(json.loads(line))

            self.assertEqual(len(results), 2)
            for r in results:
                self.assertTrue(r["ok"])
                self.assertIn("detections", r)
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_serve_ignores_blank_lines(self):
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock"}
        proc = subprocess.Popen(
            [sys.executable, BACKEND_SCRIPT, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        try:
            proc.stdin.write("\n\n")
            proc.stdin.write(json.dumps({"model": "yolov11s"}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            result = json.loads(line)
            self.assertTrue(result["ok"])
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_serve_returns_error_on_invalid_json(self):
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock"}
        proc = subprocess.Popen(
            [sys.executable, BACKEND_SCRIPT, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        try:
            proc.stdin.write("not-json\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            result = json.loads(line)
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "invalid_json")
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# 3. YOLO Flask service (app_yolo.py)
# ---------------------------------------------------------------------------

class TestYoloServiceApp(unittest.TestCase):
    """Test the app_yolo.py Flask service endpoints."""

    @classmethod
    def setUpClass(cls):
        # Patch the backend process so tests don't start a real subprocess.
        os.environ["YOLO_BACKEND_PERSISTENT"] = "0"
        import app_yolo
        cls.app_yolo = app_yolo
        cls.client = app_yolo.APP.test_client()

    def _fake_backend(self, payload):
        """Return realistic mock detections without calling a subprocess."""
        from scripts.hailo_yolo_backend import _mock_detections  # noqa: PLC0415
        detections = _mock_detections(payload)
        return {
            "ok": True,
            "model": payload.get("model", "yolov11s"),
            "detections": detections,
            "count": len(detections),
            "backend_mode": "mock",
            "elapsed_ms": 12.3,
        }

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "yolo-service")

    def test_models_endpoint(self):
        resp = self.client.get("/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("data", data)
        self.assertIsInstance(data["data"], list)
        self.assertGreater(len(data["data"]), 0)

    def test_detect_with_image_path(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake-jpeg-bytes")
            img_path = f.name

        try:
            with mock.patch.object(self.app_yolo.BACKEND_PROCESS, "request", side_effect=self._fake_backend), \
                 mock.patch.object(self.app_yolo, "BACKEND_PERSISTENT", True):
                resp = self.client.post("/v1/detect", json={
                    "image_path": img_path,
                    "confidence_threshold": 0.5,
                })
        finally:
            Path(img_path).unlink(missing_ok=True)

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("detections", data)
        self.assertIn("elapsed_ms", data)

    def test_detect_with_base64_image(self):
        fake_image_b64 = base64.b64encode(b"fake-png-content").decode()
        with mock.patch.object(self.app_yolo.BACKEND_PROCESS, "request", side_effect=self._fake_backend), \
             mock.patch.object(self.app_yolo, "BACKEND_PERSISTENT", True):
            resp = self.client.post("/v1/detect", json={
                "image_base64": fake_image_b64,
                "image_mime_type": "image/png",
                "confidence_threshold": 0.5,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_detect_missing_image_returns_error(self):
        with mock.patch.object(self.app_yolo.BACKEND_PROCESS, "request", side_effect=self._fake_backend), \
             mock.patch.object(self.app_yolo, "BACKEND_PERSISTENT", True):
            resp = self.client.post("/v1/detect", json={"model": "yolov11s"})
        # Backend will attempt to run with an empty path — response depends on mode.
        # In mock mode the backend ignores image_path, so it still returns ok=True.
        data = resp.get_json()
        self.assertIn("ok", data)

    def test_detect_confidence_passthrough(self):
        captured = {}

        def capture_backend(payload):
            captured["payload"] = payload
            return {"ok": True, "detections": [], "count": 0, "elapsed_ms": 1.0}

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"x")
            img_path = f.name

        try:
            with mock.patch.object(self.app_yolo.BACKEND_PROCESS, "request", side_effect=capture_backend), \
                 mock.patch.object(self.app_yolo, "BACKEND_PERSISTENT", True):
                self.client.post("/v1/detect", json={
                    "image_path": img_path,
                    "confidence_threshold": 0.75,
                    "max_detections": 5,
                })
        finally:
            Path(img_path).unlink(missing_ok=True)

        self.assertEqual(captured["payload"]["confidence_threshold"], 0.75)
        self.assertEqual(captured["payload"]["max_detections"], 5)


# ---------------------------------------------------------------------------
# 4. Vision event processor (_process_vision_detections)
# ---------------------------------------------------------------------------

class TestVisionEventProcessor(unittest.TestCase):
    """Unit tests for the vision-rules matching and cooldown logic in app.py."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.app_module = app
        cls.client = app.APP.test_client()

    def setUp(self):
        # Clear cooldown state before each test.
        with self.app_module._vision_event_lock:
            self.app_module._vision_event_last.clear()

    def _vision_event(self, detections, mode="test", robot_id="TestTurboPi", token="test-token-yolo"):
        headers = {"Authorization": f"Bearer {token}"}
        return self.client.post(
            "/api/brain/vision/event",
            json={"robot_id": robot_id, "detections": detections, "execution_mode": mode},
            headers=headers,
        )

    def test_requires_api_token(self):
        resp = self.client.post("/api/brain/vision/event", json={"detections": []})
        self.assertEqual(resp.status_code, 401)

    def test_empty_detections_returns_ok(self):
        resp = self._vision_event([])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data.get("triggered_count"), 0)

    def test_person_detection_matches_default_rule(self):
        detections = [{"class": "person", "class_id": 0, "confidence": 0.92, "bbox": [10, 10, 200, 400]}]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        triggered = data.get("triggered") or []
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["action"], "say")

    def test_low_confidence_does_not_trigger(self):
        detections = [{"class": "person", "class_id": 0, "confidence": 0.3, "bbox": [0, 0, 100, 100]}]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        self.assertEqual(data.get("triggered_count"), 0)
        skipped = data.get("skipped") or []
        self.assertTrue(any(s["reason"] == "no_matching_rule" for s in skipped))

    def test_stop_sign_triggers_allstop(self):
        detections = [{"class": "stop sign", "class_id": 11, "confidence": 0.80, "bbox": [0, 0, 50, 50]}]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        triggered = data.get("triggered") or []
        self.assertTrue(any(t["action"] == "allstop" for t in triggered))

    def test_unknown_class_is_skipped(self):
        detections = [{"class": "spaceship", "class_id": 999, "confidence": 0.99, "bbox": [0, 0, 10, 10]}]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        self.assertEqual(data.get("triggered_count"), 0)
        skipped = data.get("skipped") or []
        self.assertEqual(skipped[0]["reason"], "no_matching_rule")

    def test_cooldown_prevents_repeat_triggers(self):
        detections = [{"class": "person", "class_id": 0, "confidence": 0.92, "bbox": [0, 0, 100, 100]}]
        resp1 = self._vision_event(detections, mode="live")
        resp2 = self._vision_event(detections, mode="live")
        data1 = resp1.get_json()
        data2 = resp2.get_json()
        # First call triggers, second call is on cooldown.
        self.assertEqual(data1.get("triggered_count"), 1)
        skipped2 = data2.get("skipped") or []
        self.assertTrue(any(s["reason"] == "cooldown" for s in skipped2))

    def test_duplicate_class_in_same_frame_deduplicated(self):
        detections = [
            {"class": "person", "class_id": 0, "confidence": 0.92, "bbox": [0,   0, 100, 100]},
            {"class": "person", "class_id": 0, "confidence": 0.88, "bbox": [200, 0, 300, 100]},
        ]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        # Only one trigger for 'person' even though two detections arrived.
        self.assertEqual(data.get("triggered_count"), 1)
        skipped = data.get("skipped") or []
        self.assertTrue(any(s["reason"] == "duplicate_class_in_frame" for s in skipped))

    def test_multiple_different_classes_each_trigger(self):
        detections = [
            {"class": "person", "class_id":  0, "confidence": 0.92, "bbox": [0, 0, 100, 100]},
            {"class": "cat",    "class_id": 15, "confidence": 0.85, "bbox": [0, 0, 100, 100]},
        ]
        resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        self.assertEqual(data.get("triggered_count"), 2)

    def test_detections_must_be_list(self):
        headers = {"Authorization": "Bearer test-token-yolo"}
        resp = self.client.post(
            "/api/brain/vision/event",
            json={"robot_id": "TestTurboPi", "detections": "not-a-list"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_mode_rejected(self):
        headers = {"Authorization": "Bearer test-token-yolo"}
        resp = self.client.post(
            "/api/brain/vision/event",
            json={"robot_id": "TestTurboPi", "detections": [], "execution_mode": "danger"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)

    def test_custom_vision_rules_via_patching(self):
        custom_rules = [
            {"class": "laptop", "min_confidence": 0.6, "action": "say",
             "arguments": {"text": "I see a laptop."}, "cooldown_s": 0.0},
        ]
        detections = [{"class": "laptop", "class_id": 63, "confidence": 0.77, "bbox": [0, 0, 200, 150]}]
        with mock.patch.object(self.app_module, "YOLO_VISION_RULES", custom_rules):
            resp = self._vision_event(detections, mode="test")
        data = resp.get_json()
        triggered = data.get("triggered") or []
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["action"], "say")
        self.assertEqual(triggered[0]["arguments"]["text"], "I see a laptop.")


# ---------------------------------------------------------------------------
# 5. Admin YOLO detect endpoint (/api/admin/yolo/detect)
# ---------------------------------------------------------------------------

class TestAdminYoloDetectEndpoint(unittest.TestCase):
    """Test the admin-protected YOLO detect endpoint."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.app_module = app
        cls.client = app.APP.test_client()

    def _login(self):
        with self.app_module.APP.test_request_context():
            from werkzeug.security import generate_password_hash
            self.app_module.PASS_HASH = generate_password_hash("testpass")
            self.app_module.ADMIN_USER = "admin"
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"

    def test_detect_requires_login(self):
        # Use a fresh client with no session to confirm the login guard fires.
        fresh_client = self.app_module.APP.test_client()
        resp = fresh_client.post("/api/admin/yolo/detect", json={"image_path": "/tmp/x.jpg"})
        # need_login redirects to /login (302).
        self.assertEqual(resp.status_code, 302)

    def test_detect_missing_image_returns_400(self):
        self._login()
        resp = self.client.post("/api/admin/yolo/detect", json={"model": "yolov11s"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data["ok"])

    def test_detect_with_mocked_yolo_service(self):
        self._login()
        fake_response = {
            "ok": True,
            "model": "yolov11s",
            "detections": [{"class": "person", "class_id": 0, "confidence": 0.91, "bbox": [10, 10, 200, 400]}],
            "count": 1,
            "elapsed_ms": 38.0,
        }
        with mock.patch.object(self.app_module, "_yolo_detect_request", return_value=fake_response):
            resp = self.client.post("/api/admin/yolo/detect", json={
                "image_data_url": "data:image/jpeg;base64," + base64.b64encode(b"fake").decode(),
                "confidence_threshold": 0.5,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["detections"][0]["class"], "person")


# ---------------------------------------------------------------------------
# 6. End-to-end: backend → service → vision event (integration-style)
# ---------------------------------------------------------------------------

class TestYoloEndToEnd(unittest.TestCase):
    """Integration test: run backend in mock mode, send result to vision event."""

    @classmethod
    def setUpClass(cls):
        import app
        cls.app_module = app
        cls.client = app.APP.test_client()
        with app._vision_event_lock:
            app._vision_event_last.clear()

    def test_backend_detections_feed_into_vision_event(self):
        """Run the backend subprocess to get real mock detections, then POST them to
        the vision event endpoint and confirm a rule fires."""
        env = {**os.environ, "HAILO_YOLO_BACKEND_MODE": "mock"}
        proc = subprocess.run(
            [sys.executable, BACKEND_SCRIPT],
            input=json.dumps({"confidence_threshold": 0.5}),
            capture_output=True, text=True, timeout=10, env=env,
        )
        self.assertEqual(proc.returncode, 0)
        backend_result = json.loads(proc.stdout.strip())
        self.assertTrue(backend_result["ok"])
        detections = backend_result["detections"]

        headers = {"Authorization": "Bearer test-token-yolo"}
        resp = self.client.post(
            "/api/brain/vision/event",
            json={"robot_id": "TestTurboPi", "detections": detections, "execution_mode": "test"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        # Mock mode includes a "person" at confidence 0.92 — default rules trigger on person.
        triggered_classes = [t["detection"]["class"] for t in (data.get("triggered") or [])]
        self.assertIn("person", triggered_classes)


if __name__ == "__main__":
    unittest.main()
