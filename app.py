"""FastAPI web application for the Great.cards advisor chatbot.

Run with:  uvicorn app:app --reload --port 8000
Then open: http://localhost:8000
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from advisor.answerer import generate_answer
from advisor.config import ConfigError, load_settings
from advisor.executor import execute_plan
from advisor.guardrails import check_interpretation, check_raw_text
from advisor.interpreter import interpret
from advisor.llm.base import LLMError
from advisor.models import TurnResult
from advisor.planner import plan
from advisor.tool_catalog import CatalogError, ToolCatalog
from advisor.trace import TraceLogger

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Startup: config + catalog
# ---------------------------------------------------------------------------

try:
    settings = load_settings()
except ConfigError as exc:
    raise SystemExit(f"Configuration error: {exc}") from exc

catalog: ToolCatalog | None = None

if settings.mcp_url:
    try:
        from advisor.mcp_client import list_tools_sync
        mcp_tools = list_tools_sync(settings.mcp_url)
        catalog = ToolCatalog.from_mcp_tools(mcp_tools)
        log.info("Tool catalog loaded from MCP server (%d tools)", len(catalog.tools))
    except Exception as exc:
        log.warning("Could not load catalog from MCP server: %s", exc)

if catalog is None:
    try:
        catalog = ToolCatalog.from_snapshot(settings.tools_snapshot)
        log.info("Tool catalog loaded from local snapshot")
    except (CatalogError, FileNotFoundError) as exc:
        raise SystemExit(f"Tool catalog error: {exc}") from exc

tracer = TraceLogger(settings.log_dir)

# ---------------------------------------------------------------------------
# App + templates
# ---------------------------------------------------------------------------

app = FastAPI(title="Great.cards Advisor", version="2.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# In-memory session store.
sessions: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ResetRequest(BaseModel):
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat")
async def chat(body: ChatRequest):
    message = body.message.strip()
    session_id = body.session_id or str(uuid.uuid4())

    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    session = sessions.setdefault(session_id, {"messages": [], "user_texts": []})
    messages: list[dict[str, str]] = session["messages"]
    user_texts: list[str] = session["user_texts"]

    messages.append({"role": "user", "text": message})
    user_texts.append(message)

    # Run the blocking pipeline in a thread so we don't block the event loop.
    result: TurnResult = await asyncio.to_thread(
        _process, message, messages, user_texts
    )

    if result.answer:
        messages.append({"role": "model", "text": result.answer})
    elif result.understood:
        messages.append({"role": "model", "text": result.understood})

    return {
        "session_id": session_id,
        "answer": result.answer or result.error or "I couldn't process that.",
        "status": result.status,
        "intent": result.intent,
        "policy_notes": result.policy_notes,
    }


@app.post("/api/reset")
async def reset(body: ResetRequest):
    if body.session_id and body.session_id in sessions:
        del sessions[body.session_id]
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pipeline (sync — runs inside asyncio.to_thread)
# ---------------------------------------------------------------------------

def _process(
    text: str,
    messages: list[dict[str, str]],
    user_texts: list[str],
) -> TurnResult:
    """Run the full pipeline: guardrails → interpret → plan → execute → answer."""
    result = TurnResult(user_message=text, status="ERROR")
    raw_llm = None
    interp = None

    try:
        check_raw_text(text, result)

        interp = interpret(settings, catalog, messages)
        raw_llm = interp.model_dump_json(indent=2)

        check_interpretation(interp, result)

        result = plan(interp, catalog, user_texts)

        check_raw_text(text, result)
        check_interpretation(interp, result)

        execute_plan(settings, result)

        generate_answer(settings, result)

    except LLMError as exc:
        result.status = "ERROR"
        result.error = str(exc)
        result.answer = f"Sorry, I had trouble processing that: {exc}"
        tracer.log_turn(user_message=text, error=str(exc))
    except Exception as exc:
        result.status = "ERROR"
        result.error = f"Unexpected error: {exc}"
        result.answer = "Something went wrong. Please try again."
        log.exception("Unexpected error in pipeline")
        tracer.log_turn(user_message=text, error=str(exc))
    else:
        tracer.log_turn(
            user_message=text,
            raw_llm_output=raw_llm,
            interpretation=interp,
            turn_result=result,
        )

    return result


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print(f"\n  Great.cards Advisor — Web UI")
    print(f"  Model : {settings.llm_model} | Provider: {settings.llm_provider}")
    print(f"  MCP   : {settings.mcp_url or 'NOT CONFIGURED'}")
    print(f"  Open  : http://localhost:8000\n")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
