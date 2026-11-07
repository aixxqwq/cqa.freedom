"""Бизнес-логика: чек-листы, оценки, апелляции, пакетная обработка."""
from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from . import storage
from .checklist import Checklist
from .datasource import fetch_group
from .evaluator import evaluate
from .scoring import normalize, score

DEFAULT_CHECKLIST = Path(__file__).resolve().parent.parent / "checklists" / "default.json"


class NotFound(Exception):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------- чек-листы ----------------

def ensure_default_checklist() -> None:
    if not storage.list_all("checklists"):
        data = json.loads(DEFAULT_CHECKLIST.read_text(encoding="utf-8"))
        save_checklist(data, bump_version=False)


def save_checklist(data: dict, bump_version: bool = True) -> dict:
    """Создаёт или обновляет чек-лист. При обновлении растёт version,
    старые оценки сохраняют свой снимок чек-листа."""
    data.setdefault("id", _id("cl"))
    old = storage.get("checklists", data["id"])
    if old and bump_version:
        data["version"] = old.get("version", 1) + 1
    cl = Checklist(**data)  # валидация
    saved = cl.model_dump()
    if old and _same_content(old, saved):
        return old  # ничего не поменялось — новую версию не создаём
    saved["saved_at"] = storage.now()
    # Каждая версия хранится отдельно: её можно посмотреть и восстановить.
    storage.put("checklist_versions", f"{cl.id}@{cl.version}", saved)
    return storage.put("checklists", cl.id, saved)


def _same_content(a: dict, b: dict) -> bool:
    skip = {"version", "saved_at"}
    return {k: v for k, v in a.items() if k not in skip} == {k: v for k, v in b.items() if k not in skip}


def list_checklist_versions(checklist_id: str) -> list[dict]:
    get_checklist(checklist_id)
    versions = [v for v in storage.list_all("checklist_versions") if v["id"] == checklist_id]
    return sorted(versions, key=lambda v: v["version"], reverse=True)


def get_checklist(checklist_id: str) -> Checklist:
    data = storage.get("checklists", checklist_id)
    if not data:
        raise NotFound(f"чек-лист {checklist_id} не найден")
    return Checklist(**data)


# ---------------- диалоги ----------------

def list_conversations(group_id: str, refresh: bool = False) -> list[dict]:
    records = fetch_group(group_id, refresh=refresh)
    evals = storage.list_all("evaluations")
    latest: dict[str, dict] = {}
    for e in evals:  # list_all отсортирован по свежести
        latest.setdefault(e["conversation_id"], e)
    out = []
    for r in records:
        e = latest.get(r["conversation_id"])
        out.append({
            "conversation_id": r["conversation_id"],
            "channel": r.get("channel"),
            "language": r.get("language"),
            "messages": len(r["transcript"]),
            "has_audio": bool(r.get("audio_url")),
            "evaluation_id": e["id"] if e else None,
            "total": e["final"]["total"] if e else None,
            "critical": bool(e and e["final"]["critical_errors"]),
            "open_appeals": sum(1 for a in storage.list_all("appeals")
                                if e and a["evaluation_id"] == e["id"] and a["status"] == "open"),
        })
    return out


def get_conversation(group_id: str, conversation_id: str) -> dict:
    for r in fetch_group(group_id):
        if r["conversation_id"] == conversation_id:
            return r
    raise NotFound(f"диалог {conversation_id} не найден в группе {group_id}")


# ---------------- оценки ----------------

def _answers(ev: dict, with_overrides: bool) -> dict[str, Any]:
    answers = {i["criterion_id"]: i["answer"] for i in ev["items"]}
    if with_overrides:
        for cid, o in ev.get("overrides", {}).items():
            answers[cid] = o["answer"]
    return answers


