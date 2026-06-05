"""Hailo YOLO object detection backend.

This script is invoked by app_yolo.py and runs in one of three modes:

  mock    — returns configurable fake detections; no hardware needed.
             Good for development and CI.

  direct  — imports hailo_platform + hailo_apps Python APIs and runs
             YOLOv11 inference directly in-process.  Keeps the Hailo
             device warm between calls when used in --serve mode.

  command — shells out to a configurable command template, collecting
             JSON output.  Use this for custom hailo-apps scripts or any
             external inference runner.

Usage (called by app_yolo.py):
  python3 hailo_yolo_backend.py --serve      # persistent stdin/stdout loop
  python3 hailo_yolo_backend.py              # one-shot, reads from stdin

Payload (JSON, one line in --serve mode):
  {
    "image_path":          "/tmp/frame.jpg",
    "image_base64":        "<base64>",          # alternative to image_path
    "image_mime_type":     "image/jpeg",
    "model":               "yolov11s",          # default from HAILO_YOLO_MODEL
    "confidence_threshold": 0.5,
    "max_detections":      20
  }

Response:
  {
    "ok": true,
    "model": "yolov11s",
    "detections": [
      {"class": "person", "class_id": 0, "confidence": 0.92, "bbox": [x1, y1, x2, y2]}
    ],
    "count": 1
  }
"""

