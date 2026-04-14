import json
import os
import secrets
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from robot_brain import (
    EXECUTABLE_ACTIONS,
    TEST_ROBOT_BASE_URL,
    TEST_ROBOT_ID,
    build_llm_parser_prompt,
    extract_json_object,
    load_robot_registry,
    normalize_llm_intent,
    parse_text_command,
    robot_catalog_payload,
)

APP_DIR = Path(__file__).resolve().parent
APP = Flask(__name__, static_folder="static", template_folder="templates")
APP.secret_key = os.environ.get("FLASK_SECRET", "robot-console-ai-local-only")


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
ROBOT_REGISTRY_FILE = Path(
    os.environ.get("ROBOT_REGISTRY_FILE", "/opt/robot/robot-console/robots.json")
).expanduser()
ROBOT_TEXT_COMMAND_MODEL = (
    os.environ.get("ROBOT_TEXT_COMMAND_MODEL", "qwen2:1.5b").strip() or "qwen2:1.5b"
)
ROBOT_BRAIN_API_TOKEN = (os.environ.get("ROBOT_BRAIN_API_TOKEN") or "").strip()
TELEGRAM_EXECUTION_MODE = (os.environ.get("TELEGRAM_EXECUTION_MODE", "live").strip() or "live").lower()
PASS_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
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
    rule_result = parse_text_command(text, robots, preferred_robot_id=preferred_robot_id)
    if rule_result.get("intent", {}).get("action") != "unknown":
        rule_result["available_robots"] = robots
        return rule_result
    if not use_llm:
        rule_result["available_robots"] = robots
        return rule_result
    mode = _hailo_mode_status()
    if mode.get("active_mode") not in {"llm", "shared"}:
        rule_result["available_robots"] = robots
        rule_result["hint"] = "Switch Hailo mode to LLM for richer text-to-command parsing."
        return rule_result
    llm_result = _parse_robot_text_with_llm(text, robots, preferred_robot_id=preferred_robot_id)
    llm_result["available_robots"] = robots
    return llm_result


def _execute_robot_intent(parsed: Dict[str, Any]) -> Dict[str, Any]:
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
        if str(robot.get("id") or "") == TEST_ROBOT_ID or bool(robot.get("test_mode")):
            preview_payload: Dict[str, Any] | None = None
            preview_path = ""
            preview_method = "POST"
            if action == "say":
                preview_path = "/api/cmd/say"
                preview_payload = {"text": str(args.get("text") or "").strip()}
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
            result = _robot_request(robot, "POST", "/api/cmd/say", {"text": str(args.get("text") or "").strip()}, timeout=20.0)
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

    return {
        "ok": all(bool(item.get("ok")) for item in results),
        "parsed": parsed,
        "results": results,
        "target_count": len(results),
    }


def _telegram_ingest(text: str, robot_id: str = "", mode: str = "test", sender: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    chosen_mode = (mode or "test").strip().lower()
    if chosen_mode not in {"test", "live"}:
        return {"ok": False, "error": "invalid_mode", "mode": chosen_mode}
    parsed = _parse_robot_text_request(text, preferred_robot_id=robot_id, use_llm=True)
    if not parsed.get("ok"):
        return {"ok": False, "mode": chosen_mode, "sender": sender or {}, "parsed": parsed}
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
        return payload
    execution = _execute_robot_intent(parsed)
    payload["execution"] = execution
    payload["ok"] = bool(execution.get("ok"))
    return payload


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
