"""Модель чек-листа: критерии, веса, шкалы, N/A, секции, правила итога.

Чек-лист хранится как JSON (см. checklists/default.json) и редактируется
через интерфейс. Pydantic проверяет, что конфигурация корректна.

Типы критериев:
  binary — Да/Нет: «Да» даёт полный вес, «Нет» — ноль
  scale  — шкала; если заданы levels, допустимы только эти ступени
  flag   — флаг без баллов (например, «Грубые ошибки»): «Да» = нарушение выявлено.
           В итог не входит, отмечается отдельно.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ScaleLevel(BaseModel):
    value: float
    label: str = ""   # описание ступени — подсказка для ИИ и для людей


class Scale(BaseModel):
    min: float = 0
    max: float = 5
    # Ступени шкалы. Если заданы, ответ обязан совпасть с одной из них,
    # а min/max берутся из ступеней.
    levels: list[ScaleLevel] = []

    @model_validator(mode="after")
    def _check(self):
        if self.levels:
            self.levels = sorted(self.levels, key=lambda l: l.value, reverse=True)
            values = [l.value for l in self.levels]
            if len(values) != len(set(values)):
                raise ValueError("ступени шкалы должны быть разными")
            self.min, self.max = min(values), max(values)
        if self.max <= self.min:
            raise ValueError("верх шкалы должен быть больше низа")
        return self

    def allowed(self) -> Optional[list[float]]:
        return [l.value for l in self.levels] or None


class Section(BaseModel):
    id: str
    name: str


class Criterion(BaseModel):
    id: str
    title: str
    # Подробная формулировка для ИИ: что значит «Да».
    description: str = ""
    section: Optional[str] = None
    weight: float = Field(ge=0)
    type: Literal["binary", "scale", "flag"] = "binary"
    scale: Optional[Scale] = None
    na_allowed: bool = False
    # Условие, при котором ставится N/A.
    na_rule: str = ""
    # Грубая ошибка: для binary/scale — ответ «Нет»/минимум шкалы,
    # для flag — всегда (ответ «Да»).
    critical: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.type == "scale" and self.scale is None:
            self.scale = Scale()
        if self.type != "scale":
            self.scale = None
        if self.type == "flag":
            self.weight = 0
            self.na_allowed = False
            self.critical = True
        elif self.weight <= 0:
            raise ValueError(f"критерий {self.id}: вес должен быть больше 0")
        return self


class Rounding(BaseModel):
    decimals: int = 2
    # half_up: 2.5 -> 3 (как в Excel). bankers: 2.5 -> 2 (как round() в Python).
    mode: Literal["half_up", "bankers", "floor", "ceil"] = "half_up"


class Checklist(BaseModel):
    id: str
    name: str
    version: int = 1
    # Общие правила оценки (памятка) — целиком попадают в промпт ИИ.
    guidance: str = ""
    sections: list[Section] = []
    criteria: list[Criterion]
    rounding: Rounding = Rounding()
    # Что делать при грубой ошибке:
    #   flag         - только отметить, итог не меняется
    #   zero_total   - итог = 0
    #   zero_section - секция с грубой ошибкой = 0
    critical_policy: Literal["flag", "zero_total", "zero_section"] = "flag"

    @model_validator(mode="after")
    def _check(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("id критериев должны быть уникальны")
        section_ids = {s.id for s in self.sections}
        for c in self.criteria:
            if c.section and c.section not in section_ids:
                raise ValueError(f"критерий {c.id}: неизвестная секция {c.section}")
        return self

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)
