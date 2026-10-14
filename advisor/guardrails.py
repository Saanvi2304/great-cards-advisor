"""Policy flags that code enforces regardless of what the LLM says.

These run on the raw user text (before the LLM) and on the parsed
interpretation (after the LLM). They add issues or policy notes to the
TurnResult, ensuring the app never silently ignores banned topics.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from advisor.models import Interpretation, Issue, TurnResult

# ---- keyword patterns (case-insensitive) ------------------------------------

_TAX_RE = re.compile(
    r"\b(80c|80d|section 80|tax\s*(?:benefit|saving|deduction|exemption|rebate)"
    r"|income\s*tax|itr|tds|gst\s+input)\b",
    re.IGNORECASE,
)

_LOAN_RE = re.compile(
    r"\b(personal\s*loan|home\s*loan|car\s*loan|emi\s+convert"
    r"|loan\s+against|balance\s+transfer)\b",
    re.IGNORECASE,
)

_APPROVAL_GUARANTEE_RE = re.compile(
    r"\b(guaranteed?\s+approv|pre[- ]?approv|sure(ly)?\s+(get|approv)"
    r"|100\s*%\s*(approv|chance)|will\s+i\s+(definitely|surely)\s+get)\b",
    re.IGNORECASE,
)

_INVEST_RE = re.compile(
    r"\b(stock|mutual\s*fund|sip|fixed\s+deposit|fd\s+rate|ppf|nps"
    r"|elss|demat|trading)\b",
    re.IGNORECASE,
)


def _add_policy(result: "TurnResult", note: str) -> None:
    if note not in result.policy_notes:
        result.policy_notes.append(note)


# ---- pre-LLM checks (on raw text) ------------------------------------------

def check_raw_text(text: str, result: "TurnResult") -> None:
    """Add policy notes for topics spotted by keywords in the user's message."""
    if _TAX_RE.search(text):
        _add_policy(
            result,
            "Tax questions are out of scope. Credit cards may offer tax-saving "
            "benefits, but this advisor cannot give tax advice.",
        )

    if _LOAN_RE.search(text):
        _add_policy(
            result,
            "Loan and EMI-conversion questions are out of scope. This advisor "
            "covers credit card selection only.",
        )

    if _APPROVAL_GUARANTEE_RE.search(text):
        _add_policy(
            result,
            "Card approval can never be guaranteed. Eligibility checks are based "
            "on published criteria; the issuing bank makes the final decision.",
        )

    if _INVEST_RE.search(text):
        _add_policy(
            result,
            "Investment and savings-product questions are out of scope.",
        )


# ---- post-LLM checks (on interpretation) -----------------------------------

def check_interpretation(interp: "Interpretation", result: "TurnResult") -> None:
    """Validate that the LLM's interpretation respects policy rules."""
    # If the LLM marked it out_of_scope, trust it (it matched an obvious case).
    if interp.intent == "out_of_scope":
        return

    # If keywords flagged tax/loan but the LLM still picked a tool intent,
    # the policy notes were already added above; no need to override the intent
    # because the notes will be shown alongside the proposed action.

    # Approval-guarantee: if the intent is eligibility_check, add the caveat
    # even if the keyword pattern didn't fire (the user may have phrased it
    # differently and the LLM still routed correctly).
    if interp.intent == "eligibility_check":
        _add_policy(
            result,
            "Card approval can never be guaranteed. Eligibility checks are based "
            "on published criteria; the issuing bank makes the final decision.",
        )
