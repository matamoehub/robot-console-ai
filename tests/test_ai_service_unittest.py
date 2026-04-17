import unittest
import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest import mock

import app
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

    def test_slack_url_verification(self):
        body = {"type": "url_verification", "challenge": "abc123"}
        payload = json.dumps(body).encode("utf-8")
        timestamp = "1710000000"
        secret = "slack-test-secret"
        signature = "v0=" + hmac.new(
            secret.encode("utf-8"),
            f"v0:{timestamp}:".encode("utf-8") + payload,
            hashlib.sha256,
        ).hexdigest()
        with mock.patch.dict(os.environ, {"SLACK_SIGNING_SECRET": secret}, clear=False), \
             mock.patch("app.SLACK_SIGNING_SECRET", secret), \
             mock.patch("app.time.time", return_value=int(timestamp)):
            resp = self.client.post(
                "/api/brain/slack/events",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": signature,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["challenge"], "abc123")

    def test_slack_event_dispatches_background_worker(self):
        body = {
            "type": "event_callback",
            "event": {"type": "app_mention", "channel": "C123", "text": "<@Ubot> say hello", "user": "U123", "ts": "123.456"},
        }
        payload = json.dumps(body).encode("utf-8")
        timestamp = "1710000000"
        secret = "slack-test-secret"
        signature = "v0=" + hmac.new(
            secret.encode("utf-8"),
            f"v0:{timestamp}:".encode("utf-8") + payload,
            hashlib.sha256,
        ).hexdigest()
        started = []

        class DummyThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                started.append((self.target, self.args, self.daemon))

        with mock.patch("app.SLACK_SIGNING_SECRET", secret), \
             mock.patch("app.time.time", return_value=int(timestamp)), \
             mock.patch("app.threading.Thread", side_effect=DummyThread):
            resp = self.client.post(
                "/api/brain/slack/events",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Slack-Request-Timestamp": timestamp,
                    "X-Slack-Signature": signature,
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(started)
        target, args, daemon = started[0]
        self.assertEqual(target.__name__, "_process_slack_event")
        self.assertEqual(args[0]["channel"], "C123")
        self.assertTrue(daemon)

    def test_execute_robot_intent_say_enables_llm_remote_before_remote_control(self):
        parsed = {
            "target_scope": "single",
            "target_robot_id": "TestTonyPi",
            "intent": {"action": "say", "arguments": {"text": "hello"}},
        }
        robot = {"id": "TestTonyPi", "base_url": "http://robot", "token": "abc"}
        with mock.patch("app._robot_registry", return_value=[robot]), \
             mock.patch("app._ensure_robot_remote_mode", return_value={"ok": True, "response": {"ok": True}}) as ensure_mode, \
             mock.patch("app._robot_remote_text_command", return_value={"ok": True, "robot_id": "TestTonyPi", "response": {"ok": True}}) as remote_cmd, \
             mock.patch("app._audit_robot_action"):
            result = app._execute_robot_intent(parsed)

        self.assertTrue(result["ok"])
        ensure_mode.assert_called_once_with(robot)
        remote_cmd.assert_called_once_with(robot, "say hello", sender={"source": "robot-console-ai"})

    def test_execute_robot_intent_say_returns_mode_enable_failure(self):
        parsed = {
            "target_scope": "single",
            "target_robot_id": "TestTonyPi",
            "intent": {"action": "say", "arguments": {"text": "hello"}},
        }
        robot = {"id": "TestTonyPi", "base_url": "http://robot", "token": "abc"}
        with mock.patch("app._robot_registry", return_value=[robot]), \
             mock.patch("app._ensure_robot_remote_mode", return_value={"ok": False, "response": {"user_message": "Turn robot LLM remote on"}}), \
             mock.patch("app._robot_remote_text_command") as remote_cmd, \
             mock.patch("app._audit_robot_action"):
            result = app._execute_robot_intent(parsed)

        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["error"], "failed_to_enable_llm_remote")
        remote_cmd.assert_not_called()


if __name__ == '__main__':
    unittest.main()