def _recalculate(ev: dict) -> dict:
    cl = Checklist(**ev["checklist"])
    ev["original"] = score(cl, _answers(ev, False)).model_dump()
    ev["final"] = score(cl, _answers(ev, True)).model_dump()
    # Удобный для фронтенда вид: всё по критерию в одном объекте.
    orig = {c["criterion_id"]: c for c in ev["original"]["criteria"]}
    fin = {c["criterion_id"]: c for c in ev["final"]["criteria"]}
    ev["view"] = [
        {
            **item,
            "title": fin[item["criterion_id"]]["title"],
            "section": fin[item["criterion_id"]]["section"],
            "original": orig[item["criterion_id"]],
            "final": fin[item["criterion_id"]],
            "override": ev.get("overrides", {}).get(item["criterion_id"]),
        }
        for item in ev["items"]
    ]
    return ev


def run_evaluation(group_id: str, conversation_id: str, checklist_id: str) -> dict:
    conv = get_conversation(group_id, conversation_id)
    cl = get_checklist(checklist_id)
    result = evaluate(conv, cl)
    ev = {
        "id": _id("ev"),
        "group_id": group_id,
        "conversation_id": conversation_id,
        "checklist_id": cl.id,
        "checklist_version": cl.version,
        "checklist": cl.model_dump(),
        "created_at": storage.now(),
        "model": result["model"],
        "usage": result["usage"],
        "items": result["items"],
        "votes": result.get("votes", 1),
        "runs": result.get("runs", []),
        "duration_s": result.get("duration_s"),
        "overrides": {},
    }
    _recalculate(ev)
    return storage.put("evaluations", ev["id"], ev)


# ---------- эталонная разметка и точность ----------

def save_ground_truth(conversation_id: str, answers: dict, author: str = "", checklist_id: str = "default") -> dict:
    """Ответы эталона по критериям (как поставил бы человек-проверяющий)."""
    cl = get_checklist(checklist_id)
    clean = {}
    for c in cl.criteria:
        if c.id in answers and answers[c.id] not in (None, ""):
            a = answers[c.id]
            if c.type == "scale" and a != "na":
                a = float(a)
                a = int(a) if a.is_integer() else a
            normalize(c, a)  # проверка: допустимый ли ответ
            clean[c.id] = a
    if not clean:
        raise ValueError("Не заполнен ни один критерий")
    doc = {"id": conversation_id, "conversation_id": conversation_id, "checklist_id": cl.id,
           "answers": clean, "author": author, "updated_at": storage.now()}
    return storage.put("ground_truth", conversation_id, doc)


def list_ground_truth() -> list[dict]:
    return storage.list_all("ground_truth")


def delete_ground_truth(conversation_id: str) -> None:
    storage.delete("ground_truth", conversation_id)


