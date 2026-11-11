"""Gemini provider, exercised through the real Google Gen AI SDK against a fake HTTP server.

Proves the request is built and the answer parsed. It does not measure match quality.
"""
import json

import httpx2
import pytest
from google import genai
from google.genai import types

from app.core.config import Settings
from app.services.ai_matching import gemini_evaluator
from app.services.ai_matching.claude_evaluator import AIUnavailable
from app.services.ai_matching.engine import AI_UNAVAILABLE_FLAG, MatchConfig, MatchingEngine

ANSWER = {
    "requirements": {"equipment_or_component": "ODU", "material": None, "make": "Daikin", "model": "RXQ38BRY16",
                     "capacity": "38 HP", "size": None, "activities": ["supply"], "additional_requirements": []},
    "selections": [{"item_no": "CO-21405", "selection_type": "Explicit", "confidence": -5, "reason": "Exact model"}],
    "unresolved": [], "needs_review": False, "review_reason": None,
}


def fake_client(text=None, finish="STOP", status=200, seen=None, blocked=False):
    def handler(req: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.update(json.loads(req.content))
            seen["_url"] = str(req.url)
        if status != 200:
            return httpx2.Response(status, json={"error": {"code": status, "message": "nope", "status": "X"}})
        if blocked:
            return httpx2.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        return httpx2.Response(200, json={"candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish}]})
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return genai.Client(api_key="test", http_options=types.HttpOptions(httpx_client=http))


def test_request_shape_and_parsing():
    seen = {}
    r = gemini_evaluator.evaluate({"sales_line": "38 HP - RXQ38BRY16"}, fake_client(json.dumps(ANSWER), seen=seen))
    assert "models/gemini-3.8-flash:generateContent" in seen["_url"]
    assert "BOQ engineer" in json.dumps(seen["systemInstruction"])
    cfg = seen["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and "selections" in json.dumps(cfg["responseJsonSchema"])
    assert r.selections[0].item_no == "CO-21405" and r.selections[0].confidence == 0  # clamped


def test_api_error_is_unavailable():
    with pytest.raises(AIUnavailable, match="403"):
        gemini_evaluator.evaluate({}, fake_client(status=403))


def test_blocked_prompt_is_unavailable():
    with pytest.raises(AIUnavailable, match="declined"):
        gemini_evaluator.evaluate({}, fake_client(blocked=True))


def test_truncated_answer_is_unavailable():
    with pytest.raises(AIUnavailable, match="output tokens"):
        gemini_evaluator.evaluate({}, fake_client('{"requirements"', finish="MAX_TOKENS"))


def test_malformed_answer_is_unavailable():
    with pytest.raises(AIUnavailable, match="no usable result"):
        gemini_evaluator.evaluate({}, fake_client("not json"))


def test_engine_uses_gemini_and_validates_against_master(indexes, line):
    answer = dict(ANSWER, selections=ANSWER["selections"] + [
        {"item_no": "CO-99999", "selection_type": "Explicit", "confidence": 90, "reason": "invented"}])
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="gemini"), ai_client=fake_client(json.dumps(answer)))
    r = e.match_line(line("1.1.2.1"))
    assert r.engine == "gemini"
    assert [(s.component.item_no, s.source) for s in r.selections] == [("CO-21405", "gemini")]
    assert any("CO-99999" in n and "not shortlisted" in n for n in r.notes)


def test_engine_falls_back_to_rules_when_gemini_fails(indexes, line):
    e = MatchingEngine(indexes, MatchConfig(["Highside Supply"], mode="gemini"), ai_client=fake_client(status=500))
    r = e.match_line(line("1.1.2.1"))
    assert r.engine == "rule" and AI_UNAVAILABLE_FLAG in r.selections[0].flags


def test_env_file_beats_a_machine_wide_variable(tmp_path, monkeypatch):
    # A stale Windows GEMINI_API_KEY must not replace the project's own key.
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=gemini\nGEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-windows")
    monkeypatch.delenv("AI_MODE", raising=False)
    s = Settings(_env_file=str(env))
    assert (s.gemini_api_key, s.resolved_ai_mode) == ("from-dotenv", "gemini")


def test_variable_still_used_when_env_file_leaves_it_out(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("AI_PROVIDER=gemini\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-windows")
    assert Settings(_env_file=str(env)).gemini_api_key == "from-windows"


@pytest.mark.parametrize("env,mode,model", [
    ({"ai_provider": "gemini", "gemini_api_key": "k"}, "gemini", "gemini-3.8-flash"),
    ({"ai_provider": "gemini", "openai_api_key": "k"}, "mock", ""),
    ({"ai_provider": "gemini", "gemini_api_key": "k", "gemini_model": "gemini-x"}, "gemini", "gemini-x"),
])
def test_provider_selection(env, mode, model):
    s = Settings(_env_file=None, **{"anthropic_api_key": None, "openai_api_key": None, "gemini_api_key": None,
                                    "ai_mode": "auto", **env})
    assert (s.resolved_ai_mode, s.active_model) == (mode, model)
