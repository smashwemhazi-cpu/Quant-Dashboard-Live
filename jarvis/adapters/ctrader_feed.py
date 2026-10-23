"""Read-only live-position feed from the cTrader Open API, for DONNA's position-risk view.

What it does, every few minutes (scheduler job "ctrader_positions"):
  connect (JSON over WebSocket) -> authorise the app -> authorise the account -> read balance, open
  positions, unrealised P&L and current prices -> store one snapshot via rhythm.store_positions().
It only ever sends read requests. On top of that it refuses to run with a token that has trading
permission (SCOPE_TRADE), so even a wrongly issued token cannot be used to trade from this server.

Settings (in /etc/vertex1/vertex1.env, never in chat or git):
  DONNA_CTRADER_CLIENT_ID / DONNA_CTRADER_CLIENT_SECRET   from your app at openapi.ctrader.com
  DONNA_CTRADER_ACCESS_TOKEN / DONNA_CTRADER_REFRESH_TOKEN issued with the read-only "account info" scope
  DONNA_CTRADER_LOGIN      the account number you see in cTrader (default: JARVIS_PERSONAL_ACCOUNT)
Tokens are refreshed over the same connection (ProtoOARefreshTokenReq) when cTrader reports them invalid
or after DONNA_CTRADER_REFRESH_DAYS; the new pair is kept in the state DB, which then takes precedence.

Check the connection by hand:  python -m jarvis.adapters.ctrader_feed check
Message numbers and fields are from Spotware's official definitions (github.com/spotware/openapi-proto-messages).
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.core import audit
from jarvis.core.config import settings

HOSTS = {True: "wss://live.ctraderapi.com:5036", False: "wss://demo.ctraderapi.com:5036"}
TIMEOUT = float(os.getenv("DONNA_CTRADER_TIMEOUT", "20"))
REFRESH_DAYS = float(os.getenv("DONNA_CTRADER_REFRESH_DAYS", "20"))   # access tokens last about 30 days

# payload types (OpenApiModelMessages.proto / OpenApiCommonModelMessages.proto)
HEARTBEAT = 51
APP_AUTH_REQ, APP_AUTH_RES = 2100, 2101
ACCOUNT_AUTH_REQ, ACCOUNT_AUTH_RES = 2102, 2103
SYMBOLS_LIST_REQ, SYMBOLS_LIST_RES = 2114, 2115
TRADER_REQ, TRADER_RES = 2121, 2122
RECONCILE_REQ, RECONCILE_RES = 2124, 2125
SUBSCRIBE_SPOTS_REQ, SUBSCRIBE_SPOTS_RES = 2127, 2128
UNSUBSCRIBE_SPOTS_REQ = 2129
SPOT_EVENT = 2131
ERROR_RES = 2142
ACCOUNTS_BY_TOKEN_REQ, ACCOUNTS_BY_TOKEN_RES = 2149, 2150
REFRESH_TOKEN_REQ, REFRESH_TOKEN_RES = 2173, 2174
UNREALIZED_PNL_REQ, UNREALIZED_PNL_RES = 2187, 2188
PRICE_SCALE = 100_000          # spot prices are in 1/100000 of a unit
TOKEN_ERRORS = {"CH_ACCESS_TOKEN_INVALID", "OA_AUTH_TOKEN_EXPIRED"}


class FeedError(RuntimeError):
    pass


class TokenInvalid(FeedError):
    pass


def configured() -> bool:
    return bool(os.getenv("DONNA_CTRADER_CLIENT_ID") and os.getenv("DONNA_CTRADER_CLIENT_SECRET")
                and (os.getenv("DONNA_CTRADER_ACCESS_TOKEN") or _stored_tokens()))


# ------------------------------------------------------------------ token store
def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS ctrader_tokens (id INTEGER PRIMARY KEY CHECK (id = 1), access TEXT, "
              "refresh TEXT, issued TEXT)")
    return c


def _stored_tokens() -> tuple[str, str, str] | None:
    with _db() as c:
        row = c.execute("SELECT access, refresh, issued FROM ctrader_tokens WHERE id = 1").fetchone()
    return tuple(row) if row else None  # type: ignore[return-value]


def _save_tokens(access: str, refresh: str) -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO ctrader_tokens (id, access, refresh, issued) VALUES (1,?,?,?)",
                  (access, refresh, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def tokens() -> tuple[str, str, datetime | None]:
    """(access, refresh, issued) - the refreshed pair in the DB wins over the env file's original pair."""
    stored = _stored_tokens()
    if stored:
        return stored[0], stored[1], datetime.fromisoformat(stored[2])
    return (os.getenv("DONNA_CTRADER_ACCESS_TOKEN", "").strip(), os.getenv("DONNA_CTRADER_REFRESH_TOKEN", "").strip(),
            None)


