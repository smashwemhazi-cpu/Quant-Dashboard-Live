"""Personal trading journal and cBot monitor.

Inputs arrive through the same inbox as the back-office exports (see backoffice.ingest):
  cTrader account statement   cT_<account>_<date>.xlsx  (sheet "Records": Symbol, Opening direction,
                              Closing time, Entry price, Closing price, Closing Quantity, Net $, Balance $)
  cTrader cBot log            <date>_-_<BotName>_Instance_<id>.log  (cTrader Automate log export)

Statement times are in the cTrader client's display timezone; set JARVIS_CTRADER_UTC_OFFSET
(hours, e.g. 4 for UTC+4) so session analysis is in UTC. cBot log times are UTC.
Personal results go to JARVIS_PERSONAL_TARGETS only, never to the company report channel.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

MAX_LOSS_PCT = float(os.getenv("JARVIS_PERSONAL_MAX_LOSS_PCT", "3"))      # single-trade loss vs balance
MAX_TRADES_DAY = int(os.getenv("JARVIS_PERSONAL_MAX_TRADES_DAY", "20"))
REENTRY_MIN = 5          # a new trade this soon after a loss counts as a quick re-entry
BOT_STALE_HOURS = float(os.getenv("JARVIS_BOT_STALE_HOURS", "24"))
SESSIONS = [(0, 7, "Asia"), (7, 12, "London"), (12, 16, "London/NY overlap"), (16, 21, "New York"), (21, 24, "Late")]
PERSONAL_TARGETS = [t for t in os.getenv("JARVIS_PERSONAL_TARGETS", "").split(",") if t]


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS pt_trades (account TEXT, closed_utc TEXT, symbol TEXT, side TEXT, entry REAL,
            exit REAL, qty REAL, net REAL, balance REAL, PRIMARY KEY (account, closed_utc, symbol, entry, exit));
        CREATE TABLE IF NOT EXISTS bot_events (instance TEXT, bot TEXT, ts TEXT, kind TEXT, msg TEXT,
            net REAL, equity REAL, PRIMARY KEY (instance, ts, msg));
    """)
    return c


# ------------------------------------------------------------------ statements
def is_statement(d: pd.DataFrame) -> bool:
    cols = {c.lower() for c in d.columns}
    return {"symbol", "opening direction", "closing time"} <= cols and any(c.startswith("net") for c in cols)