def accuracy(checklist_id: str = "default") -> dict:
    """Сравнивает последнюю оценку ИИ по каждому размеченному диалогу с эталоном."""
    cl = get_checklist(checklist_id)
    gts = {g["conversation_id"]: g for g in list_ground_truth()}
    latest: dict[str, dict] = {}
    for e in storage.list_all("evaluations"):
        if e["conversation_id"] in gts and e.get("model") != "mock":
            latest.setdefault(e["conversation_id"], e)
    per_crit = {c.id: {"title": c.title, "match": 0, "total": 0, "close": 0} for c in cl.criteria}
    rows, diffs = [], []
    gross = {"tp": 0, "fp": 0, "fn": 0}
    for cid, gt in gts.items():
        ev = latest.get(cid)
        if not ev:
            rows.append({"conversation_id": cid, "evaluated": False})
            continue
        ai = {i["criterion_id"]: i["answer"] for i in ev["items"]}
        mism = []
        for c in cl.criteria:
            if c.id not in gt["answers"] or c.id not in ai:
                continue
            g, a = gt["answers"][c.id], ai[c.id]
            ok = str(g) == str(a) or (g != "na" and a != "na" and c.type == "scale" and float(g) == float(a))
            pc = per_crit[c.id]
            pc["total"] += 1
            pc["match"] += ok
            if not ok and c.type == "scale" and "na" not in (g, a):
                levels = c.scale.allowed() or []
                if levels and abs(levels.index(float(g)) - levels.index(float(a))) == 1:
                    pc["close"] += 1
            if not ok:
                mism.append({"criterion_id": c.id, "title": c.title, "ground_truth": g, "ai": a})
            if c.type == "flag":
                gross["tp"] += g == "yes" and a == "yes"
                gross["fp"] += g != "yes" and a == "yes"
                gross["fn"] += g == "yes" and a != "yes"
        gt_total = None
        if all(c.id in gt["answers"] for c in cl.criteria if c.type != "flag"):
            gt_total = score(cl, {**{c.id: "no" for c in cl.criteria if c.type == "flag"}, **gt["answers"]}).total
        ai_total = ev["original"]["total"]
        if gt_total is not None and ai_total is not None:
            diffs.append(abs(gt_total - ai_total))
        rows.append({"conversation_id": cid, "evaluated": True, "evaluation_id": ev["id"],
                     "ai_total": ai_total, "gt_total": gt_total, "mismatches": mism,
                     "matched": sum(1 for c in cl.criteria if c.id in gt["answers"]) - len(mism),
                     "compared": sum(1 for c in cl.criteria if c.id in gt["answers"] and c.id in ai)})
    total_match = sum(p["match"] for p in per_crit.values())
    total_cmp = sum(p["total"] for p in per_crit.values())
    return {
        "labeled": len(gts),
        "compared_dialogs": sum(1 for r in rows if r["evaluated"]),
        "criterion_accuracy": round(total_match / total_cmp * 100, 1) if total_cmp else None,
        "total_mae": round(sum(diffs) / len(diffs), 2) if diffs else None,
        "gross_errors": gross,
        "per_criterion": [{"criterion_id": k, **v, "accuracy": round(v["match"] / v["total"] * 100, 1) if v["total"] else None}
                          for k, v in per_crit.items()],
        "dialogs": sorted(rows, key=lambda r: (not r["evaluated"], -len(r.get("mismatches", [])))),
    }


def get_evaluation(evaluation_id: str) -> dict:
    ev = storage.get("evaluations", evaluation_id)
    if not ev:
        raise NotFound(f"оценка {evaluation_id} не найдена")
    return ev


def list_evaluations(conversation_id: Optional[str] = None) -> list[dict]:
    evs = storage.list_all("evaluations")
    if conversation_id:
        evs = [e for e in evs if e["conversation_id"] == conversation_id]
    return [
        {k: e[k] for k in ("id", "conversation_id", "checklist_id", "checklist_version", "created_at", "model")}
        | {"total": e["final"]["total"], "original_total": e["original"]["total"]}
        for e in evs
    ]


# ---------------- апелляции ----------------

def create_appeal(evaluation_id: str, criterion_id: str, comment: str, author: str = "оператор") -> dict:
    ev = get_evaluation(evaluation_id)
    item = next((i for i in ev["items"] if i["criterion_id"] == criterion_id), None)
    if not item:
        raise NotFound(f"критерий {criterion_id} не найден в оценке")
    conv = get_conversation(ev["group_id"], ev["conversation_id"])
    # Фрагмент для проверяющего: реплики-доказательства и проверенный участок ±1.
    idx = set(item["evidence"])
    if item.get("checked_range"):
        idx |= set(range(item["checked_range"]["from"], item["checked_range"]["to"] + 1))
    idx = {j for i in idx for j in (i - 1, i, i + 1) if 0 <= j < len(conv["transcript"])}
    appeal = {
        "id": _id("ap"),
        "evaluation_id": evaluation_id,
        "conversation_id": ev["conversation_id"],
        "criterion_id": criterion_id,
        "criterion_title": Checklist(**ev["checklist"]).criterion(criterion_id).title,
        "comment": comment,
        "author": author,
        "created_at": storage.now(),
        "status": "open",
        "disputed": {
            "answer": ev["overrides"].get(criterion_id, {}).get("answer", item["answer"]),
            "explanation": item["explanation"],
            "evidence": item["evidence"],
            "checked_range": item.get("checked_range"),
        },
        "fragment": [conv["transcript"][i] for i in sorted(idx)],
        "audio_url": conv.get("audio_url"),
        "group_id": ev["group_id"],
        "decision": None,
        "total_before": ev["final"]["total"],
        "total_after": None,
    }
    return storage.put("appeals", appeal["id"], appeal)


