import base64
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request

APP_DIR = Path(__file__).resolve().parent
APP = Flask(__name__)


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


for candidate in (
    Path("/opt/robot/etc/robot-console-ai.env"),
    Path("/etc/robot-console-ai/robot-console-ai.env"),
    APP_DIR / ".env",
):
    try:
        _load_env_file(candidate)
    except Exception:
        pass


APP_TITLE = (os.environ.get("VLM_SERVICE_TITLE") or "Robot Console VLM").strip() or "Robot Console VLM"
APP_HOST = (os.environ.get("VLM_SERVICE_HOST") or "0.0.0.0").strip() or "0.0.0.0"
APP_PORT = int((os.environ.get("VLM_SERVICE_PORT") or "8090").strip() or "8090")
MODEL_ID = (os.environ.get("VLM_MODEL_ID") or "local-vlm").strip() or "local-vlm"
BACKEND_CMD = (os.environ.get("VLM_BACKEND_CMD") or "").strip()
DEFAULT_PROMPT = (
    os.environ.get("VLM_DEFAULT_PROMPT")
    or "Describe this image in a concise way and answer the user's question if one is provided."
).strip()
BACKEND_TIMEOUT = float((os.environ.get("VLM_BACKEND_TIMEOUT") or "120").strip() or "120")


def _decode_data_url(value: str) -> Tuple[bytes, str]:
    header, encoded = value.split(",", 1)
    mime_type = "application/octet-stream"
    if ":" in header and ";" in header:
        mime_type = header.split(":", 1)[1].split(";", 1)[0].strip() or mime_type
    return base64.b64decode(encoded), mime_type


def _extract_prompt_and_image(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt_parts: List[str] = []
    image_path = ""
    image_base64 = ""
    image_mime_type = ""
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, str):
            if message.get("role") == "user":
                prompt_parts.append(content.strip())
            continue
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            kind = (item.get("type") or "").strip().lower()
            if kind == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    prompt_parts.append(text)
            elif kind == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                image_url = str(image_url or "").strip()
                if not image_url:
                    continue
                if image_url.startswith("data:"):
                    _, encoded = image_url.split(",", 1)
                    image_base64 = encoded
                    image_mime_type = image_url.split(";", 1)[0].split(":", 1)[1]
                    image_path = ""
                elif image_url.startswith("file://"):
                    image_path = image_url[7:]
                    image_base64 = ""
                    image_mime_type = ""
                elif image_url.startswith("/"):
                    image_path = image_url
                    image_base64 = ""
                    image_mime_type = ""
    return {
        "prompt": "\n".join(part for part in prompt_parts if part).strip() or DEFAULT_PROMPT,
        "image_path": image_path,
        "image_base64": image_base64,
        "image_mime_type": image_mime_type,
    }


def _normalize_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    if data.get("messages"):
        payload = _extract_prompt_and_image(list(data.get("messages") or []))
    else:
        payload = {
            "prompt": str(data.get("prompt") or DEFAULT_PROMPT).strip() or DEFAULT_PROMPT,
            "image_path": str(data.get("image_path") or "").strip(),
            "image_base64": str(data.get("image_base64") or "").strip(),
            "image_mime_type": str(data.get("image_mime_type") or "").strip(),
        }
    payload["model"] = str(data.get("model") or MODEL_ID).strip() or MODEL_ID
    payload["max_tokens"] = int(data.get("max_tokens") or 256)
    return payload


def _materialize_temp_image(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    if payload.get("image_path") or not payload.get("image_base64"):
        return payload, ""
    blob = base64.b64decode(payload["image_base64"])
    suffix = ".img"
    mime_type = payload.get("image_mime_type") or ""
    if mime_type == "image/jpeg":
        suffix = ".jpg"
    elif mime_type == "image/png":
        suffix = ".png"
    elif mime_type == "image/webp":
        suffix = ".webp"
    elif mime_type in {"image/heic", "image/heif"}:
        suffix = ".heic"
    fd, temp_path = tempfile.mkstemp(prefix="vlm-image-", suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        handle.write(blob)
    payload = dict(payload)
    payload["image_path"] = temp_path
    return payload, temp_path


def _invoke_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not BACKEND_CMD:
        return {
            "ok": False,
            "error": "backend_not_configured",
            "message": "Set VLM_BACKEND_CMD to an executable that reads JSON on stdin and returns JSON or plain text on stdout.",
        }
    cmd = shlex.split(BACKEND_CMD)
    temp_path = ""
    try:
        materialized_payload, temp_path = _materialize_temp_image(payload)
        proc = subprocess.run(
            cmd,
            input=json.dumps(materialized_payload),
            capture_output=True,
            text=True,
            timeout=BACKEND_TIMEOUT,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "backend_failed",
                "returncode": proc.returncode,
                "stderr": stderr,
                "stdout": stdout,
                "cmd": " ".join(shlex.quote(part) for part in cmd),
            }
        if not stdout:
            return {"ok": False, "error": "backend_empty_output", "cmd": " ".join(shlex.quote(part) for part in cmd)}
        try:
            parsed = json.loads(stdout)
            text = str(parsed.get("text") or parsed.get("output") or "").strip()
            if text:
                return {"ok": True, "text": text, "raw": parsed}
        except json.JSONDecodeError:
            pass
        return {"ok": True, "text": stdout}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "backend_timeout", "timeout": BACKEND_TIMEOUT}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@APP.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "service": "vlm-service",
            "title": APP_TITLE,
            "model": MODEL_ID,
            "backend_configured": bool(BACKEND_CMD),
        }
    )


@APP.get("/v1/models")
def models():
    return jsonify({"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}]})


@APP.post("/v1/caption")
def caption():
    payload = _normalize_payload(request.get_json(silent=True) or {})
    result = _invoke_backend(payload)
    status = 200 if result.get("ok") else 503
    return jsonify(result), status


@APP.post("/v1/chat/completions")
def chat_completions():
    body = request.get_json(silent=True) or {}
    payload = _normalize_payload(body)
    result = _invoke_backend(payload)
    if not result.get("ok"):
        return jsonify(result), 503
    text = result.get("text") or ""
    return jsonify(
        {
            "id": "chatcmpl-local-vlm",
            "object": "chat.completion",
            "model": payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    )


if __name__ == "__main__":
    APP.run(host=APP_HOST, port=APP_PORT)
