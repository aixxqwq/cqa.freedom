"""Сквозной сценарий защиты в mock-режиме: оценка → N/A → апелляция → новый итог → смена критерия."""
import os

os.environ["CQA_EVALUATOR"] = "mock"
# Пустое значение (а не удаление), чтобы backend/.env не подставил настоящий API: тесты всегда на mock-данных
os.environ["CQA_API_URL"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from cqa import storage  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(storage, "_db", sqlite3.connect(tmp_path / "t.db", check_same_thread=False))
    storage._db.execute("CREATE TABLE docs (collection TEXT, id TEXT, data TEXT, updated_at TEXT, PRIMARY KEY (collection, id))")
    from main import app
    with TestClient(app) as c:
        yield c


def test_full_flow(client):
    convs = client.get("/api/conversations").json()
    assert len(convs) == 3

    ev = client.post("/api/evaluations", json={"conversation_id": "mock-kz-003"}).json()
    assert ev["final"]["total"] is not None
    ident = next(v for v in ev["view"] if v["criterion_id"] == "identification")
    assert ident["answer"] == "na" and ident["na_reason"]

    # Апелляция по шкальному критерию: проверяющий ставит 20 → итог растёт
    ap = client.post("/api/appeals", json={
        "evaluation_id": ev["id"], "criterion_id": "consultation", "comment": "Консультация была полной",
    }).json()
    assert ap["status"] == "open" and ap["fragment"]
    dec = client.post(f"/api/appeals/{ap['id']}/decision", json={
        "action": "change", "new_answer": 20, "reason": "Ответ полный и точный",
    }).json()
    assert dec["total_after"] > dec["total_before"]
    ev2 = client.get(f"/api/evaluations/{ev['id']}").json()
    assert ev2["original"]["total"] == ev["final"]["total"]
    cons = next(v for v in ev2["view"] if v["criterion_id"] == "consultation")
    assert cons["override"]["answer"] == 20

    # Недопустимая ступень шкалы отклоняется
    ap2 = client.post("/api/appeals", json={
        "evaluation_id": ev["id"], "criterion_id": "dialog_management", "comment": "x"}).json()
    r = client.post(f"/api/appeals/{ap2['id']}/decision", json={"action": "change", "new_answer": 9, "reason": "y"})
    assert r.status_code == 400

    # Смена критерия через настройки: версия растёт, новая оценка его использует
    cl = client.get("/api/checklists/default").json()
    cl["criteria"][0] = {"id": "name_client", "title": "Обращение по имени", "weight": 10,
                         "type": "binary", "section": "start",
                         "description": "Да — оператор обратился к клиенту по имени"}
    saved = client.put("/api/checklists/default", json=cl).json()
    assert saved["version"] == 2
    ev3 = client.post("/api/evaluations", json={"conversation_id": "mock-ru-001"}).json()
    assert ev3["checklist_version"] == 2
    assert ev3["items"][0]["criterion_id"] == "name_client"


def test_checklist_versions(client):
    cl = client.get("/api/checklists/default").json()
    # сохранение без изменений не создаёт новую версию
    same = client.put("/api/checklists/default", json=cl).json()
    assert same["version"] == cl["version"]
    cl["criteria"][0]["weight"] = 12
    v2 = client.put("/api/checklists/default", json=cl).json()
    assert v2["version"] == cl["version"] + 1 and v2["saved_at"]
    versions = client.get("/api/checklists/default/versions").json()
    assert [v["version"] for v in versions] == [v2["version"], cl["version"]]
    assert versions[1]["criteria"][0]["weight"] == 10  # старая версия не перезаписана


def test_invalid_checklist_returns_400(client):
    cl = client.get("/api/checklists/default").json()
    cl["criteria"][0]["section"] = "unknown"
    r = client.put("/api/checklists/default", json=cl)
    assert r.status_code == 400


def test_appeal_decided_once(client):
    ev = client.post("/api/evaluations", json={"conversation_id": "mock-ru-001"}).json()
    ap = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "closing", "comment": "x"}).json()
    r = client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "change", "new_answer": "na", "reason": "y"})
    assert r.status_code == 200  # closing допускает N/A
    r = client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "confirm", "reason": "y"})
    assert r.status_code == 400  # уже рассмотрена
