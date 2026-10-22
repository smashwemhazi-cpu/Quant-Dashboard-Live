"""Append-only audit log (SQLite). Brokerages are regulated: every tool call,
every AI answer and every approval is recorded with who/when/what."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.core.config import settings

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    Path(settings.state_db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.state_db, check_same_thread=False)
    c.execute(
        """CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
            payload TEXT NOT NULL)"""
    )
    return c


def log(actor: str, event: str, payload: dict[str, Any] | None = None) -> None:
    row = (
        datetime.now(timezone.utc).isoformat(),
        actor,
        event,
        json.dumps(payload or {}, default=str)[:20000],
    )
    with _lock, _conn() as c:
        c.execute("INSERT INTO audit (ts, actor, event, payload) VALUES (?,?,?,?)", row)


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT ts, actor, event, payload FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(zip(("ts", "actor", "event", "payload"), r)) for r in rows]
