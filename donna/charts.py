"""DONNA Charts: SH's private charts dashboard, opened through the same SSH tunnel as the console.

Shows, from DONNA's own records (no Claude credit needed, nothing is sent anywhere):
  - the account: equity curve from the read-only cTrader feed, drawdown from the peak, today's daily-loss line
  - risk now: open risk vs the limit, room before the daily-loss stop, positions without a hard stop
  - closed trades: cumulative P&L, P&L per day and per symbol, win rate, profit factor, expectancy
  - my edge: win rate vs break-even, behaviour after losses, results by symbol / session / size, rule replay
  - markets: daily closes of the report watchlist with 20/50-day averages, moves, volatility and trend,
    with the entry and stop of any open position drawn on its chart
  - compliance: PAD flags and the trading KPIs

Read-only by design: every function here only reads. It never places, changes or closes a trade.
Same protection as the console (donna/console.py): pages answer only through the tunnel, the API also needs
the X-Donna: console header. Anything else gets a 404.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from donna.console import _section, console_api, tunnel_only

PAGE = Path(__file__).with_name("charts.html")
EQUITY_POINTS = 600                                                   # enough for a smooth 14-day curve
TRADE_DAYS = int(os.getenv("DONNA_CHARTS_TRADE_DAYS", "90"))
MARKET_BARS = int(os.getenv("DONNA_CHARTS_MARKET_BARS", "180"))       # about 9 months of daily bars
MARKET_TTL = float(os.getenv("DONNA_CHARTS_MARKET_TTL_MIN", "60")) * 60  # Yahoo is asked at most once an hour

router = APIRouter()


@router.get("/charts", response_class=HTMLResponse, dependencies=[Depends(tunnel_only)])
def charts_page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={
        "Cache-Control": "no-store", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                   "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"})


# ------------------------------------------------------------------ account
def _snapshots(days: int = 14) -> list[dict[str, Any]]:
    from jarvis.agents import rhythm

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with rhythm._db() as c:
        return [dict(r) for r in c.execute("SELECT ts, equity, start_of_day_equity, positions FROM position_snapshots "
                                           "WHERE ts >= ? ORDER BY id", (since,))]


def thin(rows: list[Any], n: int) -> list[Any]:
    """At most n evenly spaced rows, always keeping the first and the last."""
    if len(rows) <= n:
        return rows
    step = (len(rows) - 1) / (n - 1)
    return [rows[round(i * step)] for i in range(n)]


def drawdown(values: list[float]) -> tuple[list[float], float]:
    """Drawdown from the running peak, in % of the peak, and the worst one."""
    out, peak = [], -math.inf
    for v in values:
        peak = max(peak, v)
        out.append((v - peak) / peak * 100 if peak > 0 else 0.0)
    return out, min(out, default=0.0)


def account() -> dict[str, Any]:
    from jarvis.core.config import settings

    rows = _snapshots()
    if not rows:
        return {"error": "NO DATA - live positions not connected (cTrader: vertex1-connect ctrader)"}
    pts = thin(rows, EQUITY_POINTS)
    eq = [r["equity"] for r in pts]
    dd, worst = drawdown(eq)
    last = rows[-1]
    sod = last["start_of_day_equity"]
    stop_line = sod - sod * settings.max_daily_loss_pct / 100 if sod else None
    return {"t": [r["ts"] for r in pts], "equity": eq, "drawdown": dd, "max_drawdown_pct": worst,
            "start": rows[0]["equity"], "now": last["equity"], "start_of_day": sod, "daily_stop_level": stop_line,
            "change_pct": (last["equity"] / rows[0]["equity"] - 1) * 100 if rows[0]["equity"] else None}


def open_levels() -> dict[str, list[dict[str, Any]]]:
    """Entry and stop of each open position from the latest snapshot, keyed by watchlist symbol."""
    from jarvis.agents import rhythm

    with rhythm._db() as c:
        row = c.execute("SELECT positions FROM position_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    out: dict[str, list[dict[str, Any]]] = {}
    for p in json.loads(row["positions"]) if row else []:
        key = _watch_key(p.get("symbol", ""))
        if key:
            out.setdefault(key, []).append({"side": p.get("side"), "units": p.get("units"),
                                            "entry": p.get("entry"), "stop": p.get("stop")})
    return out


def _watch_key(symbol: str) -> str | None:
    """cTrader symbol -> watchlist key (XAUUSD, US30, NAS100 ...); None when it isn't on the watchlist."""
    from jarvis.adapters.market import WATCHLIST

    s = "".join(ch for ch in symbol.upper() if ch.isalnum())
    aliases = {"USTEC": "NAS100", "NAS100": "NAS100", "US100": "NAS100", "NDX": "NAS100", "DJ30": "US30",
               "US30": "US30", "WS30": "US30", "DE40": "GER40", "GER40": "GER40", "DAX40": "GER40",
               "USOIL": "WTI", "XTIUSD": "WTI", "WTI": "WTI", "BTCUSD": "BTCUSD", "XBTUSD": "BTCUSD"}
    if s in aliases:
        return aliases[s]
    return next((i.key for i in WATCHLIST if s.startswith(i.key)), None)


