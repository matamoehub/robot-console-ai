#!/usr/bin/env python3
import os
import time
from typing import Any, Dict, List

import requests


TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_ALLOWED_CHAT_IDS = {
    item.strip()
    for item in (os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS") or "").split(",")
    if item.strip()
}
TELEGRAM_DEFAULT_ROBOT_ID = (os.environ.get("TELEGRAM_DEFAULT_ROBOT_ID") or "").strip()
ROBOT_BRAIN_API_BASE_URL = (
    os.environ.get("ROBOT_BRAIN_API_BASE_URL") or "http://127.0.0.1:8080/api/brain"
).strip().rstrip("/")
ROBOT_BRAIN_API_TOKEN = (os.environ.get("ROBOT_BRAIN_API_TOKEN") or "").strip()
TELEGRAM_EXECUTION_MODE = (os.environ.get("TELEGRAM_EXECUTION_MODE") or "live").strip().lower()


def _telegram_api(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    response = requests.post(url, json=payload, timeout=45)
    response.raise_for_status()
    return response.json()


def _brain_execute(text: str, robot_id: str = "", *, chat_id: int = 0, display_name: str = "", username: str = "") -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {ROBOT_BRAIN_API_TOKEN}"} if ROBOT_BRAIN_API_TOKEN else {}
    payload = {
        "text": text,
        "robot_id": robot_id or TELEGRAM_DEFAULT_ROBOT_ID,
        "mode": TELEGRAM_EXECUTION_MODE,
        "chat_id": chat_id,
        "display_name": display_name,
        "username": username,
    }
    response = requests.post(f"{ROBOT_BRAIN_API_BASE_URL}/telegram/ingest", json=payload, headers=headers, timeout=90)
    return {"status_code": response.status_code, "data": response.json()}


def _format_result(result: Dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if not data.get("ok"):
        return f"Command failed:\n{data}"
    parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
    preview = data.get("preview") if isinstance(data.get("preview"), dict) else {}
    summary = str(preview.get("summary") or ((parsed.get("intent") or {}).get("summary")) or "done")
    lines = [f"Command: {summary}", f"Mode: {data.get('mode') or TELEGRAM_EXECUTION_MODE}"]
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    for item in execution.get("results") or []:
        robot_id = str(item.get("robot_id") or "robot")
        status = "ok" if item.get("ok") else "failed"
        lines.append(f"- {robot_id}: {status}")
    if data.get("mode") == "test":
        lines.append("Preview only. No live robot command was sent.")
    return "\n".join(lines)


def _allowed(chat_id: int) -> bool:
    if not TELEGRAM_ALLOWED_CHAT_IDS:
        return True
    return str(chat_id) in TELEGRAM_ALLOWED_CHAT_IDS


def main() -> int:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    if not ROBOT_BRAIN_API_TOKEN:
        raise SystemExit("ROBOT_BRAIN_API_TOKEN is required")

    offset = 0
    while True:
        try:
            data = _telegram_api("getUpdates", {"timeout": 30, "offset": offset})
            updates: List[Dict[str, Any]] = data.get("result") or []
            for update in updates:
                offset = int(update.get("update_id") or 0) + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = int(chat.get("id") or 0)
                text = str(message.get("text") or "").strip()
                if not chat_id or not text:
                    continue
                if not _allowed(chat_id):
                    _telegram_api("sendMessage", {"chat_id": chat_id, "text": "This chat is not allowed to control robots."})
                    continue
                from_user = message.get("from") or {}
                result = _brain_execute(
                    text,
                    chat_id=chat_id,
                    display_name=str(chat.get("title") or from_user.get("first_name") or "").strip(),
                    username=str(from_user.get("username") or "").strip(),
                )
                _telegram_api("sendMessage", {"chat_id": chat_id, "text": _format_result(result)})
        except Exception as exc:
            time.sleep(3.0)
            print(f"[telegram_robot_bot] error: {exc}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
