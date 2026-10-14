"""Planner: turns an Interpretation into a validated TurnResult.

The planner is the core deterministic layer. It takes the LLM's structured
extraction (Interpretation) and:

1. Maps intent → tool (a fixed table, not LLM choice).
2. Converts user-stated amounts to the units each tool field expects.
3. Builds the tool arguments dict.
4. Adds evidence showing where each argument came from.
5. Identifies missing required information and adds issues/clarifying questions.
6. Adds a card-name resolution step when card aliases are needed.
7. Runs JSON-schema + business-rule validation.
"""
from __future__ import annotations

import logging
from typing import Any

from advisor.amounts import format_inr
from advisor.models import (
    BLOCKING_LEVELS,
    Confidence,
    Evidence,
    Interpretation,
    Issue,
    PlannedStep,
    SpendItem,
    TurnResult,
)
from advisor.tool_catalog import (
    ANNUAL_SPEND_KEYS,
    INTENT_TO_TOOL,
    QUARTERLY_COUNT_KEYS,
    RESOLVER_TOOL,
    ToolCatalog,
)
from advisor.validation import validate_step

log = logging.getLogger(__name__)


# ---- unit conversion --------------------------------------------------------

def _convert_spend(item: SpendItem, target_unit: str) -> tuple[float, str]:
    """Convert a user-stated spend to the unit the tool field expects.

    Returns (converted_value, human-readable note explaining the conversion).
    """
    value = item.amount
    stated = item.period

    if target_unit == "monthly":
        if stated == "annual":
            return round(value / 12, 2), f"{item.quote} (annual ÷ 12)"
        if stated == "quarterly":
            return round(value / 3, 2), f"{item.quote} (quarterly ÷ 3)"
        if stated == "weekly":
            return round(value * 4.33, 2), f"{item.quote} (weekly × 4.33)"
        return value, f"{item.quote} (monthly)"

    if target_unit == "annual":
        if stated == "monthly":
            return round(value * 12, 2), f"{item.quote} (monthly × 12)"
        if stated == "quarterly":
            return round(value * 4, 2), f"{item.quote} (quarterly × 4)"
        if stated == "weekly":
            return round(value * 52, 2), f"{item.quote} (weekly × 52)"
        return value, f"{item.quote} (annual)"

    if target_unit == "quarterly":
        if stated == "monthly":
            return round(value * 3, 2), f"{item.quote} (monthly × 3)"
        if stated == "annual":
            return round(value / 4, 2), f"{item.quote} (annual ÷ 4)"
        if stated == "weekly":
            return round(value * 13, 2), f"{item.quote} (weekly × 13)"
        return value, f"{item.quote} (quarterly)"

    # Fallback: no conversion.
    return value, f"{item.quote}"


def _stated_period_issue(item: SpendItem) -> Issue | None:
    """Return an issue if the user didn't state a time period."""
    if item.period == "not_stated":
        return Issue(
            level="ambiguous",
            message=f"Time period not stated for {item.spend_key} "
                    f"({item.quote}). Is that per month or per year?",
        )
    return None


# ---- planner ----------------------------------------------------------------

