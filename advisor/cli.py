"""Terminal interface for the Great.cards advisor.

Handles:
- Reading user input (with readline support on Unix).
- Pretty-printing the TurnResult.
- A short conversation loop with /new and /quit commands.
- Optional --debug flag to also print the raw LLM output.
"""
from __future__ import annotations

import sys
from typing import Optional

from advisor.amounts import format_inr
from advisor.models import BLOCKING_LEVELS, TurnResult

# ANSI colours (degrade gracefully if the terminal doesn't support them).
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"


def _c(code: str, text: str) -> str:
    """Wrap text in an ANSI colour code."""
    return f"{code}{text}{_RESET}"


def _safe_print(*args, **kwargs) -> None:
    """Print that gracefully handles encoding errors on Windows terminals."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        # Replace problematic characters.
        text = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        )
        print(text, **kwargs)


def print_banner() -> None:
    _safe_print()
    _safe_print(_c(_BOLD + _CYAN, "  +----------------------------------------------+"))
    _safe_print(_c(_BOLD + _CYAN, "  |      Great.cards Credit Card Advisor         |"))
    _safe_print(_c(_BOLD + _CYAN, "  |          Step 1 -- Plan Mode                 |"))
    _safe_print(_c(_BOLD + _CYAN, "  +----------------------------------------------+"))
    _safe_print()
    _safe_print(f"  {_c(_DIM, 'Ask a credit card question. The advisor will understand')} ")
    _safe_print(f"  {_c(_DIM, 'your question and show what tool call it would make.')} ")
    _safe_print(f"  {_c(_DIM, 'No actual tool calls are executed in Step 1.')} ")
    _safe_print()
    _safe_print(f"  Commands:  {_c(_BOLD, '/new')}  reset conversation    "
                f"{_c(_BOLD, '/quit')}  exit")
    _safe_print(f"  Flag:      {_c(_BOLD, '--debug')}  also show raw LLM output")
    _safe_print()


def print_result(result: TurnResult, debug_raw: Optional[str] = None) -> None:
    """Pretty-print a TurnResult to stdout."""
    print()

    # Debug: raw LLM output.
    if debug_raw is not None:
        print(_c(_DIM, "─── Raw LLM output ───"))
        print(_c(_DIM, debug_raw))
        print(_c(_DIM, "──────────────────────"))
        print()

    # Header line.
    print(f"  {_c(_BOLD, 'You asked')}      : {result.user_message}")

    if result.understood:
        print(f"  {_c(_BOLD, 'Understood as')}  : {result.understood}"
              f"   {_c(_DIM, f'(confidence: {result.confidence})')}")

    # --- status-specific display ---
    if result.status == "OUT_OF_SCOPE":
        print(f"  {_c(_BOLD, 'Status')}         : {_c(_RED, 'OUT OF SCOPE')}")
        if result.out_of_scope_reason:
            print(f"  {_c(_BOLD, 'Reason')}         : {result.out_of_scope_reason}")
        _print_policy_notes(result)
        _print_separator()
        return

    if result.status == "ERROR":
        print(f"  {_c(_BOLD, 'Status')}         : {_c(_RED, 'ERROR')}")
        if result.error:
            print(f"  {_c(_BOLD, 'Error')}          : {result.error}")
        _print_separator()
        return

    # --- proposed steps ---
    for i, step in enumerate(result.steps, 1):
        prefix = f"Step {i}" if len(result.steps) > 1 else "Proposed tool"
        print(f"  {_c(_BOLD, prefix):26s}: {_c(_GREEN, step.tool)}")
        if step.arguments:
            print(f"  {'Arguments':26s}: {_format_args(step.arguments)}")
        if step.pending_arguments:
            for k, v in step.pending_arguments.items():
                print(f"  {'':26s}  {_c(_DIM, f'{k}: {v}')}")
        if step.purpose:
            print(f"  {'Purpose':26s}: {_c(_DIM, step.purpose)}")

    # --- evidence ---
    if result.evidence:
        print()
        print(f"  {_c(_BOLD, 'Evidence')}:")
        for ev in result.evidence:
            val_str = (
                format_inr(ev.value) if isinstance(ev.value, (int, float)) and ev.value >= 100
                else str(ev.value)
            )
            line = f"    {ev.argument} = {val_str}"
            line += f"  ← {_c(_DIM, ev.source)}"
            if ev.note:
                line += f"  {_c(_YELLOW, ev.note)}"
            print(line)

    # --- issues ---
    if result.issues:
        print()
        blocking = [i for i in result.issues if i.level in BLOCKING_LEVELS]
        info_issues = [i for i in result.issues if i.level not in BLOCKING_LEVELS]

        if blocking:
            print(f"  {_c(_BOLD + _YELLOW, 'Issues')}:")
            for issue in blocking:
                icon = "⚠" if issue.level in ("missing", "ambiguous") else "✗"
                print(f"    {_c(_YELLOW, icon)} [{issue.level}] {issue.message}")
        if info_issues:
            for issue in info_issues:
                print(f"    {_c(_DIM, 'ℹ')} [{issue.level}] {issue.message}")

    # --- clarifying questions ---
    if result.clarifying_questions:
        print()
        print(f"  {_c(_BOLD + _MAGENTA, 'Clarifying question')}:")
        for q in result.clarifying_questions:
            print(f"    → {q}")

    # --- policy notes ---
    _print_policy_notes(result)

    # --- follow-ups ---
    if result.follow_ups:
        print()
        for f in result.follow_ups:
            print(f"  {_c(_DIM, f'💡 {f}')}")

    # --- status line ---
    print()
    if result.status == "READY":
        print(f"  {_c(_BOLD, 'Status')}         : "
              f"{_c(_GREEN, 'READY — NOT EXECUTED (Step 1 only prepares the action)')}")
    elif result.status == "NEEDS_CLARIFICATION":
        print(f"  {_c(_BOLD, 'Status')}         : "
              f"{_c(_YELLOW, 'NEEDS CLARIFICATION — answer the question above')}")

    _print_separator()


def _print_policy_notes(result: TurnResult) -> None:
    if result.policy_notes:
        print()
        for note in result.policy_notes:
            print(f"  {_c(_DIM, f'📋 {note}')}")


def _print_separator() -> None:
    print()
    print(_c(_DIM, "  " + "─" * 60))
    print()


def _format_args(args: dict) -> str:
    """Format arguments dict for display."""
    parts = []
    for k, v in args.items():
        if isinstance(v, (int, float)) and v >= 100:
            parts.append(f"{k}: {format_inr(v)}")
        else:
            parts.append(f"{k}: {v!r}")
    return "{" + ", ".join(parts) + "}"


def read_input(prompt: str = "  You > ") -> str | None:
    """Read one line from stdin. Returns None on EOF."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
