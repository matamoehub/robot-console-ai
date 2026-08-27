import queue
import time
import unittest
from pathlib import Path
from unittest import mock

import app


class MaterializeAudioPayloadAllowlistTest(unittest.TestCase):
    """Bug 1: _materialize_audio_payload must do real path containment,
    not a string-prefix check (which is bypassable with sibling
    directories like /opt/robot-evil or /tmpfoo)."""

    def test_sibling_directory_is_rejected(self):
        # /opt/robot-evil starts with the string "/opt/robot" but is not
        # actually inside the allowed /opt/robot directory.
        evil_path = "/opt/robot-evil/passwd"
        result_path, error = app._materialize_audio_payload({"audio_path": evil_path})
        self.assertIsNone(result_path)
        self.assertEqual(error, "audio_path_not_allowed")

    def test_tmp_sibling_directory_is_rejected(self):
        evil_path = "/tmpfoo/x"
        result_path, error = app._materialize_audio_payload({"audio_path": evil_path})
        self.assertIsNone(result_path)
        self.assertEqual(error, "audio_path_not_allowed")

    def test_genuine_allowed_path_is_accepted(self):
        real_dir = Path("/opt/robot/tmp_test_audio_allowlist")
        real_file = real_dir / "bar.wav"
        try:
            real_dir.mkdir(parents=True, exist_ok=True)
            real_file.write_bytes(b"fake-wav-data")
            result_path, error = app._materialize_audio_payload(
                {"audio_path": str(real_file)}
            )
            self.assertIsNone(error)
            self.assertEqual(result_path, real_file.resolve())
        except PermissionError:
            self.skipTest("cannot create files under /opt/robot in this environment")
        finally:
            try:
                if real_file.exists():
                    real_file.unlink()
                if real_dir.exists():
                    real_dir.rmdir()
            except Exception:
                pass

    def test_genuine_tmp_path_is_accepted(self):
        import tempfile

        with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".wav", delete=False) as f:
            f.write(b"fake-wav-data")
            tmp_path = Path(f.name)
        try:
            result_path, error = app._materialize_audio_payload(
                {"audio_path": str(tmp_path)}
            )
            self.assertIsNone(error)
            self.assertEqual(result_path, tmp_path.resolve())
        finally:
            tmp_path.unlink(missing_ok=True)


class STTProcessTimeoutTest(unittest.TestCase):
    """Bug 2: _STTProcess.request must actually enforce its timeout param
    instead of blocking forever on a wedged backend process."""

    def test_timeout_raises_and_recovers(self):
        stt = app._STTProcess()

        class FakeStdout:
            def readline(self):
                # Simulate a wedged backend that never responds.
                time.sleep(5)
                return '{"ok": true}\n'

        class FakeStdin:
            def write(self, _data):
                pass

            def flush(self):
                pass

        class FakeStderr:
            def read(self, _n=None):
                return ""

        class FakeProc:
            def __init__(self):
                self.stdin = FakeStdin()
                self.stdout = FakeStdout()
                self.stderr = FakeStderr()
                self.killed = False

            def poll(self):
                return None

            def kill(self):
                self.killed = True

        fake_proc = FakeProc()
        stt._proc = fake_proc

        start = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            stt.request({"audio_path": "/tmp/x.wav"}, timeout=0.2)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 2.0, "request() should not block for the full readline duration")
        self.assertIn("timeout", str(ctx.exception))
        # Recovery path: dead/wedged process reference is dropped so the
        # next call starts a fresh subprocess.
        self.assertIsNone(stt._proc)
        self.assertTrue(fake_proc.killed)


if __name__ == "__main__":
    unittest.main()
