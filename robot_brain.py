import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


MASTER_MODES = [
    {
        "id": "lesson",
        "label": "Lesson",
        "summary": "Default teaching mode with lessons and Jupyter ready.",
    },
    {
        "id": "remote_joystick",
        "label": "Remote Control",
        "summary": "Joystick and manual motion control mode.",
    },
    {
        "id": "llm_voice",
        "label": "LLM Voice",
        "summary": "Voice-on-robot mode using HQ AI orchestration.",
    },
    {
        "id": "llm_remote",
        "label": "LLM Remote",
        "summary": "Remote text and messaging control through HQ AI.",
    },
    {
        "id": "swarm",
        "label": "Swarm",
        "summary": "Coordinated multi-robot swarm mode.",
    },
]

TEST_ROBOT_BASE_URL = "http://test-robot.invalid"
TEST_ROBOTS = [
    {
        "id": "TestTurboPi",
        "base_url": TEST_ROBOT_BASE_URL,
        "token": "",
        "type": "Test TurboPi",
        "robot_type": "turbopi",
        "catalog_label": "Test TurboPi",
        "hostname": "preview-only",
        "ip": "",
        "test_mode": True,
    },
    {
        "id": "TestTonyPi",
        "base_url": TEST_ROBOT_BASE_URL,
        "token": "",
        "type": "Test TonyPi",
        "robot_type": "tonypi",
        "catalog_label": "Test TonyPi",
        "hostname": "preview-only",
        "ip": "",
        "test_mode": True,
    },
    {
        "id": "TestSpiderPi",
        "base_url": TEST_ROBOT_BASE_URL,
        "token": "",
        "type": "Test SpiderPi",
        "robot_type": "spiderpi",
        "catalog_label": "Test SpiderPi",
        "hostname": "preview-only",
        "ip": "",
        "test_mode": True,
    },
    {
        "id": "TestMentorPi",
        "base_url": TEST_ROBOT_BASE_URL,
        "token": "",
        "type": "Test MentorPi",
        "robot_type": "mentorpi",
        "catalog_label": "Test MentorPi",
        "hostname": "preview-only",
        "ip": "",
        "test_mode": True,
    },
]


FAMILY_ALIASES = {
    "turbopi": "turbopi",
    "mata_turbopi": "turbopi",
    "mataturbopi": "turbopi",
    "turbo pi": "turbopi",
    "tonypi": "tonypi",
    "tony pi": "tonypi",
    "tonypi pro": "tonypi",
    "spiderpi": "spiderpi",
    "spider pi": "spiderpi",
    "mentorpi": "mentorpi",
    "mentor pi": "mentorpi",
    "metropi": "mentorpi",
    "metro pi": "mentorpi",
}


