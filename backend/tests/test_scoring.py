import json
from pathlib import Path

import pytest

from cqa.checklist import Checklist
from cqa.scoring import round_score, score
from cqa.checklist import Rounding


def make(criteria, **kw):
    return Checklist(
        id="t",
        name="t",
        sections=kw.pop("sections", []),
        criteria=criteria,
        **kw,
    )


def binary(cid, w, **kw):
    return {"id": cid, "title": cid, "weight": w, "type": "binary", **kw}


def test_all_yes_is_100():
    cl = make([binary("a", 30), binary("b", 70)])
    r = score(cl, {"a": "yes", "b": "yes"})
    assert r.total == 100


def test_weights_applied():
    cl = make([binary("a", 30), binary("b", 70)])
    r = score(cl, {"a": "yes", "b": "no"})
    assert r.total == 30


def test_na_redistributes_weight():
    # c = N/A -> вес 20 делится между a(30) и b(50) пропорционально: 37.5 / 62.5
    cl = make([binary("a", 30), binary("b", 50), binary("c", 20, na_allowed=True)])
    r = score(cl, {"a": "yes", "b": "no", "c": "na"})
    assert r.total_raw == pytest.approx(37.5)
    assert r.total == 37.5  # округление до 2 знаков по умолчанию
    eff = {c.criterion_id: c.effective_weight for c in r.criteria}
    assert eff["c"] == 0
    assert eff["a"] + eff["b"] == pytest.approx(100)


def test_na_not_allowed_raises():
    cl = make([binary("a", 10)])
    with pytest.raises(ValueError):
        score(cl, {"a": "na"})


def test_scale_is_weighted():
    cl = make([
        {"id": "s", "title": "s", "weight": 50, "type": "scale", "scale": {"min": 0, "max": 5}},
        binary("b", 50),
    ])
    r = score(cl, {"s": 4, "b": "yes"})
    assert r.total_raw == pytest.approx(90)  # 50*0.8 + 50


def test_scale_out_of_range():
    cl = make([{"id": "s", "title": "s", "weight": 1, "type": "scale", "scale": {"min": 1, "max": 5}}])
    with pytest.raises(ValueError):
        score(cl, {"s": 0})


def test_critical_flag_only():
    cl = make([binary("a", 50), binary("crit", 50, critical=True)])
    r = score(cl, {"a": "yes", "crit": "no"})
    assert r.critical_errors == ["crit"]
    assert r.total == 50


def test_critical_zero_total():
    cl = make([binary("a", 50), binary("crit", 50, critical=True)], critical_policy="zero_total")
    r = score(cl, {"a": "yes", "crit": "no"})
    assert r.total == 0


def test_sections():
    cl = make(
        [binary("a", 10, section="s1"), binary("b", 10, section="s1"), binary("c", 10, section="s2")],
        sections=[{"id": "s1", "name": "S1"}, {"id": "s2", "name": "S2"}],
    )
    r = score(cl, {"a": "yes", "b": "no", "c": "yes"})
    sec = {s.id: s.score for s in r.sections}
    assert sec == {"s1": 50, "s2": 100}


def test_all_na_gives_none():
    cl = make([binary("a", 10, na_allowed=True)])
    r = score(cl, {"a": "na"})
    assert r.total is None


@pytest.mark.parametrize("mode,value,expected", [
    ("half_up", 2.5, 3), ("bankers", 2.5, 2), ("floor", 2.9, 2), ("ceil", 2.1, 3),
])
def test_rounding_modes(mode, value, expected):
    assert round_score(value, Rounding(decimals=0, mode=mode)) == expected


def load_default():
    path = Path(__file__).parent.parent / "checklists" / "default.json"
    return Checklist(**json.loads(path.read_text(encoding="utf-8")))


def test_default_checklist_loads():
    cl = load_default()
    assert len(cl.criteria) == 8
    assert sum(c.weight for c in cl.criteria) == 100
    assert cl.rounding.decimals == 2


def test_scale_levels_only():
    cl = load_default()
    base = {"contact": "yes", "need": "yes", "identification": "yes", "empathy": "yes",
            "dialog_management": 10, "closing": "yes", "gross_errors": "no"}
    with pytest.raises(ValueError):
        score(cl, {**base, "consultation": 12})  # 12 — не ступень (0/5/10/15/20)
    assert score(cl, {**base, "consultation": 20}).total == 100


def test_pdf_example_na_and_rounding():
    # Идентификация N/A → её 20 баллов делятся между остальными (сумма весов 80).
    # Баллы: 10 + 0 + 20*15/20 + 15 + 15*7/10 + 5 = 55.5 из 80 → 69.375 → 69.38
    cl = load_default()
    r = score(cl, {"contact": "yes", "need": "no", "identification": "na", "consultation": 15,
                   "empathy": "yes", "dialog_management": 7, "closing": "yes", "gross_errors": "no"})
    assert r.total_raw == pytest.approx(69.375)
    assert r.total == 69.38
    assert sum(c.max_points for c in r.criteria) == pytest.approx(100)


def test_gross_error_flag_has_no_points():
    cl = load_default()
    answers = {"contact": "yes", "need": "yes", "identification": "yes", "consultation": 20,
               "empathy": "yes", "dialog_management": 10, "closing": "yes"}
    clean = score(cl, {**answers, "gross_errors": "no"})
    dirty = score(cl, {**answers, "gross_errors": "yes"})
    assert clean.total == dirty.total == 100
    assert dirty.critical_errors == ["gross_errors"] and clean.critical_errors == []
    flag = next(c for c in dirty.criteria if c.criterion_id == "gross_errors")
    assert flag.scored is False and flag.points == 0


def test_all_na_total_not_calculated():
    cl = make([binary("a", 10, na_allowed=True), {"id": "f", "title": "f", "weight": 0, "type": "flag"}])
    r = score(cl, {"a": "na", "f": "no"})
    assert r.total is None