import base64
import json
import os
import random
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Configuration (read from env so app_yolo.py's env file applies here too)
# ---------------------------------------------------------------------------

BACKEND_MODE: str = (os.environ.get("HAILO_YOLO_BACKEND_MODE") or "mock").strip().lower()
DEFAULT_MODEL: str = (os.environ.get("HAILO_YOLO_MODEL") or "yolov11s").strip()
HAILO_YOLO_APP_DIR: str = (os.environ.get("HAILO_YOLO_APP_DIR") or "").strip()
HAILO_YOLO_HEF_PATH: str = (os.environ.get("HAILO_YOLO_HEF_PATH") or "").strip()
COMMAND_TEMPLATE: str = (os.environ.get("HAILO_YOLO_COMMAND_TEMPLATE") or "").strip()
TIMEOUT_S: float = float(os.environ.get("HAILO_YOLO_TIMEOUT", "60").strip() or "60")

# JSON list of detections used by mock mode when set.
# Example: '[{"class":"person","class_id":0,"confidence":0.9,"bbox":[10,10,200,400]}]'
MOCK_DETECTIONS_JSON: str = (os.environ.get("HAILO_YOLO_MOCK_DETECTIONS") or "").strip()


# ---------------------------------------------------------------------------
# COCO class names (80 standard classes)
# ---------------------------------------------------------------------------

COCO_CLASSES: List[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def _mock_detections(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return deterministic or configured fake detections.

    Coordinates are expressed as fractions of the image size so they look
    correct on any uploaded image.  If the caller passes image_width and
    image_height we convert to absolute pixel coords; otherwise we fall back
    to absolute coords sized for a 640×640 frame.
    """
    if MOCK_DETECTIONS_JSON:
        try:
            return json.loads(MOCK_DETECTIONS_JSON)
        except Exception:
            pass

    threshold = float(payload.get("confidence_threshold") or 0.5)
    max_det   = int(payload.get("max_detections") or 20)
    img_w     = int(payload.get("image_width")  or 640)
    img_h     = int(payload.get("image_height") or 640)

    # Normalised bbox coords (x1, y1, x2, y2) as fractions of image size.
    # These look reasonable regardless of the image dimensions.
    candidates_norm = [
        {"class": "person",    "class_id":  0, "confidence": 0.92, "bbox_n": [0.05, 0.04, 0.44, 0.95]},
        {"class": "chair",     "class_id": 56, "confidence": 0.84, "bbox_n": [0.48, 0.30, 0.82, 0.97]},
        {"class": "laptop",    "class_id": 63, "confidence": 0.77, "bbox_n": [0.22, 0.52, 0.49, 0.75]},
        {"class": "cell phone","class_id": 67, "confidence": 0.61, "bbox_n": [0.31, 0.62, 0.41, 0.79]},
        {"class": "dog",       "class_id": 16, "confidence": 0.53, "bbox_n": [0.59, 0.12, 0.94, 0.83]},
    ]

    result = []
    for c in candidates_norm:
        if c["confidence"] < threshold:
            continue
        x1n, y1n, x2n, y2n = c["bbox_n"]
        result.append({
            "class":      c["class"],
            "class_id":   c["class_id"],
            "confidence": c["confidence"],
            "bbox": [
                int(x1n * img_w), int(y1n * img_h),
                int(x2n * img_w), int(y2n * img_h),
            ],
        })

    rng = random.Random(str(payload.get("image_path") or "") or "mock")
    rng.shuffle(result)
    return result[:max_det]


# ---------------------------------------------------------------------------
# Command mode
# ---------------------------------------------------------------------------

def _command_detect(image_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not COMMAND_TEMPLATE:
        return {
            "ok": False,
            "error": "missing_hailo_yolo_command_template",
            "hint": "Set HAILO_YOLO_COMMAND_TEMPLATE in your .env file.",
        }

    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    threshold = float(payload.get("confidence_threshold") or 0.5)
    max_det = int(payload.get("max_detections") or 20)

    command = COMMAND_TEMPLATE.format(
        image_path=shlex.quote(image_path),
        model=shlex.quote(model),
        threshold=threshold,
        max_detections=max_det,
    )

    app_dir = HAILO_YOLO_APP_DIR or None
    proc = subprocess.run(
        ["sh", "-lc", command],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
        cwd=app_dir,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "yolo_command_failed",
            "returncode": proc.returncode,
            "stderr": stderr,
            "stdout": stdout,
        }

    # Try to parse JSON output from the command.
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, list):
            return {"ok": True, "detections": parsed, "model": model}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", True)
            return parsed
    except Exception:
        pass

    return {"ok": False, "error": "yolo_command_no_json", "stdout": stdout, "stderr": stderr}


# ---------------------------------------------------------------------------
# Direct mode (hailo_platform Python API)
# ---------------------------------------------------------------------------

_DIRECT_CONTEXT: Optional[Dict[str, Any]] = None


def _init_direct_context(model: str) -> Dict[str, Any]:
    """Load the YOLO HEF and configure the Hailo device.

    This is called once; subsequent calls reuse the loaded network group.
    """
    if HAILO_YOLO_APP_DIR:
        root = Path(HAILO_YOLO_APP_DIR).expanduser().resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

    from hailo_platform import (  # type: ignore[import]
        HEF,
        VDevice,
        ConfigureParams,
        InferVStreams,
        InputVStreamParams,
        OutputVStreamParams,
        FormatType,
    )
    from hailo_apps.python.core.common.defines import (  # type: ignore[import]
        HAILO10H_ARCH,
        SHARED_VDEVICE_GROUP_ID,
    )
    from hailo_apps.python.core.common.core import resolve_hef_path  # type: ignore[import]

    hef_path = HAILO_YOLO_HEF_PATH or resolve_hef_path(None, app_name=model, arch=HAILO10H_ARCH)
    if not hef_path or not Path(str(hef_path)).exists():
        raise RuntimeError(f"YOLO HEF not found for model={model}. Set HAILO_YOLO_HEF_PATH.")

    params = VDevice.create_params()
    params.group_id = SHARED_VDEVICE_GROUP_ID
    vdevice = VDevice(params)
    hef = HEF(str(hef_path))
    configure_params = ConfigureParams.create_from_hef(hef, interface=None)
    network_groups = vdevice.configure(hef, configure_params)
    network_group = network_groups[0]

    input_params = InputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)
    output_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)

    return {
        "vdevice": vdevice,
        "network_group": network_group,
        "input_params": input_params,
        "output_params": output_params,
        "hef": hef,
        "model": model,
    }


def _direct_detect(image_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run YOLO inference directly via hailo_platform."""
    global _DIRECT_CONTEXT

    import numpy as np  # type: ignore[import]
    import cv2  # type: ignore[import]

    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    threshold = float(payload.get("confidence_threshold") or 0.5)
    max_det = int(payload.get("max_detections") or 20)

    try:
        if _DIRECT_CONTEXT is None or _DIRECT_CONTEXT.get("model") != model:
            _DIRECT_CONTEXT = _init_direct_context(model)
    except Exception as exc:
        return {"ok": False, "error": f"hailo_init_failed: {exc}"}

    ctx = _DIRECT_CONTEXT
    image = cv2.imread(image_path)
    if image is None:
        return {"ok": False, "error": "image_load_failed", "image_path": image_path}

    # YOLO on Hailo expects 640x640 RGB float32 normalised to [0, 1].
    input_h, input_w = 640, 640
    resized = cv2.resize(image, (input_w, input_h))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    tensor = (rgb.astype(np.float32) / 255.0)[np.newaxis]  # (1, 640, 640, 3)

    from hailo_platform import InferVStreams  # type: ignore[import]

    ng = ctx["network_group"]
    ng_params = ng.create_params()

    try:
        with InferVStreams(ng, ctx["input_params"], ctx["output_params"]) as pipeline:
            with ng.activate(ng_params):
                input_dict = {list(pipeline.get_input_vstream_infos())[0].name: tensor}
                raw_output = pipeline.infer(input_dict)
    except Exception as exc:
        return {"ok": False, "error": f"hailo_infer_failed: {exc}"}

    # Post-process YOLO output.  Hailo YOLO outputs decoded boxes directly:
    # each row is [y1_norm, x1_norm, y2_norm, x2_norm, class_id, confidence].
    orig_h, orig_w = image.shape[:2]
    detections: List[Dict[str, Any]] = []

    for output_tensor in raw_output.values():
        arr = np.squeeze(output_tensor)
        if arr.ndim == 1:
            arr = arr[np.newaxis]
        for row in arr:
            if len(row) < 6:
                continue
            y1n, x1n, y2n, x2n = float(row[0]), float(row[1]), float(row[2]), float(row[3])
            class_id = int(row[4])
            confidence = float(row[5])
            if confidence < threshold:
                continue
            class_name = COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else f"class_{class_id}"
            detections.append({
                "class": class_name,
                "class_id": class_id,
                "confidence": round(confidence, 4),
                "bbox": [
                    int(x1n * orig_w), int(y1n * orig_h),
                    int(x2n * orig_w), int(y2n * orig_h),
                ],
            })

    detections.sort(key=lambda d: d["confidence"], reverse=True)
    return {"ok": True, "model": model, "detections": detections[:max_det]}


def _release_direct_context() -> None:
    global _DIRECT_CONTEXT
    if _DIRECT_CONTEXT is None:
        return
    try:
        vdevice = _DIRECT_CONTEXT.get("vdevice")
        if vdevice is not None:
            vdevice.release()
    except Exception:
        pass
    _DIRECT_CONTEXT = None


# ---------------------------------------------------------------------------
# Shared entry point
# ---------------------------------------------------------------------------

def _decode_base64_image(payload: Dict[str, Any]) -> Optional[str]:
    """Materialise a base64 image to a temp file, return path (caller cleans up)."""
    b64 = str(payload.get("image_base64") or "").strip()
    if not b64:
        return None
    mime = str(payload.get("image_mime_type") or "image/jpeg").lower()
    ext_map = {
        "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/png": ".png", "image/webp": ".webp",
        "image/heic": ".heic", "image/heif": ".heic",
    }
    suffix = ext_map.get(mime, ".jpg")
    blob = base64.b64decode(b64)
    fd, path = tempfile.mkstemp(prefix="yolo-", suffix=suffix)
    try:
        import os as _os
        with _os.fdopen(fd, "wb") as fh:
            fh.write(blob)
    except Exception:
        return None
    return path


def _run_detection(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a single detection request to the configured backend."""
    model = str(payload.get("model") or DEFAULT_MODEL).strip()
    image_path = str(payload.get("image_path") or "").strip()
    temp_path: Optional[str] = None

    # If a base64 image was provided, materialise it.
    if not image_path and payload.get("image_base64"):
        temp_path = _decode_base64_image(payload)
        if not temp_path:
            return {"ok": False, "error": "base64_decode_failed"}
        image_path = temp_path

    try:
        if BACKEND_MODE == "mock":
            detections = _mock_detections(payload)
            return {
                "ok": True,
                "model": model,
                "detections": detections,
                "count": len(detections),
                "backend_mode": "mock",
            }

        if not image_path or not Path(image_path).exists():
            return {"ok": False, "error": "missing_or_invalid_image_path", "image_path": image_path}

        if BACKEND_MODE == "direct":
            result = _direct_detect(image_path, payload)
        else:
            result = _command_detect(image_path, payload)

        if result.get("ok"):
            result["count"] = len(result.get("detections") or [])
            result.setdefault("model", model)
            result["backend_mode"] = BACKEND_MODE
        return result

    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Serve loop (persistent mode used by app_yolo.py)
# ---------------------------------------------------------------------------

def serve() -> None:
    """Read one JSON payload per stdin line, write one JSON response per stdout line."""
    try:
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
            result = _run_detection(payload)
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        _release_direct_context()


def main() -> int:
    if "--serve" in sys.argv:
        serve()
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        sys.stdout.write(json.dumps({"ok": False, "error": "invalid_json"}) + "\n")
        return 1
    result = _run_detection(payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
