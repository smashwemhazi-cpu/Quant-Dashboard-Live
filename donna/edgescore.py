"""Inbox for SH's Market Toolkit (edgescore\\donna.py on SH's PC): it posts JSON, DONNA stores it.

Address (from SH's PC, while the DONNA Console link is open):  POST http://localhost:8765/api/edgescore
  header  X-Donna-Token: <token>      (or  Authorization: Bearer <token>)
  body    one JSON object, or a list of objects, at most 256 KB

Safe by default:
  - never on the public internet: answers only through the SSH link, like the Console (tunnel_only);
  - refuses everything until the token is made on the server with `vertex1-connect edgescore`
    (stored as DONNA_EDGESCORE_TOKEN in /etc/vertex1/vertex1.env, never in code or chat);
  - log-only: the message is stored and audited. Nothing is traded, sent or broadcast.
"""
from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from donna.console import tunnel_only

MAX_BYTES = 256 * 1024
KEEP_ROWS = 5000
# Fields picked out for a quick summary if the Toolkit sends them; the full JSON is always kept.
SUMMARY_KEYS = ("symbol", "ticker", "instrument", "score", "edge_score", "edgescore", "bias",
                "direction", "signal", "regime", "timeframe", "confidence")

router = APIRouter()


def _token() -> str:
    return os.getenv("DONNA_EDGESCORE_TOKEN", "")


def require_token(x_donna_token: str = Header(default=""), authorization: str = Header(default="")) -> None:
    want = _token()
    if not want:
        raise HTTPException(503, "edgescore inbox not set up yet: run `vertex1-connect edgescore` on the server")
    got = x_donna_token or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
    if not (got and hmac.compare_digest(got.encode(), want.encode())):
        raise HTTPException(401, "invalid token")


def _init(c: Any) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS edgescore_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL,
        symbol TEXT, summary TEXT, payload TEXT NOT NULL)""")


def summarise(item: Any) -> tuple[str | None, str]:
    if not isinstance(item, dict):
        return None, str(item)[:200]
    sym = next((str(item[k]) for k in ("symbol", "ticker", "instrument") if item.get(k) not in (None, "")), None)
    parts = [f"{k}={item[k]}" for k in SUMMARY_KEYS if k in item and not isinstance(item[k], (dict, list))]
    return (sym[:32] if sym else None), (", ".join(parts) or "keys: " + ", ".join(list(item)[:12]))[:300]


@router.post("/api/edgescore", dependencies=[Depends(tunnel_only), Depends(require_token)])
async def receive(request: Request) -> dict[str, Any]:
    from donna.app import audit, db
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, "too large (max 256 KB)")
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "too large (max 256 KB)")
    try:
        data = json.loads(raw or b"null")
    except ValueError:
        raise HTTPException(400, "body is not valid JSON") from None
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(i, dict) for i in items) or len(items) > 500:
        raise HTTPException(422, "send one JSON object or a list of up to 500 objects")
    now, ids = datetime.now(timezone.utc).isoformat(), []
    with db() as c:
        _init(c)
        for it in items:
            sym, summ = summarise(it)
            cur = c.execute("INSERT INTO edgescore_inbox (received_at, symbol, summary, payload) VALUES (?,?,?,?)",
                            (now, sym, summ, json.dumps(it, default=str)))
            ids.append(cur.lastrowid)
        c.execute("DELETE FROM edgescore_inbox WHERE id <= (SELECT MAX(id) FROM edgescore_inbox) - ?", (KEEP_ROWS,))
    audit("edgescore", "EDGESCORE_RECEIVED", {"count": len(ids), "first_id": ids[0]})
    return {"ok": True, "stored": len(ids), "ids": ids}


@router.get("/api/edgescore/latest", dependencies=[Depends(tunnel_only), Depends(require_token)])
def latest(limit: int = 10) -> dict[str, Any]:
    from donna.app import db
    with db() as c:
        _init(c)
        rows = c.execute("SELECT id, received_at, symbol, summary, payload FROM edgescore_inbox "
                         "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    return {"items": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]}
