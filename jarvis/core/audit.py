"""Append-only audit log (SQLite). Brokerages are regulated: every tool call,
every AI answer and every approval is recorded with who/when/what.

Tamper-evident: each row stores a SHA-256 over its own content and the previous row's hash, so editing or
deleting a row breaks the chain from that point. verify() walks it; `!audit` in Discord shows the result."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.core.config import settings

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False, timeout=30)
    c.execute(
        """CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
            payload TEXT NOT NULL)"""
    )
    cols = {r[1] for r in c.execute("PRAGMA table_info(audit)")}
    if "hash" not in cols:  # databases from before the chain: older rows stay unhashed, the chain starts after them
        c.execute("ALTER TABLE audit ADD COLUMN prev_hash TEXT")
        c.execute("ALTER TABLE audit ADD COLUMN hash TEXT")
    return c


def _digest(prev: str, ts: str, actor: str, event: str, payload: str) -> str:
    return hashlib.sha256("\x1f".join((prev, ts, actor, event, payload)).encode()).hexdigest()


def log(actor: str, event: str, payload: dict[str, Any] | None = None) -> None:
    row = (
        datetime.now(timezone.utc).isoformat(),
        actor,
        event,
        json.dumps(payload or {}, default=str)[:20000],
    )
    with _lock:
        c = _conn()
        c.isolation_level = None
        try:  # the scheduler, the bot and the API all write here: read-last + insert must be one locked step
            c.execute("BEGIN IMMEDIATE")
            last = c.execute("SELECT hash FROM audit WHERE hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
            prev = last[0] if last else ""
            c.execute("INSERT INTO audit (ts, actor, event, payload, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                      (*row, prev, _digest(prev, *row)))
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT ts, actor, event, payload FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(zip(("ts", "actor", "event", "payload"), r)) for r in rows]


def verify() -> dict[str, Any]:
    """Recompute the chain. ok=False names the first row that was changed, deleted around or inserted."""
    with _lock, _conn() as c:
        rows = c.execute("SELECT id, ts, actor, event, payload, prev_hash, hash FROM audit WHERE hash IS NOT NULL "
                         "ORDER BY id").fetchall()
        legacy = c.execute("SELECT COUNT(*) FROM audit WHERE hash IS NULL").fetchone()[0]
    prev = ""
    for rid, ts, actor, event, payload, prev_hash, h in rows:
        if prev_hash != prev or h != _digest(prev, ts, actor, event, payload):
            return {"ok": False, "checked": len(rows), "first_bad_id": rid, "unhashed_older_rows": legacy}
        prev = h
    return {"ok": True, "checked": len(rows), "unhashed_older_rows": legacy}


def since(ts: str, limit: int = 500) -> list[dict[str, Any]]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT ts, actor, event, payload FROM audit WHERE ts >= ? ORDER BY id LIMIT ?",
                         (ts, limit)).fetchall()
    return [dict(zip(("ts", "actor", "event", "payload"), r)) for r in rows]
