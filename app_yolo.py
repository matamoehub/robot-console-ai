"""YOLO object detection HTTP service for Hailo AI HAT+ 2.

Thin Flask wrapper around scripts/hailo_yolo_backend.py.  Mirrors the
design of app_vlm.py: a persistent backend subprocess communicates over
newline-delimited JSON on stdin/stdout, keeping the Hailo device and any
loaded model warm between requests.

Endpoints:
  GET  /healthz                  Service health and configuration
  GET  /v1/models                List configured YOLO models
  POST /v1/detect                Run object detection on an image
  POST /v1/detect/url            Detect on a URL-referenced image (convenience)

Detection request body (POST /v1/detect):
  {
    "image_path":           "/tmp/frame.jpg",     # path on this host
    "image_base64":         "<base64 string>",    # alternative to image_path
    "image_mime_type":      "image/jpeg",         # required with image_base64
    "model":                "yolov11s",           # default: HAILO_YOLO_MODEL
    "confidence_threshold": 0.5,                 # default: 0.5
    "max_detections":       20                   # default: 20
  }

Detection response:
  {
    "ok": true,
    "model": "yolov11s",
    "detections": [
      {"class": "person", "class_id": 0, "confidence": 0.92, "bbox": [50, 30, 280, 430]},
      {"class": "chair",  "class_id": 56,"confidence": 0.84, "bbox": [310,200, 520, 470]}
    ],
    "count": 2,
    "elapsed_ms": 42.1,
    "backend_mode": "direct"
  }

Start via systemd (yolo-service.service) or directly:
  python3 app_yolo.py
"""

import atexit
import base64
import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request

APP_DIR = Path(__file__).resolve().parent
APP = Flask(__name__)


# ---------------------------------------------------------------------------
# Config / env loading
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


for _candidate in (
    Path("/opt/robot/etc/robot-console-ai.env"),
    Path("/etc/robot-console-ai/robot-console-ai.env"),
    APP_DIR / ".env",
):
    try:
        _load_env_file(_candidate)
    except Exception:
        pass


APP_HOST = (os.environ.get("YOLO_SERVICE_HOST") or "0.0.0.0").strip() or "0.0.0.0"
APP_PORT = int((os.environ.get("YOLO_SERVICE_PORT") or "8091").strip() or "8091")
MODEL_ID = (os.environ.get("HAILO_YOLO_MODEL") or "yolov11s").strip() or "yolov11s"
BACKEND_CMD = (os.environ.get("YOLO_BACKEND_CMD") or "").strip()
BACKEND_PERSISTENT = (os.environ.get("YOLO_BACKEND_PERSISTENT") or "1").strip().lower() not in {"0", "false", "no", "off"}
BACKEND_TIMEOUT = float((os.environ.get("HAILO_YOLO_TIMEOUT") or "60").strip() or "60")


# ---------------------------------------------------------------------------
# Persistent backend process
# ---------------------------------------------------------------------------

