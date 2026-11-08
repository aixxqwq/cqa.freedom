"""Выгрузка результатов в формат организаторов (results.json, Формат JSON 1.0).

Правила из «Приложения к студенческому гайду»:
  * одна запись на каждый диалог набора, без пропусков и повторов ID;
  * 8 критериев с кодами организаторов, value: true/false/null(N/A)/число шкалы;
  * evidence_turns — номера реплик с 1 по исходному массиву транскрипта;
  * final_score 0..100 с двумя знаками, critical_flag = value критерия critical;
  * если диалог не оценён — status "error", пустой criteria, score null и причина.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import storage
from .datasource import fetch_group

# наш id критерия → код организаторов
CRITERIA_MAP = {
    "contact": "contact",
    "need": "discovery",
    "identification": "identification",
    "consultation": "consultation",
    "empathy": "empathy",
    "dialog_management": "dialogue",
    "closing": "closure",
    "gross_errors": "critical",
}
ALLOWED = {
    "contact": {True, False},
    "discovery": {True, False, None},
    "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None},
    "empathy": {True, False},
    "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None},
    "critical": {True, False},
}


def _value(answer):
    if answer == "na":
        return None
    if answer == "yes":
        return True
    if answer == "no":
        return False
    v = float(answer)
    return int(v) if v.is_integer() else v


_REF = re.compile(r"\[(\d+)\]")


def _renumber(text: str) -> str:
    """В нашей системе реплики нумеруются с 0, у организаторов — с 1: [9] → [10]."""
    return _REF.sub(lambda m: f"[{int(m.group(1)) + 1}]", text or "")


def _latest_evaluations(ids: set[str]) -> dict[str, dict]:
    """Последняя оценка ИИ (не mock) по основному чек-листу для каждого диалога."""
    latest: dict[str, dict] = {}
    for ev in storage.list_all("evaluations"):  # отсортировано: новые первыми
        cid = ev["conversation_id"]
        if cid not in ids or cid in latest or ev.get("model") == "mock":
            continue
        crit_ids = {i["criterion_id"] for i in ev["items"]}
        if set(CRITERIA_MAP) <= crit_ids:
            latest[cid] = ev
    return latest


def build_results(group_id: str, team_name: str, mode: str = "external_api") -> dict:
    convs = fetch_group(group_id)
    evs = _latest_evaluations({c["conversation_id"] for c in convs})
    results, models = [], []
    for conv in convs:
        cid = conv["conversation_id"]
        ev = evs.get(cid)
        if not ev:
            results.append({"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                            "error": "Диалог не был оценён системой."})
            continue
        if ev["model"] not in models:
            models.append(ev["model"])
        items = {i["criterion_id"]: i for i in ev["items"]}
        criteria = []
        for ours, theirs in CRITERIA_MAP.items():
            it = items[ours]
            criteria.append({
                "criterion_id": theirs,
                "value": _value(it["answer"]),
                "rationale": _renumber(it.get("na_reason") if it["answer"] == "na" and it.get("na_reason")
                                       else it.get("explanation", "")),
                "evidence_turns": sorted({int(i) + 1 for i in it.get("evidence", [])}),
            })
        # Итог системы (исходная оценка ИИ, до решений по апелляциям)
        total = ev["original"]["total"]
        critical = next(c["value"] for c in criteria if c["criterion_id"] == "critical")
        results.append({
            "conversation_id": cid,
            "status": "ok",
            "criteria": criteria,
            "score": {"final_score": round(total, 2) if total is not None else None, "critical_flag": bool(critical)},
        })
    return {
        "schema_version": "1.0",
        "team_name": team_name,
        "model": {"mode": mode, "name": ", ".join(models) or "—"},
        "results": results,
    }


def validate(data: dict, expected_count: Optional[int] = None) -> list[str]:
    """Проверки из раздела «Перед push». Возвращает список проблем (пустой — всё хорошо)."""
    problems = []
    for key in ("schema_version", "team_name", "model", "results"):
        if key not in data:
            problems.append(f"нет поля {key}")
    if data.get("schema_version") != "1.0":
        problems.append("schema_version должен быть \"1.0\"")
    if data.get("model", {}).get("mode") not in ("local", "external_api", "hybrid"):
        problems.append("model.mode: local, external_api или hybrid")
    ids = [r.get("conversation_id") for r in data.get("results", [])]
    if len(ids) != len(set(ids)):
        problems.append("есть повторяющиеся conversation_id")
    if expected_count is not None and len(ids) != expected_count:
        problems.append(f"записей {len(ids)}, ожидалось {expected_count}")
    for r in data.get("results", []):
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                problems.append(f"{cid}: у ошибки должны быть criteria=[], score=null и error")
            continue
        if r.get("status") != "ok":
            problems.append(f"{cid}: status должен быть ok или error")
            continue
        codes = [c["criterion_id"] for c in r["criteria"]]
        if sorted(codes) != sorted(ALLOWED):
            problems.append(f"{cid}: нужны все 8 критериев ровно по разу, есть {codes}")
        for c in r["criteria"]:
            if c["criterion_id"] in ALLOWED and c["value"] not in ALLOWED[c["criterion_id"]]:
                problems.append(f"{cid}: {c['criterion_id']} = {c['value']!r} — недопустимое значение")
            if any((not isinstance(t, int)) or t < 1 for t in c.get("evidence_turns", [])):
                problems.append(f"{cid}: {c['criterion_id']} — evidence_turns должны быть номерами с 1")
        s = r.get("score") or {}
        fs = s.get("final_score")
        if fs is not None and not (0 <= fs <= 100):
            problems.append(f"{cid}: final_score вне 0..100")
        crit = next((c["value"] for c in r["criteria"] if c["criterion_id"] == "critical"), None)
        if s.get("critical_flag") != crit:
            problems.append(f"{cid}: critical_flag не совпадает с value критерия critical")
    return problems


def write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
