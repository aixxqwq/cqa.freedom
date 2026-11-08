"""Оценка диалога: ИИ читает транскрипт и отвечает по каждому критерию.

ИИ возвращает только ответы, объяснения и номера реплик-доказательств.
Баллы считает scoring.py — так итог детерминирован и проверяем.

Режимы (переменная CQA_EVALUATOR; если не задана — выбирается по тому, какой ключ есть):
  * claude   — Claude API (нужен ANTHROPIC_API_KEY)
  * deepseek — DeepSeek API (нужен DEEPSEEK_API_KEY)
  * mock     — простая эвристика без ИИ, чтобы фронтенд мог работать офлайн
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Any

from .checklist import Checklist, Criterion
from .scoring import normalize

MODEL = os.getenv("CQA_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("CQA_EFFORT", "medium")
# Цены за 1M токенов (input, output) для подсчёта стоимости одной оценки.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50),
          # DeepSeek: цена «в часы пик» (верхняя оценка); вне пика вдвое дешевле
          "deepseek-flash": (0.30, 1.20), "deepseek-v4-pro": (1.32, 3.96)}

DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
# Режим рассуждений DeepSeek: disabled (быстрее и дешевле, как старый V3) или enabled
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled")
DEEPSEEK_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/chat/completions"


def provider() -> str:
    explicit = os.getenv("CQA_EVALUATOR")
    if explicit:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    return "mock"

SYSTEM_PROMPT = """Ты — специалист по контролю качества (QA) контакт-центра брокерской компании.
Ты оцениваешь работу ОПЕРАТОРА по чек-листу. Диалоги бывают на русском, казахском и английском;
объяснения всегда пиши на русском.

Правила:
- Оценивай только реплики с ролью operator. Реплики client, bot и system — это контекст:
  они помогают понять ситуацию, но за действия бота оператор не отвечает.
- Для каждого критерия дай ответ:
    Да/Нет: "yes" (выполнено) или "no" (не выполнено);
    флаг: "yes" (нарушение выявлено) или "no" (не выявлено);
    шкала: одна из допустимых ступеней числом в строке, например "15";
    "na" — только если критерий допускает N/A и его условие выполнено. Тогда заполни na_reason.
- evidence — номера реплик [N], на которых основан вывод. Цитируй только то, что есть в тексте.
- Если действие ПРОПУЩЕНО (ответ "no"), укажи проверенный участок разговора:
  checked_from и checked_to — номера первой и последней реплики, где действие должно было быть.
  Если участок не нужен, поставь -1 и -1.
- explanation — 1–3 предложения: почему такой ответ, со ссылкой на номера реплик.
- Не выдумывай факты, которых нет в транскрипте. Следуй буквально формулировке критерия и описаниям
  ступеней: не будь ни строже, ни мягче чек-листа. Если сомневаешься, выбери ответ, который точнее всего
  соответствует формулировке, и коротко объясни сомнение.
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_id": {"type": "string"},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "integer"}},
                    "checked_from": {"type": "integer"},
                    "checked_to": {"type": "integer"},
                    "na_reason": {"type": "string"},
                },
                "required": [
                    "criterion_id", "answer", "explanation", "evidence",
                    "checked_from", "checked_to", "na_reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["criteria"],
    "additionalProperties": False,
}


def format_transcript(conversation: dict) -> str:
    lines = []
    for i, m in enumerate(conversation["transcript"]):
        lines.append(f"[{i}] {m.get('timestamp', '')} {m.get('speaker', '?')}: {m.get('text', '')}")
    return "\n".join(lines)


def format_checklist(checklist: Checklist) -> str:
    out = []
    for c in checklist.criteria:
        if c.type == "binary":
            kind = 'Да/Нет → "yes"/"no"'
        elif c.type == "flag":
            kind = 'ФЛАГ без баллов: "yes" = нарушение выявлено, "no" = не выявлено'
        else:
            kind = f"шкала {c.scale.min:g}..{c.scale.max:g}"
        na = f'N/A ("na") разрешён, только если: {c.na_rule or "критерий неприменим"}' if c.na_allowed else "N/A запрещён"
        block = f"- id={c.id} | {c.title} | {kind} | {na}\n  {c.description}"
        if c.type == "scale" and c.scale.levels:
            block += "\n  Допустимые ступени (выбери ровно одну):"
            for lv in c.scale.levels:
                block += f"\n    {lv.value:g} — {lv.label}"
        out.append(block)
    return "\n".join(out)


