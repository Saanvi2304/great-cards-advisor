"""Data shapes used by the pipeline.

* ``Interpretation`` is the strict "form" the LLM fills in. It only records
  what the user said; it never contains tool names or final tool arguments.
* ``TurnResult`` is what the application produces for one user message. The
  CLI (and later a web UI) only displays it.

Fields that must match the live tool schema (spend keys, category slugs,
employment values) are plain strings here; their allowed values are injected
into the LLM's response schema from data/mcp_tools.json at runtime, and are
checked again by code afterwards.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Intent = Literal[
    "spend_recommendation",
    "browse_cards",
    "card_details",
    "compare_cards",
    "eligibility_check",
    "unclear",
    "out_of_scope",
]
Period = Literal["monthly", "weekly", "quarterly", "annual", "not_stated"]
Confidence = Literal["high", "medium", "low"]


# --------------------------------------------------------------------------
# LLM output ("interpretation form")
# --------------------------------------------------------------------------
class SpendItem(BaseModel):
    spend_key: str = Field(description="Exactly one of the allowed spending keys.")
    amount: float = Field(
        description=(
            "The number exactly as the user stated it, expanded to full rupees "
            "(15k -> 15000, 1.5 lakh -> 150000) or to a visit count for lounge keys. "
            "No arithmetic, no unit conversion."
        )
    )
    period: Period = Field(description="The time period the user stated for this amount.")
    quote: str = Field(description="The exact words from the user's message that contain this amount.")


class BrowseFilters(BaseModel):
    category: Optional[str] = Field(default=None, description="One allowed list_cards category, or null.")
    free_cards_only: bool = Field(default=False, description="True only if the user asked for lifetime-free / no-fee cards.")
    card_network: Optional[str] = Field(default=None, description="One allowed card network, or null.")
    annual_fee_range: Optional[str] = Field(default=None, description="One allowed annual fee range, or null.")
    bank_name: Optional[str] = Field(default=None, description="Bank name exactly as the user wrote it, or null.")


class EligibilityInfo(BaseModel):
    pincode: Optional[str] = Field(default=None, description="6-digit pincode exactly as typed by the user, or null. Never derive from a city.")
    income_amount: Optional[float] = Field(default=None, description="Income in full rupees as stated (10 LPA -> 1000000), or null.")
    income_period: Period = Field(default="not_stated", description="Period the user stated for the income.")
    income_quote: Optional[str] = Field(default=None, description="Exact words containing the income, or null.")
    employment_status: Optional[str] = Field(default=None, description="One allowed employment value, or null if not stated.")



class Interpretation(BaseModel):
    intent: Intent
    confidence: Confidence
    summary: str = Field(description="One neutral sentence restating what the user wants. No card names you invented, no facts or advice.")
    spends: list[SpendItem] = Field(default_factory=list)
    card_mentions: list[str] = Field(default_factory=list, description="Card names exactly as the user wrote them. Never aliases or slugs.")
    filters: BrowseFilters = Field(default_factory=BrowseFilters)
    eligibility: EligibilityInfo = Field(default_factory=EligibilityInfo)
    requested_top_n: Optional[int] = Field(default=None, description="Number of cards the user explicitly asked for (e.g. 'top 3'), or null.")
    ambiguities: list[str] = Field(default_factory=list, description="Things that are unclear and would change the tool or its arguments.")
    clarifying_question: Optional[str] = Field(default=None, description="One short question to resolve the ambiguities, or null.")
    out_of_scope_reason: Optional[str] = Field(default=None, description="Why the request is out of scope, or null.")


# --------------------------------------------------------------------------
# Application output
# --------------------------------------------------------------------------
Status = Literal["READY", "NEEDS_CLARIFICATION", "OUT_OF_SCOPE", "ERROR"]
IssueLevel = Literal["missing", "ambiguous", "unverified", "invalid", "assumption", "info"]

# Issue levels that stop an action from being READY.
BLOCKING_LEVELS = {"missing", "ambiguous", "unverified", "invalid"}


class Issue(BaseModel):
    level: IssueLevel
    message: str


class Evidence(BaseModel):
    argument: str
    value: Any
    source: str = Field(description="Where the value came from (user quote or rule).")
    note: Optional[str] = None


class PlannedStep(BaseModel):
    tool: str
    arguments: dict[str, Any]
    purpose: str
    # Arguments that will be filled from an earlier step's result in a later stage.
    pending_arguments: dict[str, str] = Field(default_factory=dict)
    executed: bool = False  # Always False in Step 1.


class TurnResult(BaseModel):
    user_message: str
    status: Status
    intent: Optional[str] = None
    confidence: Optional[str] = None
    understood: Optional[str] = None
    steps: list[PlannedStep] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    policy_notes: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    out_of_scope_reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def main_step(self) -> Optional[PlannedStep]:
        return self.steps[-1] if self.steps else None
