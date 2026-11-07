"""Загрузка своих диалогов (транскрипт текстом или JSON + необязательная аудиозапись).

Загруженные диалоги живут в наборе group_id = "UPLOAD" и оцениваются так же,
как диалоги из API организаторов.

Формат текста — одна реплика на строку:
    [00:05] Оператор: Здравствуйте! Меня зовут Алина.
    Клиент: Хочу пополнить счёт.
    [01:10] bot: Перевожу на оператора.
Время в квадратных скобках необязательно. Роли: оператор/operator/агент,
клиент/client/customer, бот/bot, система/system (регистр не важен).
Строка без «Роль:» продолжает предыдущую реплику.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from pathlib import Path
from typing import Optional

from . import storage

GROUP = "UPLOAD"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".webm", ".aac"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024

ROLES = {
    "оператор": "operator", "operator": "operator", "агент": "operator", "agent": "operator",
    "менеджер": "operator", "op": "operator",
    "клиент": "client", "client": "client", "customer": "client", "клиентка": "client",
    "бот": "bot", "bot": "bot",
    "система": "system", "system": "system", "сист": "system",
}
LINE = re.compile(r"^\s*(?:\[(?P<ts>[^\]]+)\]\s*)?(?P<role>[^\s:：][^:：]{0,20})\s*[:：]\s*(?P<text>.*)$")


def parse_text(text: str) -> list[dict]:
    """Текст «Роль: реплика» → список реплик как у API организаторов."""
    messages: list[dict] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = LINE.match(raw)
        role = ROLES.get(m.group("role").strip().lower()) if m else None
        if m and role:
            messages.append({"timestamp": (m.group("ts") or "").strip(), "speaker": role, "text": m.group("text").strip()})
        elif messages:
            messages[-1]["text"] += "\n" + raw.strip()
        else:
            raise ValueError(f"Не понятна роль в строке: «{raw.strip()[:60]}». Начните строку с «Оператор:» или «Клиент:»")
    if not messages:
        raise ValueError("Транскрипт пустой")
    if not any(m["speaker"] == "operator" for m in messages):
        raise ValueError("В транскрипте нет ни одной реплики оператора — оценивать нечего")
    return messages


def parse_transcript(raw: str) -> list[dict]:
    """Принимает текст или JSON (массив реплик, запись API или весь ответ API)."""
    s = raw.strip()
    if s.startswith("[") or s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return parse_text(raw)  # например, строки начинаются с [00:05]
        if isinstance(data, dict):
            data = data.get("transcript") or (data.get("data", {}).get("records") or [{}])[0].get("transcript")
        if not isinstance(data, list) or not data:
            raise ValueError("В JSON не найден массив transcript")
        out = []
        for m in data:
            role = ROLES.get(str(m.get("speaker", "")).lower())
            if not role:
                raise ValueError(f"Неизвестная роль: {m.get('speaker')!r}")
            out.append({"timestamp": str(m.get("timestamp", "")), "speaker": role, "text": str(m.get("text", ""))})
        return out
    return parse_text(raw)


def _save_audio(conv_id: str, audio_base64: str, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        raise ValueError(f"Формат аудио {ext or '?'} не поддерживается. Можно: {', '.join(sorted(AUDIO_EXT))}")
    if "," in audio_base64[:100]:  # data:audio/mpeg;base64,....
        audio_base64 = audio_base64.split(",", 1)[1]
    data = base64.b64decode(audio_base64)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Аудиофайл больше 25 МБ")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{conv_id}{ext}"
    (UPLOAD_DIR / name).write_bytes(data)
    return f"/uploads/{name}"


def create(transcript_raw: str, language: str = "RU", channel: str = "agent chat",
           conversation_id: Optional[str] = None, audio_base64: Optional[str] = None,
           audio_filename: Optional[str] = None, audio_url: Optional[str] = None) -> dict:
    transcript = parse_transcript(transcript_raw)
    conv_id = (conversation_id or "").strip() or f"up-{language.lower()}-{uuid.uuid4().hex[:6]}"
    if not re.fullmatch(r"[\w\-.]{1,60}", conv_id):
        raise ValueError("ID диалога: только буквы, цифры, «-», «_» и «.»")
    if audio_base64:
        audio_url = _save_audio(conv_id, audio_base64, audio_filename or "")
        channel = "voice call"
    record = {
        "conversation_id": conv_id,
        "channel": channel,
        "language": language.upper(),
        "transcript": [{**m, "idx": i} for i, m in enumerate(transcript)],
        "audio_url": audio_url or None,
        "group_id": GROUP,
        "uploaded_at": storage.now(),
    }
    return storage.put("uploads", conv_id, record)


def list_uploaded() -> list[dict]:
    return sorted(storage.list_all("uploads"), key=lambda r: r["uploaded_at"])


def delete(conv_id: str) -> None:
    rec = storage.get("uploads", conv_id)
    if rec and rec.get("audio_url", "") and rec["audio_url"].startswith("/uploads/"):
        (UPLOAD_DIR / rec["audio_url"].split("/")[-1]).unlink(missing_ok=True)
    storage.delete("uploads", conv_id)
