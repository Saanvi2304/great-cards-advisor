"""Provider-agnostic LLM interface.

The only public entry point is ``generate_structured()``.  It takes a system
prompt, a list of conversation messages, and a Pydantic model class, and
returns a validated instance of that model or raises ``LLMError``.
"""
from __future__ import annotations

from typing import Any, Type, TypeVar

from pydantic import BaseModel

from advisor.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Any problem with the LLM call (network, auth, parse, rate-limit)."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def generate_structured(
    settings: Settings,
    system_prompt: str,
    messages: list[dict[str, str]],
    schema: Type[T],
) -> T:
    """Call the configured LLM and return a validated ``schema`` instance.

    Parameters
    ----------
    settings:
        Application settings (provider name, model, key, timeout).
    system_prompt:
        The system instruction sent to the model.
    messages:
        Conversation history as ``[{"role": "user"|"model", "text": ...}]``.
    schema:
        A Pydantic model class.  The provider adapter is responsible for
        requesting JSON output that matches this schema, parsing the
        response, and validating it with ``schema.model_validate()``.

    Returns
    -------
    An instance of *schema* if the model returns valid JSON.

    Raises
    ------
    LLMError
        On missing API key, network error, auth failure, rate-limit,
        timeout, or unparseable response.
    """
    if settings.llm_provider == "gemini":
        from advisor.llm.gemini import call_gemini
        return call_gemini(settings, system_prompt, messages, schema)
    else:
        raise LLMError(f"Unknown LLM provider: {settings.llm_provider!r}")