def _snap(c: Criterion, value: float) -> float:
    """Ближайшая допустимая ступень шкалы (при равенстве — нижняя, строже)."""
    allowed = c.scale.allowed()
    value = min(max(value, c.scale.min), c.scale.max)
    if not allowed:
        return value
    return min(sorted(allowed), key=lambda a: abs(a - value))


def _clean_item(c: Criterion, raw: dict, n_messages: int) -> dict:
    """Приводит ответ ИИ к допустимому виду, чтобы scoring не упал."""
    answer: Any = str(raw.get("answer", "")).strip().lower()
    if answer in ("n/a", "н/д"):
        answer = "na"
    if c.type == "scale" and answer != "na":
        try:
            answer = _snap(c, float(answer))
        except ValueError:
            answer = c.scale.min
        if float(answer).is_integer():
            answer = int(answer)
    if answer == "na" and not c.na_allowed:
        answer = "no" if c.type == "binary" else ("yes" if c.type == "flag" else c.scale.min)
    if c.type in ("binary", "flag") and answer not in ("yes", "no", "na"):
        answer = "no"
    normalize(c, answer)  # финальная проверка

    evidence = sorted({i for i in raw.get("evidence", []) if 0 <= i < n_messages})
    cf, ct = raw.get("checked_from", -1), raw.get("checked_to", -1)
    checked = {"from": cf, "to": ct} if 0 <= cf <= ct < n_messages else None
    return {
        "criterion_id": c.id,
        "answer": answer,
        "explanation": raw.get("explanation", ""),
        "evidence": evidence,
        "checked_range": checked,
        "na_reason": raw.get("na_reason") or None if answer == "na" else None,
    }


VOTES = int(os.getenv("CQA_VOTES", "3"))


def evaluate(conversation: dict, checklist: Checklist) -> dict:
    """Возвращает {"items": [...], "model", "usage", "runs", "votes", "duration_s"}.

    ИИ оценивает диалог VOTES раз независимо (параллельно), по каждому критерию
    берётся ответ большинства, для шкалы без большинства — медиана. Так оценка
    стабильнее, а доля совпавших голосов показывает уверенность.
    """
    mode = provider()
    started = time.time()
    if mode == "mock":
        runs = [_evaluate_mock(conversation, checklist)]
    else:
        fn = {"claude": _evaluate_claude, "deepseek": _evaluate_deepseek}.get(mode)
        if not fn:
            raise RuntimeError(f"Неизвестный CQA_EVALUATOR: {mode}")
        n = max(1, VOTES)
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(fn, conversation, checklist) for _ in range(n)]
            runs, errors = [], []
            for f in futures:
                try:
                    runs.append(f.result())
                except Exception as e:  # noqa: BLE001 — одна неудачная попытка не валит всю оценку
                    errors.append(e)
            if not runs:
                raise errors[0]
    result = aggregate(runs, checklist)
    result["duration_s"] = round(time.time() - started, 1)
    return result


def aggregate(runs: list[dict], checklist: Checklist) -> dict:
    items = []
    for i, c in enumerate(checklist.criteria):
        votes = [r["items"][i] for r in runs]
        answers = [v["answer"] for v in votes]
        counts = Counter(str(a) for a in answers)
        top, top_n = counts.most_common(1)[0]
        if top_n * 2 > len(answers) or len(answers) == 1:
            final = next(a for a in answers if str(a) == top)
        elif c.type == "scale" and any(a != "na" for a in answers):
            nums = sorted(float(a) for a in answers if a != "na")
            final = _snap(c, median(nums))
            final = int(final) if float(final).is_integer() else final
        else:
            final = answers[0]
        # объяснение и доказательства — из голоса, совпавшего с итоговым ответом (или ближайшего)
        base = next((v for v in votes if str(v["answer"]) == str(final)), votes[0])
        agree = sum(1 for a in answers if str(a) == str(final))
        items.append({**base, "answer": final, "votes": answers, "agreement": f"{agree}/{len(answers)}"})
    usage = {
        "input_tokens": sum(r["usage"]["input_tokens"] for r in runs),
        "output_tokens": sum(r["usage"]["output_tokens"] for r in runs),
        "cost_usd": round(sum(r["usage"]["cost_usd"] for r in runs), 4),
    }
    return {
        "items": items,
        "model": runs[0]["model"],
        "usage": usage,
        "votes": len(runs),
        "runs": [{"answers": {it["criterion_id"]: it["answer"] for it in r["items"]},
                  "raw": r.get("raw"), "usage": r["usage"], "duration_s": r.get("duration_s")} for r in runs],
    }


