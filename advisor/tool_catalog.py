"""Knowledge about the real Great.cards MCP tools.

Everything here is grounded in data/mcp_tools.json (a verbatim snapshot of the
server's tool list). Values that the schema leaves as free text but the tool
description lists explicitly are kept as small constants, each citing its
source, so they are easy to review.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Intent -> the one tool that serves it. The LLM picks the intent; code picks
# the tool, so a model mistake can never select an unlisted tool.
INTENT_TO_TOOL: dict[str, str] = {
    "spend_recommendation": "recommend_cards",
    "browse_cards": "list_cards",
    "card_details": "get_card_details",
    "compare_cards": "compare_cards",
    "eligibility_check": "check_eligibility",
}

# Its description says: "Use before details, comparison, or category recommendation tools."
RESOLVER_TOOL = "whatsapp_resolve_card_or_category"

ALLOWED_TOOLS: frozenset[str] = frozenset(INTENT_TO_TOOL.values()) | {RESOLVER_TOOL}

# Tools present on the server that this chatbot must never propose.
EXCLUDED_TOOLS: dict[str, str] = {
    "run_api_diagnostics": "backend health checks - an operations tool, not for end users",
    "generate_content_brief": "marketing content generator (blogs, reels, 'push to apply')",
    "get_advisor_context": "session-level playbook; planned for a later stage, never a user action",
    "whatsapp_get_next_question": "server-side flow helper; clarifications are decided by this app",
    "whatsapp_recommend_cards_for_spend_profile": "duplicate of recommend_cards",
    "whatsapp_recommend_cards_for_category_spend": "duplicate of recommend_cards for one category",
    "whatsapp_compare_cards": "duplicate of compare_cards",
    "whatsapp_get_card_details": "duplicate of get_card_details",
}

# recommend_cards parameters that are settings, not spending categories.
RECOMMEND_SETTINGS = {"top_n", "response_format"}

# Units, from the recommend_cards description ("IMPORTANT UNITS") and the
# per-field descriptions: these fields are ANNUAL rupees ...
ANNUAL_SPEND_KEYS = frozenset(
    {"flights_annual", "hotels_annual", "insurance_health_annual",
     "insurance_car_or_bike_annual", "life_insurance"}
)
# ... and these are lounge VISITS per quarter (counts, not rupees).
QUARTERLY_COUNT_KEYS = frozenset(
    {"domestic_lounge_usage_quarterly", "international_lounge_usage_quarterly"}
)
# Every other spend key is MONTHLY rupees.

# list_cards.card_networks has no enum; these are the values its description lists.
DOCUMENTED_CARD_NETWORKS = ("Visa", "Mastercard", "RuPay", "American Express")

# list_cards.annual_fees has no enum; these are the ranges its description lists.
# "free" is also mentioned there, but the dedicated free_cards="true" filter is
# used for lifetime-free requests instead (the description maps it both ways).
DOCUMENTED_FEE_RANGES = ("500-1000", "1000-2000", "2000-5000", "5000-10000", "10000+")


class CatalogError(Exception):
    """Raised when the snapshot is missing tools this app depends on."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCatalog:
    """Registry of MCP tool definitions.

    Construct via one of the classmethods:
    - ``ToolCatalog.from_snapshot(path)`` — load from a local JSON file.
    - ``ToolCatalog.from_mcp_tools(tools)`` — load from a live MCP
      ``list_tools()`` response so the app can run without the snapshot.
    """

    def __init__(self, tools_list: list[dict[str, Any]]):
        self.tools: dict[str, ToolSpec] = {
            t["name"]: ToolSpec(t["name"], t["description"], t["input_schema"])
            for t in tools_list
        }
        missing = sorted(ALLOWED_TOOLS - self.tools.keys())
        if missing:
            raise CatalogError(f"Tool snapshot is missing required tools: {', '.join(missing)}")

    @classmethod
    def from_snapshot(cls, snapshot_path: Path) -> "ToolCatalog":
        """Load from a local JSON snapshot file (data/mcp_tools.json)."""
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return cls(data["tools"])

    @classmethod
    def from_mcp_tools(cls, mcp_tools: list) -> "ToolCatalog":
        """Build from the MCP SDK's ``list_tools()`` result objects."""
        tools_list = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in mcp_tools
        ]
        return cls(tools_list)

    # ---- generic lookups ---------------------------------------------------
    def schema(self, tool: str) -> dict[str, Any]:
        return self.tools[tool].input_schema

    def properties(self, tool: str) -> dict[str, Any]:
        return self.schema(tool).get("properties", {})

    def property_description(self, tool: str, prop: str) -> str:
        return self.properties(tool).get(prop, {}).get("description", "")

    # ---- values derived from the real schemas --------------------------------
    @property
    def spend_keys(self) -> list[str]:
        return [k for k in self.properties("recommend_cards") if k not in RECOMMEND_SETTINGS]

    @property
    def list_categories(self) -> list[str]:
        enum = self.properties("list_cards")["category"].get("enum", [])
        return [c for c in enum if c]  # "" means "all cards"; not a category

    @property
    def employment_statuses(self) -> list[str]:
        return list(self.properties("check_eligibility")["employment_status"]["enum"])

    def spend_unit(self, key: str) -> str:
        """Return 'annual', 'quarterly' or 'monthly' for a recommend_cards key."""
        if key in ANNUAL_SPEND_KEYS:
            return "annual"
        if key in QUARTERLY_COUNT_KEYS:
            return "quarterly"
        return "monthly"
