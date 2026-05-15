import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


BACKEND_MODE = (os.environ.get("STT_BACKEND_MODE", "mock").strip() or "mock").lower()
COMMAND_TEMPLATE = (os.environ.get("STT_COMMAND_TEMPLATE") or "").strip()
DEFAULT_LANGUAGE = (os.environ.get("STT_DEFAULT_LANGUAGE", "en").strip() or "en")
DEFAULT_MOCK_TRANSCRIPT = (os.environ.get("STT_MOCK_TRANSCRIPT") or "").strip()
TIMEOUT_S = float(os.environ.get("STT_TRANSCRIBE_TIMEOUT", "90").strip() or "90")


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def _mock_transcribe(audio_path: Path, payload: dict) -> dict:
    mock_text = str(payload.get("mock_text") or "").strip()
    if not mock_text:
        mock_text = DEFAULT_MOCK_TRANSCRIPT
    if not mock_text:
        stem = audio_path.stem.replace("_", " ").replace("-", " ").strip()
        mock_text = stem or "say hello"
    return {
        "ok": True,
        "text": mock_text,
        "language": str(payload.get("language") or DEFAULT_LANGUAGE).strip(),
        "backend_mode": "mock",
    }


def _command_transcribe(audio_path: Path, payload: dict) -> dict:
    if not COMMAND_TEMPLATE:
        return {"ok": False, "error": "missing_stt_command_template", "backend_mode": "command"}

    command = COMMAND_TEMPLATE.format(
        audio_path=shlex.quote(str(audio_path)),
        language=shlex.quote(str(payload.get("language") or DEFAULT_LANGUAGE).strip()),
        prompt=shlex.quote(str(payload.get("prompt") or "").strip()),
    )
    proc = subprocess.run(
        ["sh", "-lc", command],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "stt_command_failed",
            "backend_mode": "command",
            "cmd": command,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    try:
        parsed = json.loads(stdout) if stdout else {}
        if isinstance(parsed, dict) and parsed.get("text"):
            parsed.setdefault("ok", True)
            parsed.setdefault("backend_mode", "command")
            return parsed
    except Exception:
        pass
    return {
        "ok": bool(stdout),
        "text": stdout,
        "language": str(payload.get("language") or DEFAULT_LANGUAGE).strip(),
        "backend_mode": "command",
        "stderr": stderr,
    }


def _transcribe_payload(payload: dict) -> dict:
    audio_path = Path(str(payload.get("audio_path") or "").strip()).expanduser()
    if not audio_path.exists():
        return {"ok": False, "error": "audio_path_not_found"}
    if BACKEND_MODE == "command":
        return _command_transcribe(audio_path, payload)
    return _mock_transcribe(audio_path, payload)


def serve() -> None:
    """Persistent serve mode: read one JSON request per stdin line, write one JSON response per stdout line.

    This keeps the process (and any loaded model) warm between requests, eliminating
    per-request process-startup and model-load overhead when used with the Hailo STT backend.
    """
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except Exception:
            sys.stdout.write(json.dumps({"ok": False, "error": "invalid_json"}) + "\n")
            sys.stdout.flush()
            continue
        result = _transcribe_payload(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        _emit({"ok": False, "error": "invalid_json"})
        return 1

    result = _transcribe_payload(payload)
    _emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        raise SystemExit(main())
