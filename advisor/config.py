"""Application settings, read from the project's .env file.

Step 1 deliberately does not read MCP_URL: nothing in this stage talks to the
Great.cards server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TOOLS_SNAPSHOT = DATA_DIR / "mcp_tools.json"

DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def load_env_file(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing values.

    Same simple format as the original inspection script, so no extra
    dependency is needed.
    """
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _clean(value: str | None) -> str | None:
    """Treat empty values and example placeholders as 'not set'."""
    if value is None:
        return None
    value = value.strip()
    if not value or value.upper().startswith("PASTE YOUR"):
        return None
    return value


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    llm_timeout_seconds: float
    # repr=False keeps the key out of any accidental print/log of Settings.
    gemini_api_key: str | None = field(default=None, repr=False)
    mcp_url: str | None = None
    log_dir: Path = LOG_DIR
    tools_snapshot: Path = TOOLS_SNAPSHOT


def load_settings() -> Settings:
    load_env_file()

    provider = (_clean(os.getenv("LLM_PROVIDER")) or DEFAULT_PROVIDER).lower()
    model = _clean(os.getenv("LLM_MODEL")) or DEFAULT_MODEL

    timeout_raw = _clean(os.getenv("LLM_TIMEOUT_SECONDS"))
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError as exc:
        raise ConfigError("LLM_TIMEOUT_SECONDS in .env must be a number of seconds.") from exc
    if timeout <= 0:
        raise ConfigError("LLM_TIMEOUT_SECONDS in .env must be greater than 0.")

    if not TOOLS_SNAPSHOT.is_file():
        raise ConfigError(f"Tool schema snapshot not found: {TOOLS_SNAPSHOT}")

    return Settings(
        llm_provider=provider,
        llm_model=model,
        llm_timeout_seconds=timeout,
        gemini_api_key=_clean(os.getenv("GEMINI_API_KEY")),
        mcp_url=_clean(os.getenv("MCP_URL")),
    )