def build_user_prompt(conversation: dict, checklist: Checklist) -> str:
    return (
        f"Язык диалога: {conversation.get('language')}. Канал: {conversation.get('channel')}.\n\n"
        f"ОБЩИЕ ПРАВИЛА ОЦЕНКИ:\n{checklist.guidance}\n\n"
        f"ЧЕК-ЛИСТ:\n{format_checklist(checklist)}\n\n"
        f"ТРАНСКРИПТ (номер реплики в квадратных скобках):\n{format_transcript(conversation)}\n\n"
        f"Оцени оператора по КАЖДОМУ критерию чек-листа."
    )


def _items_from_json(text: str, conversation: dict, checklist: Checklist) -> list[dict]:
    data = json.loads(text)
    raw = {r.get("criterion_id"): r for r in data.get("criteria", []) if isinstance(r, dict)}
    n = len(conversation["transcript"])
    return [
        _clean_item(c, raw.get(c.id) or {"answer": "no", "explanation": "Модель не вернула ответ по критерию"}, n)
        for c in checklist.criteria
    ]


# DeepSeek в JSON-режиме не знает схему: описываем формат словами и примером (слово json обязательно).
DEEPSEEK_FORMAT = """
Ответ верни строго в формате json, без пояснений вокруг, по такому образцу:
{"criteria": [
  {"criterion_id": "contact", "answer": "yes", "explanation": "Да. В [3] оператор поздоровался и представился.",
   "evidence": [3], "checked_from": -1, "checked_to": -1, "na_reason": ""},
  {"criterion_id": "consultation", "answer": "15", "explanation": "...", "evidence": [5, 7],
   "checked_from": 5, "checked_to": 9, "na_reason": ""}
]}
Поля обязательны для КАЖДОГО критерия чек-листа: criterion_id (как в чек-листе), answer (строка),
explanation, evidence (массив номеров реплик), checked_from, checked_to (числа, -1 если не нужно), na_reason.
"""


