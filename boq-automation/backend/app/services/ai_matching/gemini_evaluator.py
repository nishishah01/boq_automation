"""Google Gemini contextual evaluation of a shortlist.

Same contract as the Claude and OpenAI evaluators: one sales line, its shortlist only,
a schema-checked answer, then the same validation against the Item Master.
"""
import json
from functools import lru_cache

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app.core.config import get_settings

from .claude_evaluator import SYSTEM, AIUnavailable, ClaudeLineResult
from .openai_evaluator import _LineResult  # all fields required, no numeric bounds

STOPPED = {"MAX_TOKENS": "Gemini ran out of output tokens", "SAFETY": "Gemini safety filter blocked this request",
           "RECITATION": "Gemini stopped for recitation", "PROHIBITED_CONTENT": "Gemini declined this request",
           "BLOCKLIST": "Gemini declined this request", "SPII": "Gemini declined this request"}


@lru_cache(maxsize=1)
def _shared_client(api_key: str, timeout_s: int) -> genai.Client:
    """One client for the process, so connections are reused across lines and threads.

    The SDK has no timeout by default: one stalled connection used to hang a whole job. Its
    own retries on 429/5xx are kept, but capped at two attempts so an overloaded model fails
    over to rule scoring quickly instead of after a long back-off.
    """
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=timeout_s * 1000, retry_options=types.HttpRetryOptions(attempts=2)))


def evaluate(payload: dict, client: genai.Client | None = None) -> ClaudeLineResult:
    """One line, one call. Raises AIUnavailable so the engine can degrade to rule scoring."""
    settings = get_settings()
    client = client or _shared_client(settings.gemini_api_key, settings.ai_timeout_seconds)
    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=json.dumps(payload, ensure_ascii=False, indent=1),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                response_json_schema=_LineResult.model_json_schema(),
                max_output_tokens=16000,
            ),
        )
    except errors.APIError as e:
        raise AIUnavailable(f"Gemini API error {e.code}: {e.message}") from e
    except Exception as e:  # connection failures surface as transport-library errors
        raise AIUnavailable(f"Gemini request failed: {e}") from e

    feedback = getattr(response, "prompt_feedback", None)
    if feedback is not None and getattr(feedback, "block_reason", None):
        raise AIUnavailable("Gemini declined this request")
    candidates = response.candidates or []
    if not candidates:
        raise AIUnavailable("Gemini returned no answer")
    reason = str(getattr(candidates[0].finish_reason, "value", candidates[0].finish_reason) or "")
    if reason in STOPPED:
        raise AIUnavailable(STOPPED[reason])
    try:
        parsed = _LineResult.model_validate_json(response.text or "")
    except ValidationError as e:
        raise AIUnavailable("Gemini returned no usable result") from e
    data = parsed.model_dump()
    for s in data["selections"]:
        s["confidence"] = max(0, min(100, s["confidence"]))
    return ClaudeLineResult.model_validate(data)
