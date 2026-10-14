"""LLM-based question interpreter.

Builds the system prompt from the template + live schema values, calls the
LLM with the conversation history, and returns a validated ``Interpretation``
instance.  On a parse failure it retries once, including the error in the
follow-up prompt so the model can self-correct.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from advisor.config import PROMPTS_DIR, Settings
from advisor.llm.base import LLMError, generate_structured
from advisor.models import Interpretation
from advisor.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE: str | None = None


def _load_prompt_template() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        path = PROMPTS_DIR / "interpret.md"
        _PROMPT_TEMPLATE = path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def _build_system_prompt(catalog: ToolCatalog) -> str:
    """Fill the prompt template with real values from the tool snapshot."""
    template = _load_prompt_template()
    return (
        template
        .replace("{spend_keys}", ", ".join(catalog.spend_keys))
        .replace("{categories}", ", ".join(catalog.list_categories))
        .replace("{employment_statuses}", ", ".join(catalog.employment_statuses))
    )


def interpret(
    settings: Settings,
    catalog: ToolCatalog,
    messages: list[dict[str, str]],
) -> Interpretation:
    """Interpret the user's message via the LLM.

    Parameters
    ----------
    settings:
        Application settings.
    catalog:
        The loaded tool catalog (provides enum values for the prompt).
    messages:
        Conversation history as ``[{"role": "user"|"model", "text": ...}]``.

    Returns
    -------
    Interpretation
        A validated Pydantic model with the LLM's structured extraction.

    Raises
    ------
    LLMError
        If both the initial call and the retry fail.
    """
    system_prompt = _build_system_prompt(catalog)

    try:
        return generate_structured(settings, system_prompt, messages, Interpretation)
    except LLMError as first_err:
        if not first_err.retryable:
            raise

        log.warning("First LLM call failed (%s), retrying with error context.", first_err)

        # Retry: include the error so the model can self-correct.
        retry_messages = messages + [
            {
                "role": "model",
                "text": f"[Previous attempt failed: {first_err}. Please try again, "
                        f"making sure to return valid JSON matching the schema.]",
            },
        ]
        try:
            return generate_structured(
                settings, system_prompt, retry_messages, Interpretation,
            )
        except LLMError:
            # If the retry also fails, raise the original error.
            raise first_err from None