def risk() -> dict[str, Any]:
    from jarvis.agents import rhythm

    r = rhythm.position_risk()
    if "error" in r:
        return r
    return {k: r.get(k) for k in ("asof", "stale", "age_hours", "equity", "open_risk", "open_risk_pct",
                                  "open_risk_limit_pct", "floating", "no_stop", "unconverted", "day_pnl",
                                  "daily_loss_room", "daily_loss_limit")} | {
        "positions": [{k: p[k] for k in ("symbol", "side", "units", "floating", "risk_at_stop", "no_stop",
                                         "stop_in_profit", "converted")} for p in r["positions"]]}


# ------------------------------------------------------------------ closed trades
def trades(days: int = TRADE_DAYS) -> dict[str, Any]:
    from jarvis.agents import personal

    j = personal.journal(days=days)
    if "error" in j:
        return j
    with personal._db() as c:
        rows = c.execute("SELECT closed_utc, symbol, net FROM pt_trades WHERE account = ? AND closed_utc >= ? "
                         "ORDER BY closed_utc", (j["account"], j["from"])).fetchall()
    cum, total, per_day = [], 0.0, {}
    for closed, _sym, net in rows:
        total += net
        cum.append(round(total, 2))
        per_day[closed[:10]] = per_day.get(closed[:10], 0.0) + net
    stats = {k: j[k] for k in ("account", "from", "to", "trades", "net", "win_rate", "avg_win", "avg_loss",
                               "profit_factor", "expectancy", "max_drawdown")}
    return {**stats, "t": [r[0][:16] for r in rows], "cumulative": cum,
            "days": sorted(per_day), "day_net": [round(per_day[d], 2) for d in sorted(per_day)],
            "by_symbol": [{"symbol": s["symbol"], "net": round(s["net"], 2), "trades": s["trades"],
                           "win_rate": s["win_rate"]} for s in j["by_symbol"]],
            "big_losses": len(j["big_losses"])}


# ------------------------------------------------------------------ my edge
def edge(days: int = TRADE_DAYS) -> dict[str, Any]:
    from jarvis.agents import edge as e

    return e.report(days)


# ------------------------------------------------------------------ compliance
def compliance() -> dict[str, Any]:
    from jarvis.agents import kpi, pad

    def pad_part() -> dict[str, Any]:
        f = pad.flags(30)
        by: dict[str, int] = {}
        for x in f:
            by[x["check_name"]] = by.get(x["check_name"], 0) + 1
        return {"flags_30d": len(f), "by_check": by,
                "latest": [{"when": x["first_seen_utc"][:16], "symbol": x["symbol"], "check": x["check_name"],
                            "detail": (x["detail"] or "")[:160]} for x in f[:8]]}

    def kpi_part() -> dict[str, Any]:
        rows = kpi.scorecard()
        mine = [r for r in rows if r["dept"] == "trading"]
        return {"counts": kpi.counts(rows),
                "trading": [{"name": r["name"], "status": r["status"], "value": r["value"], "unit": r["unit"],
                             "target": r["target"], "note": r["note"]}
                            for r in sorted(mine, key=lambda r: kpi.ORDER[r["status"]])]}

    return {"pad": _section(pad_part), "kpi": _section(kpi_part)}


