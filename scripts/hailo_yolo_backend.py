"""Hailo YOLO object detection backend.

This script is invoked by app_yolo.py and runs in one of three modes:

  mock    — returns configurable fake detections; no hardware needed.
             Good for development and CI.

  direct  — uses hailo_apps' HailoInfer (async InferModel API, required on
             Hailo-10H) plus its object_detection_post_process decoder to
             run inference in-process.  Keeps the Hailo device warm between
             calls when used in --serve mode.  Only decodes HEF-baked-NMS
             output (yolov8/yolov11-style models); YOLO26 needs a different
             decoder and isn't wired up yet.

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
    """Load the YOLO HEF via hailo_apps' HailoInfer wrapper.

    Hailo-10H only supports the async InferModel/ConfiguredInferModel API
    (the older synchronous HEF + ConfigureParams + vdevice.configure() flow
    used by earlier versions of this file is Hailo-8-only and raises
    HAILO_NOT_IMPLEMENTED on Hailo-10H). HailoInfer wraps that async API and
    already sets scheduling_algorithm=ROUND_ROBIN + a shared group id so this
    can coexist with the VLM backend, which uses the same wrapper pattern.

    This is called once; subsequent calls reuse the loaded model unless a
    different model is requested.
    """
    if HAILO_YOLO_APP_DIR:
        root = Path(HAILO_YOLO_APP_DIR).expanduser().resolve()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

    from hailo_apps.python.core.common.hailo_inference import HailoInfer  # type: ignore[import]
    from hailo_apps.python.core.common.defines import HAILO10H_ARCH  # type: ignore[import]
    from hailo_apps.python.core.common.core import resolve_hef_path  # type: ignore[import]

    hef_path = HAILO_YOLO_HEF_PATH or resolve_hef_path(None, app_name=model, arch=HAILO10H_ARCH)
    if not hef_path or not Path(str(hef_path)).exists():
        raise RuntimeError(f"YOLO HEF not found for model={model}. Set HAILO_YOLO_HEF_PATH.")

    hailo_inference = HailoInfer(str(hef_path), batch_size=1)
    height, width, _channels = hailo_inference.get_input_shape()

    return {
        "hailo_inference": hailo_inference,
        "height": int(height),
        "width": int(width),
        "model": model,
    }


def _direct_detect(image_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run YOLO inference via HailoInfer (the async API Hailo-10H requires).

    NOTE: this uses hailo_apps' object_detection_post_process.extract_detections,
    which decodes the HEF-baked-NMS output format used by yolov8/yolov11-style
    models (matches HAILO_YOLO_MODEL=yolov8m). YOLO26 is NMS-free and needs a
    different decoder (hailo_apps' yolo26/object_detection ONNX postproc) —
    not yet wired up here.
    """
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

    if HAILO_YOLO_APP_DIR:
        obj_det_dir = (
            Path(HAILO_YOLO_APP_DIR).expanduser().resolve()
            / "hailo_apps" / "python" / "standalone_apps" / "object_detection"
        )
        if str(obj_det_dir) not in sys.path:
            sys.path.insert(0, str(obj_det_dir))
    try:
        from object_detection_post_process import extract_detections  # type: ignore[import]
    except Exception as exc:
        return {"ok": False, "error": f"postprocess_import_failed: {exc}"}

    input_h, input_w = ctx["height"], ctx["width"]
    orig_h, orig_w = image.shape[:2]

    # Letterbox to a square before resizing to the model's input shape — this
    # matches extract_detections/denormalize_and_rm_pad's assumption about how
    # normalised boxes map back to original image coordinates.
    size = max(orig_h, orig_w)
    padding_length = abs(orig_h - orig_w) // 2
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    if orig_h <= orig_w:
        canvas[padding_length:padding_length + orig_h, :, :] = image
    else:
        canvas[:, padding_length:padding_length + orig_w, :] = image
    resized = cv2.resize(canvas, (input_w, input_h), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.uint8)

    result_holder: Dict[str, Any] = {}

    def _on_done(completion_info, bindings_list, _holder=result_holder) -> None:
        if completion_info.exception:
            _holder["error"] = str(completion_info.exception)
            return
        bindings = bindings_list[0]
        if len(bindings._output_names) == 1:
            _holder["output"] = bindings.output().get_buffer()
        else:
            _holder["output"] = {
                name: bindings.output(name).get_buffer() for name in bindings._output_names
            }

    try:
        job = ctx["hailo_inference"].run([rgb], _on_done)
        job.wait(int(TIMEOUT_S * 1000))
    except Exception as exc:
        return {"ok": False, "error": f"hailo_infer_failed: {exc}"}

    if "error" in result_holder:
        return {"ok": False, "error": f"hailo_infer_failed: {result_holder['error']}"}
    if "output" not in result_holder:
        return {"ok": False, "error": "hailo_infer_timeout"}

    raw_output = result_holder["output"]
    detections_by_class = raw_output if isinstance(raw_output, list) else list(raw_output.values())[0]

    config_data = {"visualization_params": {"score_thres": threshold, "max_boxes_to_draw": max_det}}
    try:
        extracted = extract_detections(image, detections_by_class, config_data)
    except Exception as exc:
        return {"ok": False, "error": f"postprocess_failed: {exc}"}

    detections: List[Dict[str, Any]] = []
    for box, class_id, score in zip(
        extracted["detection_boxes"], extracted["detection_classes"], extracted["detection_scores"]
    ):
        class_id = int(class_id)
        class_name = COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else f"class_{class_id}"
        x1, y1, x2, y2 = [int(v) for v in box]
        detections.append({
            "class": class_name,
            "class_id": class_id,
            "confidence": round(float(score), 4),
            "bbox": [x1, y1, x2, y2],
        })

    return {"ok": True, "model": model, "detections": detections}


def _release_direct_context() -> None:
    global _DIRECT_CONTEXT
    if _DIRECT_CONTEXT is None:
        return
    try:
        hailo_inference = _DIRECT_CONTEXT.get("hailo_inference")
        if hailo_inference is not None:
            hailo_inference.close()
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