def decide_appeal(appeal_id: str, action: str, reason: str,
                  new_answer: Any = None, reviewer: str = "проверяющий") -> dict:
    """action: confirm (оценка верна) | change (меняем ответ на new_answer)."""
    appeal = storage.get("appeals", appeal_id)
    if not appeal:
        raise NotFound(f"апелляция {appeal_id} не найдена")
    if appeal["status"] != "open":
        raise ValueError("апелляция уже рассмотрена")
    if not reason.strip():
        raise ValueError("укажите причину решения")
    ev = get_evaluation(appeal["evaluation_id"])
    if action == "change":
        criterion = Checklist(**ev["checklist"]).criterion(appeal["criterion_id"])
        normalize(criterion, new_answer)  # бросит ValueError, если ответ недопустим
        ev["overrides"][appeal["criterion_id"]] = {
            "answer": new_answer, "reason": reason, "appeal_id": appeal_id,
            "reviewer": reviewer, "decided_at": storage.now(),
        }
        _recalculate(ev)
        storage.put("evaluations", ev["id"], ev)
    elif action != "confirm":
        raise ValueError("action должен быть confirm или change")
    appeal.update({
        "status": "resolved",
        "decision": {"action": action, "new_answer": new_answer if action == "change" else None,
                     "reason": reason, "reviewer": reviewer, "decided_at": storage.now()},
        "total_after": ev["final"]["total"],
    })
    return storage.put("appeals", appeal_id, appeal)


def list_appeals(status: Optional[str] = None, evaluation_id: Optional[str] = None) -> list[dict]:
    aps = storage.list_all("appeals")
    if status:
        aps = [a for a in aps if a["status"] == status]
    if evaluation_id:
        aps = [a for a in aps if a["evaluation_id"] == evaluation_id]
    return aps


# ---------------- пакетная оценка ----------------

# Каждая оценка сама делает 3 параллельных запроса (голосование), поэтому диалогов одновременно — 3.
_executor = ThreadPoolExecutor(max_workers=3)
_jobs: dict[str, dict] = {}


def start_batch(group_id: str, checklist_id: str, only_missing: bool = False) -> dict:
    get_checklist(checklist_id)
    convs = fetch_group(group_id)
    if only_missing:
        done = {e["conversation_id"] for e in storage.list_all("evaluations")
                if e["checklist_id"] == checklist_id}
        convs = [c for c in convs if c["conversation_id"] not in done]
    job = {"id": _id("job"), "group_id": group_id, "checklist_id": checklist_id,
           "total": len(convs), "done": 0, "failed": [], "status": "running"}
    _jobs[job["id"]] = job

    def work(cid: str):
        try:
            run_evaluation(group_id, cid, checklist_id)
        except Exception as e:  # noqa: BLE001 — одна ошибка не должна ронять пакет
            job["failed"].append({"conversation_id": cid, "error": str(e)})
        finally:
            job["done"] += 1
            if job["done"] == job["total"]:
                job["status"] = "finished"

    if not convs:
        job["status"] = "finished"
    for c in convs:
        _executor.submit(work, c["conversation_id"])
    return job


def get_job(job_id: str) -> dict:
    if job_id not in _jobs:
        raise NotFound(f"задача {job_id} не найдена")
    return _jobs[job_id]