# ------------------------------------------------------------------ protocol
class Session:
    """Request/response over one connection. `transport` has async send(str) and recv() -> str."""

    def __init__(self, transport: Any) -> None:
        self.t = transport
        self.pending: list[dict[str, Any]] = []   # events that arrived while waiting for something else

    async def _next(self) -> dict[str, Any]:
        if self.pending:
            return self.pending.pop(0)
        return json.loads(await asyncio.wait_for(self.t.recv(), TIMEOUT))

    async def ask(self, payload_type: int, payload: dict[str, Any], expect: int) -> dict[str, Any]:
        mid = uuid.uuid4().hex[:12]
        await self.t.send(json.dumps({"clientMsgId": mid, "payloadType": payload_type, "payload": payload}))
        held = []
        try:
            while True:
                msg = await self._next()
                pt = msg.get("payloadType")
                if pt == HEARTBEAT:
                    continue
                if pt == ERROR_RES and msg.get("clientMsgId") in (mid, None):
                    p = msg.get("payload", {})
                    code = p.get("errorCode", "")
                    if code in TOKEN_ERRORS:
                        raise TokenInvalid(code)
                    raise FeedError(f"{code}: {p.get('description', '')}".strip(": "))
                if pt == expect:
                    return msg.get("payload", {})
                held.append(msg)
        finally:
            self.pending = held + self.pending

    async def spots(self, account: int, symbol_ids: list[int]) -> dict[int, tuple[float | None, float | None]]:
        """Latest bid/ask per symbol. The first spot event after subscribing carries the current price."""
        if not symbol_ids:
            return {}
        await self.ask(SUBSCRIBE_SPOTS_REQ, {"ctidTraderAccountId": account, "symbolId": symbol_ids}, SUBSCRIBE_SPOTS_RES)
        out: dict[int, tuple[float | None, float | None]] = {}
        deadline = asyncio.get_running_loop().time() + TIMEOUT
        held = []
        while len([s for s in out.values() if None not in s]) < len(symbol_ids):
            if asyncio.get_running_loop().time() > deadline:
                break
            try:
                msg = await self._next()
            except asyncio.TimeoutError:
                break
            if msg.get("payloadType") != SPOT_EVENT:
                if msg.get("payloadType") != HEARTBEAT:
                    held.append(msg)
                continue
            p = msg["payload"]
            sid = int(p["symbolId"])
            bid, ask = out.get(sid, (None, None))
            out[sid] = (p["bid"] / PRICE_SCALE if p.get("bid") else bid, p["ask"] / PRICE_SCALE if p.get("ask") else ask)
        self.pending = held + self.pending
        try:
            await self.t.send(json.dumps({"payloadType": UNSUBSCRIBE_SPOTS_REQ,
                                          "payload": {"ctidTraderAccountId": account, "symbolId": symbol_ids}}))
        except Exception:  # unsubscribing is courtesy; the connection closes right after anyway
            pass
        return out


def _side(v: Any) -> str:
    return "buy" if v in (1, "BUY", "1") else "sell"


