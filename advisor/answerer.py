"""Generate a human-readable answer from MCP tool results.

Takes the executed ``TurnResult`` (with ``step.result`` populated by the
executor) and uses the LLM to compose a natural-language response grounded
in the actual tool data.
"""
from __future__ import annotations

import json
import logging

from advisor.config import PROMPTS_DIR, Settings
from advisor.llm.base import LLMError, generate_text
from advisor.models import TurnResult

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE: str | None = None


def _load_prompt() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        path = PROMPTS_DIR / "answer.md"
        _PROMPT_TEMPLATE = path.read_text(encoding="utf-8")
    return _PROMPT_TEMPLATE


def generate_answer(settings: Settings, result: TurnResult) -> None:
    """Compose a user-facing answer and set ``result.answer`` in place.

    If the result has no executed steps or all steps errored, a fallback
    message is set instead of calling the LLM.
    """
    # Nothing to answer for non-READY states.
    if result.status != "READY":
        _set_fallback_answer(result)
        return

    executed = [s for s in result.steps if s.executed and s.result]
    if not executed:
        result.answer = "I planned the right tool call but couldn't execute it. Please try again."
        return

    # Check if all results are errors.
    all_errors = all(r.result.get("is_error") for r in executed)
    if all_errors:
        result.answer = (
            "The Great.cards server returned errors for all tool calls. "
            "This might be a temporary issue — please try again in a moment."
        )
        return

    # Build the LLM prompt with tool results as context.
    system_prompt = _load_prompt()

    # Compose the user-side message: original question + tool results.
    tool_context_parts = []
    for step in executed:
        raw = step.result.get("raw", "") if step.result else ""
        # Truncate very large results to avoid token waste.
        if len(raw) > 8000:
            raw = raw[:8000] + "\n... (truncated)"
        tool_context_parts.append(
            f"Tool: {step.tool}\n"
            f"Purpose: {step.purpose}\n"
            f"Result:\n{raw}"
        )

    tool_context = "\n\n---\n\n".join(tool_context_parts)

    messages = [
        {
            "role": "user",
            "text": (
                f"User's question: {result.user_message}\n\n"
                f"Tool results:\n\n{tool_context}\n\n"
                f"Please compose a helpful answer based on these tool results."
            ),
        },
    ]

    try:
        result.answer = generate_text(settings, system_prompt, messages)
    except LLMError as exc:
        log.warning("Answer generation failed: %s", exc)
        # Fallback: show raw results summary.
        result.answer = _raw_fallback(result)


def _set_fallback_answer(result: TurnResult) -> None:
    """Set a sensible answer for non-READY states."""
    if result.status == "OUT_OF_SCOPE":
        result.answer = (
            result.out_of_scope_reason
            or "This question is outside the scope of credit card advice."
        )
    elif result.status == "NEEDS_CLARIFICATION":
        parts = []
        if result.understood:
            parts.append(result.understood)
        if result.clarifying_questions:
            parts.append("\n".join(f"• {q}" for q in result.clarifying_questions))
        if result.policy_notes:
            parts.append("\n".join(f"Note: {n}" for n in result.policy_notes))
        result.answer = "\n\n".join(parts) if parts else "Could you provide more details?"
    elif result.status == "ERROR":
        result.answer = result.error or "Something went wrong. Please try again."


def _raw_fallback(result: TurnResult) -> str:
    """Build a minimal answer from raw tool results when LLM fails."""
    parts = ["Here's what I found (raw data):"]
    for step in result.steps:
        if step.executed and step.result:
            raw = step.result.get("raw", "No data")
            if len(raw) > 2000:
                raw = raw[:2000] + "..."
            parts.append(f"\n**{step.tool}:**\n{raw}")
    return "\n".join(parts)
