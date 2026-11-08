"""DeepSeek-провайдер: запрос и разбор ответа без настоящей сети."""
import json

import httpx
import pytest

from cqa import evaluator
from tests.test_voting import CL

CONV = {"language": "RU", "channel": "agent chat", "transcript": [
    {"timestamp": "00:00", "speaker": "operator", "text": "Здравствуйте, меня зовут Алина", "idx": 0},
    {"timestamp": "00:05", "speaker": "client", "text": "Спасибо", "idx": 1},
]}


def answer_json(**over):
    base = {c.id: ("15" if c.type == "scale" and c.id == "consultation" else "7" if c.type == "scale" else "yes")
            for c in CL.criteria}
    base["gross_errors"] = "no"
    base.update(over)
    return json.dumps({"criteria": [{"criterion_id": k, "answer": v, "explanation": f"{k}", "evidence": [0],
                                     "checked_from": -1, "checked_to": -1, "na_reason": ""} for k, v in base.items()]})


class FakeResp:
    def __init__(self, content, status=200, finish="stop"):
        self.status_code = status
        self._data = {"model": "deepseek-flash", "choices": [{"message": {"content": content}, "finish_reason": finish}],
                      "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


def test_deepseek_parses_and_costs(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    sent = {}

    def fake_post(url, json=None, **kw):
        sent.update(json)
        return FakeResp(answer_json())
    monkeypatch.setattr(httpx, "post", fake_post)
    res = evaluator._evaluate_deepseek(CONV, CL)
    assert sent["response_format"] == {"type": "json_object"} and "json" in sent["messages"][0]["content"]
    assert {i["criterion_id"]: i["answer"] for i in res["items"]}["consultation"] == 15
    assert res["usage"]["cost_usd"] == pytest.approx((1000 * 0.30 + 500 * 1.20) / 1e6)


def test_deepseek_retries_blank_then_succeeds(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    replies = iter([FakeResp("   "), FakeResp(answer_json())])
    monkeypatch.setattr(httpx, "post", lambda *a, **k: next(replies))
    assert len(evaluator._evaluate_deepseek(CONV, CL)["items"]) == len(CL.criteria)


def test_deepseek_no_balance(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp("", status=402))
    with pytest.raises(RuntimeError, match="недостаточно средств"):
        evaluator._evaluate_deepseek(CONV, CL)


def test_provider_choice(monkeypatch):
    monkeypatch.delenv("CQA_EVALUATOR", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    assert evaluator.provider() == "deepseek"
