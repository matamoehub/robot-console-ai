import unittest
from pathlib import Path
from unittest import mock

from app import APP


class AiServiceAppTest(unittest.TestCase):
    def setUp(self):
        self.client = APP.test_client()
        with self.client.session_transaction() as sess:
            sess["user"] = "admin"

    def test_version_endpoint(self):
        resp = self.client.get('/api/version')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['app'], 'robot-console-ai')

    def test_admin_redirects_without_login(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        resp = self.client.get('/admin')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers.get('Location', ''))

    def test_admin_stt_transcribe_requires_audio(self):
        resp = self.client.post('/api/admin/stt/transcribe', json={})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error"], "missing_audio_input")

    def test_admin_voice_command_preview(self):
        with mock.patch("app._materialize_audio_payload", return_value=(Path(__file__), None)), \
             mock.patch("app._stt_transcribe", return_value={"ok": True, "text": "say hello", "language": "en", "backend_mode": "mock", "elapsed_ms": 12.5}), \
             mock.patch("app._parse_robot_text_request", return_value={
                 "ok": True,
                 "target_scope": "single",
                 "target_robot_id": "TestTonyPi",
                 "intent": {"action": "say", "summary": 'Say "hello"', "arguments": {"text": "hello"}, "executable": True},
             }):
            resp = self.client.post('/api/admin/voice/command', json={"audio_base64": "ZmFrZQ==", "robot_id": "TestTonyPi"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["mode"], "test")
        self.assertEqual(data["transcript"]["text"], "say hello")
        self.assertEqual(data["parsed"]["intent"]["action"], "say")


if __name__ == '__main__':
    unittest.main()