def _evaluate_deepseek(conversation: dict, checklist: Checklist) -> dict:
    import httpx

    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("Не задан DEEPSEEK_API_KEY в backend/.env")
    started = time.time()
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + DEEPSEEK_FORMAT},
            {"role": "user", "content": build_user_prompt(conversation, checklist)},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": DEEPSEEK_THINKING},
        # с рассуждениями модели нужно больше места: рассуждение тоже входит в лимит ответа
        "max_tokens": 8000 if DEEPSEEK_THINKING == "disabled" else 32000,
    }
    last_error = "пустой ответ"
    for _ in range(2):  # DeepSeek иногда возвращает пустой JSON — повторяем один раз
        r = httpx.post(DEEPSEEK_URL, json=body, timeout=180,
                       headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        if r.status_code == 402:
            raise RuntimeError("DeepSeek: недостаточно средств на балансе")
        if r.status_code == 401:
            raise RuntimeError("DeepSeek: неверный API-ключ")
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if choice.get("finish_reason") == "length":
            last_error = "ответ обрезан (max_tokens)"
            continue
        if not text:
            continue
        try:
            items = _items_from_json(text, conversation, checklist)
        except (json.JSONDecodeError, ValueError, AttributeError) as e:
            last_error = f"некорректный JSON: {e}"
            continue
        u = data.get("usage", {})
        tin, tout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        p_in, p_out = PRICES.get(DEEPSEEK_MODEL, (0, 0))
        return {
            "items": items,
            "model": data.get("model", DEEPSEEK_MODEL),
            "usage": {"input_tokens": tin, "output_tokens": tout,
                      "cost_usd": round((tin * p_in + tout * p_out) / 1_000_000, 5)},
            "raw": text,
            "duration_s": round(time.time() - started, 1),
        }
    raise RuntimeError(f"DeepSeek: {last_error}")


def _evaluate_claude(conversation: dict, checklist: Checklist) -> dict:
    started = time.time()
    import anthropic

    client = anthropic.Anthropic()
    user = build_user_prompt(conversation, checklist)
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        # Если основная модель откажет по политике безопасности, API сам повторит запрос на другой.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Модель отказалась оценивать этот диалог")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Ответ модели обрезан (max_tokens)")
    text = next(b.text for b in response.content if b.type == "text")
    raw = {r["criterion_id"]: r for r in json.loads(text)["criteria"]}

    n = len(conversation["transcript"])
    items = []
    for c in checklist.criteria:
        r = raw.get(c.id) or {"answer": "no", "explanation": "Модель не вернула ответ по критерию"}
        items.append(_clean_item(c, r, n))

    u = response.usage
    p_in, p_out = PRICES.get(MODEL, (0, 0))
    cost = (u.input_tokens * p_in + u.output_tokens * p_out) / 1_000_000
    return {
        "items": items,
        "model": response.model,
        "usage": {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "cost_usd": round(cost, 4)},
        "raw": text,
        "duration_s": round(time.time() - started, 1),
    }


# ---------- mock: эвристика для разработки без ключа ----------

_GREET = re.compile(r"здравств|добр(ый|ое)|привет|hello|hi\b|good (morning|afternoon)|сәлем|қайырлы", re.I)
_BYE = re.compile(r"до свидания|всего доброго|хорошего дня|goodbye|have a nice|сау болыңыз|еще вопрос|other questions", re.I)
_IDENT = re.compile(r"кодов|паспорт|иин|дата рождения|verif|date of birth|жеке куәлік|код", re.I)


def _evaluate_mock(conversation: dict, checklist: Checklist) -> dict:
    msgs = conversation["transcript"]
    ops = [i for i, m in enumerate(msgs) if m.get("speaker") == "operator"]
    first_op = ops[0] if ops else 0
    last_op = ops[-1] if ops else 0

    def find(rx):
        return [i for i in ops if rx.search(msgs[i].get("text", ""))]

    items = []
    for c in checklist.criteria:
        if c.type == "flag":
            raw = {"answer": "no", "evidence": [], "explanation": "[mock] Нарушений не найдено."}
        elif c.id in ("greeting", "contact"):
            hit = [i for i in find(_GREET) if i <= first_op + 1]
            raw = {"answer": "yes" if hit else "no", "evidence": hit[:1] or [first_op],
                   "explanation": "[mock] Поиск приветствия в первой реплике оператора.",
                   "checked_from": first_op, "checked_to": first_op}
        elif c.id == "closing":
            hit = find(_BYE)
            raw = {"answer": "yes" if hit else "no", "evidence": hit[-1:] or [last_op],
                   "explanation": "[mock] Поиск прощания в конце диалога.",
                   "checked_from": max(first_op, last_op - 2), "checked_to": last_op}
        elif c.id == "identification" and c.na_allowed and not find(_IDENT):
            raw = {"answer": "na", "evidence": [], "explanation": "[mock] Проверки личности не требовалось.",
                   "na_reason": "Запрос не требовал идентификации."}
        elif c.type == "scale":
            allowed = c.scale.allowed() or [c.scale.max - 1]
            second = allowed[1] if len(allowed) > 1 else allowed[0]  # вторая сверху ступень
            raw = {"answer": f"{second:g}", "evidence": ops[:2],
                   "explanation": "[mock] Условная оценка без ИИ."}
        else:
            raw = {"answer": "yes", "evidence": ops[:1], "explanation": "[mock] Условная оценка без ИИ."}
        raw.setdefault("checked_from", -1)
        raw.setdefault("checked_to", -1)
        items.append(_clean_item(c, raw, len(msgs)))
    return {"items": items, "model": "mock", "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0},
            "raw": json.dumps({"criteria": items}, ensure_ascii=False), "duration_s": 0}
