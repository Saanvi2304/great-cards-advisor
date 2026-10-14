"""Entry point: ``python -m advisor``.

Wires together config → catalog → interpreter → planner → CLI display.
Keeps a short in-memory conversation so the user can answer clarifying
questions. /new resets, /quit exits.
"""
from __future__ import annotations

import logging
import sys

from advisor.cli import print_banner, print_result, read_input
from advisor.config import ConfigError, load_settings
from advisor.guardrails import check_interpretation, check_raw_text
from advisor.interpreter import interpret
from advisor.llm.base import LLMError
from advisor.planner import plan
from advisor.tool_catalog import CatalogError, ToolCatalog
from advisor.trace import TraceLogger


def main() -> None:
    debug = "--debug" in sys.argv

    # Configure logging.
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(name)s %(levelname)s: %(message)s",
    )

    # --- startup checks ------------------------------------------------------
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"\n  ✗ Configuration error: {exc}\n", file=sys.stderr)
        sys.exit(1)

    try:
        catalog = ToolCatalog(settings.tools_snapshot)
    except (CatalogError, FileNotFoundError, Exception) as exc:
        print(f"\n  ✗ Tool catalog error: {exc}\n", file=sys.stderr)
        sys.exit(1)

    tracer = TraceLogger(settings.log_dir)

    print_banner()
    print(f"  Model: {settings.llm_model}  |  Provider: {settings.llm_provider}")
    print(f"  Trace log: {tracer.path}")
    print()

    # --- conversation loop ---------------------------------------------------
    messages: list[dict[str, str]] = []
    user_texts: list[str] = []

    while True:
        text = read_input()
        if text is None:
            print("\n  Goodbye!\n")
            break
        if not text:
            continue

        # Commands.
        if text.lower() in ("/quit", "/exit", "quit", "exit"):
            print("\n  Goodbye!\n")
            break
        if text.lower() in ("/new", "/reset", "new", "reset"):
            messages.clear()
            user_texts.clear()
            print("\n  ↺  Conversation reset.\n")
            continue

        # Add user message to history.
        messages.append({"role": "user", "text": text})
        user_texts.append(text)

        # --- pipeline --------------------------------------------------------
        from advisor.models import TurnResult

        result = TurnResult(user_message=text, status="ERROR")
        raw_llm = None

        try:
            # 1. Pre-LLM guardrails.
            check_raw_text(text, result)

            # 2. Interpret via LLM.
            interp = interpret(settings, catalog, messages)
            raw_llm = interp.model_dump_json(indent=2)

            # 3. Post-LLM guardrails.
            check_interpretation(interp, result)

            # 4. Plan.
            result = plan(interp, catalog, user_texts)

            # Re-apply policy notes from pre-LLM check (plan creates a fresh result).
            pre_notes = [n for n in result.policy_notes]  # already from plan
            check_raw_text(text, result)
            check_interpretation(interp, result)

        except LLMError as exc:
            result.status = "ERROR"
            result.error = str(exc)
            tracer.log_turn(
                user_message=text,
                error=str(exc),
            )
        except Exception as exc:
            result.status = "ERROR"
            result.error = f"Unexpected error: {exc}"
            logging.getLogger(__name__).exception("Unexpected error")
            tracer.log_turn(
                user_message=text,
                error=str(exc),
            )
        else:
            tracer.log_turn(
                user_message=text,
                raw_llm_output=raw_llm,
                interpretation=interp,
                turn_result=result,
            )

        # Add model "response" to conversation for multi-turn context.
        if result.understood:
            messages.append({
                "role": "model",
                "text": result.understood or "I could not understand that.",
            })

        # Display.
        print_result(result, debug_raw=raw_llm if debug else None)


if __name__ == "__main__":
    main()
