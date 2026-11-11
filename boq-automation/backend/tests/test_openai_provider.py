"""OpenAI provider, exercised through the real OpenAI SDK against a fake HTTP server.

This proves the request is built and the answer parsed correctly. It does not measure how
well an OpenAI model matches: that needs a real key and real BOQs.
"""
import json

import httpx2
import openai
import pytest

from app.core.config import Settings
from app.services.ai_matching import openai_evaluator
from app.services.ai_matching.claude_evaluator import AIUnavailable
from app.services.ai_matching.engine import AI_UNAVAILABLE_FLAG, MatchConfig, MatchingEngine

ANSWER = {
    "requirements": {"equipment_or_component": "ODU", "material": None, "make": "Daikin", "model": "RXQ38BRY16",
                     "capacity": "38 HP", "size": None, "activities": ["supply"], "additional_requirements": []},
    "selections": [{"item_no": "CO-21405", "selection_type": "Explicit", "confidence": 140, "reason": "Exact model"}],
    "unresolved": [], "needs_review": False, "review_reason": None,
}


def fake_client(content=None, refusal=None, finish="stop", status=200, seen=None):
    def handler(req: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.update(json.loads(req.content))
            seen["_url"] = str(req.url)
        if status != 200:
            return httpx2.Response(status, json={"error": {"message": "nope", "type": "x"}})
        message = {"role": "assistant", "content": content, "refusal": refusal}
        return httpx2.Response(200, json={
            "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-5.5",
            "choices": [{"index": 0, "message": message, "finish_reason": finish, "logprobs": None}]})
    return openai.OpenAI(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def test_request_shape_and_parsing():
    seen = {}
    r = openai_evaluator.evaluate({"sales_line": "38 HP - RXQ38BRY16"}, fake_client(json.dumps(ANSWER), seen=seen))
    assert seen["_url"].endswith("/chat/completions") and seen["model"] == "gpt-5.5"
    assert seen["messages"][0]["role"] == "system" and "BOQ engineer" in seen["messages"][0]["content"]
    fmt = seen["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert r.selections[0].item_no == "CO-21405"
    assert r.selections[0].confidence == 100  # clamped into range


def test_refusal_is_unavailable():
    with pytest.raises(AIUnavailable, match="declined"):
        openai_evaluator.evaluate({}, fake_client(None, refusal="I can't help with that"))


def test_api_error_is_unavailable():
    with pytest.raises(AIUnavailable, match="401"):
        openai_evaluator.evaluate({}, fake_client(status=401))


def test_truncated_answer_is_unavailable():
    with pytest.raises(AIUnavailable, match="output tokens"):
        openai_evaluator.evaluate({}, fake_client('{"requirements"', finish="length"))


def test_engine_uses_openai_and_validates_against_master(indexes, line):
    answer = dict(ANSWER, selections=ANSWER["selections"] + [
        {"item_no": "CO-99999", "selection_type": "Explicit", "confidence": 90, "reason": "invented"}])
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="openai"),
                       ai_client=fake_client(json.dumps(answer)))
    r = e.match_line(line("1.1.2.1"))
    assert r.engine == "openai"
    assert [(s.component.item_no, s.source) for s in r.selections] == [("CO-21405", "openai")]
    assert r.selections[0].component.cost_sheet_type == "Highside Supply"
    assert any("CO-99999" in n and "not shortlisted" in n for n in r.notes)


def test_engine_falls_back_to_rules_when_openai_fails(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="openai"), ai_client=fake_client(status=500))
    r = e.match_line(line("1.1.2.1"))
    assert r.engine == "rule" and r.selections[0].component.item_no == "CO-21405"
    assert AI_UNAVAILABLE_FLAG in r.selections[0].flags


@pytest.mark.parametrize("env,mode,model", [
    ({}, "mock", ""),
    ({"anthropic_api_key": "k"}, "claude", "claude-opus-5-5"),
    ({"ai_provider": "openai", "openai_api_key": "k"}, "openai", "gpt-5.5"),
    ({"ai_provider": "openai", "anthropic_api_key": "k"}, "mock", ""),
    ({"ai_provider": "openai", "openai_api_key": "k", "openai_model": "gpt-6-sol"}, "openai", "gpt-6-sol"),
])
def test_provider_selection(env, mode, model):
    s = Settings(_env_file=None, **{"anthropic_api_key": None, "openai_api_key": None, "ai_mode": "auto", **env})
    assert (s.resolved_ai_mode, s.active_model) == (mode, model)
