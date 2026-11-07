"""Подсчёт баллов. Чистая математика, без ИИ — легко тестировать и объяснять жюри.

Правила:
  * каждый ответ переводится в долю 0..1
      binary: yes -> 1, no -> 0
      scale:  (value - min) / (max - min)
  * N/A исключает критерий; его вес перераспределяется между применимыми
    пропорционально их весам (effective_weight = weight / sum(applicable) * 100)
  * баллы критерия = effective_weight * доля, итог = сумма баллов (0..100)
  * секции считаются так же, но внутри своей секции
  * грубая ошибка отмечается отдельно; влияние на итог — по critical_policy
  * округление — по правилу из чек-листа, только в самом конце
"""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Optional, Union

from pydantic import BaseModel

from .checklist import Checklist, Criterion, Rounding

Answer = Union[str, float, int, None]  # "yes" | "no" | "na" | число шкалы


class CriterionScore(BaseModel):
    criterion_id: str
    title: str
    section: Optional[str]
    answer: Answer
    is_na: bool
    weight: float               # вес из чек-листа
    effective_weight: float     # вес после перераспределения N/A (сумма = 100)
    normalized: Optional[float] # доля 0..1, None для N/A
    points: float               # заработанные баллы (без округления)
    max_points: float           # = effective_weight
    scored: bool                # False для флагов (грубые ошибки) — без баллов
    critical_error: bool


class SectionScore(BaseModel):
    id: str
    name: str
    score: Optional[float]      # None, если вся секция N/A
    critical_error: bool


class ScoreResult(BaseModel):
    total: Optional[float]      # округлённый итог 0..100 (None, если всё N/A)
    total_raw: Optional[float]
    critical_errors: list[str]  # id критериев с грубой ошибкой
    critical_policy: str
    sections: list[SectionScore]
    criteria: list[CriterionScore]


def is_na(answer: Answer) -> bool:
    return isinstance(answer, str) and answer.lower() == "na"


def normalize(criterion: Criterion, answer: Answer) -> Optional[float]:
    """Доля 0..1 для ответа; None для N/A. Бросает ValueError на неверный ответ."""
    if answer is None:
        raise ValueError(f"{criterion.id}: нет ответа")
    if is_na(answer):
        if not criterion.na_allowed:
            raise ValueError(f"{criterion.id}: N/A не разрешён для этого критерия")
        return None
    if criterion.type in ("binary", "flag"):
        a = str(answer).lower()
        if a in ("yes", "1", "true"):
            return 1.0
        if a in ("no", "0", "false"):
            return 0.0
        raise ValueError(f"{criterion.id}: ожидался yes/no/na, получено {answer!r}")
    # scale
    s = criterion.scale
    try:
        v = float(answer)
    except (TypeError, ValueError):
        raise ValueError(f"{criterion.id}: ожидалось число, получено {answer!r}")
    allowed = s.allowed()
    if allowed is not None and v not in allowed:
        raise ValueError(f"{criterion.id}: {v:g} не ступень шкалы {[f'{x:g}' for x in allowed]}")
    if not (s.min <= v <= s.max):
        raise ValueError(f"{criterion.id}: {v:g} вне шкалы [{s.min:g}; {s.max:g}]")
    return (v - s.min) / (s.max - s.min)


def is_critical_error(criterion: Criterion, normalized: Optional[float]) -> bool:
    if normalized is None:
        return False
    if criterion.type == "flag":
        return normalized == 1.0          # «Да» = нарушение выявлено
    return criterion.critical and normalized == 0.0


def round_score(value: Optional[float], rounding: Rounding) -> Optional[float]:
    if value is None:
        return None
    mode = {
        "half_up": ROUND_HALF_UP,
        "bankers": ROUND_HALF_EVEN,
        "floor": ROUND_FLOOR,
        "ceil": ROUND_CEILING,
    }[rounding.mode]
    # repr через str, чтобы 2.675 не превратилось в 2.67499999...
    q = Decimal(1).scaleb(-rounding.decimals)
    return float(Decimal(str(value)).quantize(q, rounding=mode))


def _weighted(items: list[tuple[Criterion, Optional[float]]]) -> tuple[Optional[float], dict[str, float]]:
    """Возвращает (итог 0..100 или None, effective_weight по id). Флаги не участвуют."""
    applicable = [(c, n) for c, n in items if n is not None and c.type != "flag"]
    total_w = sum(c.weight for c, _ in applicable)
    eff = {c.id: 0.0 for c, _ in items}
    if total_w == 0:
        return None, eff
    total = 0.0
    for c, n in applicable:
        eff[c.id] = c.weight / total_w * 100
        total += eff[c.id] * n
    return total, eff


def score(checklist: Checklist, answers: dict[str, Answer]) -> ScoreResult:
    """answers: {criterion_id: "yes"|"no"|"na"|число}."""
    items: list[tuple[Criterion, Optional[float]]] = []
    for c in checklist.criteria:
        items.append((c, normalize(c, answers.get(c.id))))

    total_raw, eff = _weighted(items)
    critical = [c.id for c, n in items if is_critical_error(c, n)]

    criteria_scores = [
        CriterionScore(
            criterion_id=c.id,
            title=c.title,
            section=c.section,
            answer=answers.get(c.id),
            is_na=n is None,
            weight=c.weight,
            effective_weight=eff[c.id],
            normalized=n,
            points=eff[c.id] * n if n is not None and c.type != "flag" else 0.0,
            max_points=eff[c.id],
            scored=c.type != "flag",
            critical_error=c.id in critical,
        )
        for c, n in items
    ]

    sections: list[SectionScore] = []
    for s in checklist.sections:
        sec_items = [(c, n) for c, n in items if c.section == s.id]
        sec_total, _ = _weighted(sec_items)
        sec_critical = any(c.id in critical for c, _ in sec_items)
        if sec_critical and checklist.critical_policy == "zero_section" and sec_total is not None:
            sec_total = 0.0
        sections.append(
            SectionScore(
                id=s.id,
                name=s.name,
                score=round_score(sec_total, checklist.rounding),
                critical_error=sec_critical,
            )
        )

    if critical and total_raw is not None:
        if checklist.critical_policy == "zero_total":
            total_raw = 0.0
        elif checklist.critical_policy == "zero_section":
            # Баллы критериев из «обнулённых» секций не засчитываются.
            bad_sections = {c.section for c, _ in items if c.id in critical}
            total_raw = sum(
                cs.points for cs in criteria_scores if cs.section not in bad_sections
            )

    if total_raw is not None and math.isclose(total_raw, round(total_raw), abs_tol=1e-9):
        total_raw = float(round(total_raw))

    return ScoreResult(
        total=round_score(total_raw, checklist.rounding),
        total_raw=total_raw,
        critical_errors=critical,
        critical_policy=checklist.critical_policy,
        sections=sections,
        criteria=criteria_scores,
    )
