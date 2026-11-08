from cqa import export
from tests.test_api import client  # noqa: F401


def test_export_format(client, tmp_path, monkeypatch):  # noqa: F811
    from cqa import storage
    ev = client.post("/api/evaluations", json={"conversation_id": "mock-ru-001"}).json()
    doc = storage.get("evaluations", ev["id"]); doc["model"] = "test-model"; storage.put("evaluations", ev["id"], doc)

    data = export.build_results("DEV", "Команда", "external_api")
    by = {r["conversation_id"]: r for r in data["results"]}
    assert len(by) == 3  # все диалоги набора, включая неоценённые
    ok = by["mock-ru-001"]
    assert ok["status"] == "ok"
    assert [c["criterion_id"] for c in ok["criteria"]] == list(export.CRITERIA_MAP.values())
    vals = {c["criterion_id"]: c["value"] for c in ok["criteria"]}
    assert vals["identification"] is None and vals["critical"] is False and isinstance(vals["dialogue"], int)
    assert all(t >= 1 for c in ok["criteria"] for t in c["evidence_turns"])
    assert ok["score"]["critical_flag"] is False and 0 <= ok["score"]["final_score"] <= 100
    err = by["mock-en-002"]
    assert err == {"conversation_id": "mock-en-002", "status": "error", "criteria": [], "score": None,
                   "error": "Диалог не был оценён системой."}
    assert export.validate(data, 3) == []
    assert export.validate(data, 10) == ["записей 3, ожидалось 10"]
    assert export._renumber("В [0] и [9]") == "В [1] и [10]"
