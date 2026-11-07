"""Простое хранилище документов на SQLite: коллекция + id + JSON.

Коллекции: checklists, evaluations, appeals.
Для хакатона этого достаточно; файл data/cqa.db можно удалить, чтобы начать заново.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cqa.db"
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.execute(
        "CREATE TABLE IF NOT EXISTS docs ("
        " collection TEXT, id TEXT, data TEXT, updated_at TEXT,"
        " PRIMARY KEY (collection, id))"
    )
    return c


_db = _conn()


def put(collection: str, doc_id: str, data: dict) -> dict:
    with _lock:
        _db.execute(
            "INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?)",
            (collection, doc_id, json.dumps(data, ensure_ascii=False), now()),
        )
        _db.commit()
    return data


def get(collection: str, doc_id: str) -> Optional[dict]:
    with _lock:
        row = _db.execute(
            "SELECT data FROM docs WHERE collection=? AND id=?", (collection, doc_id)
        ).fetchone()
    return json.loads(row[0]) if row else None


def list_all(collection: str) -> list[dict]:
    with _lock:
        rows = _db.execute(
            "SELECT data FROM docs WHERE collection=? ORDER BY updated_at DESC", (collection,)
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def delete(collection: str, doc_id: str) -> None:
    with _lock:
        _db.execute("DELETE FROM docs WHERE collection=? AND id=?", (collection, doc_id))
        _db.commit()