def _is_view_scope(v: Any) -> bool:
    return v in (0, "SCOPE_VIEW", "0", None)   # absent = proto default SCOPE_VIEW


def _rate(gross_pnl: float, side: str, units: float, entry: float, close: float, symbol: str) -> float | None:
    """Quote->deposit currency rate. Exact from cTrader's own P&L when the price has moved; else from the pair name."""
    move = (close - entry) * (1 if side == "buy" else -1) * units
    if abs(move) > 1e-9 and gross_pnl:
        r = gross_pnl / move
        if r > 0:
            return r
    from jarvis.agents.trading import quote_to_account

    return quote_to_account(symbol.replace("/", ""), close)


async def snapshot(transport: Any, login: str, access: str, refresh: str, client_id: str, client_secret: str,
                   refresh_due: bool = False) -> dict[str, Any]:
    s = Session(transport)
    await s.ask(APP_AUTH_REQ, {"clientId": client_id, "clientSecret": client_secret}, APP_AUTH_RES)
    if refresh_due and refresh:
        r = await s.ask(REFRESH_TOKEN_REQ, {"refreshToken": refresh}, REFRESH_TOKEN_RES)
        access, refresh = r["accessToken"], r["refreshToken"]
        _save_tokens(access, refresh)
        audit.log("ctrader_feed", "token_refreshed", {})
    accts = await s.ask(ACCOUNTS_BY_TOKEN_REQ, {"accessToken": access}, ACCOUNTS_BY_TOKEN_RES)
    if not _is_view_scope(accts.get("permissionScope")):
        raise FeedError("this token has TRADING permission - DONNA only accepts a read-only (account info) token. "
                        "Issue a new token with the account-info scope and replace it in the env file.")
    match = [a for a in accts.get("ctidTraderAccount", []) if str(a.get("traderLogin")) == str(login)]
    if not match:
        found = ", ".join(str(a.get("traderLogin")) for a in accts.get("ctidTraderAccount", [])) or "none"
        raise FeedError(f"account {login} is not granted to this token (granted: {found})")
    acct = int(match[0]["ctidTraderAccountId"])
    await s.ask(ACCOUNT_AUTH_REQ, {"ctidTraderAccountId": acct, "accessToken": access}, ACCOUNT_AUTH_RES)
    trader = (await s.ask(TRADER_REQ, {"ctidTraderAccountId": acct}, TRADER_RES))["trader"]
    balance = trader["balance"] / 10 ** trader.get("moneyDigits", 2)
    rec = await s.ask(RECONCILE_REQ, {"ctidTraderAccountId": acct}, RECONCILE_RES)
    positions = rec.get("position", [])
    pnl_res = await s.ask(UNREALIZED_PNL_REQ, {"ctidTraderAccountId": acct}, UNREALIZED_PNL_RES) if positions else {}
    pnl_scale = 10 ** pnl_res.get("moneyDigits", 2)
    pnl = {int(p["positionId"]): (p["grossUnrealizedPnL"] / pnl_scale, p["netUnrealizedPnL"] / pnl_scale)
           for p in pnl_res.get("positionUnrealizedPnL", [])}
    names: dict[int, str] = {}
    if positions:
        lst = await s.ask(SYMBOLS_LIST_REQ, {"ctidTraderAccountId": acct}, SYMBOLS_LIST_RES)
        names = {int(x["symbolId"]): x.get("symbolName", str(x["symbolId"])) for x in lst.get("symbol", [])}
    ids = sorted({int(p["tradeData"]["symbolId"]) for p in positions})
    px = await s.spots(acct, ids)
    out, unpriced = [], []
    for p in positions:
        td = p["tradeData"]
        sid, side = int(td["symbolId"]), _side(td["tradeSide"])
        units, entry = td["volume"] / 100, float(p.get("price") or 0)
        bid, ask = px.get(sid, (None, None))
        close = bid if side == "buy" else ask           # the price the position would close at
        symbol = names.get(sid, str(sid))
        gross, _ = pnl.get(int(p["positionId"]), (0.0, 0.0))
        if close is None or not entry:
            unpriced.append(symbol)
            continue
        out.append({"symbol": symbol, "side": side, "units": units, "entry": entry,
                    "stop": p.get("stopLoss") or None, "price": close,
                    "quote_to_account": _rate(gross, side, units, entry, close, symbol)})
    equity = balance + sum(n for _, n in pnl.values())
    return {"login": login, "balance": balance, "equity": equity, "positions": out, "unpriced": unpriced,
            "token_refreshed": refresh_due}