class _BackendProcess:
    """Persistent hailo_yolo_backend subprocess.

    Communicates over newline-delimited JSON on stdin/stdout.
    Thread-safe; one request at a time (the GIL + inference lock).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen[str]] = None

    def _start(self) -> "subprocess.Popen[str]":
        cmd_str = BACKEND_CMD
        if not cmd_str:
            backend_script = APP_DIR / "scripts" / "hailo_yolo_backend.py"
            cmd_str = f"python3 {shlex.quote(str(backend_script))}"
        cmd = [*shlex.split(cmd_str), "--serve"]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._proc = proc
        return proc

    def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                proc = self._start()
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("backend_pipes_unavailable")
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                stderr_text = ""
                if proc.stderr is not None:
                    try:
                        import select as _sel
                        ready, _, _ = _sel.select([proc.stderr], [], [], 0.2)
                        if ready:
                            stderr_text = proc.stderr.read(4096)
                    except Exception:
                        pass
                self._proc = None
                raise RuntimeError(stderr_text.strip() or "backend_no_response")
            return json.loads(line)

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


BACKEND_PROCESS = _BackendProcess()
atexit.register(BACKEND_PROCESS.close)


def _warmup_backend() -> None:
    """Fire a mock-mode warmup request so the backend process is live before the first real call."""
    if not BACKEND_PERSISTENT:
        return
    try:
        BACKEND_PROCESS.request({
            "image_path": "",
            "model": MODEL_ID,
            "confidence_threshold": 0.5,
            "max_detections": 1,
        })
    except Exception:
        pass


threading.Thread(target=_warmup_backend, daemon=True, name="yolo-warmup").start()


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _decode_base64_to_temp(b64: str, mime_type: str) -> Tuple[Optional[str], str]:
    """Decode a base64 image to a temp file.  Returns (path, error_str)."""
    ext_map = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/png": ".png", "image/webp": ".webp",
    }
    suffix = ext_map.get(mime_type.lower(), ".jpg")
    try:
        blob = base64.b64decode(b64)
    except Exception as exc:
        return None, f"base64_decode_failed: {exc}"
    fd, path = tempfile.mkstemp(prefix="yolo-", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return path, ""


def _normalize_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "image_path":           str(body.get("image_path") or "").strip(),
        "image_base64":         str(body.get("image_base64") or "").strip(),
        "image_mime_type":      str(body.get("image_mime_type") or "image/jpeg").strip(),
        "model":                str(body.get("model") or MODEL_ID).strip() or MODEL_ID,
        "confidence_threshold": float(body.get("confidence_threshold") or 0.5),
        "max_detections":       int(body.get("max_detections") or 20),
    }


# ---------------------------------------------------------------------------
# Backend invocation
# ---------------------------------------------------------------------------

def _invoke_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    temp_path: Optional[str] = None
    started = time.perf_counter()

    try:
        # Materialise base64 image if no path given.
        if not payload["image_path"] and payload["image_base64"]:
            temp_path, err = _decode_base64_to_temp(payload["image_base64"], payload["image_mime_type"])
            if err or not temp_path:
                return {"ok": False, "error": err or "base64_decode_failed"}
            payload = dict(payload)
            payload["image_path"] = temp_path
            payload["image_base64"] = ""  # avoid re-sending large blob

        if BACKEND_PERSISTENT:
            result = BACKEND_PROCESS.request(payload)
        else:
            # One-shot subprocess fallback.
            cmd_str = BACKEND_CMD
            if not cmd_str:
                backend_script = APP_DIR / "scripts" / "hailo_yolo_backend.py"
                cmd_str = f"python3 {shlex.quote(str(backend_script))}"
            proc = subprocess.run(
                shlex.split(cmd_str),
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=BACKEND_TIMEOUT,
                check=False,
            )
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "error": "backend_failed",
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "").strip(),
                }
            result = json.loads(proc.stdout.strip()) if proc.stdout.strip() else {}

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        result["elapsed_ms"] = elapsed_ms
        return result

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Flask endpoints
# ---------------------------------------------------------------------------

@APP.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "service": "yolo-service",
        "model": MODEL_ID,
        "backend_configured": True,
        "backend_persistent": BACKEND_PERSISTENT,
        "backend_mode": (os.environ.get("HAILO_YOLO_BACKEND_MODE") or "mock"),
    })


@APP.get("/v1/models")
def models():
    return jsonify({
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local-hailo"}],
    })


@APP.post("/v1/detect")
def detect():
    body = request.get_json(silent=True) or {}
    payload = _normalize_payload(body)
    result = _invoke_backend(payload)
    status = 200 if result.get("ok") else 503
    return jsonify(result), status


@APP.post("/v1/detect/url")
def detect_url():
    """Convenience endpoint: download image from a URL then detect."""
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing_url"}), 400

    try:
        import urllib.request
        fd, temp_path = tempfile.mkstemp(prefix="yolo-url-", suffix=".jpg")
        os.close(fd)
        urllib.request.urlretrieve(url, temp_path)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"url_download_failed: {exc}"}), 503

    try:
        payload = _normalize_payload({**body, "image_path": temp_path})
        result = _invoke_backend(payload)
        result["source_url"] = url
        return jsonify(result), 200 if result.get("ok") else 503
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    APP.run(host=APP_HOST, port=APP_PORT)