ROBOT_FAMILY_CATALOG = {
    "turbopi": {
        "label": "MataTurboPi",
        "library_version": "student_robot_v2",
        "source_repo": "MataTurboPi",
        "capabilities": [
            {
                "namespace": "move",
                "commands": [
                    "forward",
                    "backward",
                    "left",
                    "right",
                    "turn_left",
                    "turn_right",
                    "diagonal_left",
                    "diagonal_right",
                    "drift_left",
                    "drift_right",
                    "stop",
                ],
            },
            {"namespace": "eyes", "commands": ["set_both", "left", "right", "blink", "wink", "off"]},
            {"namespace": "camera", "commands": ["center", "left", "right", "up", "down"]},
            {"namespace": "voice", "commands": ["say", "speak", "voices", "select_voice"]},
            {"namespace": "vision", "commands": ["find_color", "find_face", "snapshot"]},
            {"namespace": "buzzer", "commands": ["beep"]},
            {"namespace": "sonar", "commands": ["distance_cm"]},
        ],
        "text_examples": [
            "Tell Mata01 to say hello",
            "Put Mata03 into lesson mode",
            "Make Mata07 stop",
            "Center Mata02 camera",
        ],
    },
    "tonypi": {
        "label": "MataTonyPi",
        "library_version": "student_robot_v2",
        "source_repo": "MataTonyPi",
        "capabilities": [
            {"namespace": "anim", "commands": ["wave", "greet", "dance", "celebrate", "yes", "no", "scan"]},
            {"namespace": "head", "commands": ["look_left", "look_right", "look_up", "look_down", "center", "nod", "shake"]},
            {"namespace": "arms", "commands": ["left_up", "right_up", "hands_up", "open", "close", "grab_pose", "carry_pose", "release_pose"]},
            {"namespace": "pose", "commands": ["ready", "neutral", "bow", "stand", "sit", "carry"]},
            {"namespace": "motion", "commands": ["walk_forward", "walk_backward", "turn_left", "turn_right", "step_left", "step_right", "stop"]},
            {"namespace": "vision", "commands": ["find_color", "find_face"]},
            {"namespace": "pickup", "commands": ["pick_up", "carry", "release"]},
            {"namespace": "voice", "commands": ["say"]},
        ],
        "text_examples": [
            "Make Tony01 wave",
            "Put Tony01 into LLM remote mode",
            "Tell Tony01 to stop and bow",
        ],
    },
    "spiderpi": {
        "label": "MataSpiderPi",
        "library_version": "student_robot_v2",
        "source_repo": "MataSpiderPi",
        "capabilities": [
            {"namespace": "body", "commands": ["forward", "backward", "left", "right", "turn_left", "turn_right", "stop", "dance", "wave", "attack", "kick", "twist"]},
            {"namespace": "arm", "commands": ["home", "ready", "open", "close", "pick", "carry", "place", "turn_left", "turn_right"]},
            {"namespace": "vision", "commands": ["snapshot", "find_color", "detect_faces", "recognize_hands", "detect_pose", "find_tag"]},
            {"namespace": "sound", "commands": ["say", "beep", "melody"]},
            {"namespace": "distance", "commands": ["cm", "mm", "is_close"]},
        ],
        "text_examples": [
            "Make SpiderPi wave",
            "Tell SpiderPi to pick up the block",
            "Put SpiderPi into swarm mode",
        ],
    },
    "mentorpi": {
        "label": "MataMentorPi",
        "library_version": "student_robot_v3",
        "source_repo": "MataMentorPi",
        "capabilities": [
            {"namespace": "move", "commands": ["forward", "backward", "left", "right", "turn_left", "turn_right", "stop"]},
            {"namespace": "eyes", "commands": ["set_both", "set_left", "set_right"]},
            {"namespace": "camera", "commands": ["center_all", "glance_left", "glance_right", "look_up", "look_down"]},
            {"namespace": "voice", "commands": ["say", "speak"]},
            {"namespace": "vision", "commands": ["find_color", "snapshot"]},
            {"namespace": "sonar", "commands": ["distance_cm"]},
            {"namespace": "line", "commands": ["start", "stop"]},
            {"namespace": "tracking", "commands": ["start", "stop"]},
            {"namespace": "avoidance", "commands": ["start", "stop"]},
            {"namespace": "lidar", "commands": ["status"]},
            {"namespace": "depth", "commands": ["status"]},
            {"namespace": "slam", "commands": ["status"]},
            {"namespace": "nav", "commands": ["status"]},
            {"namespace": "autodrive", "commands": ["start", "stop"]},
            {"namespace": "multi", "commands": ["status"]},
            {"namespace": "ai", "commands": ["status"]},
        ],
        "text_examples": [
            "Put Mata00 into remote control mode",
            "Tell Mata00 to say lesson started",
            "Switch Mata00 to LLM voice mode",
        ],
    },
}


EXECUTABLE_ACTIONS = {
    "say": {"label": "Speak text", "path": "/api/cmd/say", "method": "POST"},
    "soundoff": {"label": "Sound off", "path": "/api/cmd/soundoff", "method": "POST"},
    "allstop": {"label": "Emergency stop", "path": "/api/cmd/allstop", "method": "POST"},
    "master_mode": {"label": "Switch master mode", "path": "/api/admin/master-mode/activate", "method": "POST"},
    "camera_center": {"label": "Center camera", "path": "/api/camera/center", "method": "POST"},
    "camera_nod": {"label": "Nod camera", "path": "/api/camera/nod", "method": "POST"},
    "camera_shake": {"label": "Shake camera", "path": "/api/camera/shake", "method": "POST"},
    "camera_wiggle": {"label": "Wiggle camera", "path": "/api/camera/wiggle", "method": "POST"},
    "llm_service": {"label": "Toggle robot LLM service", "path": "/api/service/play_llm/{op}", "method": "POST"},
}

NON_COMMAND_PHRASES = (
    "this is a test",
    "robot console ai",
    "testing",
    "test message",
    "hello test",
)