async def _run(login: str) -> dict[str, Any]:
    import aiohttp

    access, refresh, issued = tokens()
    if not access:
        raise FeedError("DONNA_CTRADER_ACCESS_TOKEN not set")
    # tokens from the env file have no known issue date: they are refreshed when cTrader rejects them
    due = issued is not None and datetime.now(timezone.utc) - issued > timedelta(days=REFRESH_DAYS)
    host = HOSTS[os.getenv("DONNA_CTRADER_LIVE", "1").strip() not in ("0", "false", "no")]
    cid, secret = os.environ["DONNA_CTRADER_CLIENT_ID"].strip(), os.environ["DONNA_CTRADER_CLIENT_SECRET"].strip()

    class WS:
        def __init__(self, ws: Any) -> None:
            self.ws = ws

        async def send(self, text: str) -> None:
            await self.ws.send_str(text)

        async def recv(self) -> str:
            m = await self.ws.receive()
            if m.type != aiohttp.WSMsgType.TEXT:
                raise FeedError(f"connection closed by cTrader ({m.type.name})")
            return m.data

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT * 4)) as http:
        for attempt in (1, 2):
            async with http.ws_connect(host, heartbeat=10) as ws:
                try:
                    return await snapshot(WS(ws), login, access, refresh, cid, secret, refresh_due=due)
                except TokenInvalid:
                    if attempt == 2 or not refresh:
                        raise FeedError("cTrader says the access token is invalid or expired and refreshing failed - "
                                        "issue a new read-only token (see DONNA.md)")
                    due = True                         # second pass refreshes first
    raise FeedError("unreachable")


def poll() -> dict[str, Any]:
    """One snapshot into the risk view. Never raises; returns {'error': ...} instead."""
    if not configured():
        return {"error": "cTrader feed not configured (DONNA_CTRADER_CLIENT_ID / _SECRET / _ACCESS_TOKEN)"}
    login = os.getenv("DONNA_CTRADER_LOGIN", os.getenv("JARVIS_PERSONAL_ACCOUNT", "")).strip()
    if not login:
        return {"error": "DONNA_CTRADER_LOGIN not set - the account number shown in cTrader"}
    try:
        snap = asyncio.run(_run(login))
    except FeedError as e:
        return {"error": str(e)}
    except Exception as e:  # network, TLS, timeouts: report, don't crash the scheduler
        return {"error": f"cTrader unreachable ({type(e).__name__})"}
    from jarvis.agents import rhythm

    rhythm.store_positions(snap["positions"], snap["equity"], None, source=f"ctrader:{login}")
    audit.log("ctrader_feed", "snapshot", {"positions": len(snap["positions"]), "unpriced": snap["unpriced"]})
    return {k: v for k, v in snap.items() if k != "positions"} | {"positions": len(snap["positions"])}


if __name__ == "__main__":
    # python -m jarvis.adapters.ctrader_feed check   -> one snapshot, prints the risk view
    if sys.argv[1:] == ["check"]:
        r = poll()
        if "error" in r:
            print("ERROR", r["error"])
            sys.exit(1)
        from jarvis.agents import rhythm

        print(f"Connected to {r['login']}: balance {r['balance']:,.2f}, equity {r['equity']:,.2f}, "
              f"{r['positions']} open position(s)" + (f", no price for {r['unpriced']}" if r["unpriced"] else ""))
        print("\n".join(rhythm.risk_lines()))
    else:
        print(__doc__)
