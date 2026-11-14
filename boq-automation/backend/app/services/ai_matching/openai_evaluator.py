"""OpenAI contextual evaluation of a shortlist.

Same contract as claude_evaluator: one sales line, its shortlist only, a schema-checked
answer, and the same downstream validation against the Item Master before anything is
exported. The prompt is shared so both providers are judged on the same instructions.
"""
import json
from functools import lru_cache
from typing import Literal

import openai
from pydantic import BaseModel

from app.core.config import get_settings

from .claude_evaluator import SYSTEM, AIUnavailable, ClaudeLineResult, ScopeCategory


# OpenAI strict structured output wants every field required (nullable instead of
# defaulted) and no numeric bounds, so these mirror the Claude models without them.
class _Selection(BaseModel):
    item_no: str
    selection_type: Literal["Explicit", "Inferred"]
    confidence: int
    reason: str


class _Requirements(BaseModel):
    equipment_or_component: str
    material: str | None
    make: str | None
    model: str | None
    capacity: str | None
    size: str | None
    activities: list[str]
    additional_requirements: list[str]


class _LineResult(BaseModel):
    requirements: _Requirements
    selections: list[_Selection]
    unresolved: list[str]
    needs_review: bool
    review_reason: str | None
    scope_category: ScopeCategory | None = None  # the SDK still lists it as required (nullable)


@lru_cache(maxsize=1)
def _shared_client(api_key: str | None, timeout_s: int) -> openai.OpenAI:
    """One client for the process (connection reuse), with a bounded wait per call."""
    return openai.OpenAI(api_key=api_key, timeout=timeout_s, max_retries=1)


def evaluate(payload: dict, client: openai.OpenAI | None = None) -> ClaudeLineResult:
    """One line, one call. Raises AIUnavailable so the engine can degrade to rule scoring."""
    settings = get_settings()
    client = client or _shared_client(settings.openai_api_key, settings.ai_timeout_seconds)
    try:
        completion = client.chat.completions.parse(
            model=settings.openai_model,
            max_completion_tokens=16000,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}],
            response_format=_LineResult,
        )
    except openai.LengthFinishReasonError as e:
        raise AIUnavailable("OpenAI ran out of output tokens") from e
    except openai.ContentFilterFinishReasonError as e:
        raise AIUnavailable("OpenAI content filter blocked this request") from e
    except openai.RateLimitError as e:
        raise AIUnavailable(f"OpenAI rate limited: {e}") from e
    except openai.APIStatusError as e:
        raise AIUnavailable(f"OpenAI API error {e.status_code}: {e.message}") from e
    except openai.APIConnectionError as e:
        raise AIUnavailable(f"OpenAI connection failed: {e}") from e

    message = completion.choices[0].message
    if message.refusal:
        raise AIUnavailable("OpenAI declined this request")
    if message.parsed is None:
        raise AIUnavailable("OpenAI returned no usable result")
    data = message.parsed.model_dump()
    for s in data["selections"]:
        s["confidence"] = max(0, min(100, s["confidence"]))
    return ClaudeLineResult.model_validate(data)
