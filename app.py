import json
import logging
import hashlib
import hmac
import mimetypes
import os
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from robot_brain import (
    EXECUTABLE_ACTIONS,
    TEST_ROBOT_BASE_URL,
    build_llm_parser_prompt,
    extract_json_object,
    load_robot_registry,
    normalize_llm_intent,
    parse_text_command,
    parse_text_command_plan,
    robot_catalog_payload,
)

APP_DIR = Path(__file__).resolve().parent
APP = Flask(__name__, static_folder="static", template_folder="templates")
APP.secret_key = os.environ.get("FLASK_SECRET", "robot-console-ai-local-only")
LOG_LEVEL_NAME = (os.environ.get("ROBOT_CONSOLE_AI_LOG_LEVEL", "INFO").strip() or "INFO").upper()
LOG_LEVEL = getattr(logging, LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("robot-console-ai")


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


def _load_version() -> str:
    env_version = (os.environ.get("ROBOT_CONSOLE_AI_VERSION") or "").strip()
    if env_version:
        return env_version
    version_file = APP_DIR / "VERSION"
    if version_file.exists():
        return (version_file.read_text(encoding="utf-8") or "1.0").strip() or "1.0"
    return "1.0"


APP_VERSION = _load_version()
APP_TITLE = os.environ.get("ROBOT_CONSOLE_AI_TITLE", "Robot Console AI").strip() or "Robot Console AI"
APP_SERVICE_NAME = os.environ.get("ROBOT_CONSOLE_AI_SERVICE", "robot-console-ai").strip() or "robot-console-ai"
APP_PORT = int(os.environ.get("ROBOT_CONSOLE_AI_PORT", "8080"))
APP_REPO_DIR = Path(os.environ.get("ROBOT_CONSOLE_AI_REPO_DIR", str(APP_DIR))).expanduser()
APP_UPDATE_SCRIPT = Path(os.environ.get("ROBOT_CONSOLE_AI_UPDATE_SCRIPT", "/opt/robot/bin/robot-console-ai-update")).expanduser()
APP_RESTART_SCRIPT = Path(os.environ.get("ROBOT_CONSOLE_AI_RESTART_SCRIPT", "/opt/robot/bin/robot-console-ai-restart")).expanduser()
UPDATE_TEST_LOG_PATH = Path(os.environ.get("ROBOT_CONSOLE_AI_UPDATE_TEST_LOG_PATH", "/tmp/robot-console-ai-update-tests.log")).expanduser()
UPDATE_TEST_RC_PATH = Path(os.environ.get("ROBOT_CONSOLE_AI_UPDATE_TEST_RC_PATH", "/tmp/robot-console-ai-update-tests.rc")).expanduser()
PASS_HASH_FILE = Path(os.environ.get("PASS_HASH_FILE", "/opt/robot/etc/robot-console-ai.passhash")).expanduser()
HAILO_OLLAMA_SERVICE_NAME = os.environ.get("HAILO_OLLAMA_SERVICE", "hailo-ollama").strip() or "hailo-ollama"
VLM_SERVICE_UNIT_NAME = os.environ.get("VLM_SERVICE_NAME", "vlm-service").strip() or "vlm-service"
HAILO_OLLAMA_API_BASE_URL = os.environ.get("HAILO_OLLAMA_API_BASE_URL", "http://127.0.0.1:8000").strip() or "http://127.0.0.1:8000"
VLM_API_BASE_URL = os.environ.get("VLM_API_BASE_URL", "http://127.0.0.1:8090").strip() or "http://127.0.0.1:8090"
STT_BACKEND_CMD = (os.environ.get("STT_BACKEND_CMD") or "").strip()
STT_BACKEND_MODE = (os.environ.get("STT_BACKEND_MODE", "mock").strip() or "mock").lower()
STT_DEFAULT_LANGUAGE = (os.environ.get("STT_DEFAULT_LANGUAGE", "en").strip() or "en")
STT_TRANSCRIBE_TIMEOUT = float(os.environ.get("STT_TRANSCRIBE_TIMEOUT", "90").strip() or "90")
STT_USES_HAILO = (os.environ.get("STT_USES_HAILO", "0").strip() or "0").lower() in {"1", "true", "yes", "on"}
ROBOT_REGISTRY_FILE = Path(
    os.environ.get("ROBOT_REGISTRY_FILE", "/opt/robot/robot-console/robots.json")
).expanduser()
ROBOT_TEXT_COMMAND_MODEL = (
    os.environ.get("ROBOT_TEXT_COMMAND_MODEL", "qwen2:1.5b").strip() or "qwen2:1.5b"
)
ROBOT_BRAIN_API_TOKEN = (os.environ.get("ROBOT_BRAIN_API_TOKEN") or "").strip()
TELEGRAM_EXECUTION_MODE = (os.environ.get("TELEGRAM_EXECUTION_MODE", "live").strip() or "live").lower()
SLACK_SIGNING_SECRET = (os.environ.get("SLACK_SIGNING_SECRET") or "").strip()
SLACK_BOT_TOKEN = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
SLACK_ALLOWED_CHANNEL_IDS = {
    item.strip()
    for item in (os.environ.get("SLACK_ALLOWED_CHANNEL_IDS") or "").split(",")
    if item.strip()
}
SLACK_DEFAULT_ROBOT_ID = (os.environ.get("SLACK_DEFAULT_ROBOT_ID") or "").strip()
SLACK_EXECUTION_MODE = (os.environ.get("SLACK_EXECUTION_MODE", "test").strip() or "test").lower()
ROBOT_BRAIN_AUDIT_LOG = Path(
    os.environ.get("ROBOT_BRAIN_AUDIT_LOG", "/opt/robot/logs/robot-brain-actions.log")
).expanduser()
PASS_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
ROBOT_BRAIN_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
ADMIN_USER = os.environ.get("RCAI_USER", "admin").strip() or "admin"
if not PASS_HASH_FILE.exists():
    PASS_HASH_FILE.write_text(generate_password_hash(secrets.token_urlsafe(24)))
PASS_HASH = PASS_HASH_FILE.read_text().strip()


DEFAULT_AI_SERVICES = [
    {
        "key": "hailo-ollama",
        "label": "Hailo Ollama",
        "service_name": os.environ.get("HAILO_OLLAMA_SERVICE", "hailo-ollama").strip() or "hailo-ollama",
        "health_url": os.environ.get("HAILO_OLLAMA_HEALTH_URL", "http://127.0.0.1:8000/hailo/v1/list").strip(),
        "description": "Primary LLM backend for the AI HAT+ 2.",
        "journal_unit": os.environ.get("HAILO_OLLAMA_SERVICE", "hailo-ollama").strip() or "hailo-ollama",
        "control_script": os.environ.get("HAILO_OLLAMA_CONTROL_SCRIPT", "").strip(),
    },
    {
        "key": "vlm-service",
        "label": "VLM Service",
        "service_name": os.environ.get("VLM_SERVICE_NAME", "vlm-service").strip() or "vlm-service",
        "health_url": os.environ.get("VLM_HEALTH_URL", "http://127.0.0.1:8090/healthz").strip(),
        "description": "Optional local vision-language service running on this Pi.",
        "journal_unit": os.environ.get("VLM_SERVICE_NAME", "vlm-service").strip() or "vlm-service",
        "control_script": os.environ.get("VLM_CONTROL_SCRIPT", "").strip(),
    },
    {
        "key": "open-webui",
        "label": "Open WebUI",
        "service_name": os.environ.get("OPEN_WEBUI_SERVICE", "open-webui").strip() or "open-webui",
        "health_url": os.environ.get("OPEN_WEBUI_HEALTH_URL", "http://127.0.0.1:3000").strip(),
        "description": "Optional browser UI that talks to the Ollama backend.",
        "journal_unit": os.environ.get("OPEN_WEBUI_SERVICE", "open-webui").strip() or "open-webui",
        "control_script": os.environ.get("OPEN_WEBUI_CONTROL_SCRIPT", "").strip(),
    },
]


def _load_ai_services() -> List[Dict[str, Any]]:
    raw = (os.environ.get("AI_LOCAL_SERVICES_JSON") or "").strip()
    if not raw:
        return DEFAULT_AI_SERVICES
    try:
        data = json.loads(raw)
    except Exception:
        return DEFAULT_AI_SERVICES
    if not isinstance(data, list):
        return DEFAULT_AI_SERVICES
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        service_name = str(item.get("service_name") or "").strip()
        if not key or not service_name:
            continue
        out.append(
            {
                "key": key,
                "label": str(item.get("label") or key).strip(),
                "service_name": service_name,
                "health_url": str(item.get("health_url") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "journal_unit": str(item.get("journal_unit") or service_name).strip(),
                "control_script": str(item.get("control_script") or "").strip(),
            }
        )
    return out or DEFAULT_AI_SERVICES


AI_SERVICES = _load_ai_services()
AI_SERVICE_MAP = {svc["key"]: svc for svc in AI_SERVICES}
HAILO_DEVICE_LOCK = threading.RLock()


def need_login(fn):
    from functools import wraps

    @wraps(fn)
    def wrap(*args, **kwargs):
        if session.get("user") != ADMIN_USER:
            return redirect(url_for("login_page", next=request.path))
        return fn(*args, **kwargs)

    return wrap


def _systemctl_run(args: List[str], timeout: float = 20.0) -> Dict[str, Any]:
    attempts = [
        ["systemctl", *args],
        ["sudo", "-n", "systemctl", *args],
    ]
    last: Dict[str, Any] | None = None
    for cmd in attempts:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            result = {
                "ok": p.returncode == 0,
                "cmd": " ".join(shlex.quote(x) for x in cmd),
                "returncode": p.returncode,
                "stdout": (p.stdout or "").strip(),
                "stderr": (p.stderr or "").strip(),
            }
            if result["ok"]:
                return result
            last = result
        except Exception as exc:
            last = {"ok": False, "cmd": " ".join(shlex.quote(x) for x in cmd), "error": str(exc)}
    return last or {"ok": False, "cmd": "systemctl", "error": "systemctl_failed"}


def _tail_text(path: Path, max_lines: int = 120) -> str:
    try:
        txt = path.read_text(errors="ignore")
    except Exception:
        return ""
    lines = txt.splitlines()
    if len(lines) <= max_lines:
        return txt
    return "\n".join(lines[-max_lines:])


def _git_pull_ff_only(repo_dir: Path) -> Dict[str, Any]:
    if APP_UPDATE_SCRIPT.exists():
        cmd = [str(APP_UPDATE_SCRIPT)] if os.access(APP_UPDATE_SCRIPT, os.X_OK) else ["sh", str(APP_UPDATE_SCRIPT)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
            return {
                "ok": p.returncode == 0,
                "mode": "script",
                "cmd": " ".join(shlex.quote(x) for x in cmd),
                "stdout": (p.stdout or "").strip(),
                "stderr": (p.stderr or "").strip(),
                "returncode": p.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "mode": "script", "error": "update_script_timeout", "cmd": str(APP_UPDATE_SCRIPT)}
        except Exception as exc:
            return {"ok": False, "mode": "script", "error": str(exc), "cmd": str(APP_UPDATE_SCRIPT)}

    cmd = ["git", "-C", str(repo_dir), "pull", "--ff-only"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        return {
            "ok": p.returncode == 0,
            "mode": "git",
            "cmd": " ".join(shlex.quote(x) for x in cmd),
            "stdout": (p.stdout or "").strip(),
            "stderr": (p.stderr or "").strip(),
            "returncode": p.returncode,
        }
    except Exception as exc:
        return {"ok": False, "mode": "git", "error": str(exc), "cmd": " ".join(shlex.quote(x) for x in cmd)}


def _restart_command_shell(service_name: str) -> tuple[str, str]:
    svc = (service_name or "").strip() or APP_SERVICE_NAME
    pid = os.getpid()
    kill_cmd = f"kill -TERM {pid}"
    if APP_RESTART_SCRIPT.exists():
        q_script = shlex.quote(str(APP_RESTART_SCRIPT))
        run_script = q_script if os.access(APP_RESTART_SCRIPT, os.X_OK) else f"sh {q_script}"
        return f"({run_script} || {kill_cmd})", "script_or_self_kill"
    q_svc = shlex.quote(svc)
    return f"(systemctl restart {q_svc} || sudo -n systemctl restart {q_svc} || {kill_cmd})", "systemctl_or_self_kill"


def _start_post_update_tasks_detached(repo_dir: Path, service_name: str) -> Dict[str, Any]:
    q_repo = shlex.quote(str(repo_dir))
    q_log = shlex.quote(str(UPDATE_TEST_LOG_PATH))
    q_rc = shlex.quote(str(UPDATE_TEST_RC_PATH))
    restart_cmd, restart_mode = _restart_command_shell(service_name)
    test_cmd = (
        "if command -v pytest >/dev/null 2>&1; then pytest -q; "
        "elif python3 -c 'import pytest' >/dev/null 2>&1; then python3 -m pytest -q; "
        "else python3 -m unittest discover -s tests -p 'test_*.py'; fi"
    )
    script = (
        "set +e; "
        f"rm -f {q_rc}; : > {q_log}; "
        f"cd {q_repo}; "
        f"echo '$ {test_cmd}' >> {q_log}; "
        f"{test_cmd} >> {q_log} 2>&1; "
        "trc=$?; "
        f"echo \"$trc\" > {q_rc}; "
        f"echo \"tests_rc=$trc\" >> {q_log}; "
        f"echo \"restart_mode={restart_mode}\" >> {q_log}; "
        f"echo '$ {restart_cmd}' >> {q_log}; "
        f"{restart_cmd} >> {q_log} 2>&1; "
        "rst=$?; "
        f"echo \"restart_rc=$rst\" >> {q_log}; "
        "exit 0"
    )
    try:
        p = subprocess.Popen(["sh", "-lc", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True, "queued": True, "pid": p.pid, "cmd": "sh -lc <post-update-script>"}
    except Exception as exc:
        return {"ok": False, "queued": False, "error": str(exc), "cmd": "sh -lc <post-update-script>"}


def _start_tests_only_detached(repo_dir: Path) -> Dict[str, Any]:
    q_repo = shlex.quote(str(repo_dir))
    q_log = shlex.quote(str(UPDATE_TEST_LOG_PATH))
    q_rc = shlex.quote(str(UPDATE_TEST_RC_PATH))
    test_cmd = (
        "if command -v pytest >/dev/null 2>&1; then pytest -q; "
        "elif python3 -c 'import pytest' >/dev/null 2>&1; then python3 -m pytest -q; "
        "else python3 -m unittest discover -s tests -p 'test_*.py'; fi"
    )
    script = (
        "set +e; "
        f"rm -f {q_rc}; : > {q_log}; "
        f"cd {q_repo}; "
        f"echo '$ {test_cmd}' >> {q_log}; "
        f"{test_cmd} >> {q_log} 2>&1; "
        "trc=$?; "
        f"echo \"$trc\" > {q_rc}; "
        f"echo \"tests_rc=$trc\" >> {q_log}; "
        "exit 0"
    )
    try:
        p = subprocess.Popen(["sh", "-lc", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"ok": True, "queued": True, "pid": p.pid, "cmd": "sh -lc <tests-only-script>"}
    except Exception as exc:
        return {"ok": False, "queued": False, "error": str(exc), "cmd": "sh -lc <tests-only-script>"}


def _service_health(url: str) -> Dict[str, Any]:
    target = (url or "").strip()
    if not target:
        return {"configured": False, "ok": False}
    try:
        import requests
        r = requests.get(target, timeout=4.0)
        return {"configured": True, "ok": r.ok, "status_code": r.status_code, "url": target}
    except Exception as exc:
        return {"configured": True, "ok": False, "url": target, "error": str(exc)}


def _http_json_request(method: str, url: str, *, payload: Dict[str, Any] | None = None, timeout: float = 120.0) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        import requests

        kwargs: Dict[str, Any] = {"timeout": timeout}
        if payload is not None:
            kwargs["json"] = payload
        r = requests.request(method.upper(), url, **kwargs)
        content_type = (r.headers.get("content-type") or "").lower()
        data = r.json() if "application/json" in content_type else {"raw": r.text}
        return {
            "ok": r.ok,
            "status_code": r.status_code,
            "url": url,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "response": data,
        }
    except Exception as exc:
        return {"ok": False, "url": url, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc)}


def _slack_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    started = time.perf_counter()
    if not SLACK_BOT_TOKEN:
        return {"ok": False, "error": "missing_slack_bot_token"}
    try:
        import requests

        response = requests.post(
            f"https://slack.com/api/{method}",
            json=payload,
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=45.0,
        )
        data = response.json()
        return {
            "ok": bool(response.ok and data.get("ok")),
            "status_code": response.status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "response": data,
        }
    except Exception as exc:
        return {"ok": False, "elapsed_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc)}


def _slack_signature_ok(req) -> bool:
    if not SLACK_SIGNING_SECRET:
        return False
    timestamp = str(req.headers.get("X-Slack-Request-Timestamp") or "").strip()
    signature = str(req.headers.get("X-Slack-Signature") or "").strip()
    if not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except Exception:
        return False
    if age > 60 * 5:
        return False
    body = req.get_data(cache=True)
    digest = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode("utf-8"),
        f"v0:{timestamp}:".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def _slack_allowed_channel(channel_id: str) -> bool:
    if not SLACK_ALLOWED_CHANNEL_IDS:
        return True
    return str(channel_id or "").strip() in SLACK_ALLOWED_CHANNEL_IDS


def _slack_clean_text(text: str) -> str:
    cleaned = []
    for part in str(text or "").split():
        if part.startswith("<@") and part.endswith(">"):
            continue
        cleaned.append(part)
    return " ".join(cleaned).strip()


def _stt_extension_for_mime(mime_type: str) -> str:
    lowered = str(mime_type or "").strip().lower()
    if lowered == "audio/webm":
        return ".webm"
    if lowered in {"audio/wav", "audio/x-wav"}:
        return ".wav"
    if lowered == "audio/mp4":
        return ".m4a"
    if lowered == "audio/mpeg":
        return ".mp3"
    guessed = mimetypes.guess_extension(lowered or "audio/wav")
    return guessed or ".wav"


def _materialize_audio_payload(body: Dict[str, Any]) -> tuple[Optional[Path], Optional[str]]:
    audio_path = str(body.get("audio_path") or "").strip()
    if audio_path:
        p = Path(audio_path).expanduser()
        if not p.exists():
            return None, "audio_path_not_found"
        return p, None

    audio_data_url = str(body.get("audio_data_url") or "").strip()
    audio_base64 = str(body.get("audio_base64") or "").strip()
    mime_type = str(body.get("audio_mime_type") or "").strip() or "audio/wav"
    if audio_data_url:
        if not audio_data_url.startswith("data:") or "," not in audio_data_url:
            return None, "invalid_audio_data_url"
        header, encoded = audio_data_url.split(",", 1)
        header_mime = header[5:].split(";", 1)[0].strip()
        if header_mime:
            mime_type = header_mime
        audio_base64 = encoded.strip()
    if not audio_base64:
        return None, "missing_audio_input"

    try:
        import base64

        raw = base64.b64decode(audio_base64, validate=False)
    except Exception:
        return None, "invalid_audio_base64"

    suffix = _stt_extension_for_mime(mime_type)
    with tempfile.NamedTemporaryFile(prefix="robot-console-ai-audio-", suffix=suffix, delete=False) as handle:
        handle.write(raw)
        return Path(handle.name), None


def _normalize_audio_for_stt(audio_path: Path) -> tuple[Optional[Path], Optional[str], bool]:
    suffix = audio_path.suffix.lower()
    if suffix == ".wav":
        LOGGER.info("STT audio already wav path=%s", audio_path)
        return audio_path, None, False

    ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000", str(audio_path.with_suffix(".wav"))]
    LOGGER.info("Converting audio for STT input=%s output=%s", audio_path, audio_path.with_suffix(".wav"))
    try:
        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=60.0, check=False)
    except Exception as exc:
        LOGGER.exception("STT audio conversion exec failed input=%s", audio_path)
        return None, f"audio_conversion_failed: {exc}", False
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "No such file or directory" in stderr and "ffmpeg" in stderr:
            LOGGER.error("ffmpeg not available for STT conversion input=%s", audio_path)
            return None, "ffmpeg_not_available", False
        LOGGER.error("STT audio conversion failed input=%s stderr=%s", audio_path, stderr or proc.stdout or "ffmpeg_error")
        return None, f"audio_conversion_failed: {stderr or proc.stdout or 'ffmpeg_error'}", False

    converted = audio_path.with_suffix(".wav")
    if not converted.exists():
        LOGGER.error("STT audio conversion did not create wav input=%s output=%s", audio_path, converted)
        return None, "audio_conversion_failed: wav_not_created", False
    LOGGER.info("STT audio conversion complete output=%s", converted)
    return converted, None, True


def _stt_transcribe(audio_path: Path, *, prompt: str = "", language: str = "", mock_text: str = "") -> Dict[str, Any]:
    if not audio_path.exists():
        LOGGER.error("STT audio path not found path=%s", audio_path)
        return {"ok": False, "error": "audio_path_not_found", "audio_path": str(audio_path)}

    backend_cmd = STT_BACKEND_CMD
    if not backend_cmd:
        default_script = APP_DIR / "scripts" / "stt_backend.py"
        backend_cmd = f"python3 {shlex.quote(str(default_script))}"

    payload = {
        "audio_path": str(audio_path),
        "prompt": str(prompt or "").strip(),
        "language": str(language or STT_DEFAULT_LANGUAGE or "").strip(),
        "mock_text": str(mock_text or "").strip(),
    }
    started = time.perf_counter()
    LOGGER.info("Starting STT transcription backend_mode=%s audio_path=%s language=%s", STT_BACKEND_MODE, audio_path, payload["language"])
    try:
        proc = subprocess.run(
            shlex.split(backend_cmd),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=STT_TRANSCRIBE_TIMEOUT,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "stt_backend_exec_failed",
            "cmd": backend_cmd,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": str(exc),
        }

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        LOGGER.error(
            "STT backend failed returncode=%s elapsed_ms=%s cmd=%s stdout=%s stderr=%s",
            proc.returncode,
            elapsed_ms,
            backend_cmd,
            stdout,
            stderr,
        )
        return {
            "ok": False,
            "error": "stt_backend_failed",
            "cmd": backend_cmd,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        }
    try:
        parsed = json.loads(stdout) if stdout else {}
    except Exception:
        parsed = {"ok": True, "text": stdout}
    result = {
        "ok": bool(parsed.get("ok", True)),
        "cmd": backend_cmd,
        "elapsed_ms": elapsed_ms,
        "text": str(parsed.get("text") or "").strip(),
        "language": str(parsed.get("language") or payload["language"]).strip(),
        "backend_mode": str(parsed.get("backend_mode") or STT_BACKEND_MODE).strip(),
    }
    if stderr:
        result["stderr"] = stderr
    if not result["text"]:
        LOGGER.error("STT returned empty transcript elapsed_ms=%s cmd=%s stderr=%s stdout=%s", elapsed_ms, backend_cmd, stderr, stdout)
        result["ok"] = False
        result["error"] = str(parsed.get("error") or "empty_transcript")
    else:
        LOGGER.info("STT transcription complete elapsed_ms=%s text=%s", elapsed_ms, result["text"])
    return result


def _compact_audit_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, list):
        if not value:
            return []
        simple_items = []
        for item in value[:8]:
            simple_items.append(_compact_audit_value(item, depth=depth + 1))
        if len(value) > 8:
            simple_items.append(f"... {len(value) - 8} more")
        return simple_items
    if not isinstance(value, dict):
        return value

    if "available_robots" in value:
        robots = value.get("available_robots") if isinstance(value.get("available_robots"), list) else []
        value = dict(value)
        value["available_robots"] = {
            "count": len(robots),
            "ids": [str(item.get("id") or "") for item in robots[:8] if isinstance(item, dict)],
        }

    preferred_keys = (
        "ok",
        "error",
        "mode",
        "source",
        "text",
        "target_scope",
        "target_robot_id",
        "mentioned_robot_ids",
        "summary",
        "action",
        "intent",
        "arguments",
        "sender",
        "preview",
        "execution",
        "results",
        "result",
        "step_results",
        "target_count",
        "multi_step",
        "robot_id",
        "status_code",
        "url",
        "elapsed_ms",
        "response",
        "mode_result",
        "hint",
    )
    out: Dict[str, Any] = {}
    for key in preferred_keys:
        if key in value:
            out[key] = _compact_audit_value(value[key], depth=depth + 1)

    if "intent" in out and isinstance(out["intent"], dict):
        intent = out["intent"]
        intent_keys = ("action", "executable", "summary", "arguments")
        out["intent"] = {key: intent[key] for key in intent_keys if key in intent}

    if "preview" in out and isinstance(out["preview"], dict):
        preview = out["preview"]
        preview_keys = ("target_scope", "target_robot_id", "summary", "action")
        out["preview"] = {key: preview[key] for key in preview_keys if key in preview}

    if "sender" in out and isinstance(out["sender"], dict):
        sender = out["sender"]
        sender_keys = ("source", "channel_id", "user_id", "thread_ts", "display_name", "username", "chat_id", "device_id")
        out["sender"] = {key: sender[key] for key in sender_keys if key in sender and sender[key] not in ("", None)}

    if "response" in out and isinstance(out["response"], dict):
        response = out["response"]
        response_keys = ("ok", "error", "message", "current_mode")
        compact_response = {key: response[key] for key in response_keys if key in response}
        if not compact_response and "raw" in response:
            compact_response["raw"] = str(response.get("raw") or "")[:200]
        out["response"] = compact_response or "[response omitted]"

    if "results" in out and isinstance(out["results"], list):
        trimmed_results = []
        for item in out["results"][:8]:
            if isinstance(item, dict):
                trimmed_results.append(
                    {
                        key: item[key]
                        for key in ("ok", "robot_id", "error", "status_code", "elapsed_ms", "url", "response", "mode_result")
                        if key in item
                    }
                )
            else:
                trimmed_results.append(item)
        if len(out["results"]) > 8:
            trimmed_results.append(f"... {len(out['results']) - 8} more")
        out["results"] = trimmed_results

    if "step_results" in out and isinstance(out["step_results"], list):
        trimmed_steps = []
        for item in out["step_results"][:8]:
            if isinstance(item, dict):
                trimmed_steps.append(
                    {
                        key: _compact_audit_value(item[key], depth=depth + 1)
                        for key in ("index", "text", "intent", "result")
                        if key in item
                    }
                )
            else:
                trimmed_steps.append(item)
        if len(out["step_results"]) > 8:
            trimmed_steps.append(f"... {len(out['step_results']) - 8} more")
        out["step_results"] = trimmed_steps

    for key in ("source", "text", "preferred_robot_id", "mode"):
        if key in value and key not in out:
            out[key] = _compact_audit_value(value[key], depth=depth + 1)
    return out


def _audit_robot_action(event_type: str, payload: Dict[str, Any]) -> None:
    entry = {
        "ts": int(time.time()),
        "event": str(event_type or "").strip() or "unknown",
        "payload": _compact_audit_value(payload),
    }
    try:
        with ROBOT_BRAIN_AUDIT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _api_token_ok(req) -> bool:
    expected = (ROBOT_BRAIN_API_TOKEN or "").strip()
    if not expected:
        return False
    supplied = (req.headers.get("X-Robot-Brain-Token") or "").strip()
    if not supplied:
        auth = (req.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            supplied = auth.split(" ", 1)[1].strip()
    return bool(supplied) and secrets.compare_digest(supplied, expected)


def _robot_registry() -> List[Dict[str, Any]]:
    return load_robot_registry(ROBOT_REGISTRY_FILE)


def _robot_by_id(robot_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(robot_id or "").strip()
    if not wanted:
        return None
    for robot in _robot_registry():
        if str(robot.get("id") or "").strip() == wanted:
            return robot
    return None


def _robot_request(robot: Dict[str, Any], method: str, path: str, payload: Dict[str, Any] | None = None, timeout: float = 20.0) -> Dict[str, Any]:
    base_url = str(robot.get("base_url") or "").rstrip("/")
    if not base_url:
        return {"ok": False, "error": "robot_missing_base_url", "robot": robot}
    url = f"{base_url}{path}"
    started = time.perf_counter()
    try:
        import requests

        headers = {}
        token = str(robot.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        kwargs: Dict[str, Any] = {"timeout": timeout, "headers": headers}
        if payload is not None:
            kwargs["json"] = payload
        response = requests.request(method.upper(), url, **kwargs)
        content_type = (response.headers.get("content-type") or "").lower()
        data = response.json() if "application/json" in content_type else {"raw": response.text}
        return {
            "ok": response.ok,
            "robot_id": robot.get("id"),
            "url": url,
            "status_code": response.status_code,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "response": data,
        }
    except Exception as exc:
        return {
            "ok": False,
            "robot_id": robot.get("id"),
            "url": url,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
        }


def _robot_master_mode_status(robot: Dict[str, Any]) -> Dict[str, Any]:
    return _robot_request(robot, "GET", "/api/admin/master-mode/status", timeout=10.0)


def _ensure_robot_remote_mode(robot: Dict[str, Any]) -> Dict[str, Any]:
    return _robot_request(
        robot,
        "POST",
        "/api/admin/master-mode/activate",
        {"mode": "llm_remote"},
        timeout=20.0,
    )


def _robot_remote_text_command(robot: Dict[str, Any], text: str, sender: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {
        "text": str(text or "").strip(),
        "source": str((sender or {}).get("source") or "robot-console-ai"),
        "user_id": str((sender or {}).get("user_id") or "").strip() or None,
        "channel_id": str((sender or {}).get("channel_id") or "").strip() or None,
    }
    return _robot_request(robot, "POST", "/api/remote/control", payload, timeout=20.0)


def _parse_robot_text_with_llm(text: str, robots: List[Dict[str, Any]], preferred_robot_id: str = "") -> Dict[str, Any]:
    prompt = build_llm_parser_prompt(text, robots, preferred_robot_id=preferred_robot_id)
    llm_res = _hailo_ollama_chat(ROBOT_TEXT_COMMAND_MODEL, prompt, options={"num_predict": 192})
    parsed_text = (((llm_res.get("response") or {}).get("message") or {}).get("content") or "").strip()
    parsed_json = extract_json_object(parsed_text)
    if not parsed_json:
        return {
            "ok": False,
            "error": "llm_parse_failed",
            "raw_text": parsed_text,
            "llm": llm_res,
        }
    normalized = normalize_llm_intent(parsed_json, robots, preferred_robot_id=preferred_robot_id)
    normalized["llm"] = llm_res
    normalized["raw_text"] = parsed_text
    return normalized


def _parse_robot_text_request(text: str, preferred_robot_id: str = "", use_llm: bool = True) -> Dict[str, Any]:
    robots = _robot_registry()
    rule_result = parse_text_command_plan(text, robots, preferred_robot_id=preferred_robot_id)
    if rule_result.get("intent", {}).get("action") != "unknown":
        rule_result["available_robots"] = robots
        _audit_robot_action(
            "parse",
            {
                "source": rule_result.get("source"),
                "text": text,
                "preferred_robot_id": preferred_robot_id,
                "intent": rule_result.get("intent"),
                "steps": rule_result.get("steps"),
            },
        )
        return rule_result
    if not use_llm:
        rule_result["available_robots"] = robots
        _audit_robot_action(
            "parse",
            {
                "source": rule_result.get("source"),
                "text": text,
                "preferred_robot_id": preferred_robot_id,
                "intent": rule_result.get("intent"),
                "steps": rule_result.get("steps"),
            },
        )
        return rule_result
    mode = _hailo_mode_status()
    if mode.get("active_mode") not in {"llm", "shared"}:
        rule_result["available_robots"] = robots
        rule_result["hint"] = "Switch Hailo mode to LLM for richer text-to-command parsing."
        _audit_robot_action(
            "parse",
            {
                "source": rule_result.get("source"),
                "text": text,
                "preferred_robot_id": preferred_robot_id,
                "intent": rule_result.get("intent"),
                "steps": rule_result.get("steps"),
                "hint": rule_result.get("hint"),
            },
        )
        return rule_result
    llm_result = _parse_robot_text_with_llm(text, robots, preferred_robot_id=preferred_robot_id)
    llm_result["available_robots"] = robots
    _audit_robot_action(
        "parse",
        {
            "source": llm_result.get("source"),
            "text": text,
            "preferred_robot_id": preferred_robot_id,
            "intent": llm_result.get("intent"),
            "steps": llm_result.get("steps"),
        },
    )
    return llm_result


def _execute_robot_intent(parsed: Dict[str, Any]) -> Dict[str, Any]:
    steps = parsed.get("steps") if isinstance(parsed.get("steps"), list) and parsed.get("steps") else None
    if steps:
        step_results: List[Dict[str, Any]] = []
        overall_ok = True
        for index, step in enumerate(steps, start=1):
            step_parsed = {
                "target_scope": step.get("target_scope") or parsed.get("target_scope"),
                "target_robot_id": step.get("target_robot_id") or parsed.get("target_robot_id"),
                "intent": step.get("intent") or {},
            }
            single_result = _execute_robot_intent({**parsed, **step_parsed, "steps": []})
            step_results.append(
                {
                    "index": index,
                    "text": step.get("text") or "",
                    "intent": step.get("intent") or {},
                    "result": single_result,
                }
            )
            overall_ok = overall_ok and bool(single_result.get("ok"))
        result = {
            "ok": overall_ok,
            "parsed": parsed,
            "step_results": step_results,
            "results": [item.get("result") for item in step_results],
            "target_count": len(step_results),
            "multi_step": True,
        }
        _audit_robot_action("execute", result)
        return result

    intent = parsed.get("intent") if isinstance(parsed.get("intent"), dict) else {}
    action = str(intent.get("action") or "").strip()
    args = intent.get("arguments") if isinstance(intent.get("arguments"), dict) else {}
    target_scope = str(parsed.get("target_scope") or "single").strip().lower()
    target_robot_id = str(parsed.get("target_robot_id") or "").strip()
    robots = _robot_registry()

    if action not in EXECUTABLE_ACTIONS:
        return {"ok": False, "error": "intent_not_executable", "parsed": parsed}

    targets = robots if target_scope == "fleet" else [robot for robot in robots if str(robot.get("id") or "") == target_robot_id]
    if not targets:
        return {"ok": False, "error": "target_robot_not_found", "parsed": parsed}

    results: List[Dict[str, Any]] = []
    for robot in targets:
        if bool(robot.get("test_mode")):
            preview_payload: Dict[str, Any] | None = None
            preview_path = ""
            preview_method = "POST"
            if action == "say":
                preview_path = "/api/remote/control"
                preview_payload = {"text": f'say {str(args.get("text") or "").strip()}', "source": "robot-console-ai"}
            elif action == "soundoff":
                preview_path = "/api/cmd/soundoff"
                preview_payload = {}
            elif action == "allstop":
                preview_path = "/api/cmd/allstop"
                preview_payload = {}
            elif action == "master_mode":
                preview_path = "/api/admin/master-mode/activate"
                preview_payload = {"mode": str(args.get("mode") or "").strip()}
            elif action == "camera_center":
                preview_path = "/api/camera/center"
                preview_payload = {}
            elif action == "camera_nod":
                preview_path = "/api/camera/nod"
                preview_payload = {"depth": float(args.get("depth") or 0.5), "speed_s": float(args.get("speed_s") or 0.25)}
            elif action == "camera_shake":
                preview_path = "/api/camera/shake"
                preview_payload = {"width": float(args.get("width") or 0.5), "speed_s": float(args.get("speed_s") or 0.25)}
            elif action == "camera_wiggle":
                preview_path = "/api/camera/wiggle"
                preview_payload = {"cycles": int(args.get("cycles") or 2), "amplitude": float(args.get("amplitude") or 0.3), "speed_s": float(args.get("speed_s") or 0.2)}
            elif action == "llm_service":
                op = str(args.get("op") or "start").strip().lower()
                preview_path = f"/api/service/play_llm/{op}"
                preview_payload = {}
            results.append(
                {
                    "ok": True,
                    "robot_id": robot.get("id"),
                    "preview_only": True,
                    "url": f"{TEST_ROBOT_BASE_URL}{preview_path}",
                    "method": preview_method,
                    "would_send": preview_payload,
                    "response": {
                        "ok": True,
                        "preview_only": True,
                        "message": "Test robot selected. No live robot command was sent.",
                    },
                }
            )
            continue

        if action == "say":
            mode_res = _ensure_robot_remote_mode(robot)
            if not mode_res.get("ok"):
                result = {
                    "ok": False,
                    "robot_id": robot.get("id"),
                    "error": "failed_to_enable_llm_remote",
                    "mode_result": mode_res,
                }
            else:
                result = _robot_remote_text_command(
                    robot,
                    f'say {str(args.get("text") or "").strip()}',
                    sender={"source": "robot-console-ai"},
                )
        elif action == "soundoff":
            result = _robot_request(robot, "POST", "/api/cmd/soundoff", {}, timeout=15.0)
        elif action == "allstop":
            result = _robot_request(robot, "POST", "/api/cmd/allstop", {}, timeout=15.0)
        elif action == "master_mode":
            result = _robot_request(robot, "POST", "/api/admin/master-mode/activate", {"mode": str(args.get("mode") or "").strip()}, timeout=20.0)
        elif action == "camera_center":
            result = _robot_request(robot, "POST", "/api/camera/center", {}, timeout=15.0)
        elif action == "camera_nod":
            result = _robot_request(robot, "POST", "/api/camera/nod", {"depth": float(args.get("depth") or 0.5), "speed_s": float(args.get("speed_s") or 0.25)}, timeout=15.0)
        elif action == "camera_shake":
            result = _robot_request(robot, "POST", "/api/camera/shake", {"width": float(args.get("width") or 0.5), "speed_s": float(args.get("speed_s") or 0.25)}, timeout=15.0)
        elif action == "camera_wiggle":
            result = _robot_request(robot, "POST", "/api/camera/wiggle", {"cycles": int(args.get("cycles") or 2), "amplitude": float(args.get("amplitude") or 0.3), "speed_s": float(args.get("speed_s") or 0.2)}, timeout=15.0)
        elif action == "llm_service":
            op = str(args.get("op") or "start").strip().lower()
            result = _robot_request(robot, "POST", f"/api/service/play_llm/{op}", {}, timeout=20.0)
            if not result.get("ok"):
                result = _robot_request(robot, "POST", f"/api/service/ros_llm/{op}", {}, timeout=20.0)
        else:
            result = {"ok": False, "error": "unsupported_action", "action": action}
        results.append(result)

    result = {
        "ok": all(bool(item.get("ok")) for item in results),
        "parsed": parsed,
        "results": results,
        "target_count": len(results),
    }
    _audit_robot_action("execute", result)
    return result


def _chat_text_ingest(
    text: str,
    robot_id: str = "",
    mode: str = "test",
    sender: Optional[Dict[str, Any]] = None,
    *,
    audit_event_type: str,
) -> Dict[str, Any]:
    chosen_mode = (mode or "test").strip().lower()
    if chosen_mode not in {"test", "live"}:
        return {"ok": False, "error": "invalid_mode", "mode": chosen_mode}
    parsed = _parse_robot_text_request(text, preferred_robot_id=robot_id, use_llm=True)
    if not parsed.get("ok"):
        result = {"ok": False, "mode": chosen_mode, "sender": sender or {}, "parsed": parsed}
        _audit_robot_action(audit_event_type, result)
        return result
    payload = {
        "ok": True,
        "mode": chosen_mode,
        "sender": sender or {},
        "parsed": parsed,
        "preview": {
            "target_scope": parsed.get("target_scope"),
            "target_robot_id": parsed.get("target_robot_id"),
            "summary": ((parsed.get("intent") or {}).get("summary") or ""),
            "action": ((parsed.get("intent") or {}).get("action") or ""),
        },
    }
    if chosen_mode == "test":
        payload["message"] = "Preview only. No robot command was executed."
        payload["planned_steps"] = parsed.get("steps") or []
        _audit_robot_action(audit_event_type, payload)
        return payload
    execution = _execute_robot_intent(parsed)
    payload["execution"] = execution
    payload["ok"] = bool(execution.get("ok"))
    _audit_robot_action(audit_event_type, payload)
    return payload


def _telegram_ingest(text: str, robot_id: str = "", mode: str = "test", sender: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _chat_text_ingest(text, robot_id=robot_id, mode=mode, sender=sender, audit_event_type="telegram")


def _slack_ingest(text: str, robot_id: str = "", mode: str = "test", sender: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return _chat_text_ingest(text, robot_id=robot_id, mode=mode, sender=sender, audit_event_type="slack")


def _format_slack_result(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
        user_message = ""
        for item in execution.get("results") or []:
            if not isinstance(item, dict):
                continue
            response = item.get("response") if isinstance(item.get("response"), dict) else {}
            if response.get("user_message"):
                user_message = str(response.get("user_message") or "").strip()
                break
            mode_result = item.get("mode_result") if isinstance(item.get("mode_result"), dict) else {}
            mode_response = mode_result.get("response") if isinstance(mode_result.get("response"), dict) else {}
            if mode_response.get("user_message"):
                user_message = str(mode_response.get("user_message") or "").strip()
                break
        error = user_message or str(result.get("error") or parsed.get("error") or "command_failed")
        return f"Robot command failed: {error}"
    preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    summary = str(preview.get("summary") or ((parsed.get("intent") or {}).get("summary")) or "done").strip()
    lines = [f"Robot command: {summary}", f"Mode: {result.get('mode') or SLACK_EXECUTION_MODE}"]
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    for item in execution.get("results") or []:
        robot_id = str(item.get("robot_id") or "robot")
        status = "ok" if item.get("ok") else "failed"
        lines.append(f"- {robot_id}: {status}")
    if result.get("mode") == "test":
        lines.append("Preview only. No live robot command was sent.")
    return "\n".join(lines)


def _process_slack_event(event: Dict[str, Any]) -> None:
    event_type = str(event.get("type") or "").strip()
    if event_type not in {"message", "app_mention"}:
        LOGGER.info("Slack event ignored reason=unsupported_event_type event_type=%s event=%s", event_type, event)
        return
    if event.get("bot_id"):
        LOGGER.info("Slack event ignored reason=bot_message event_type=%s channel=%s", event_type, str(event.get("channel") or "").strip())
        return
    subtype = str(event.get("subtype") or "").strip()
    if subtype:
        LOGGER.info(
            "Slack event ignored reason=subtype event_type=%s subtype=%s channel=%s",
            event_type,
            subtype,
            str(event.get("channel") or "").strip(),
        )
        return
    channel_id = str(event.get("channel") or "").strip()
    if not channel_id:
        LOGGER.info("Slack event ignored reason=missing_channel event_type=%s event=%s", event_type, event)
        return
    if not _slack_allowed_channel(channel_id):
        LOGGER.info(
            "Slack event ignored reason=channel_not_allowed event_type=%s channel=%s allowed_channels=%s",
            event_type,
            channel_id,
            sorted(SLACK_ALLOWED_CHANNEL_IDS),
        )
        return
    text = _slack_clean_text(str(event.get("text") or ""))
    if not text:
        LOGGER.info("Slack event ignored reason=empty_text event_type=%s channel=%s raw_text=%s", event_type, channel_id, str(event.get("text") or ""))
        return
    sender = {
        "source": "slack",
        "channel_id": channel_id,
        "user_id": str(event.get("user") or "").strip(),
        "thread_ts": str(event.get("thread_ts") or event.get("ts") or "").strip(),
    }
    LOGGER.info(
        "Slack event accepted event_type=%s channel=%s user=%s thread_ts=%s text=%s",
        event_type,
        channel_id,
        sender["user_id"],
        sender["thread_ts"],
        text,
    )
    result = _slack_ingest(
        text,
        robot_id=SLACK_DEFAULT_ROBOT_ID,
        mode=SLACK_EXECUTION_MODE,
        sender=sender,
    )
    reply = _format_slack_result(result)
    post_result = _slack_api(
        "chat.postMessage",
        {
            "channel": channel_id,
            "thread_ts": sender["thread_ts"],
            "text": reply,
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )
    if post_result.get("ok"):
        LOGGER.info("Slack reply posted channel=%s thread_ts=%s", channel_id, sender["thread_ts"])
    if not post_result.get("ok"):
        LOGGER.error("Slack reply failed channel=%s result=%s", channel_id, post_result)


def _voice_command_from_request(
    body: Dict[str, Any],
    *,
    execute_live: bool,
    sender: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    audio_path, audio_error = _materialize_audio_payload(body)
    if audio_error or audio_path is None:
        return {"ok": False, "error": audio_error or "missing_audio_input"}

    prompt = str(body.get("prompt") or "").strip()
    language = str(body.get("language") or "").strip()
    preferred_robot_id = str(body.get("robot_id") or "").strip()
    use_llm = bool(body.get("use_llm", True))
    mock_text = str(body.get("mock_text") or "").strip()
    normalized_audio_path: Optional[Path] = None
    normalized_created = False

    try:
        normalized_audio_path, normalize_error, normalized_created = _normalize_audio_for_stt(audio_path)
        if normalize_error or normalized_audio_path is None:
            result = {"ok": False, "error": normalize_error or "audio_normalization_failed", "sender": sender or {}}
            LOGGER.error("Voice command normalization failed error=%s", result["error"])
            _audit_robot_action("voice_command", result)
            return result
        if STT_USES_HAILO:
            with HAILO_DEVICE_LOCK:
                transcript = _stt_transcribe(normalized_audio_path, prompt=prompt, language=language, mock_text=mock_text)
        else:
            transcript = _stt_transcribe(normalized_audio_path, prompt=prompt, language=language, mock_text=mock_text)
    finally:
        try:
            if audio_path.parent == Path(tempfile.gettempdir()):
                audio_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if normalized_created and normalized_audio_path and normalized_audio_path.parent == Path(tempfile.gettempdir()):
                normalized_audio_path.unlink(missing_ok=True)
        except Exception:
            pass

    if not transcript.get("ok"):
        result = {"ok": False, "error": "transcription_failed", "transcript": transcript, "sender": sender or {}}
        LOGGER.error("Voice command transcription failed transcript=%s", transcript)
        _audit_robot_action("voice_command", result)
        return result

    parsed = _parse_robot_text_request(transcript.get("text") or "", preferred_robot_id=preferred_robot_id, use_llm=use_llm)
    result: Dict[str, Any] = {
        "ok": bool(parsed.get("ok")),
        "sender": sender or {},
        "transcript": transcript,
        "parsed": parsed,
        "mode": "live" if execute_live else "test",
    }
    if not parsed.get("ok"):
        LOGGER.error("Voice command parse failed transcript=%s parsed=%s", transcript.get("text"), parsed)
        _audit_robot_action("voice_command", result)
        return result

    if not execute_live:
        result["message"] = "Preview only. No robot command was executed."
        LOGGER.info("Voice command preview transcript=%s target=%s", transcript.get("text"), parsed.get("target_robot_id"))
        _audit_robot_action("voice_command", result)
        return result

    execution = _execute_robot_intent(parsed)
    result["execution"] = execution
    result["ok"] = bool(execution.get("ok"))
    LOGGER.info("Voice command live execution ok=%s transcript=%s", result["ok"], transcript.get("text"))
    _audit_robot_action("voice_command", result)
    return result


def _hailo_ollama_models() -> Dict[str, Any]:
    return _http_json_request("GET", f"{HAILO_OLLAMA_API_BASE_URL.rstrip('/')}/hailo/v1/list", timeout=20.0)


def _hailo_ollama_installed_models() -> Dict[str, Any]:
    return _http_json_request("GET", f"{HAILO_OLLAMA_API_BASE_URL.rstrip('/')}/api/tags", timeout=20.0)


def _hailo_ollama_chat(model: str, prompt: str, options: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if options:
        payload["options"] = options
    return _http_json_request("POST", f"{HAILO_OLLAMA_API_BASE_URL.rstrip('/')}/api/chat", payload=payload, timeout=120.0)


def _vlm_caption_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _http_json_request("POST", f"{VLM_API_BASE_URL.rstrip('/')}/v1/chat/completions", payload=payload, timeout=120.0)


def _vlm_models() -> Dict[str, Any]:
    return _http_json_request("GET", f"{VLM_API_BASE_URL.rstrip('/')}/v1/models", timeout=20.0)


def _service_status(item: Dict[str, Any]) -> Dict[str, Any]:
    service_name = item["service_name"]
    show = _systemctl_run([
        "show",
        service_name,
        "--property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath",
        "--no-pager",
    ], timeout=8.0)
    props: Dict[str, str] = {}
    for line in (show.get("stdout") or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return {
        "key": item["key"],
        "label": item["label"],
        "service_name": service_name,
        "description": item.get("description") or "",
        "health": _service_health(item.get("health_url") or ""),
        "load_state": props.get("LoadState") or "",
        "active_state": props.get("ActiveState") or "",
        "sub_state": props.get("SubState") or "",
        "unit_file_state": props.get("UnitFileState") or "",
        "fragment_path": props.get("FragmentPath") or "",
        "available": (props.get("LoadState") or "") not in ("", "not-found"),
        "control_script": item.get("control_script") or "",
    }


def _service_action(item: Dict[str, Any], action: str) -> Dict[str, Any]:
    op = (action or "").strip().lower()
    if op not in {"start", "stop", "restart"}:
        return {"ok": False, "error": "unsupported_action"}
    control_script = (item.get("control_script") or "").strip()
    if control_script:
        cmd = [control_script, op]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0, check=False)
            result = {
                "ok": p.returncode == 0,
                "cmd": " ".join(shlex.quote(x) for x in cmd),
                "returncode": p.returncode,
                "stdout": (p.stdout or "").strip(),
                "stderr": (p.stderr or "").strip(),
            }
        except Exception as exc:
            result = {"ok": False, "cmd": " ".join(shlex.quote(x) for x in cmd), "error": str(exc)}
    else:
        result = _systemctl_run([op, item["service_name"]], timeout=30.0)
    return {
        "ok": bool(result.get("ok")),
        "action": op,
        "service_name": item["service_name"],
        "result": result,
        "status": _service_status(item),
    }


def _wait_for_health(url: str, expect_ok: bool = True, timeout_s: float = 25.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last = {"ok": False, "url": url}
    while time.time() < deadline:
        last = _service_health(url)
        if bool(last.get("ok")) == expect_ok:
            return {"ok": True, "health": last}
        time.sleep(0.5)
    return {"ok": False, "health": last, "timeout_s": timeout_s}


def _switch_hailo_mode(target: str) -> Dict[str, Any]:
    target = (target or "").strip().lower()
    if target not in {"llm", "vlm"}:
        return {"ok": False, "error": "invalid_hailo_target"}
    desired_service = HAILO_OLLAMA_SERVICE_NAME if target == "llm" else VLM_SERVICE_UNIT_NAME
    other_service = VLM_SERVICE_UNIT_NAME if target == "llm" else HAILO_OLLAMA_SERVICE_NAME
    desired_health = HAILO_OLLAMA_API_BASE_URL.rstrip("/") + "/hailo/v1/list" if target == "llm" else VLM_API_BASE_URL.rstrip("/") + "/healthz"

    with HAILO_DEVICE_LOCK:
        steps = []
        stop_other = _systemctl_run(["stop", other_service], timeout=30.0)
        steps.append({"service": other_service, "action": "stop", **stop_other})
        if not stop_other.get("ok"):
            return {"ok": False, "error": "stop_other_failed", "target": target, "steps": steps}

        start_desired = _systemctl_run(["restart", desired_service], timeout=30.0)
        steps.append({"service": desired_service, "action": "restart", **start_desired})
        if not start_desired.get("ok"):
            return {"ok": False, "error": "restart_desired_failed", "target": target, "steps": steps}

        health = _wait_for_health(desired_health, expect_ok=True, timeout_s=25.0)
        steps.append({"service": desired_service, "action": "health_wait", **health})
        if not health.get("ok"):
            return {"ok": False, "error": "desired_health_failed", "target": target, "steps": steps}

        return {"ok": True, "target": target, "steps": steps}


def _hailo_mode_status() -> Dict[str, Any]:
    llm_health = _service_health(f"{HAILO_OLLAMA_API_BASE_URL.rstrip('/')}/hailo/v1/list")
    vlm_health = _service_health(f"{VLM_API_BASE_URL.rstrip('/')}/healthz")
    llm_status = _service_status({
        "key": "hailo-ollama",
        "label": "Hailo Ollama",
        "service_name": HAILO_OLLAMA_SERVICE_NAME,
        "description": "",
        "health_url": f"{HAILO_OLLAMA_API_BASE_URL.rstrip('/')}/hailo/v1/list",
    })
    vlm_status = _service_status({
        "key": "vlm-service",
        "label": "VLM Service",
        "service_name": VLM_SERVICE_UNIT_NAME,
        "description": "",
        "health_url": f"{VLM_API_BASE_URL.rstrip('/')}/healthz",
    })
    active_mode = "unknown"
    if llm_health.get("ok") and not vlm_health.get("ok"):
        active_mode = "llm"
    elif vlm_health.get("ok") and not llm_health.get("ok"):
        active_mode = "vlm"
    elif llm_health.get("ok") and vlm_health.get("ok"):
        active_mode = "shared"
    return {
        "ok": True,
        "active_mode": active_mode,
        "llm": llm_status,
        "vlm": vlm_status,
    }


def _service_logs(item: Dict[str, Any], lines: int = 120) -> Dict[str, Any]:
    lines = max(10, min(int(lines), 500))
    unit = item.get("journal_unit") or item["service_name"]
    attempts = [
        ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
        ["sudo", "-n", "journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
    ]
    last: Dict[str, Any] | None = None
    for cmd in attempts:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=12.0, check=False)
            result = {
                "ok": p.returncode == 0,
                "cmd": " ".join(shlex.quote(x) for x in cmd),
                "returncode": p.returncode,
                "stdout": (p.stdout or "").strip(),
                "stderr": (p.stderr or "").strip(),
            }
            if result["ok"]:
                return result
            last = result
        except Exception as exc:
            last = {"ok": False, "cmd": " ".join(shlex.quote(x) for x in cmd), "error": str(exc)}
    return last or {"ok": False, "cmd": "journalctl", "error": "journalctl_failed"}


@APP.context_processor
def inject_context() -> Dict[str, Any]:
    return {"app_version": APP_VERSION, "app_title": APP_TITLE}


@APP.get("/")
def home():
    return redirect(url_for("admin_page"))


@APP.get("/login")
def login_page():
    return render_template("login.html")


@APP.post("/login")
def login():
    user = request.form.get("username", "")
    password = request.form.get("password", "")
    if user == ADMIN_USER and check_password_hash(PASS_HASH, password):
        session["user"] = ADMIN_USER
        return redirect(request.args.get("next") or url_for("admin_page"))
    flash("Invalid username or password", "danger")
    return render_template("login.html"), 401


@APP.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@APP.get("/api/version")
def api_version():
    return jsonify({"ok": True, "app": "robot-console-ai", "version": APP_VERSION, "port": APP_PORT})


@APP.get("/admin")
@need_login
def admin_page():
    return render_template(
        "admin.html",
        service_name=APP_SERVICE_NAME,
        repo_dir=str(APP_REPO_DIR),
        ai_services=AI_SERVICES,
    )


@APP.get("/admin/robot-control")
@need_login
def admin_robot_control_page():
    return render_template(
        "robot_control.html",
        service_name=APP_SERVICE_NAME,
        repo_dir=str(APP_REPO_DIR),
        telegram_execution_mode=TELEGRAM_EXECUTION_MODE,
    )


@APP.get("/logs")
@need_login
def logs_page():
    return render_template("logs.html", ai_services=AI_SERVICES)


@APP.get("/api/admin/services")
@need_login
def api_admin_services():
    return jsonify({"ok": True, "services": [_service_status(item) for item in AI_SERVICES]})


@APP.post("/api/admin/services/<service_key>/<action>")
@need_login
def api_admin_service_action(service_key: str, action: str):
    item = AI_SERVICE_MAP.get((service_key or "").strip())
    if not item:
        return jsonify({"ok": False, "error": "unknown_service"}), 404
    result = _service_action(item, action)
    return jsonify(result), (200 if result.get("ok") else 500)


@APP.get("/api/admin/logs/<service_key>")
@need_login
def api_admin_logs(service_key: str):
    item = AI_SERVICE_MAP.get((service_key or "").strip())
    if not item:
        return jsonify({"ok": False, "error": "unknown_service"}), 404
    lines = request.args.get("lines", "120")
    result = _service_logs(item, int(lines))
    return jsonify({"ok": bool(result.get("ok")), "service": item, "logs": result}), (200 if result.get("ok") else 500)


@APP.get("/api/admin/config")
@need_login
def api_admin_config():
    return jsonify(
        {
            "ok": True,
            "title": APP_TITLE,
            "service_name": APP_SERVICE_NAME,
            "repo_dir": str(APP_REPO_DIR),
            "port": APP_PORT,
            "pass_hash_file": str(PASS_HASH_FILE),
            "hailo_ollama_api_base_url": HAILO_OLLAMA_API_BASE_URL,
            "vlm_api_base_url": VLM_API_BASE_URL,
            "robot_registry_file": str(ROBOT_REGISTRY_FILE),
            "robot_text_command_model": ROBOT_TEXT_COMMAND_MODEL,
            "robot_brain_api_token_configured": bool(ROBOT_BRAIN_API_TOKEN),
            "stt_backend_cmd": STT_BACKEND_CMD,
            "stt_backend_mode": STT_BACKEND_MODE,
            "stt_default_language": STT_DEFAULT_LANGUAGE,
            "stt_uses_hailo": STT_USES_HAILO,
            "hailo_mode": _hailo_mode_status(),
            "services": AI_SERVICES,
        }
    )


@APP.get("/api/admin/hailo/mode")
@need_login
def api_admin_hailo_mode():
    return jsonify(_hailo_mode_status())


@APP.post("/api/admin/hailo/mode/<target>")
@need_login
def api_admin_hailo_mode_switch(target: str):
    result = _switch_hailo_mode(target)
    if result.get("ok"):
        result["status"] = _hailo_mode_status()
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.get("/api/admin/llm/models")
@need_login
def api_admin_llm_models():
    result = _hailo_ollama_installed_models()
    if not result.get("ok"):
        result = _hailo_ollama_models()
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.post("/api/admin/llm/chat")
@need_login
def api_admin_llm_chat():
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()
    model = str(body.get("model") or "").strip()
    max_tokens = max(1, min(int(body.get("max_tokens") or 64), 512))
    short_answer = bool(body.get("short_answer"))
    if not prompt:
        return jsonify({"ok": False, "error": "missing_prompt"}), 400
    if not model:
        return jsonify({"ok": False, "error": "missing_model"}), 400
    if short_answer:
        prompt = f"{prompt.rstrip()}\n\nReply briefly. Use at most one short sentence."
    with HAILO_DEVICE_LOCK:
        mode = _hailo_mode_status()
        if mode.get("active_mode") not in {"llm", "shared"}:
            return jsonify({"ok": False, "error": "hailo_mode_not_llm", "mode": mode}), 503
        result = _hailo_ollama_chat(model, prompt, options={"num_predict": max_tokens})
        result["mode"] = mode
        result["request"] = {"model": model, "max_tokens": max_tokens, "short_answer": short_answer}
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.get("/api/admin/vlm/models")
@need_login
def api_admin_vlm_models():
    result = _vlm_models()
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.post("/api/admin/vlm/caption")
@need_login
def api_admin_vlm_caption():
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()
    image_data_url = str(body.get("image_data_url") or "").strip()
    model = str(body.get("model") or os.environ.get("VLM_MODEL_ID", "local-vlm")).strip() or "local-vlm"
    if not image_data_url:
        return jsonify({"ok": False, "error": "missing_image_data_url"}), 400
    with HAILO_DEVICE_LOCK:
        mode = _hailo_mode_status()
        if mode.get("active_mode") not in {"vlm", "shared"}:
            return jsonify({"ok": False, "error": "hailo_mode_not_vlm", "mode": mode}), 503
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt or DEFAULT_AI_SERVICES[1]["description"]},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
        }
        result = _vlm_caption_request(payload)
        result["mode"] = mode
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.get("/api/admin/robot-control/catalog")
@need_login
def api_admin_robot_control_catalog():
    payload = robot_catalog_payload()
    payload["registry_file"] = str(ROBOT_REGISTRY_FILE)
    return jsonify(payload)


@APP.get("/api/admin/robot-control/robots")
@need_login
def api_admin_robot_control_robots():
    robots = _robot_registry()
    enriched = []
    for robot in robots:
        item = dict(robot)
        item["master_mode_status"] = _robot_master_mode_status(robot)
        enriched.append(item)
    return jsonify({"ok": True, "robots": enriched, "registry_file": str(ROBOT_REGISTRY_FILE)})


@APP.post("/api/admin/robot-control/parse")
@need_login
def api_admin_robot_control_parse():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    preferred_robot_id = str(body.get("robot_id") or "").strip()
    use_llm = bool(body.get("use_llm", True))
    result = _parse_robot_text_request(text, preferred_robot_id=preferred_robot_id, use_llm=use_llm)
    return jsonify(result), (200 if result.get("ok") else 400)


@APP.post("/api/admin/robot-control/execute")
@need_login
def api_admin_robot_control_execute():
    body = request.get_json(silent=True) or {}
    parsed = body.get("parsed") if isinstance(body.get("parsed"), dict) else None
    if parsed is None:
        text = str(body.get("text") or "").strip()
        preferred_robot_id = str(body.get("robot_id") or "").strip()
        use_llm = bool(body.get("use_llm", True))
        parsed = _parse_robot_text_request(text, preferred_robot_id=preferred_robot_id, use_llm=use_llm)
        if not parsed.get("ok"):
            return jsonify(parsed), 400
    result = _execute_robot_intent(parsed)
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.post("/api/admin/stt/transcribe")
@need_login
def api_admin_stt_transcribe():
    body = request.get_json(silent=True) or {}
    audio_path, audio_error = _materialize_audio_payload(body)
    if audio_error or audio_path is None:
        return jsonify({"ok": False, "error": audio_error or "missing_audio_input"}), 400
    normalized_audio_path: Optional[Path] = None
    normalized_created = False
    try:
        normalized_audio_path, normalize_error, normalized_created = _normalize_audio_for_stt(audio_path)
        if normalize_error or normalized_audio_path is None:
            return jsonify({"ok": False, "error": normalize_error or "audio_normalization_failed"}), 400
        if STT_USES_HAILO:
            with HAILO_DEVICE_LOCK:
                result = _stt_transcribe(
                    normalized_audio_path,
                    prompt=str(body.get("prompt") or "").strip(),
                    language=str(body.get("language") or "").strip(),
                    mock_text=str(body.get("mock_text") or "").strip(),
                )
        else:
            result = _stt_transcribe(
                normalized_audio_path,
                prompt=str(body.get("prompt") or "").strip(),
                language=str(body.get("language") or "").strip(),
                mock_text=str(body.get("mock_text") or "").strip(),
            )
    finally:
        try:
            if audio_path.parent == Path(tempfile.gettempdir()):
                audio_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if normalized_created and normalized_audio_path and normalized_audio_path.parent == Path(tempfile.gettempdir()):
                normalized_audio_path.unlink(missing_ok=True)
        except Exception:
            pass
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.post("/api/admin/voice/command")
@need_login
def api_admin_voice_command():
    body = request.get_json(silent=True) or {}
    execute_live = bool(body.get("execute_live"))
    result = _voice_command_from_request(body, execute_live=execute_live, sender={"source": "admin-voice"})
    status = 200 if result.get("ok") else 503 if execute_live else 400
    return jsonify(result), status


@APP.post("/api/admin/telegram/dispatch")
@need_login
def api_admin_telegram_dispatch():
    body = request.get_json(silent=True) or {}
    result = _telegram_ingest(
        str(body.get("text") or "").strip(),
        robot_id=str(body.get("robot_id") or "").strip(),
        mode=str(body.get("mode") or "test").strip(),
        sender={"source": "admin", "display_name": str(body.get("display_name") or "admin").strip()},
    )
    return jsonify(result), (200 if result.get("ok") else 503 if str(result.get("mode") or "") == "live" else 400)


@APP.get("/api/brain/catalog")
def api_brain_catalog():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    payload = robot_catalog_payload()
    payload["registry_file"] = str(ROBOT_REGISTRY_FILE)
    return jsonify(payload)


@APP.get("/api/brain/robots")
def api_brain_robots():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    return jsonify({"ok": True, "robots": _robot_registry()})


@APP.post("/api/brain/parse")
def api_brain_parse():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    body = request.get_json(silent=True) or {}
    result = _parse_robot_text_request(
        str(body.get("text") or "").strip(),
        preferred_robot_id=str(body.get("robot_id") or "").strip(),
        use_llm=bool(body.get("use_llm", True)),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@APP.post("/api/brain/execute")
def api_brain_execute():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    body = request.get_json(silent=True) or {}
    parsed = body.get("parsed") if isinstance(body.get("parsed"), dict) else None
    if parsed is None:
        parsed = _parse_robot_text_request(
            str(body.get("text") or "").strip(),
            preferred_robot_id=str(body.get("robot_id") or "").strip(),
            use_llm=bool(body.get("use_llm", True)),
        )
    if not parsed.get("ok"):
        return jsonify(parsed), 400
    result = _execute_robot_intent(parsed)
    return jsonify(result), (200 if result.get("ok") else 503)


@APP.post("/api/brain/voice/command")
def api_brain_voice_command():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    body = request.get_json(silent=True) or {}
    result = _voice_command_from_request(
        body,
        execute_live=bool(body.get("execute_live")),
        sender={
            "source": "api-voice",
            "device_id": str(body.get("device_id") or "").strip(),
            "display_name": str(body.get("display_name") or "").strip(),
        },
    )
    status = 200 if result.get("ok") else 503 if bool(body.get("execute_live")) else 400
    return jsonify(result), status


@APP.post("/api/brain/telegram/ingest")
def api_brain_telegram_ingest():
    if not _api_token_ok(request):
        return jsonify({"ok": False, "error": "invalid_api_token"}), 401
    body = request.get_json(silent=True) or {}
    result = _telegram_ingest(
        str(body.get("text") or "").strip(),
        robot_id=str(body.get("robot_id") or "").strip(),
        mode=str(body.get("mode") or TELEGRAM_EXECUTION_MODE).strip(),
        sender={
            "source": "telegram",
            "chat_id": body.get("chat_id"),
            "display_name": str(body.get("display_name") or "").strip(),
            "username": str(body.get("username") or "").strip(),
        },
    )
    return jsonify(result), (200 if result.get("ok") else 503 if str(result.get("mode") or "") == "live" else 400)


@APP.post("/api/brain/slack/events")
def api_brain_slack_events():
    if not SLACK_SIGNING_SECRET:
        return jsonify({"ok": False, "error": "slack_not_configured"}), 503
    if not _slack_signature_ok(request):
        return jsonify({"ok": False, "error": "invalid_slack_signature"}), 401
    body = request.get_json(silent=True) or {}
    if str(body.get("type") or "") == "url_verification":
        return jsonify({"challenge": str(body.get("challenge") or "")})
    if str(body.get("type") or "") != "event_callback":
        return jsonify({"ok": True, "ignored": True})
    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    thread = threading.Thread(target=_process_slack_event, args=(event,), daemon=True)
    thread.start()
    return jsonify({"ok": True})


@APP.post("/api/admin/update-restart")
@need_login
def api_admin_update_restart():
    repo_dir = APP_REPO_DIR
    if not (repo_dir / ".git").exists():
        return jsonify({"ok": False, "error": "repo_not_found", "repo_dir": str(repo_dir)}), 400
    pull = _git_pull_ff_only(repo_dir)
    if not pull.get("ok"):
        return jsonify({"ok": False, "step": "git_pull", **pull}), 400
    post_update = _start_post_update_tasks_detached(repo_dir, APP_SERVICE_NAME)
    return jsonify({
        "ok": bool(post_update.get("ok")),
        "step": "post_update_queued" if post_update.get("ok") else "post_update_failed",
        "repo_dir": str(repo_dir),
        "service": APP_SERVICE_NAME,
        "git": pull,
        "post_update": post_update,
        "hint": f"If restart fails, allow this user to run: sudo -n systemctl restart {APP_SERVICE_NAME}",
    }), (200 if post_update.get("ok") else 500)


@APP.get("/api/admin/update-tests/status")
@need_login
def api_admin_update_tests_status():
    rc = -1
    try:
        rc = int((UPDATE_TEST_RC_PATH.read_text() or "-1").strip())
    except Exception:
        rc = -1

    started = UPDATE_TEST_RC_PATH.exists() or UPDATE_TEST_LOG_PATH.exists()
    full_log = ""
    try:
        full_log = UPDATE_TEST_LOG_PATH.read_text(errors="ignore")
    except Exception:
        full_log = ""

    restart_rc = None
    for ln in reversed(full_log.splitlines()):
        if ln.startswith("restart_rc="):
            try:
                restart_rc = int((ln.split("=", 1)[1] or "").strip())
            except Exception:
                restart_rc = None
            break

    return jsonify({
        "ok": True,
        "started": started,
        "running": started and rc == -1,
        "done": started and rc != -1,
        "returncode": None if rc == -1 else rc,
        "restart_returncode": restart_rc,
        "log_tail": _tail_text(UPDATE_TEST_LOG_PATH, max_lines=120),
    })


@APP.post("/api/admin/tests/run")
@need_login
def api_admin_tests_run():
    repo_dir = APP_REPO_DIR
    if not (repo_dir / ".git").exists():
        return jsonify({"ok": False, "error": "repo_not_found", "repo_dir": str(repo_dir)}), 400
    run = _start_tests_only_detached(repo_dir)
    return jsonify({
        "ok": bool(run.get("ok")),
        "step": "tests_queued" if run.get("ok") else "tests_failed_to_queue",
        "repo_dir": str(repo_dir),
        "tests": run,
    }), (200 if run.get("ok") else 500)


if __name__ == "__main__":
    APP.run(host="0.0.0.0", port=APP_PORT, debug=False)
