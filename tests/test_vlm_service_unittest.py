import base64
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("VLM_BACKEND_CMD", "")

from app_vlm import APP


class VlmServiceAppTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.test_client()

    def test_health_endpoint(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "vlm-service")

    def test_caption_requires_backend(self):
        with mock.patch("app_vlm.BACKEND_CMD", ""):
            resp = self.client.post("/v1/caption", json={"prompt": "describe", "image_path": "/tmp/x.jpg"})
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data["error"], "backend_not_configured")

    def test_chat_completion_plain_text_backend(self):
        fake_proc = mock.Mock(returncode=0, stdout="A red robot on a desk.", stderr="")
        with mock.patch("app_vlm.BACKEND_CMD", "vlm-backend"), mock.patch("subprocess.run", return_value=fake_proc):
            resp = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "local-vlm",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "What is in this image?"},
                                {"type": "image_url", "image_url": {"url": "/tmp/example.jpg"}},
                            ],
                        }
                    ],
                },
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["choices"][0]["message"]["content"], "A red robot on a desk.")

    def test_caption_materializes_data_url(self):
        image_bytes = base64.b64encode(b"fake-image").decode("ascii")
        fake_proc = mock.Mock(returncode=0, stdout=json.dumps({"text": "ok"}), stderr="")
        captured = {}

        def fake_run(cmd, input, capture_output, text, timeout, check):
            captured["payload"] = json.loads(input)
            return fake_proc

        with mock.patch("app_vlm.BACKEND_CMD", "vlm-backend"), mock.patch("subprocess.run", side_effect=fake_run):
            resp = self.client.post(
                "/v1/caption",
                json={
                    "prompt": "Describe it",
                    "image_base64": image_bytes,
                    "image_mime_type": "image/png",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(captured["payload"]["image_path"].endswith(".png"))


if __name__ == "__main__":
    unittest.main()
