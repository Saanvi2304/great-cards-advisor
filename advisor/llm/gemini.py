"""Gemini provider adapter using the official google-genai SDK.

Calls ``client.models.generate_content()`` with a ``response_schema`` so
the model returns JSON that matches our Pydantic model. Temperature is 0
for reproducibility; thinking is disabled to save tokens on this simple
extraction task.
"""
from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from advisor.config import Settings
from advisor.llm.base import LLMError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _build_contents(messages: list[dict[str, str]]) -> list[dict]:
    """Convert our message list to Gemini SDK Content format."""
    contents = []
    for msg in messages:
        role = msg["role"]
        # Gemini SDK uses "user" and "model" roles.
        if role not in ("user", "model"):
            role = "user"
        contents.append({"role": role, "parts": [{"text": msg["text"]}]})
    return contents


def call_gemini(
    settings: Settings,
    system_prompt: str,
    messages: list[dict[str, str]],
    schema: Type[T],
) -> T:
    """Call Gemini and return a validated Pydantic model instance."""
    if not settings.gemini_api_key:
        raise LLMError(
            "GEMINI_API_KEY is not set in .env. "
            "Get a free key at https://aistudio.google.com/apikey "
            "and paste it into your .env file.",
        )

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise LLMError(
            "google-genai is not installed. Run: pip install google-genai"
        ) from exc

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(
            timeout=int(settings.llm_timeout_seconds * 1000),
        ),
    )

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=schema,
    )

    contents = _build_contents(messages)

    try:
        response = client.models.generate_content(
            model=settings.llm_model,
            contents=contents,
            config=config,
        )
    except Exception as exc:
        exc_str = str(exc).lower()
        retryable = any(
            keyword in exc_str
            for keyword in ("429", "rate", "overloaded", "timeout", "unavailable")
        )
        # Never include the API key in error messages.
        safe_msg = str(exc).replace(settings.gemini_api_key or "", "***")
        raise LLMError(f"Gemini API error: {safe_msg}", retryable=retryable) from exc

    # Extract text from the response.
    raw_text = response.text
    if not raw_text:
        raise LLMError("Gemini returned an empty response.")

    log.debug("Raw Gemini response: %s", raw_text)

    # Parse and validate with Pydantic.
    try:
        return schema.model_validate_json(raw_text)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LLMError(
            f"Could not parse Gemini response as {schema.__name__}: {exc}\n"
            f"Raw response: {raw_text[:500]}",
            retryable=True,
        ) from exc
