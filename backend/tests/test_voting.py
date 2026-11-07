import json
from pathlib import Path

from cqa.checklist import Checklist
from cqa.evaluator import aggregate
from tests.test_api import client  # noqa: F401

CL = Checklist(**json.loads((Path(__file__).parent.parent / "checklists" / "default.json").read_text(encoding="utf-8")))


def run(**answers):
    base = {"contact": "yes", "need": "yes", "identification": "na", "consultation": 20,
            "empathy": "yes", "dialog_management": 10, "closing": "yes", "gross_errors": "no"}
    base.update(answers)
    return {"model": "m", "usage": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0.01},
            "items": [{"criterion_id": c.id, "answer": base[c.id], "explanation": f"{c.id}={base[c.id]}",
                       "evidence": [], "checked_range": None, "na_reason": None} for c in CL.criteria]}


def item(res, cid):
    return next(i for i in res["items"] if i["criterion_id"] == cid)


def test_majority_wins_and_explanation_matches():
    res = aggregate([run(empathy="no"), run(), run()], CL)
    e = item(res, "empathy")
    assert e["answer"] == "yes" and e["agreement"] == "2/3" and e["explanation"] == "empathy=yes"
    assert item(res, "contact")["agreement"] == "3/3"
    assert res["usage"]["cost_usd"] == 0.03 and res["votes"] == 3


def test_scale_without_majority_uses_median_level():
    res = aggregate([run(consultation=20), run(consultation=10), run(consultation=15)], CL)
    c = item(res, "consultation")
    assert c["answer"] == 15 and c["agreement"] == "1/3"


def test_accuracy_against_ground_truth(client):  # noqa: F811
    ev = client.post("/api/evaluations", json={"conversation_id": "mock-ru-001"}).json()
    # mock-оценки в точность не входят — помечаем как настоящую
    from cqa import storage
    doc = storage.get("evaluations", ev["id"]); doc["model"] = "claude-test"; storage.put("evaluations", ev["id"], doc)
    gt = {i["criterion_id"]: i["answer"] for i in ev["items"]}
    gt["empathy"] = "no"
    assert client.put("/api/ground-truth/mock-ru-001", json={"answers": gt}).status_code == 200
    acc = client.get("/api/accuracy").json()
    assert acc["compared_dialogs"] == 1
    assert acc["criterion_accuracy"] == round(7 / 8 * 100, 1)
    row = acc["dialogs"][0]
    assert [m["criterion_id"] for m in row["mismatches"]] == ["empathy"]
    bad = client.put("/api/ground-truth/mock-ru-001", json={"answers": {"consultation": 12}})
    assert bad.status_code == 400  # 12 — не ступень шкалы