def store_statement(d: pd.DataFrame, path: Path) -> tuple[str, int]:
    m = re.search(r"cT_(\d+)_", path.name)
    account = m.group(1) if m else os.getenv("JARVIS_PERSONAL_ACCOUNT", "personal")
    col = {k.lower(): k for k in d.columns}
    pick = lambda prefix: col[next(k for k in col if k.startswith(prefix))]  # noqa: E731
    offset = pd.Timedelta(hours=float(os.getenv("JARVIS_CTRADER_UTC_OFFSET", "0")))
    t = pd.to_datetime(d[col["closing time"]].astype(str), format="%d/%m/%Y %H:%M:%S.%f", errors="coerce")
    t = t.fillna(pd.to_datetime(d[col["closing time"]].astype(str), dayfirst=True, errors="coerce")) - offset
    num = lambda k: pd.to_numeric(d[pick(k)].astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce")  # noqa: E731
    rows = pd.DataFrame({"account": account, "closed_utc": t.dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
                         "symbol": d[col["symbol"]].astype(str), "side": d[col["opening direction"]].astype(str),
                         "entry": num("entry price"), "exit": num("closing price"), "qty": num("closing quantity"),
                         "net": num("net"), "balance": num("balance")}).dropna(subset=["closed_utc", "net"])
    with _db() as c:
        before = c.execute("SELECT COUNT(*) FROM pt_trades").fetchone()[0]
        c.executemany("INSERT OR IGNORE INTO pt_trades VALUES (?,?,?,?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
        added = c.execute("SELECT COUNT(*) FROM pt_trades").fetchone()[0] - before
    return account, added


def _session(h: int) -> str:
    return next(name for lo, hi, name in SESSIONS if lo <= h < hi)


def journal(account: str = "", days: int = 30) -> dict[str, Any]:
    with _db() as c:
        if not account:
            row = c.execute("SELECT account FROM pt_trades ORDER BY closed_utc DESC LIMIT 1").fetchone()
            account = os.getenv("JARVIS_PERSONAL_ACCOUNT") or (row[0] if row else "")
        d = pd.read_sql_query("SELECT * FROM pt_trades WHERE account = ? ORDER BY closed_utc", c, params=(account,))
    if d.empty:
        return {"error": "NO DATA - no cTrader account statement ingested"}
    d["t"] = pd.to_datetime(d.closed_utc)
    d = d[d.t >= d.t.max() - pd.Timedelta(days=days)].reset_index(drop=True)
    n = d.net
    wins, losses = n[n > 0], n[n < 0]
    bal_before = d.balance - n
    d["loss_pct"] = np.where(n < 0, -n / bal_before.where(bal_before > 0) * 100, 0.0)
    big = d[d.loss_pct > MAX_LOSS_PCT].sort_values("net")
    # balance moves the trades don't explain are deposits / withdrawals
    flows = (d.balance - d.balance.shift() - n).iloc[1:]
    curve = n.cumsum()
    dd = curve - curve.cummax()
    d["session"] = d.t.dt.hour.map(_session)
    gap = d.t.diff().dt.total_seconds() / 60
    quick = d[(n.shift() < 0) & (gap < REENTRY_MIN)]
    per_day = d.groupby(d.t.dt.date).net.agg(["count", "sum"])
    last_day = per_day.index.max()
    grp = lambda k: (d.groupby(k).net.agg(trades="count", net="sum", win_rate=lambda s: (s > 0).mean() * 100)  # noqa: E731
                     .sort_values("net").reset_index().to_dict("records"))
    return {
        "account": account, "from": d.t.min().date().isoformat(), "to": d.t.max().date().isoformat(),
        "trades": len(d), "net": n.sum(), "win_rate": (n > 0).mean() * 100,
        "avg_win": wins.mean() if len(wins) else 0.0, "avg_loss": losses.mean() if len(losses) else 0.0,
        "profit_factor": wins.sum() / -losses.sum() if len(losses) else None,
        "expectancy": n.mean(), "max_drawdown": dd.min(),
        "start_balance": float(bal_before.iloc[0]), "end_balance": float(d.balance.iloc[-1]),
        "cash_flows": float(flows[flows.abs() > 0.01].sum()),
        "big_losses": big[["t", "symbol", "side", "net", "loss_pct"]].assign(t=big.t.dt.strftime("%d/%m %H:%M"))
                         .to_dict("records"),
        "net_without_big_losses": n.sum() - big.net.sum(),
        "by_symbol": grp("symbol"), "by_session": grp("session"),
        "quick_reentries": {"trades": len(quick), "net": quick.net.sum()},
        "overtrading_days": per_day[per_day["count"] > MAX_TRADES_DAY].reset_index()
                            .rename(columns={"t": "day"}).astype({"day": str}).to_dict("records"),
        "last_day": {"day": str(last_day), "trades": int(per_day.loc[last_day, "count"]),
                     "net": float(per_day.loc[last_day, "sum"])},
    }


# ------------------------------------------------------------------ cBot logs
LOG_LINE = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}\.\d+)\s+(?:(\w+)\s*\|\s*)?(.*)$")
STARTED = re.compile(r"CBot instance \[([^,\]]+),\s*([^,\]]+),\s*([^\]]+)\] (started|stopped|crashed)", re.I)


def is_bot_log(path: Path) -> bool:
    return path.suffix.lower() in (".log", ".txt") and "instance" in path.name.lower()