def normalize_robot_type(value: str) -> str:
    raw = str(value or "").strip().lower()
    compact = raw.replace("-", " ").replace("_", " ")
    return FAMILY_ALIASES.get(compact, FAMILY_ALIASES.get(compact.replace(" ", ""), compact.replace(" ", "")))


def load_robot_registry(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        robot_id = str(item.get("id") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        if not robot_id or not base_url:
            continue
        robot_type = normalize_robot_type(str(item.get("type") or ""))
        family = ROBOT_FAMILY_CATALOG.get(robot_type, {})
        out.append(
            {
                "id": robot_id,
                "base_url": base_url.rstrip("/"),
                "token": str(item.get("token") or "").strip(),
                "type": str(item.get("type") or "").strip(),
                "robot_type": robot_type,
                "catalog_label": family.get("label") or str(item.get("type") or "Robot"),
                "hostname": str(item.get("hostname") or "").strip(),
                "ip": str(item.get("ip") or "").strip(),
            }
        )
    out = [*TEST_ROBOTS, *out]
    return out


def robot_catalog_payload() -> Dict[str, Any]:
    return {
        "ok": True,
        "families": ROBOT_FAMILY_CATALOG,
        "master_modes": MASTER_MODES,
        "executable_actions": EXECUTABLE_ACTIONS,
    }


def _find_robot_mentions(text: str, robots: List[Dict[str, Any]]) -> List[str]:
    lowered = text.lower()
    matches: List[str] = []
    for robot in robots:
        robot_id = str(robot.get("id") or "").strip()
        if robot_id and robot_id.lower() in lowered:
            matches.append(robot_id)
    return matches


def _infer_master_mode(text: str) -> Optional[str]:
    lowered = text.lower()
    if "lesson" in lowered:
        return "lesson"
    if "joystick" in lowered or "remote control" in lowered:
        return "remote_joystick"
    if "voice mode" in lowered or "llm voice" in lowered:
        return "llm_voice"
    if "remote llm" in lowered or "llm remote" in lowered or "remote mode" in lowered:
        return "llm_remote"
    if "swarm" in lowered:
        return "swarm"
    return None


def _extract_say_text(text: str) -> Optional[str]:
    patterns = [
        r"(?:say|speak)\s+(.+)$",
        r"tell\s+\w+\s+to\s+say\s+(.+)$",
        r"make\s+\w+\s+say\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip(" \"'")
    return None


def _extract_duration_seconds(text: str) -> Optional[float]:
    match = re.search(r"\bfor\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|s)\b", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _is_non_command_phrase(text: str) -> bool:
    lowered = str(text or "").strip().lower().strip(".!? ,;:")
    if not lowered:
        return True
    return lowered in NON_COMMAND_PHRASES


def parse_text_command(text: str, robots: List[Dict[str, Any]], preferred_robot_id: str = "") -> Dict[str, Any]:
    raw_text = str(text or "").strip()
    lowered = raw_text.lower()
    target_ids = _find_robot_mentions(raw_text, robots)
    if preferred_robot_id and not target_ids:
        target_ids = [preferred_robot_id]
    fleet = any(token in lowered for token in ("all robots", "everyone", "fleet", "all of them"))
    target_robot_id = "" if fleet else (target_ids[0] if target_ids else "")

    result: Dict[str, Any] = {
        "ok": True,
        "source": "rules",
        "text": raw_text,
        "target_scope": "fleet" if fleet else "single",
        "target_robot_id": target_robot_id,
        "mentioned_robot_ids": target_ids,
        "intent": {
            "action": "unknown",
            "executable": False,
            "arguments": {},
            "summary": "",
        },
    }

    if not raw_text:
        result["ok"] = False
        result["error"] = "missing_text"
        return result

    say_text = _extract_say_text(raw_text)
    if say_text:
        result["intent"] = {
            "action": "say",
            "executable": True,
            "arguments": {"text": say_text},
            "summary": f'Say "{say_text}"',
        }
        return result

    if "sound off" in lowered or "silence" in lowered or "quiet" in lowered:
        result["intent"] = {"action": "soundoff", "executable": True, "arguments": {}, "summary": "Stop robot sound output"}
        return result

    if "all stop" in lowered or "stop now" in lowered or re.search(r"\bstop\b", lowered):
        result["intent"] = {"action": "allstop", "executable": True, "arguments": {}, "summary": "Emergency stop"}
        return result

    mode = _infer_master_mode(raw_text)
    if mode:
        label = next((item["label"] for item in MASTER_MODES if item["id"] == mode), mode)
        result["intent"] = {
            "action": "master_mode",
            "executable": True,
            "arguments": {"mode": mode},
            "summary": f"Switch to {label}",
        }
        return result

    if "start llm" in lowered or "enable llm" in lowered:
        result["intent"] = {
            "action": "llm_service",
            "executable": True,
            "arguments": {"op": "start"},
            "summary": "Start robot LLM service",
        }
        return result

    if "stop llm" in lowered or "disable llm" in lowered:
        result["intent"] = {
            "action": "llm_service",
            "executable": True,
            "arguments": {"op": "stop"},
            "summary": "Stop robot LLM service",
        }
        return result

    if "center camera" in lowered:
        result["intent"] = {"action": "camera_center", "executable": True, "arguments": {}, "summary": "Center camera"}
        return result

    if "nod camera" in lowered or "camera nod" in lowered:
        result["intent"] = {"action": "camera_nod", "executable": True, "arguments": {"depth": 0.5, "speed_s": 0.25}, "summary": "Nod camera"}
        return result

    if "shake camera" in lowered or "camera shake" in lowered:
        result["intent"] = {"action": "camera_shake", "executable": True, "arguments": {"width": 0.5, "speed_s": 0.25}, "summary": "Shake camera"}
        return result

    if "wiggle camera" in lowered or "camera wiggle" in lowered:
        result["intent"] = {"action": "camera_wiggle", "executable": True, "arguments": {"cycles": 2, "amplitude": 0.3, "speed_s": 0.2}, "summary": "Wiggle camera"}
        return result

    move_duration = _extract_duration_seconds(raw_text)
    for phrase, family_command in (
        ("spin left", "turn_left"),
        ("spin right", "turn_right"),
        ("move forward", "forward"),
        ("move backward", "backward"),
        ("walk forward", "walk_forward"),
        ("walk backward", "walk_backward"),
        ("wave", "wave"),
        ("dance", "dance"),
        ("forward", "forward"),
        ("backward", "backward"),
        ("turn left", "turn_left"),
        ("turn right", "turn_right"),
        ("pick up", "pick"),
    ):
        if phrase in lowered:
            arguments: Dict[str, Any] = {"command": family_command}
            summary = f"Recognized robot library command: {family_command}"
            if move_duration is not None:
                arguments["duration_s"] = move_duration
                summary = f"{summary} for {move_duration:g} seconds"
            result["intent"] = {
                "action": "catalog_only",
                "executable": False,
                "arguments": arguments,
                "summary": summary,
            }
            return result

    return result


def _normalize_step_payload(payload: Dict[str, Any], target_scope: str, target_robot_id: str, mentioned_robot_ids: List[str], source_text: str) -> Dict[str, Any]:
    step_intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else {}
    return {
        "target_scope": target_scope,
        "target_robot_id": target_robot_id,
        "mentioned_robot_ids": list(mentioned_robot_ids),
        "source": payload.get("source") or "rules",
        "text": source_text,
        "intent": {
            "action": str(step_intent.get("action") or "unknown"),
            "executable": bool(step_intent.get("executable")),
            "arguments": step_intent.get("arguments") if isinstance(step_intent.get("arguments"), dict) else {},
            "summary": str(step_intent.get("summary") or "").strip(),
        },
    }


def parse_text_command_plan(text: str, robots: List[Dict[str, Any]], preferred_robot_id: str = "") -> Dict[str, Any]:
    base = parse_text_command(text, robots, preferred_robot_id=preferred_robot_id)
    if not base.get("ok"):
        return base

    raw_text = str(text or "").strip()
    split_chunks = [
        chunk.strip(" \t\r\n.;:!?")
        for chunk in re.split(r"\s+(?:and|then)\s+|[,\n;]+|(?<=[.!?])\s+", raw_text)
        if chunk.strip(" \t\r\n.;:!?")
    ]
    if len(split_chunks) <= 1:
        base["steps"] = [
            _normalize_step_payload(
                base,
                str(base.get("target_scope") or "single"),
                str(base.get("target_robot_id") or ""),
                list(base.get("mentioned_robot_ids") or []),
                raw_text,
            )
        ]
        return base

    target_scope = str(base.get("target_scope") or "single")
    target_robot_id = str(base.get("target_robot_id") or preferred_robot_id or "")
    mentioned_robot_ids = list(base.get("mentioned_robot_ids") or [])
    steps: List[Dict[str, Any]] = []
    for chunk in split_chunks:
        step = parse_text_command(chunk, robots, preferred_robot_id=target_robot_id)
        normalized = _normalize_step_payload(step, target_scope, target_robot_id, mentioned_robot_ids, chunk)
        steps.append(normalized)

    recognized_steps = [step for step in steps if str((step.get("intent") or {}).get("action") or "") != "unknown"]
    if recognized_steps and len(recognized_steps) != len(steps):
        filtered_steps = []
        for step in steps:
            action = str((step.get("intent") or {}).get("action") or "")
            if action != "unknown":
                filtered_steps.append(step)
                continue
            if not _is_non_command_phrase(step.get("text") or ""):
                filtered_steps.append(step)
        steps = filtered_steps or recognized_steps

    first_step = steps[0] if steps else {}
    first_intent = first_step.get("intent") if isinstance(first_step.get("intent"), dict) else {}
    base["intent"] = {
        "action": str(first_intent.get("action") or base.get("intent", {}).get("action") or "unknown"),
        "executable": all(bool((step.get("intent") or {}).get("executable")) for step in steps),
        "arguments": first_intent.get("arguments") if isinstance(first_intent.get("arguments"), dict) else {},
        "summary": " then ".join(
            str((step.get("intent") or {}).get("summary") or (step.get("intent") or {}).get("action") or "unknown")
            for step in steps
        ),
    }
    base["steps"] = steps
    base["multi_step"] = len(steps) > 1
    return base


def build_llm_parser_prompt(text: str, robots: List[Dict[str, Any]], preferred_robot_id: str = "") -> str:
    robot_lines = []
    for robot in robots:
        robot_lines.append(f'- {robot["id"]} ({robot.get("robot_type") or "robot"})')
    preferred = preferred_robot_id.strip()
    return f"""You are a strict robot command parser.

Return exactly one JSON object and no markdown.

Allowed actions:
- say
- soundoff
- allstop
- master_mode
- llm_service
- camera_center
- camera_nod
- camera_shake
- camera_wiggle
- catalog_only
- unknown

Allowed master modes:
- lesson
- remote_joystick
- llm_voice
- llm_remote
- swarm

Available robots:
{chr(10).join(robot_lines) if robot_lines else "- none listed"}

Preferred robot:
- {preferred or "none"}

Required JSON shape:
{{
  "action": "say|soundoff|allstop|master_mode|llm_service|camera_center|camera_nod|camera_shake|camera_wiggle|catalog_only|unknown",
  "target_scope": "single|fleet",
  "target_robot_id": "robot id or empty string",
  "arguments": {{}},
  "summary": "short summary"
}}

Rules:
- If a robot is not explicitly named, use the preferred robot if provided.
- Use target_scope=fleet only for commands clearly aimed at all robots.
- For say, set arguments.text.
- For master_mode, set arguments.mode.
- For llm_service, set arguments.op to start or stop.
- For catalog_only, set arguments.command to the library command name.
- If unsure, use action=unknown.

User text:
{text}
"""


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def normalize_llm_intent(payload: Dict[str, Any], robots: List[Dict[str, Any]], preferred_robot_id: str = "") -> Dict[str, Any]:
    action = str(payload.get("action") or "unknown").strip().lower()
    if action not in {"say", "soundoff", "allstop", "master_mode", "llm_service", "camera_center", "camera_nod", "camera_shake", "camera_wiggle", "catalog_only", "unknown"}:
        action = "unknown"
    target_scope = str(payload.get("target_scope") or "single").strip().lower()
    if target_scope not in {"single", "fleet"}:
        target_scope = "single"
    target_robot_id = str(payload.get("target_robot_id") or preferred_robot_id or "").strip()
    if target_scope == "single" and target_robot_id:
        valid_ids = {str(robot.get("id") or "").strip() for robot in robots}
        if target_robot_id not in valid_ids:
            target_robot_id = preferred_robot_id.strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    summary = str(payload.get("summary") or "").strip()
    executable = action in EXECUTABLE_ACTIONS
    return {
        "ok": True,
        "source": "llm",
        "target_scope": target_scope,
        "target_robot_id": target_robot_id,
        "intent": {
            "action": action,
            "executable": executable,
            "arguments": arguments,
            "summary": summary or action.replace("_", " "),
        },
    }
