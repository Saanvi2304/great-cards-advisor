"""JSONL trace logging for every turn.

Each turn is logged as one JSON line containing the input, raw LLM output,
parsed interpretation, validation results, and the final proposed action.
Logs stay local (``logs/`` is git-ignored) and never contain the API key.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from advisor.config import LOG_DIR

log = logging.getLogger(__name__)


class TraceLogger:
    """Append-only JSONL logger for pipeline traces."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._path = self._log_dir / f"trace_{ts}.jsonl"
        self._turn = 0

    @property
    def path(self) -> Path:
        return self._path

    def log_turn(
        self,
        *,
        user_message: str,
        raw_llm_output: str | None = None,
        interpretation: Any = None,
        turn_result: Any = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Log one complete turn as a single JSONL line."""
        self._turn += 1
        record: dict[str, Any] = {
            "turn": self._turn,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "user_message": user_message,
        }
        if raw_llm_output is not None:
            record["raw_llm_output"] = raw_llm_output
        if interpretation is not None:
            record["interpretation"] = (
                interpretation.model_dump()
                if hasattr(interpretation, "model_dump")
                else str(interpretation)
            )
        if turn_result is not None:
            record["turn_result"] = (
                turn_result.model_dump()
                if hasattr(turn_result, "model_dump")
                else str(turn_result)
            )
        if error:
            record["error"] = error
        if extra:
            record.update(extra)

        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning("Failed to write trace log: %s", exc)
