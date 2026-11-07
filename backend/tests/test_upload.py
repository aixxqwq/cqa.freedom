import base64

import pytest

from cqa.upload import parse_transcript
from tests.test_api import client  # noqa: F401  (фикстура с временной базой)


def test_parse_text_roles_and_time():
    msgs = parse_transcript(
        "[00:00] Оператор: Сәлеметсіз бе! Менің атым Дәулет.\n"
        "Клиент: Шотқа ақша салғым келеді.\n"
        "продолжение реплики клиента\n"
        "[00:20] bot: Перевожу\n"
        "System: чат закрыт"
    )
    assert [m["speaker"] for m in msgs] == ["operator", "client", "bot", "system"]
    assert msgs[0]["timestamp"] == "00:00"
    assert "продолжение" in msgs[1]["text"]


def test_parse_json_array():
    msgs = parse_transcript('[{"timestamp":"00:01","speaker":"operator","text":"Hi"},{"timestamp":"00:05","speaker":"client","text":"Hello"}]')
    assert len(msgs) == 2


@pytest.mark.parametrize("bad", ["", "Привет как дела", "Клиент: только клиент"])
def test_parse_errors(bad):
    with pytest.raises(ValueError):
        parse_transcript(bad)


def test_upload_evaluate_and_audio(client):  # noqa: F811
    audio = "data:audio/mpeg;base64," + base64.b64encode(b"ID3fake-mp3").decode()
    rec = client.post("/api/conversations/upload", json={
        "conversation_id": "my-kz-call", "language": "KZ",
        "transcript": "[00:00] Оператор: Сәлеметсіз бе! Менің атым Дәулет.\n[00:05] Клиент: Рахмет.",
        "audio_base64": audio, "audio_filename": "call.mp3",
    }).json()
    assert rec["channel"] == "voice call" and rec["audio_url"] == "/uploads/my-kz-call.mp3"
    assert client.get(rec["audio_url"]).content == b"ID3fake-mp3"
    listed = client.get("/api/conversations", params={"group_id": "UPLOAD"}).json()
    assert [c["conversation_id"] for c in listed] == ["my-kz-call"]
    ev = client.post("/api/evaluations", json={"conversation_id": "my-kz-call", "group_id": "UPLOAD"}).json()
    assert ev["final"]["total"] is not None
    assert client.delete("/api/conversations/upload/my-kz-call").status_code == 200
