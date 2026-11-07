"""Загрузка диалогов из API организаторов (GET с group_id).

Запрос идёт с сервера, поэтому проблемы CORS нет. Ответ кешируется в data/cache,
чтобы не дёргать API на каждый клик и работать, даже если API недоступен.

CQA_API_URL — ссылка с кнопки «Открыть GET для DEV». Если в ней есть {group_id},
он подставится; иначе group_id заменится/добавится как query-параметр.
Если CQA_API_URL не задан — берутся тестовые данные из data/mock/<group_id>.json.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
MOCK_DIR = DATA_DIR / "mock"


def _build_url(template: str, group_id: str) -> str:
    if "{group_id}" in template:
        return template.replace("{group_id}", group_id)
    parts = urlparse(template)
    query = dict(parse_qsl(parts.query))
    query["group_id"] = group_id
    return urlunparse(parts._replace(query=urlencode(query)))


def _normalize(record: dict) -> dict:
    """Добавляет индекс реплики (idx) — на него ссылаются доказательства и якоря #msg-N."""
    transcript = record.get("transcript") or []
    if isinstance(transcript, str):  # на всякий случай, если придёт строкой
        transcript = json.loads(transcript)
    record["transcript"] = [{**m, "idx": i} for i, m in enumerate(transcript)]
    return record


def fetch_group(group_id: str, refresh: bool = False) -> list[dict]:
    if group_id.upper() == "UPLOAD":  # свои загруженные диалоги
        from .upload import list_uploaded
        return list_uploaded()
    url_template = os.getenv("CQA_API_URL")
    if not url_template:
        mock = MOCK_DIR / f"{group_id}.json"
        if not mock.exists():
            raise FileNotFoundError(f"CQA_API_URL не задан и нет {mock}")
        return _parse(json.loads(mock.read_text(encoding="utf-8")), group_id)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{group_id}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    resp = httpx.get(_build_url(url_template, group_id), timeout=60)
    resp.raise_for_status()
    records = _parse(resp.json(), group_id)
    cache.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    return records


_audio_cache: dict[str, tuple[float, str]] = {}
_SOURCE = re.compile(r"""<source[^>]+src=["']([^"']+)["']""", re.I)


def resolve_audio(url: str) -> str:
    """audio_url из API ведёт на HTML-страницу с плеером, а не на файл.
    Достаём из неё прямую ссылку на mp3 (подписанная ссылка живёт ~1 час, поэтому кешируем на 30 минут)."""
    hit = _audio_cache.get(url)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    with httpx.stream("GET", url, timeout=30, follow_redirects=True) as r:
        r.raise_for_status()
        if r.headers.get("content-type", "").startswith("audio/"):
            direct = url
        else:
            r.read()
            m = _SOURCE.search(r.text)
            if not m:
                raise RuntimeError("На странице записи не найден аудиофайл")
            direct = html.unescape(m.group(1))
    _audio_cache[url] = (time.time(), direct)
    return direct


def _parse(payload: dict, group_id: str) -> list[dict]:
    if not payload.get("isSuccess", True):
        raise RuntimeError(f"API организаторов вернуло ошибку: {payload.get('error')}")
    records = [_normalize(r) for r in payload["data"]["records"]]
    for r in records:
        r["group_id"] = group_id
    return records
