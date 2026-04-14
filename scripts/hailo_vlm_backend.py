import json
import os
import shlex
import subprocess
import sys
from typing import Any, Dict


def _quoted(value: Any) -> str:
    return shlex.quote(str(value or ""))


def main() -> int:
    raw = sys.stdin.read()
    payload: Dict[str, Any] = json.loads(raw or "{}")
    template = (os.environ.get("HAILO_VLM_COMMAND_TEMPLATE") or "").strip()
    if not template:
        print(json.dumps({"error": "missing_hailo_vlm_command_template"}))
        return 2

    command = template.format(
        prompt=_quoted(payload.get("prompt")),
        image_path=_quoted(payload.get("image_path")),
        model=_quoted(payload.get("model")),
        max_tokens=_quoted(payload.get("max_tokens")),
        image_base64=_quoted(payload.get("image_base64")),
        image_mime_type=_quoted(payload.get("image_mime_type")),
    )
    app_dir = (os.environ.get("HAILO_VLM_APP_DIR") or "").strip() or None
    proc = subprocess.run(
        ["sh", "-lc", command],
        capture_output=True,
        text=True,
        check=False,
        cwd=app_dir,
    )
    sys.stdout.write(proc.stdout or "")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
