"""DONNA Console: her animated face, voice and live panels, opened on SH's PC through an SSH tunnel.

Never on the public internet. Every console route answers only when the request
  - comes from the server itself (the SSH tunnel ends on 127.0.0.1), with no proxy headers (nginx adds them),
  - is addressed to localhost (stops DNS-rebinding pages from reaching it through the tunnel),
  - and, for the API, carries the X-Donna: console header (a cross-site page can't add it without a CORS
    preflight, which is never granted).
Anything else gets a 404, as if the console did not exist.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

PAGE = Path(__file__).with_name("console.html")
LOCAL_CLIENTS = {"127.0.0.1", "::1"}
LOCAL_HOST = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$")
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host")

router = APIRouter()


def tunnel_only(request: Request) -> None:
    h = request.headers
    if (request.client is None or request.client.host not in LOCAL_CLIENTS
            or any(k in h for k in PROXY_HEADERS) or not LOCAL_HOST.match(h.get("host", ""))):
        raise HTTPException(404)


def console_api(request: Request) -> None:
    tunnel_only(request)
    if request.headers.get("x-donna") != "console":
        raise HTTPException(404)


@router.get("/console", response_class=HTMLResponse, dependencies=[Depends(tunnel_only)])
def console_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                   "style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:"})


def _section(fn: Any) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - one broken source must not blank the whole console
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def state() -> dict[str, Any]:
    from jarvis.agents import assistant, compliance, rhythm

    def calendar() -> Any:
        c = assistant.calendar_events()
        return c if "error" in c else c["events"][:12]

    def mail() -> dict[str, Any]:
        ib = assistant.inbox_status()
        return {"connected": assistant.inbox_connected(), "analysed_24h": ib["emails"],
                "urgent": [{"subject": r["subject"][:90], "summary": r["summary"][:160]} for r in ib["high"][:5]],
                "reply": [{"subject": r["subject"][:90], "from": r["sender_domain"]} for r in ib["needs_reply"][:5]]}

    def kris() -> Any:
        return [{"kri": k["kri"], "status": k["status"], "value": str(k["value"])[:80]}
                for k in compliance.kris() if k["status"] not in ("GREEN", "OK")][:6]

    return {"now": datetime.now(assistant.LOCAL_TZ).strftime("%a %d %b %Y %H:%M"),
            "calendar": _section(calendar),
            "tasks": _section(lambda: [{"id": t["id"], "title": t["title"][:90], "priority": t["priority"],
                                        "due": t["due"]} for t in assistant.list_tasks()[:12]]),
            "mail": _section(mail), "kris": _section(kris), "risk": _section(rhythm.risk_lines)}


@router.get("/api/console/state", dependencies=[Depends(console_api)])
def console_state() -> dict[str, Any]:
    return state()


class Say(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def reply(text: str) -> str:
    """Commands work as in Discord (!tasks, !done 12, !kri ...); anything else is conversation."""
    from donna import discord_bot
    from jarvis.agents import mind

    text = text.strip()
    if text.startswith(("!", "#")):
        return discord_bot.handle(text, channel="console") or "I don't know that command - try !help"
    return mind.chat(text, channel="console", allow_writes=False)


@router.post("/api/console/say", dependencies=[Depends(console_api)])
def console_say(body: Say) -> dict[str, str]:
    return {"reply": reply(body.text)}