def store_bot_log(path: Path) -> tuple[str, int]:
    m = re.search(r"_-_(.+?)_Instance_(\d+)", path.name)
    bot, instance = (m.group(1), m.group(2)) if m else (path.stem, path.stem)
    rows = []
    for line in path.read_text(errors="ignore").splitlines():
        lm = LOG_LINE.match(line.strip())
        if not lm:
            continue
        ts = datetime.strptime(lm.group(1), "%d/%m/%Y %H:%M:%S.%f").isoformat(sep=" ")
        msg, net, eq = lm.group(3).strip(), None, None
        sm = STARTED.search(msg)
        if sm:
            kind = sm.group(4).lower()
        elif msg.startswith("CLOSED"):
            kind = "closed"
            net = float(re.search(r"net=(-?[\d.]+)", msg).group(1)) if "net=" in msg else None
            eq = float(re.search(r"equity=(-?[\d.]+)", msg).group(1)) if "equity=" in msg else None
        elif msg.startswith("FILLED"):
            kind = "filled"
        elif re.search(r"\bFAILED\b|error|exception|crash", msg, re.I):
            kind = "failure"
        elif msg.startswith("→ Placing") and "SUCCEEDED" in msg:
            kind = "placed"
        elif msg.startswith("→ Cancelling") and "SUCCEEDED" in msg:
            kind = "cancelled"
        elif msg.startswith(("BUILD", "CONFIG", "Started on", "FEASIBILITY", "VERDICT")):
            kind = "config"
        else:
            continue
        rows.append((instance, bot, ts, kind, msg[:500], net, eq))
    with _db() as c:
        before = c.execute("SELECT COUNT(*) FROM bot_events").fetchone()[0]
        c.executemany("INSERT OR IGNORE INTO bot_events VALUES (?,?,?,?,?,?,?)", rows)
        added = c.execute("SELECT COUNT(*) FROM bot_events").fetchone()[0] - before
    return f"{bot} #{instance}", added


