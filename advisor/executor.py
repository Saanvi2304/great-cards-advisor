"""Execute planned MCP tool calls.

Takes a ``TurnResult`` whose planner has set ``steps`` to READY, connects
to the MCP server, runs each step in order, and fills in results.

Handles the card-name resolution chain: if steps include resolver calls
followed by a main step with ``pending_arguments``, the resolved aliases
are extracted and injected before the main call is made.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from advisor.config import Settings
from advisor.mcp_client import call_tools_sync
from advisor.models import PlannedStep, TurnResult
from advisor.tool_catalog import RESOLVER_TOOL

log = logging.getLogger(__name__)

# Safety limit: never execute more than this many MCP calls per user message.
MAX_CALLS_PER_TURN = 5


class ExecutionError(Exception):
    """Raised when MCP execution fails."""


def execute_plan(settings: Settings, result: TurnResult) -> None:
    """Execute all planned steps via MCP and update *result* in place.

    - Sets ``step.result`` and ``step.executed`` for each step.
    - On failure, sets ``result.status = 'ERROR'`` and ``result.error``.
    """
    if result.status != "READY" or not result.steps:
        return

    if not settings.mcp_url:
        result.error = "MCP_URL is not configured in .env — cannot execute tool calls."
        result.status = "ERROR"
        return

    steps = result.steps
    if len(steps) > MAX_CALLS_PER_TURN:
        steps = steps[:MAX_CALLS_PER_TURN]
        log.warning("Truncated steps to %d (safety limit)", MAX_CALLS_PER_TURN)

    try:
        _execute_steps(settings.mcp_url, steps)
    except Exception as exc:
        log.exception("MCP execution failed")
        result.error = f"MCP execution error: {exc}"
        result.status = "ERROR"


def _execute_steps(url: str, steps: list[PlannedStep]) -> None:
    """Build the call list, resolving card aliases along the way."""
    resolved_aliases: list[str] = []

    # Separate resolver steps from the main step.
    resolver_steps = [s for s in steps if s.tool == RESOLVER_TOOL]
    main_steps = [s for s in steps if s.tool != RESOLVER_TOOL]

    # Phase 1: execute all resolver steps in one batch.
    if resolver_steps:
        resolver_calls = [
            (s.tool, s.arguments) for s in resolver_steps
        ]
        resolver_results = call_tools_sync(url, resolver_calls)

        for step, res in zip(resolver_steps, resolver_results):
            step.result = res
            step.executed = True
            alias = _extract_alias(res)
            if alias:
                resolved_aliases.append(alias)
                log.info("Resolved alias: %s", alias)

    # Phase 2: fill pending arguments and execute main steps.
    if main_steps:
        for step in main_steps:
            if step.pending_arguments:
                _fill_pending(step, resolved_aliases)

        main_calls = [
            (s.tool, s.arguments) for s in main_steps
        ]
        main_results = call_tools_sync(url, main_calls)

        for step, res in zip(main_steps, main_results):
            step.result = res
            step.executed = True


def _fill_pending(step: PlannedStep, resolved_aliases: list[str]) -> None:
    """Inject resolved card aliases into a step's arguments."""
    for key in list(step.pending_arguments.keys()):
        if key == "card_alias" and resolved_aliases:
            step.arguments[key] = resolved_aliases[0]
        elif key == "card_aliases" and resolved_aliases:
            step.arguments[key] = resolved_aliases
    step.pending_arguments = {}


def _extract_alias(result: dict[str, Any]) -> str | None:
    """Best-effort extraction of a card alias from a resolver result."""
    if result.get("is_error"):
        return None

    data = result.get("data")

    # If data is a dict, look for common alias fields.
    if isinstance(data, dict):
        # Check nested structures.
        for key in ("alias", "card_alias", "resolved", "slug", "name",
                     "card_slug", "result"):
            if key in data:
                val = data[key]
                if isinstance(val, str) and val:
                    return val
                if isinstance(val, list) and val:
                    first = val[0]
                    if isinstance(first, str):
                        return first
                    if isinstance(first, dict):
                        for sub_key in ("alias", "card_alias", "slug", "name"):
                            if sub_key in first:
                                return first[sub_key]

        # If the dict has a "cards" or "results" list, try the first item.
        for list_key in ("cards", "results", "matches"):
            if list_key in data and isinstance(data[list_key], list) and data[list_key]:
                item = data[list_key][0]
                if isinstance(item, str):
                    return item
                if isinstance(item, dict):
                    for sub_key in ("alias", "card_alias", "slug", "name"):
                        if sub_key in item:
                            return item[sub_key]

    # If data is a plain string, it might be the alias directly.
    if isinstance(data, str) and data.strip():
        return data.strip()

    log.warning("Could not extract alias from resolver result: %s", result.get("raw", "")[:200])
    return None