def plan(
    interp: Interpretation,
    catalog: ToolCatalog,
    user_texts: list[str],
) -> TurnResult:
    """Turn an Interpretation into a TurnResult with proposed steps."""
    result = TurnResult(
        user_message=user_texts[-1] if user_texts else "",
        status="READY",
        intent=interp.intent,
        confidence=interp.confidence,
        understood=interp.summary,
    )

    # --- out_of_scope / unclear: no tool to propose --------------------------
    if interp.intent == "out_of_scope":
        result.status = "OUT_OF_SCOPE"
        result.out_of_scope_reason = (
            interp.out_of_scope_reason or "This question is outside the scope of credit card advice."
        )
        return result

    if interp.intent == "unclear":
        result.status = "NEEDS_CLARIFICATION"
        if interp.clarifying_question:
            result.clarifying_questions.append(interp.clarifying_question)
        for amb in interp.ambiguities:
            result.issues.append(Issue(level="ambiguous", message=amb))
        return result

    # --- map intent to tool --------------------------------------------------
    tool_name = INTENT_TO_TOOL.get(interp.intent)
    if tool_name is None:
        result.status = "ERROR"
        result.error = f"No tool mapped for intent '{interp.intent}'."
        return result

    args: dict[str, Any] = {}
    evidence: list[Evidence] = []
    issues: list[Issue] = []

    # --- spend_recommendation ------------------------------------------------
    if interp.intent == "spend_recommendation":
        valid_keys = set(catalog.spend_keys)
        has_spend = False
        for item in interp.spends:
            if item.spend_key not in valid_keys:
                issues.append(Issue(
                    level="invalid",
                    message=f"Unknown spend key '{item.spend_key}'. "
                            f"Allowed: {', '.join(sorted(valid_keys))}",
                ))
                continue

            # Check if period was stated.
            period_issue = _stated_period_issue(item)
            if period_issue:
                issues.append(period_issue)
                continue  # Don't convert without knowing the period.

            target_unit = catalog.spend_unit(item.spend_key)
            converted, note = _convert_spend(item, target_unit)
            args[item.spend_key] = converted
            evidence.append(Evidence(
                argument=item.spend_key,
                value=converted,
                source=note,
            ))
            has_spend = True

        if not has_spend and not issues:
            issues.append(Issue(
                level="missing",
                message="At least one spending amount is needed for a recommendation.",
            ))

        # Optional: top_n.
        if interp.requested_top_n is not None:
            args["top_n"] = interp.requested_top_n
            evidence.append(Evidence(
                argument="top_n",
                value=interp.requested_top_n,
                source="user requested",
            ))

        if not issues:
            result.follow_ups.append(
                "Adding more spending categories would improve the ranking."
            )

    # --- browse_cards --------------------------------------------------------
    elif interp.intent == "browse_cards":
        f = interp.filters
        if f.category:
            valid_cats = catalog.list_categories
            if f.category in valid_cats:
                args["category"] = f.category
                evidence.append(Evidence(
                    argument="category", value=f.category, source="user request",
                ))
            else:
                issues.append(Issue(
                    level="invalid",
                    message=f"Unknown category '{f.category}'. "
                            f"Allowed: {', '.join(valid_cats)}",
                ))
        if f.free_cards_only:
            args["free_cards"] = "true"
            evidence.append(Evidence(
                argument="free_cards", value="true",
                source="user asked for lifetime-free / no-fee",
            ))
        if f.card_network:
            args["card_networks"] = f.card_network
            evidence.append(Evidence(
                argument="card_networks", value=f.card_network,
                source="user request",
            ))
        if f.annual_fee_range:
            args["annual_fees"] = f.annual_fee_range
            evidence.append(Evidence(
                argument="annual_fees", value=f.annual_fee_range,
                source="user request",
            ))
        if f.bank_name:
            issues.append(Issue(
                level="info",
                message=f"Bank filter '{f.bank_name}' noted, but the tool "
                        f"requires numeric bank_ids which we cannot look up yet.",
            ))

    # --- card_details --------------------------------------------------------
    elif interp.intent == "card_details":
        if len(interp.card_mentions) != 1:
            issues.append(Issue(
                level="missing" if not interp.card_mentions else "ambiguous",
                message="Exactly one card name is needed for details."
                        + (f" You mentioned: {', '.join(interp.card_mentions)}"
                           if interp.card_mentions else ""),
            ))
        else:
            card_name = interp.card_mentions[0]
            evidence.append(Evidence(
                argument="card_alias",
                value=f"← needs resolution from '{card_name}'",
                source=f"user wrote: {card_name}",
            ))

    # --- compare_cards -------------------------------------------------------
    elif interp.intent == "compare_cards":
        if not (2 <= len(interp.card_mentions) <= 3):
            issues.append(Issue(
                level="missing" if len(interp.card_mentions) < 2 else "invalid",
                message=f"Compare needs 2–3 cards, got {len(interp.card_mentions)}."
                        + (f" You mentioned: {', '.join(interp.card_mentions)}"
                           if interp.card_mentions else ""),
            ))
        else:
            for card_name in interp.card_mentions:
                evidence.append(Evidence(
                    argument="card_aliases[]",
                    value=f"← needs resolution from '{card_name}'",
                    source=f"user wrote: {card_name}",
                ))

    # --- eligibility_check ---------------------------------------------------
    elif interp.intent == "eligibility_check":
        e = interp.eligibility
        if e.pincode:
            args["pincode"] = e.pincode
            evidence.append(Evidence(
                argument="pincode", value=e.pincode, source="user typed",
            ))
        else:
            issues.append(Issue(
                level="missing",
                message="A 6-digit pincode is required for eligibility check.",
            ))

        if e.income_amount is not None and e.income_period != "not_stated":
            # Convert to annual (the tool expects annual_income).
            if e.income_period == "monthly":
                annual = round(e.income_amount * 12)
                note = f"{e.income_quote} (monthly × 12)"
            elif e.income_period == "quarterly":
                annual = round(e.income_amount * 4)
                note = f"{e.income_quote} (quarterly × 4)"
            elif e.income_period == "weekly":
                annual = round(e.income_amount * 52)
                note = f"{e.income_quote} (weekly × 52)"
            else:
                annual = round(e.income_amount)
                note = f"{e.income_quote} (annual)"
            args["annual_income"] = str(annual)
            evidence.append(Evidence(
                argument="annual_income", value=annual, source=note,
            ))
        elif e.income_amount is not None and e.income_period == "not_stated":
            issues.append(Issue(
                level="ambiguous",
                message=f"Income amount {format_inr(e.income_amount)} given but "
                        f"period not stated. Is that per month or per year?",
            ))
        else:
            issues.append(Issue(
                level="missing",
                message="Income amount is required for eligibility check.",
            ))

        if e.employment_status:
            valid_emp = catalog.employment_statuses
            if e.employment_status in valid_emp:
                args["employment_status"] = e.employment_status
                evidence.append(Evidence(
                    argument="employment_status", value=e.employment_status,
                    source="user stated",
                ))
            else:
                issues.append(Issue(
                    level="invalid",
                    message=f"Unknown employment status '{e.employment_status}'. "
                            f"Allowed: {', '.join(valid_emp)}",
                ))
        else:
            issues.append(Issue(
                level="missing",
                message="Employment status (salaried / self_employed) is required.",
            ))

    # --- add card resolution step if needed ----------------------------------
    needs_resolution = (
        interp.intent in ("card_details", "compare_cards")
        and interp.card_mentions
        and not any(i.level in BLOCKING_LEVELS for i in issues)
    )

    steps: list[PlannedStep] = []
    if needs_resolution:
        for card_name in interp.card_mentions:
            steps.append(PlannedStep(
                tool=RESOLVER_TOOL,
                arguments={"phrase": card_name},
                purpose=f"Resolve '{card_name}' to a verified card alias",
            ))
        # The main tool step has pending arguments.
        if interp.intent == "card_details":
            main_step = PlannedStep(
                tool=tool_name,
                arguments=args,
                purpose=f"Get details for the resolved card",
                pending_arguments={"card_alias": "from resolution step"},
            )
        else:  # compare_cards
            main_step = PlannedStep(
                tool=tool_name,
                arguments=args,
                purpose=f"Compare the resolved cards",
                pending_arguments={"card_aliases": "from resolution steps"},
            )
        steps.append(main_step)
    else:
        if tool_name and interp.intent not in ("unclear", "out_of_scope"):
            steps.append(PlannedStep(
                tool=tool_name,
                arguments=args,
                purpose=f"Execute {interp.intent} query",
            ))

    result.steps = steps
    result.evidence = evidence
    result.issues.extend(issues)

    # LLM-generated clarifying question and ambiguities.
    if interp.clarifying_question and interp.intent != "unclear":
        result.clarifying_questions.append(interp.clarifying_question)
    for amb in interp.ambiguities:
        if not any(amb in i.message for i in result.issues):
            result.issues.append(Issue(level="ambiguous", message=amb))

    # --- validate each step --------------------------------------------------
    for step in result.steps:
        if step.arguments:  # Only validate if there are arguments.
            validate_step(step, catalog, user_texts, evidence, result)

    # --- determine final status ----------------------------------------------
    has_blocking = any(i.level in BLOCKING_LEVELS for i in result.issues)
    if has_blocking or result.clarifying_questions:
        result.status = "NEEDS_CLARIFICATION"
        # Generate code-template clarifying questions for missing fields.
        missing = [i for i in result.issues if i.level == "missing"]
        for m in missing:
            q = _missing_field_question(m.message)
            if q and q not in result.clarifying_questions:
                result.clarifying_questions.append(q)
    elif not result.steps:
        result.status = "ERROR"
        result.error = "No action could be planned."
    else:
        result.status = "READY"

    return result


def _missing_field_question(message: str) -> str | None:
    """Generate a template question for common missing fields."""
    msg_lower = message.lower()
    if "pincode" in msg_lower:
        return "What is your 6-digit pincode?"
    if "income" in msg_lower and "period" not in msg_lower:
        return "What is your annual or monthly income?"
    if "employment" in msg_lower:
        return "Are you salaried or self-employed?"
    if "spending" in msg_lower or "spend" in msg_lower:
        return "Could you share approximate monthly spending amounts?"
    if "card name" in msg_lower or "card" in msg_lower and "exactly" in msg_lower:
        return "Which card(s) are you interested in?"
    return None
