"""작동 및 오류 이벤트를 로컬 JSONL 파일에 기록합니다."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "events.jsonl"
_lock = threading.Lock()
_logger = logging.getLogger("picarx.events")


def record_event(category: str, message: str, *, level: str = "INFO", details: dict[str, Any] | None = None) -> dict[str, Any]:
    """이벤트를 콘솔 로거와 파일에 남기며, 로그 쓰기 실패가 제어를 막지 않게 합니다."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "category": category,
        "message": message,
        "details": details or {},
    }
    log_line = json.dumps(entry, ensure_ascii=False, default=str)
    getattr(_logger, "error" if level.upper() == "ERROR" else "warning" if level.upper() == "WARNING" else "info")(log_line)
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000:
                backup = LOG_PATH.with_suffix(".jsonl.1")
                backup.unlink(missing_ok=True)
                LOG_PATH.replace(backup)
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                stream.write(log_line + "\n")
    except OSError:
        _logger.exception("Could not persist PiCar-X event log")
    return entry


def read_events(limit: int = 100) -> list[dict[str, Any]]:
    """최근 로그를 최신순으로 읽습니다. 손상된 행은 건너뜁니다."""
    if not LOG_PATH.exists():
        return []
    try:
        with _lock:
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 500)):]
        result = []
        for line in reversed(lines):
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except json.JSONDecodeError:
                continue
        return result
    except OSError:
        _logger.exception("Could not read PiCar-X event log")
        return []
