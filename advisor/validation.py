"""JSON-schema validation and business-rule checks.

Validates the proposed tool arguments against the real JSON schema from
``data/mcp_tools.json`` and applies additional business rules that the
schema does not enforce (e.g. positive amounts, sane upper limits, number
grounding).
"""
from __future__ import annotations

import logging
from typing import Any

import jsonschema

from advisor.amounts import is_grounded
from advisor.models import (
    BLOCKING_LEVELS,
    Evidence,
    Issue,
    PlannedStep,
    TurnResult,
)
from advisor.tool_catalog import ToolCatalog

log = logging.getLogger(__name__)

# Sanity limits.
MAX_MONTHLY_SPEND = 10_000_000  # ₹1 crore per month.
MAX_TOP_N = 10


def validate_step(
    step: PlannedStep,
    catalog: ToolCatalog,
    user_texts: list[str],
    evidence: list[Evidence],
    result: TurnResult,
) -> None:
    """Validate a planned step's arguments in place, adding issues to *result*.

    1. JSON-schema validation against the real tool schema.
    2. Business rules (positive amounts, sane limits, number grounding).
    """
    _validate_json_schema(step, catalog, result)
    _validate_business_rules(step, user_texts, evidence, result)


# ---- JSON-schema validation -------------------------------------------------

def _validate_json_schema(
    step: PlannedStep,
    catalog: ToolCatalog,
    result: TurnResult,
) -> None:
    """Check the arguments against the tool's real input JSON schema."""
    schema = catalog.schema(step.tool)
    validator = jsonschema.Draft7Validator(schema)
    for error in sorted(validator.iter_errors(step.arguments), key=str):
        result.issues.append(Issue(
            level="invalid",
            message=f"Schema validation error for {step.tool}: {error.message} "
                    f"(at {'.'.join(str(p) for p in error.absolute_path) or 'root'})",
        ))


# ---- business rules ---------------------------------------------------------

def _validate_business_rules(
    step: PlannedStep,
    user_texts: list[str],
    evidence: list[Evidence],
    result: TurnResult,
) -> None:
    """Apply rules the JSON schema doesn't enforce."""
    args = step.arguments

    # 1. top_n sanity.
    if "top_n" in args:
        top_n = args["top_n"]
        if not isinstance(top_n, int) or top_n < 1 or top_n > MAX_TOP_N:
            result.issues.append(Issue(
                level="invalid",
                message=f"top_n must be between 1 and {MAX_TOP_N}, got {top_n}.",
            ))

    # 2. Positive amounts + sanity ceiling.
    for key, val in args.items():
        if not isinstance(val, (int, float)):
            continue
        if key in ("top_n",):
            continue  # Already handled.
        if val < 0:
            result.issues.append(Issue(
                level="invalid",
                message=f"Negative value for {key}: {val}.",
            ))
        elif val > MAX_MONTHLY_SPEND and key not in (
            "annual_income",
            "flights_annual",
            "hotels_annual",
            "insurance_health_annual",
            "insurance_car_or_bike_annual",
            "life_insurance",
        ):
            result.issues.append(Issue(
                level="info",
                message=f"{key}={val:,.0f} is very high — please double-check.",
            ))

    # 3. Number grounding: every rupee amount must come from the user's text.
    for ev in evidence:
        if isinstance(ev.value, (int, float)) and ev.value > 0:
            if not is_grounded(ev.value, user_texts):
                result.issues.append(Issue(
                    level="unverified",
                    message=f"Amount {ev.value:,.0f} for '{ev.argument}' could not "
                            f"be traced to a number the user typed.",
                ))

    # 4. Pincode must be a 6-digit number the user actually typed.
    if "pincode" in args:
        pincode = str(args["pincode"])
        if len(pincode) != 6 or not pincode.isdigit():
            result.issues.append(Issue(
                level="invalid",
                message=f"Pincode must be exactly 6 digits, got '{pincode}'.",
            ))

    # 5. Card aliases must never be invented (checked in planner; here we just
    #    flag if they somehow got through with suspicious patterns).
    for key in ("card_alias", "card_aliases"):
        if key in args:
            val = args[key] if isinstance(args[key], list) else [args[key]]
            for alias in val:
                if isinstance(alias, str) and alias != alias.lower():
                    result.issues.append(Issue(
                        level="info",
                        message=f"Card alias '{alias}' contains uppercase — aliases "
                                f"are typically all-lowercase slugs.",
                    ))
