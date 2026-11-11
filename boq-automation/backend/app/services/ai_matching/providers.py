"""Route a shortlist to the configured AI provider.

Each provider's SDK is imported only when that provider is used, so a missing optional
package (say google-genai when you use Claude) cannot stop the app from starting.
"""
import importlib

from .claude_evaluator import AIUnavailable, ClaudeLineResult

AI_MODES = {"claude", "openai", "gemini"}
_MODULES = {"claude": "claude_evaluator", "openai": "openai_evaluator", "gemini": "gemini_evaluator"}
_PACKAGES = {"claude": "anthropic", "openai": "openai", "gemini": "google-genai"}


def evaluate(mode: str, payload: dict, client=None) -> ClaudeLineResult:
    if mode not in _MODULES:
        raise AIUnavailable(f"Unknown AI mode '{mode}'")
    try:
        module = importlib.import_module(f"{__package__}.{_MODULES[mode]}")
    except ImportError as e:
        raise AIUnavailable(f"The {_PACKAGES[mode]} package is not installed; "
                            "run: pip install -r requirements.txt") from e
    return module.evaluate(payload, client)
