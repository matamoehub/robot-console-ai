import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def _quoted(value: Any) -> str:
    return shlex.quote(str(value or ""))


def _run_direct(payload: Dict[str, Any]) -> int:
    app_dir = (os.environ.get("HAILO_VLM_APP_DIR") or "").strip()
    if app_dir:
        root = Path(app_dir).expanduser().resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

    import cv2
    import numpy as np
    from hailo_platform import VDevice
    from hailo_platform.genai import VLM
    from hailo_apps.python.core.common.defines import HAILO10H_ARCH, SHARED_VDEVICE_GROUP_ID, VLM_CHAT_APP
    from hailo_apps.python.core.common.core import resolve_hef_path

    hef_path = resolve_hef_path(None, app_name=VLM_CHAT_APP, arch=HAILO10H_ARCH)
    if hef_path is None:
        print(json.dumps({"ok": False, "error": "hef_not_found"}), flush=True)
        return 2

    image_path = str(payload.get("image_path") or "").strip()
    if not image_path:
        print(json.dumps({"ok": False, "error": "missing_image_path"}), flush=True)
        return 2

    image = cv2.imread(image_path)
    if image is None:
        print(json.dumps({"ok": False, "error": "image_load_failed", "image_path": image_path}), flush=True)
        return 2

    if len(image.shape) == 3 and image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (336, 336), interpolation=cv2.INTER_LINEAR).astype(np.uint8)

    prompt_text = str(payload.get("prompt") or "").strip() or "Describe this image."
    prompt = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "You are a helpful assistant that analyzes images and answers questions about them.",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt_text},
            ],
        },
    ]

    global _DIRECT_CONTEXT
    if _DIRECT_CONTEXT is None:
        params = VDevice.create_params()
        params.group_id = SHARED_VDEVICE_GROUP_ID
        vdevice = VDevice(params)
        vlm = VLM(vdevice, str(hef_path))
        _DIRECT_CONTEXT = {"vdevice": vdevice, "vlm": vlm}
    vdevice = _DIRECT_CONTEXT["vdevice"]
    vlm = _DIRECT_CONTEXT["vlm"]
    try:
        try:
            vlm.clear_context()
        except Exception:
            pass
        response = vlm.generate_all(
            prompt=prompt,
            frames=[image],
            temperature=0.1,
            seed=42,
            max_generated_tokens=int(payload.get("max_tokens") or 200),
        )
        text = str(response or "")
        if "[{'type'" in text:
            text = text.split("[{'type'")[0]
        if "<|im_end|>" in text:
            text = text.split("<|im_end|>")[0]
        try:
            vlm.clear_context()
        except Exception:
            pass
        print(json.dumps({"ok": True, "text": text.strip()}), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), flush=True)
        return 1


def _release_direct_context() -> None:
    global _DIRECT_CONTEXT
    if _DIRECT_CONTEXT is None:
        return
    vlm = _DIRECT_CONTEXT.get("vlm")
    vdevice = _DIRECT_CONTEXT.get("vdevice")
    try:
        if vlm:
            vlm.release()
    except Exception:
        pass
    try:
        if vdevice:
            vdevice.release()
    except Exception:
        pass
    _DIRECT_CONTEXT = None


def _serve_forever() -> int:
    try:
        for raw_line in sys.stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            payload: Dict[str, Any] = json.loads(raw_line)
            mode = (os.environ.get("HAILO_VLM_BACKEND_MODE") or "direct").strip().lower()
            if mode == "direct":
                rc = _run_direct(payload)
                if rc != 0:
                    # _run_direct already printed a JSON error line.
                    continue
            else:
                template = (os.environ.get("HAILO_VLM_COMMAND_TEMPLATE") or "").strip()
                if not template:
                    print(json.dumps({"ok": False, "error": "missing_hailo_vlm_command_template"}), flush=True)
                    continue
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
                stdout = (proc.stdout or "").strip()
                if proc.returncode != 0:
                    print(
                        json.dumps({
                            "ok": False,
                            "error": "backend_failed",
                            "returncode": proc.returncode,
                            "stderr": (proc.stderr or "").strip(),
                            "stdout": stdout,
                        }),
                        flush=True,
                    )
                    continue
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    parsed = {"ok": True, "text": stdout}
                if "ok" not in parsed:
                    parsed["ok"] = True
                print(json.dumps(parsed), flush=True)
    finally:
        _release_direct_context()
    return 0


def main() -> int:
    global _DIRECT_CONTEXT
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        return _serve_forever()
    raw = sys.stdin.read()
    payload: Dict[str, Any] = json.loads(raw or "{}")
    mode = (os.environ.get("HAILO_VLM_BACKEND_MODE") or "direct").strip().lower()
    if mode == "direct":
        return _run_direct(payload)
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


_DIRECT_CONTEXT = None


if __name__ == "__main__":
    raise SystemExit(main())