def bots(now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with _db() as c:
        ev = pd.read_sql_query("SELECT * FROM bot_events ORDER BY ts", c)
    out = []
    for inst, e in ev.groupby("instance"):
        e = e.assign(t=pd.to_datetime(e.ts))
        last = e.t.max()
        runs = e[e.kind.isin(["started", "stopped", "crashed"])]
        state = runs.kind.iloc[-1] if len(runs) else "unknown"
        if state == "started":
            state = "running"
        sym = STARTED.search(runs.msg.iloc[-1]) if len(runs) else None
        day = e[e.t >= last - pd.Timedelta(hours=24)]
        closed = day[day.kind == "closed"]
        fails = day[day.kind == "failure"]
        build = e[e.msg.str.startswith("BUILD")].msg
        out.append({
            "bot": e.bot.iloc[0], "instance": inst, "state": state,
            "symbol": f"{sym.group(2)} {sym.group(3)}" if sym else "", "build": build.iloc[-1][6:] if len(build) else "",
            "last_event": last.strftime("%Y-%m-%d %H:%M"), "hours_since": (now - last.to_pydatetime()).total_seconds() / 3600,
            "placed": int((day.kind == "placed").sum()), "cancelled": int((day.kind == "cancelled").sum()),
            "fills": int((day.kind == "filled").sum()), "closed": len(closed), "net": float(closed.net.sum()),
            "equity": float(closed.equity.dropna().iloc[-1]) if closed.equity.notna().any() else None,
            "failures": len(fails), "last_failure": fails.msg.iloc[-1] if len(fails) else "",
        })
    return out


# ------------------------------------------------------------------ report
def _m(v: float) -> str:
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def report_lines() -> list[str]:
    L: list[str] = []
    j = journal()
    if "error" in j:
        L.append(f"PERSONAL TRADING: {j['error']}")
    else:
        pf = f"{j['profit_factor']:.2f}" if j["profit_factor"] is not None else "n/a"
        L.append(f"PERSONAL TRADING (#{j['account']}, {j['from']} to {j['to']} UTC):")
        L.append(f" {j['trades']} trades | net {_m(j['net'])} | win rate {j['win_rate']:.0f}% | "
                 f"avg win {_m(j['avg_win'])} vs avg loss {_m(j['avg_loss'])} | profit factor {pf}")
        L.append(f" Last day {j['last_day']['day']}: {j['last_day']['trades']} trades, {_m(j['last_day']['net'])} "
                 f"| max drawdown {_m(j['max_drawdown'])}"
                 + (f" | deposits/withdrawals {_m(j['cash_flows'])}" if j["cash_flows"] else ""))
        if j["big_losses"]:
            L.append(f" !! {len(j['big_losses'])} trades lost more than {MAX_LOSS_PCT:g}% of the balance:")
            L += [f"    {x['t']} {x['symbol']} {x['side']} {_m(x['net'])} ({x['loss_pct']:.1f}% of balance)"
                  for x in j["big_losses"][:5]]
            L.append(f"    Without them the period would be {_m(j['net_without_big_losses'])} - "
                     "the result is decided by stop discipline, not by the win rate.")
        if j["avg_loss"] and j["avg_win"] and abs(j["avg_loss"]) > 1.5 * j["avg_win"]:
            L.append(f" !! Average loss is {abs(j['avg_loss']) / j['avg_win']:.1f}x the average win - "
                     f"a {j['win_rate']:.0f}% win rate cannot carry that.")
        s = [x for x in j["by_session"] if x["trades"] >= 5]
        if s:
            L.append(f" Sessions (UTC): worst {s[0]['session']} {_m(s[0]['net'])} over {s[0]['trades']} trades, "
                     f"best {s[-1]['session']} {_m(s[-1]['net'])} over {s[-1]['trades']}")
        L.append(" Symbols: " + ", ".join(f"{x['symbol']} {_m(x['net'])} ({x['trades']})" for x in j["by_symbol"]))
        if j["quick_reentries"]["trades"]:
            L.append(f" Re-entries within {REENTRY_MIN} min of a loss: {j['quick_reentries']['trades']} trades, "
                     f"{_m(j['quick_reentries']['net'])}")
        if j["overtrading_days"]:
            L.append(f" Days over {MAX_TRADES_DAY} trades: " + ", ".join(
                f"{x['day']} ({x['count']}, {_m(x['sum'])})" for x in j["overtrading_days"]))
    L.append("")
    b = bots()
    if not b:
        L.append("BOTS: NO DATA - no cBot log uploaded")
    else:
        L.append("BOTS (from uploaded cTrader logs):")
        for x in b:
            stale = "  !! no log for {:.0f}h - upload a fresh log or check the bot".format(x["hours_since"]) \
                if x["hours_since"] > BOT_STALE_HOURS else ""
            L.append(f" {x['bot']} #{x['instance']} {x['symbol']} build {x['build'] or '?'} - {x['state']}, "
                     f"last event {x['last_event']} UTC{stale}")
            L.append(f"  Last 24h of log: {x['placed']} orders placed, {x['cancelled']} cancelled, {x['fills']} filled, "
                     f"{x['closed']} closed {_m(x['net'])}" + (f" | equity {_m(x['equity'])}" if x["equity"] else ""))
            if x["failures"]:
                L.append(f"  !! {x['failures']} failures - last: {x['last_failure'][:160]}")
            if x["placed"] >= 10 and x["fills"] <= x["placed"] * 0.05:
                L.append(f"  Fill rate {x['fills']}/{x['placed']} orders - the ladder is mostly re-quoting, "
                         "check the limit offset vs typical pullback")
    return L


@register
class PersonalAgent(Agent):
    name = "personal"
    description = "Personal trading journal from cTrader statements, and cBot health from uploaded logs."

    @tool()
    def trading_journal(self, account: str = "", days: int = 30) -> dict[str, Any]:
        """Personal account stats: win rate, payoff, profit factor, drawdown, oversized losses vs balance,
        by symbol / UTC session, quick re-entries after losses and over-trading days."""
        return journal(account, days)

    @tool()
    def bot_status(self) -> list[dict[str, Any]]:
        """Each cBot instance: running/stopped, last event, orders placed/cancelled/filled, closed P&L, failures."""
        return bots()