@router.get("/api/charts/data", dependencies=[Depends(console_api)])
def charts_data() -> dict[str, Any]:
    from jarvis.agents import assistant

    return {"now": datetime.now(assistant.LOCAL_TZ).strftime("%a %d %b %Y %H:%M"),
            "account": _section(account), "risk": _section(risk), "trades": _section(trades),
            "edge": _section(edge), "compliance": _section(compliance)}


# ------------------------------------------------------------------ markets
def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= n:
            run -= values[i - n]
        out.append(run / n if i >= n - 1 else None)
    return out


def _pct(a: float, b: float) -> float | None:
    return (a / b - 1) * 100 if b else None


def market_card(key: str, label: str, dates: list[str], closes: list[float], source: str, decimals: int) -> dict[str, Any]:
    s20, s50 = sma(closes, 20), sma(closes, 50)
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - 20), len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    vol = None
    if len(rets) >= 10:
        m = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    last = closes[-1]
    trend = "no data"
    if s50[-1] is not None and s20[-1] is not None:
        trend = ("up" if last > s50[-1] and s20[-1] > s50[-1] else
                 "down" if last < s50[-1] and s20[-1] < s50[-1] else "mixed")
    year = closes[-252:]
    keep = slice(-MARKET_BARS, None)
    return {"key": key, "label": label, "source": source, "decimals": decimals, "last": last, "asof": dates[-1],
            "chg_1d": _pct(last, closes[-2]) if len(closes) > 1 else None,
            "chg_5d": _pct(last, closes[-6]) if len(closes) > 5 else None,
            "chg_20d": _pct(last, closes[-21]) if len(closes) > 20 else None,
            "vol_20d": vol, "trend": trend, "from_high": _pct(last, max(year)), "from_low": _pct(last, min(year)),
            "t": dates[keep], "close": closes[keep], "sma20": s20[keep], "sma50": s50[keep]}


_cache: dict[str, Any] = {"at": 0.0, "cards": None}
_lock = threading.Lock()


def _load_markets() -> list[dict[str, Any]]:
    from jarvis.adapters.market import WATCHLIST, daily_bars

    cards = []
    for inst in WATCHLIST:
        try:
            df, src = daily_bars(inst, period="2y")
        except Exception as e:  # noqa: BLE001 - one bad symbol must not blank the others
            df, src = None, f"unavailable: {type(e).__name__}"
        if df is None or df.empty or len(df) < 2:
            cards.append({"key": inst.key, "label": inst.label, "error": src})
            continue
        cards.append(market_card(inst.key, inst.label, [d.strftime("%Y-%m-%d") for d in df.index],
                                 [round(float(v), inst.decimals + 2) for v in df["close"]], src, inst.decimals))
    return cards


def markets(force: bool = False) -> dict[str, Any]:
    with _lock:  # two open tabs must not both ask Yahoo
        age = time.time() - _cache["at"]
        if _cache["cards"] is None or age > MARKET_TTL or (force and age > 300):  # a refresh at most every 5 min
            _cache["cards"], _cache["at"] = _load_markets(), time.time()
        cards, at = _cache["cards"], _cache["at"]
    levels = _section(open_levels)
    levels = levels if isinstance(levels, dict) and "error" not in levels else {}
    return {"loaded": datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds"),
            "cards": [c | {"positions": levels.get(c["key"], [])} for c in cards]}


@router.get("/api/charts/markets", dependencies=[Depends(console_api)])
def charts_markets(refresh: bool = False) -> dict[str, Any]:
    return markets(force=refresh)
