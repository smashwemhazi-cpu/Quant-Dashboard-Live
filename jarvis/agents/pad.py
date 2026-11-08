"""PAD guardrails: read-only checks of the owner's own cTrader trades against the PAD Policy (v0.2, approved
7 Nov 2026), plus a monthly self-review record (PDF) for the owner's own file. Board and MLRO involvement was waived by
the owner on 7 Nov 2026: this monitors the owner's personal trades only.

Where the data comes from
  Every cTrader snapshot (jarvis/adapters/ctrader_feed.py, read-only token) calls record(): open positions
  (with their cTrader open time and stop-loss) and closed trades are kept in pad_positions, and the checks
  run straight away. Nothing here can place, change or close a trade - it only reads what the feed stored.

Checks (policy clause)                                 Defaults (env to change)
  Restricted List trade (s.5.1)                        !pad restrict <symbol>
  Per-trade clearance missing (s.4, s.5.2)             crypto, single-name shares, Watch List
  Closed in profit before the minimum hold (s.6.1)     48 h; single-name shares 14 working days
  Leverage above the client cap for the class (s.6)    1:200 FX/metals/indices, 1:100 commodities,
                                                       1:50 crypto, 1:20 shares; 1:100 above USD 50k equity
  No stop-loss / more than 2% of equity at risk (6.3)  DONNA_PAD_MAX_RISK_PCT, grace DONNA_PAD_STOP_GRACE_MIN
  More than 10 trades in a day (s.6.2)                 DONNA_PAD_MAX_TRADES_DAY
  Long and short in the same symbol at once (s.6.4)

Only positions opened on or after DONNA_PAD_EFFECTIVE (default 2026-11-07, the approval date) are checked.
Days are local days (JARVIS_LOCAL_TZ - set it to Indian/Mauritius on the server).

Discord
  !pad                                   today's PAD status and open flags
  !pad clear <symbol> <buy/sell> <lots> <reason>    log your own pre-clearance before you trade
  !pad list | !pad restrict <sym> [why] | !pad watch <sym> [why] | !pad unlist <sym>
  !pad class <sym> <forex|metal|index|commodity|crypto|share>   fix how a symbol is classed
  !pad flags [days] | !pad rules | !pad pack [YYYY-MM]  (self-review PDF)
Scheduler: job "pad_pack" sends last month's PDF to the personal Discord channel on day DONNA_PAD_PACK_DAY.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register

CLASSES = ("forex", "metal", "index", "commodity", "crypto", "share")
CLASS_WORDS = {"forex": "Forex", "metal": "Precious metals", "index": "Equity indices",
               "commodity": "Commodities (non-metal)", "crypto": "Cryptocurrencies", "share": "Single-name shares"}
CAPS = {"forex": 200, "metal": 200, "index": 200, "commodity": 100, "crypto": 50, "share": 20}  # T&Cs s.9.1
TIER_EQUITY, TIER_CAP = 50_000.0, 100                                                          # T&Cs s.9.2
HOLD_HOURS = float(os.getenv("DONNA_PAD_HOLD_HOURS", "48"))
HOLD_SHARE_WORKDAYS = int(os.getenv("DONNA_PAD_HOLD_SHARE_WORKDAYS", "14"))
PER_TRADE = {"crypto", "share"}
MAX_TRADES_DAY = int(os.getenv("DONNA_PAD_MAX_TRADES_DAY", "10"))
MAX_RISK_PCT = float(os.getenv("DONNA_PAD_MAX_RISK_PCT", "2"))
STOP_GRACE_MIN = float(os.getenv("DONNA_PAD_STOP_GRACE_MIN", "5"))
EFFECTIVE = os.getenv("DONNA_PAD_EFFECTIVE", "2026-11-07")
PACK_DAY = int(os.getenv("DONNA_PAD_PACK_DAY", "1"))
FIRM = os.getenv("DONNA_FIRM_NAME", "Vertex 1 Brokers")

CHECK_WORDS = {"restricted": "Restricted List trade (s.5.1)", "clearance": "No pre-clearance logged (s.4)",
               "holding": "Closed in profit before minimum hold (s.6.1)", "leverage": "Leverage above cap (s.6)",
               "no_stop": "No stop-loss (s.6.3)", "risk": "Risk above limit (s.6.3)",
               "activity": "Too many trades in a day (s.6.2)", "opposite": "Long and short at once (s.6.4)"}

FIAT = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "SGD", "HKD", "ZAR", "MXN",
        "TRY", "PLN", "HUF", "CZK", "CNH", "CNY", "ILS", "THB", "INR", "RUB", "KES", "NGN", "MUR"}
CRYPTO = re.compile(r"^(BTC|ETH|LTC|XRP|BCH|SOL|DOGE|ADA|DOT|LINK|BNB|AVAX|MATIC|POL|XLM|UNI|TRX|SHIB|TON|"
                    r"ATOM|XTZ|EOS|ETC|FIL|NEAR|APT|ARB|OP|PEPE|SUI|USDT|USDC)")
METAL = re.compile(r"^(XAU|XAG|XPT|XPD|GOLD|SILVER|PLATINUM|PALLADIUM)")
COMMODITY = re.compile(r"^(US?OIL|UKOIL|WTI|BRENT|XBR|XTI|XNG|NGAS|NATGAS|OIL|CL|COPPER|XCU|CORN|WHEAT|SOY|"
                       r"COFFEE|COCOA|SUGAR|COTTON|GASOLINE|HEATOIL)")
INDEX = re.compile(r"^(US\d{2,4}|SPX|SP500|NAS\d*|NDX|USTEC|DJ\d*|DOW|GER\d{2}|DE\d{2}|DAX|UK\d{3}|FTSE|JP\d{3}|"
                   r"JPN\d{3}|NIK|FRA\d{2}|EU\d{2}|ESTX|STOXX|AUS\d{3}|HK\d{2}|CHINA\d*|CN\d{2}|ESP\d{2}|ITA\d{2}|"
                   r"SWI\d{2}|NETH\d{2}|VIX|DXY|USDX|SA40|IND\d{2})")


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS pad_positions (login TEXT, position_id TEXT, symbol TEXT, side TEXT, units REAL,
            opened_utc TEXT, stop REAL, entry REAL, first_seen_utc TEXT, max_leverage REAL, max_risk_pct REAL,
            closed_utc TEXT, exit REAL, net REAL, PRIMARY KEY (login, position_id));
        CREATE TABLE IF NOT EXISTS pad_lists (symbol TEXT PRIMARY KEY, list TEXT, reason TEXT, added_utc TEXT);
        CREATE TABLE IF NOT EXISTS pad_clearances (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT, day TEXT,
            symbol TEXT, side TEXT, lots REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS pad_flags (login TEXT, position_id TEXT, symbol TEXT, check_name TEXT,
            detail TEXT, first_seen_utc TEXT, PRIMARY KEY (login, position_id, check_name));
        CREATE TABLE IF NOT EXISTS pad_class (symbol TEXT PRIMARY KEY, asset_class TEXT);
        CREATE TABLE IF NOT EXISTS pad_closes (login TEXT, position_id TEXT, closed_utc TEXT, exit REAL, net REAL,
            PRIMARY KEY (login, position_id, closed_utc));
    """)
    return c


# ------------------------------------------------------------------ helpers
def norm(symbol: str) -> str:
    """'EUR/USD', 'eurusd.r', 'XAUUSD+' -> 'EURUSD' / 'XAUUSD'; shares keep their exchange part ('AAPL.US')."""
    s = re.sub(r"[+#]+$", "", symbol.upper().replace("/", "").replace(" ", ""))
    return re.sub(r"\.(R|A|B|I|C|X|M|PRO|ECN|RAW|STD)$", "", s)


def asset_class(symbol: str, overrides: dict[str, str] | None = None) -> str:
    s = norm(symbol)
    if overrides is None:
        with _db() as c:
            overrides = {r["symbol"]: r["asset_class"] for r in c.execute("SELECT * FROM pad_class")}
    if s in overrides:
        return overrides[s]
    if CRYPTO.match(s):
        return "crypto"
    if METAL.match(s):
        return "metal"
    if len(s) == 6 and s[:3] in FIAT and s[3:] in FIAT:
        return "forex"
    if COMMODITY.match(s):
        return "commodity"
    if INDEX.match(s):
        return "index"
    return "share"   # anything unrecognised gets the strictest rules; fix with !pad class


def cap_for(cls: str, equity: float | None) -> int:
    cap = CAPS[cls]
    return min(cap, TIER_CAP) if equity and equity > TIER_EQUITY else cap


def _local_tz() -> Any:
    from jarvis.agents.mind import LOCAL_TZ

    return LOCAL_TZ


def _ts(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _dt(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _local_day(s: str) -> str:
    return _dt(s).astimezone(_local_tz()).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ recording (called by the cTrader feed)
def record(login: str, positions: list[dict[str, Any]], closed: list[dict[str, Any]], equity: float | None,
           now: datetime | None = None) -> dict[str, Any]:
    """Store what one cTrader snapshot showed, then run the checks. Returns the new flags."""
    now_s = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        for p in positions:
            pid = str(p.get("position_id") or "")
            if not pid:
                continue
            opened = _ts(p.get("opened_ms"))
            rate = p.get("quote_to_account") or 1.0
            lev = p["units"] * p["price"] * rate / equity if equity and equity > 0 else None
            risk = None
            if p.get("stop") is not None and equity and equity > 0:
                sign = 1 if p["side"] == "buy" else -1
                risk = max(0.0, sign * (p["entry"] - p["stop"]) * p["units"] * rate) / equity * 100
            c.execute("INSERT OR IGNORE INTO pad_positions (login, position_id, symbol, side, units, opened_utc, "
                      "entry, first_seen_utc) VALUES (?,?,?,?,?,?,?,?)",
                      (login, pid, p["symbol"], p["side"], p["units"], opened, p["entry"], now_s))
            c.execute("UPDATE pad_positions SET stop = ?, units = ?, opened_utc = COALESCE(opened_utc, ?), "
                      "max_leverage = MAX(COALESCE(max_leverage, 0), COALESCE(?, 0)), "
                      "max_risk_pct = CASE WHEN ? IS NULL THEN max_risk_pct "
                      "ELSE MAX(COALESCE(max_risk_pct, 0), ?) END WHERE login = ? AND position_id = ?",
                      (p.get("stop"), p["units"], opened, lev, risk, risk, login, pid))
        for t in closed:
            pid = str(t.get("position_id") or "")
            if not pid:
                continue
            c.execute("INSERT OR IGNORE INTO pad_positions (login, position_id, symbol, side, units, opened_utc, "
                      "entry, first_seen_utc) VALUES (?,?,?,?,?,?,?,?)",
                      (login, pid, t["symbol"], t["side"], t["qty"], t.get("opened_utc"), t["entry"], now_s))
            # the feed re-reads the last hour each run: a closing deal (or each part of a partial close) counts once
            c.execute("INSERT OR IGNORE INTO pad_closes VALUES (?,?,?,?,?)",
                      (login, pid, t["closed_utc"][:19], t["exit"], t["net"]))
            c.execute("UPDATE pad_positions SET closed_utc = (SELECT MAX(closed_utc) FROM pad_closes WHERE login = ? "
                      "AND position_id = ?), exit = ?, net = (SELECT SUM(net) FROM pad_closes WHERE login = ? AND "
                      "position_id = ?), opened_utc = COALESCE(opened_utc, ?) WHERE login = ? AND position_id = ?",
                      (login, pid, t["exit"], login, pid, t.get("opened_utc"), login, pid))
    open_ids = {str(p.get("position_id")) for p in positions if p.get("position_id")}
    return {"new_flags": evaluate(login, open_ids=open_ids, equity=equity, now=now)}


# ------------------------------------------------------------------ checks
def _clearances(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute("SELECT * FROM pad_clearances ORDER BY ts_utc").fetchall()


def evaluate(login: str, open_ids: set[str] | None = None, equity: float | None = None,
             now: datetime | None = None) -> list[dict[str, Any]]:
    """Run every check over positions opened since EFFECTIVE; store and return the flags not seen before."""
    now = now or datetime.now(timezone.utc)
    now_s = now.strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        rows = [r for r in c.execute("SELECT * FROM pad_positions WHERE login = ? AND opened_utc IS NOT NULL "
                                     "ORDER BY opened_utc", (login,))
                if _local_day(r["opened_utc"]) >= EFFECTIVE]
        lists = {r["symbol"]: r for r in c.execute("SELECT * FROM pad_lists")}
        overrides = {r["symbol"]: r["asset_class"] for r in c.execute("SELECT * FROM pad_class")}
        clear = _clearances(c)
        found: list[tuple[str, str, str, str]] = []   # position_id, symbol, check, detail
        for r in rows:
            sym, pid = norm(r["symbol"]), r["position_id"]
            cls = asset_class(sym, overrides)
            listed = lists.get(sym)
            if listed and listed["list"] == "restricted" and r["opened_utc"] >= listed["added_utc"]:
                found.append((pid, sym, "restricted", f"{sym} is on the Restricted List ({listed['reason'] or 'no reason'})"))
            watch = listed is not None and listed["list"] == "watch"
            if cls in PER_TRADE or watch:
                day = _local_day(r["opened_utc"])
                ok = any(norm(k["symbol"]) == sym and k["day"] == day and k["ts_utc"] <= r["opened_utc"]
                         and (not k["side"] or k["side"] == r["side"]) for k in clear)
                if not ok:
                    why = "Watch List" if watch else CLASS_WORDS[cls].lower()
                    found.append((pid, sym, "clearance", f"{r['side']} {sym} ({why}) opened {r['opened_utc'][:16]} UTC "
                                                         "with no !pad clear logged before it that day"))
            if r["closed_utc"] and (r["net"] or 0) > 0:
                o, cl = _dt(r["opened_utc"]), _dt(r["closed_utc"])
                if cls == "share":
                    held = int(np.busday_count(o.date(), cl.date()))
                    if held < HOLD_SHARE_WORKDAYS:
                        found.append((pid, sym, "holding", f"{sym} closed in profit after {held} working day(s); "
                                                           f"minimum {HOLD_SHARE_WORKDAYS}"))
                else:
                    hours = (cl - o).total_seconds() / 3600
                    if hours < HOLD_HOURS:
                        found.append((pid, sym, "holding", f"{sym} closed in profit after {hours:.1f} h; "
                                                           f"minimum {HOLD_HOURS:g} h"))
            cap = cap_for(cls, equity)
            if r["max_leverage"] and r["max_leverage"] > cap:
                found.append((pid, sym, "leverage", f"{sym} at about 1:{r['max_leverage']:.0f} of equity; cap 1:{cap} "
                                                    f"for {CLASS_WORDS[cls].lower()}"))
            is_open = open_ids is not None and pid in open_ids
            age_min = (now - _dt(r["opened_utc"])).total_seconds() / 60
            if is_open and r["stop"] is None and age_min >= STOP_GRACE_MIN:
                found.append((pid, sym, "no_stop", f"{r['side']} {sym} open {age_min:.0f} min with no stop-loss"))
            if r["max_risk_pct"] and r["max_risk_pct"] > MAX_RISK_PCT:
                found.append((pid, sym, "risk", f"{sym} risks {r['max_risk_pct']:.1f}% of equity to its stop; "
                                                f"limit {MAX_RISK_PCT:g}%"))
        per_day: dict[str, int] = {}
        for r in rows:
            d = _local_day(r["opened_utc"])
            per_day[d] = per_day.get(d, 0) + 1
        for d, n in per_day.items():
            if n > MAX_TRADES_DAY:
                found.append((f"day:{d}", "-", "activity", f"{n} trades opened on {d}; limit {MAX_TRADES_DAY}"))
        if open_ids:
            sides: dict[str, set[str]] = {}
            for r in rows:
                if r["position_id"] in open_ids:
                    sides.setdefault(norm(r["symbol"]), set()).add(r["side"])
            for sym, s in sides.items():
                if s == {"buy", "sell"}:
                    found.append((f"opp:{sym}:{now.date()}", sym, "opposite", f"long and short {sym} open at the same time"))
        new = []
        for pid, sym, chk, detail in found:
            cur = c.execute("INSERT OR IGNORE INTO pad_flags VALUES (?,?,?,?,?,?)",
                            (login, pid, sym, chk, detail, now_s))
            if cur.rowcount:
                new.append({"position_id": pid, "symbol": sym, "check": chk, "detail": detail})
    if new:
        audit.log("pad", "flags", {"login": login, "new": new})
    return new


# ------------------------------------------------------------------ owner actions
def add_clearance(symbol: str, side: str, lots: float | None, reason: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        cid = c.execute("INSERT INTO pad_clearances (ts_utc, day, symbol, side, lots, reason) VALUES (?,?,?,?,?,?)",
                        (ts, _local_day(ts), norm(symbol), side, lots, reason)).lastrowid
    audit.log("pad", "clearance", {"id": cid, "symbol": norm(symbol), "side": side, "lots": lots, "reason": reason})
    return int(cid or 0)


def set_list(symbol: str, which: str | None, reason: str = "") -> None:
    with _db() as c:
        if which is None:
            c.execute("DELETE FROM pad_lists WHERE symbol = ?", (norm(symbol),))
        else:
            c.execute("INSERT OR REPLACE INTO pad_lists VALUES (?,?,?,?)", (norm(symbol), which, reason, _now()))
    audit.log("pad", "list", {"symbol": norm(symbol), "list": which or "removed", "reason": reason})


def set_class(symbol: str, cls: str) -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO pad_class VALUES (?,?)", (norm(symbol), cls))
    audit.log("pad", "class", {"symbol": norm(symbol), "class": cls})


def flags(days: int = 30) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM pad_flags WHERE first_seen_utc >= ? "
                                           "ORDER BY first_seen_utc DESC", (since,))]


# ------------------------------------------------------------------ text
def rules_lines() -> list[str]:
    L = ["PAD RULES (Policy v0.2, approved 7 Nov 2026)", f" Checked from {EFFECTIVE}. DONNA reads trades only."]
    for cls in CLASSES:
        hold = f"{HOLD_SHARE_WORKDAYS} working days" if cls == "share" else f"{HOLD_HOURS:g} h"
        L.append(f" {CLASS_WORDS[cls]:<26} cap 1:{CAPS[cls]:<4} hold {hold:<16} "
                 + ("clearance per trade" if cls in PER_TRADE else "standing clearance"))
    L += [f" Above USD {TIER_EQUITY:,.0f} equity the cap is at most 1:{TIER_CAP}",
          f" Stop-loss on every trade, at most {MAX_RISK_PCT:g}% of equity at risk; at most {MAX_TRADES_DAY} trades a day",
          " Early exit allowed to cut a loss; closing in profit before the hold is flagged"]
    return L


def status_lines() -> list[str]:
    today = datetime.now(_local_tz()).date().isoformat()
    with _db() as c:
        n_today = sum(1 for r in c.execute("SELECT opened_utc FROM pad_positions WHERE opened_utc IS NOT NULL")
                      if _local_day(r["opened_utc"]) == today)
        cl = [r for r in _clearances(c) if r["day"] == today]
        lists = c.execute("SELECT list, COUNT(*) n FROM pad_lists GROUP BY list").fetchall()
        tracked = c.execute("SELECT COUNT(*) FROM pad_positions").fetchone()[0]
    fl = flags(30)
    L = [f"PAD STATUS {today}", f" Trades opened today: {n_today} (limit {MAX_TRADES_DAY})",
         f" Clearances logged today: {len(cl)}" + "".join(f"\n  #{k['id']} {k['side']} {k['symbol']} "
                                                          f"{k['lots'] or ''} - {k['reason']}" for k in cl),
         " Lists: " + (", ".join(f"{r['list']} {r['n']}" for r in lists) or "none yet (!pad restrict <symbol>)")]
    if not tracked:
        L.append(" NO DATA - no trades seen yet from the cTrader feed (it records from the next snapshot)")
    L.append(f"Flags in the last 30 days: {len(fl)}")
    L += [f" {f['first_seen_utc'][:10]} {CHECK_WORDS.get(f['check_name'], f['check_name'])}: {f['detail']}"
          for f in fl[:12]]
    if len(fl) > 12:
        L.append(f" ... and {len(fl) - 12} more (!pad flags)")
    return L


def digest_lines() -> list[str]:
    """For the morning NEEDS YOU list: only new flags from the last day."""
    fl = flags(1)
    return [f"PAD: {len(fl)} new flag(s) on your own trades - {fl[0]['detail']} (!pad)"] if fl else []


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!pad":
        return None
    sub, _, arg = rest.strip().partition(" ")
    sub, arg = sub.lower(), arg.strip()
    if sub in ("", "status"):
        return "\n".join(status_lines())
    if sub == "rules":
        return "\n".join(rules_lines())
    if sub == "clear":
        m = re.match(r"(\S+)\s+(buy|sell|long|short)\s+([\d.]+)\s+(.+)", arg, re.I | re.S)
        if not m:
            return "Usage: !pad clear <symbol> <buy/sell> <lots> <reason>   e.g. !pad clear BTCUSD buy 0.05 swing on ETF flows"
        side = {"long": "buy", "short": "sell"}.get(m.group(2).lower(), m.group(2).lower())
        sym = norm(m.group(1))
        listed = None
        with _db() as c:
            row = c.execute("SELECT * FROM pad_lists WHERE symbol = ?", (sym,)).fetchone()
            listed = dict(row) if row else None
        if listed and listed["list"] == "restricted":
            return (f"{sym} is on the Restricted List ({listed['reason'] or 'no reason'}). Under PAD s.5.1 it cannot "
                    "be cleared. Nothing logged.")
        cid = add_clearance(sym, side, float(m.group(3)), m.group(4).strip())
        return (f"Clearance #{cid} logged: {side} {sym} {m.group(3)} lots, valid today ({_local_day(_now())}). "
                "Kept in your PAD record. DONNA does not place the trade.")
    if sub in ("restrict", "watch"):
        sym, _, why = arg.partition(" ")
        if not sym:
            return f"Usage: !pad {sub} <symbol> [reason]"
        set_list(sym, "restricted" if sub == "restrict" else "watch", why.strip())
        return f"{norm(sym)} added to the {'Restricted' if sub == 'restrict' else 'Watch'} List."
    if sub == "unlist":
        if not arg:
            return "Usage: !pad unlist <symbol>"
        set_list(arg.split()[0], None)
        return f"{norm(arg.split()[0])} removed from the PAD lists."
    if sub == "list":
        with _db() as c:
            rows = c.execute("SELECT * FROM pad_lists ORDER BY list, symbol").fetchall()
        return "\n".join(f"{r['list'].upper():<10} {r['symbol']:<12} {r['added_utc'][:10]}  {r['reason'] or ''}"
                         for r in rows) or "Both lists are empty. !pad restrict <symbol> [why] / !pad watch <symbol> [why]"
    if sub == "class":
        sym, _, cls = arg.partition(" ")
        cls = cls.strip().lower()
        if not sym or cls not in CLASSES:
            return "Usage: !pad class <symbol> <" + "|".join(CLASSES) + ">"
        set_class(sym, cls)
        return f"{norm(sym)} is now treated as {CLASS_WORDS[cls].lower()}."
    if sub == "flags":
        days = int(arg) if arg.isdigit() else 30
        fl = flags(days)
        return "\n".join(f"{f['first_seen_utc'][:16]} {CHECK_WORDS.get(f['check_name'], f['check_name'])}: {f['detail']}"
                         for f in fl) or f"No PAD flags in the last {days} days."
    if sub == "pack":
        return "The PAD pack is a PDF - send !pad pack [YYYY-MM] in the DONNA command channel."
    return ("Usage: !pad | !pad clear <symbol> <buy/sell> <lots> <reason> | !pad list | !pad restrict <symbol> [why] | "
            "!pad watch <symbol> [why] | !pad unlist <symbol> | !pad class <symbol> <class> | !pad flags [days] | "
            "!pad rules | !pad pack [YYYY-MM]")


# ------------------------------------------------------------------ each rule as a plain statement (dashboard)
RULES: list[tuple[str, str, str, str]] = [   # check, clause, the rule in plain words, why it exists
    ("activity", "s.6.2", f"Open at most {MAX_TRADES_DAY} trades a day.",
     "Caps impulse trading; your own results show most damage on busy days."),
    ("no_stop", "s.6.3", "Put a stop-loss on every trade.", "A trade without a stop has no limit on what it can lose."),
    ("risk", "s.6.3", f"Risk at most {MAX_RISK_PCT:g}% of equity on any one trade.",
     "Keeps one bad trade from doing lasting damage."),
    ("holding", "s.6.1", f"Hold a trade at least {HOLD_HOURS:g} hours before taking a profit "
                         f"({HOLD_SHARE_WORKDAYS} working days for single shares). Cutting a loss early is allowed.",
     "Staff dealing must not look like short-term speculation against clients."),
    ("leverage", "s.6", "Stay within the client leverage cap for the asset class (1:200 FX, metals and indices; "
                        "1:100 commodities; 1:50 crypto; 1:20 shares).", "Staff get no better terms than clients."),
    ("clearance", "s.4, s.5.2", "Log a pre-clearance (!pad clear) before every crypto, single-share or Watch List trade.",
     "Creates the record that you checked for conflicts first."),
    ("restricted", "s.5.1", "Never trade a symbol on the Restricted List.", "Avoids trading where the firm has a conflict."),
    ("opposite", "s.6.4", "Never hold a long and a short in the same symbol at once.",
     "Hedging yourself is a sign of churning, not of a view."),
]


def rule_status(now: datetime | None = None) -> dict[str, Any]:
    """Each PAD rule with: kept / broken N times / no trades yet, since the policy started (EFFECTIVE).
    Plus a practice look-back over the last 30 days of the journal, clearly marked as not breaches."""
    now = now or datetime.now(timezone.utc)
    with _db() as c:
        opened = [r["opened_utc"] for r in c.execute("SELECT opened_utc FROM pad_positions WHERE opened_utc IS NOT NULL")
                  if _local_day(r["opened_utc"]) >= EFFECTIVE]
        fl = [dict(r) for r in c.execute("SELECT * FROM pad_flags ORDER BY first_seen_utc DESC")]
    by: dict[str, list[dict[str, Any]]] = {}
    for f in fl:
        by.setdefault(f["check_name"], []).append(f)
    rules = []
    for check, clause, text, why in RULES:
        n = len(by.get(check, []))
        status = "broken" if n else "kept" if opened else "no trades"
        rules.append({"check": check, "clause": clause, "rule": text, "why": why, "status": status, "breaches": n,
                      "last": by[check][0]["detail"] if n else "",
                      "last_when": by[check][0]["first_seen_utc"][:16] if n else ""})
    order = {"broken": 0, "no trades": 1, "kept": 2}
    rules.sort(key=lambda r: order[r["status"]])
    return {"effective": EFFECTIVE, "trades_checked": len(opened), "flags": len(fl), "rules": rules,
            "kept": sum(r["status"] == "kept" for r in rules), "broken": sum(r["status"] == "broken" for r in rules),
            "practice": practice(now)}


def practice(now: datetime | None = None, days: int = 30) -> dict[str, Any]:
    """The rules that can be replayed on the trade journal, over the last `days` days of trading. Most of these
    trades are from before the policy started, so this is a rehearsal, not a breach record."""
    from jarvis.agents import edge

    d = edge.load(days)
    if d.empty:
        return {"trades": 0}
    per_day = d.groupby("day").size()
    busy = per_day[per_day > MAX_TRADES_DAY]
    big = d[(d.net < 0) & d.bal_before.notna() & (-d.net > d.bal_before * MAX_RISK_PCT / 100)]
    known = d[d.hold_min.notna() & (d.net > 0)]
    quick = known[known.hold_min < HOLD_HOURS * 60]
    return {"trades": int(len(d)), "days": int(len(per_day)), "from": str(d.t.min())[:10], "to": str(d.t.max())[:10],
            "busy_days": int(len(busy)), "max_trades_day": int(per_day.max()),
            "big_losses": int(len(big)), "wins_hold_known": int(len(known)), "wins_before_hold": int(len(quick))}


# ------------------------------------------------------------------ monthly self-review record
def month_bounds(month: str | None = None, today: date | None = None) -> tuple[str, str, str]:
    """'2026-11' -> (label, first day, first day of next month). Default: last full month."""
    if month:
        y, m = (int(x) for x in month.split("-"))
    else:
        t = today or datetime.now(_local_tz()).date()
        y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return f"{y}-{m:02d}", start.isoformat(), end.isoformat()


def pack_sections(month: str | None = None, today: date | None = None) -> tuple[str, list[tuple[str, list[str]]]]:
    label, start, end = month_bounds(month, today)
    in_month = lambda s: s is not None and start <= _local_day(s) < end  # noqa: E731
    with _db() as c:
        pos = [r for r in c.execute("SELECT * FROM pad_positions ORDER BY opened_utc")
               if in_month(r["opened_utc"]) or in_month(r["closed_utc"])]
        clear = [r for r in _clearances(c) if start <= r["day"] < end]
        fl = [r for r in c.execute("SELECT * FROM pad_flags ORDER BY first_seen_utc") if in_month(r["first_seen_utc"])]
        lists = c.execute("SELECT * FROM pad_lists ORDER BY list, symbol").fetchall()
        overrides = {r["symbol"]: r["asset_class"] for r in c.execute("SELECT * FROM pad_class")}
    flagged = {r["position_id"] for r in fl}
    net = sum(r["net"] or 0 for r in pos if in_month(r["closed_utc"]))
    summary = [f"Trades opened or closed in {label}: {len(pos)}", f"Clearances logged: {len(clear)}",
               f"Flags raised: {len(fl)}", f"Realised net P&L on trades closed in the month: {net:,.2f}",
               "Self-review by the Compliance Officer (PAD Policy s.4.3, s.8). Reviewed: ____________  "
               "Date: ________", "Outcome: [ ] no concerns  [ ] follow-up needed (note below)"]
    trades = [f"{'Opened (UTC)':<17} {'Closed (UTC)':<17} {'Symbol':<10} {'Side':<4} {'Units':>10} {'Class':<9} "
              f"{'Net':>10} Flag"]
    for r in pos:
        trades.append(f"{(r['opened_utc'] or '?')[:16]:<17} {(r['closed_utc'] or 'open')[:16]:<17} "
                      f"{norm(r['symbol'])[:10]:<10} {r['side']:<4} {r['units'] or 0:>10,.0f} "
                      f"{asset_class(r['symbol'], overrides):<9} "
                      f"{(r['net'] if r['net'] is not None else 0):>10,.2f} {'YES' if r['position_id'] in flagged else ''}")
    secs = [("Summary", summary),
            ("Trades", trades if pos else ["No trades in the month."]),
            ("Pre-clearances (self-approved)",
             [f"#{k['id']} {k['ts_utc'][:16]} UTC  {k['side']} {k['symbol']} {k['lots'] or ''} - {k['reason']}"
              for k in clear] or ["None logged."]),
            ("Flags", [f"{f['first_seen_utc'][:16]} {CHECK_WORDS.get(f['check_name'], f['check_name'])}: {f['detail']}"
                       for f in fl] or ["No flags."]),
            ("Restricted and Watch Lists (current)",
             [f"{r['list']:<10} {r['symbol']:<12} since {r['added_utc'][:10]}  {r['reason'] or ''}" for r in lists]
             or ["Both lists empty."]),
            ("Rules applied", rules_lines()[1:]),
            ("Limits", ["Covers only the cTrader account connected to DONNA (read-only). Other accounts, Connected "
                        "Persons and client flow are not visible here."])]
    return label, secs


def build_pdf(month: str | None = None, today: date | None = None) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    from jarvis.agents.pack import _latin1

    label, secs = pack_sections(month, today)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(f"{FIRM} PAD review {label}")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin1(f"{FIRM} - PAD self-review, {label}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, "Compliance Officer's personal account dealing - self-review record. Prepared by D.O.N.N.A from cTrader.",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 6, "Strictly private and confidential. Keep for 7 years (PAD Policy s.4.4, s.7).",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for title, lines in secs:
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin1(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Courier", "", 7.5)
        for line in lines or ["-"]:
            pdf.multi_cell(0, 3.8, _latin1(line) or " ", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def filename(month: str | None = None, today: date | None = None) -> str:
    return f"pad-self-review-{month_bounds(month, today)[0]}.pdf"


@register
class PadAgent(Agent):
    name = "pad"
    description = "PAD guardrails: read-only checks of the owner's own trades and a monthly self-review record."

    def monthly(self, send: bool = True, today: date | None = None) -> dict[str, Any]:
        """Scheduler hook (not a Brain tool): only runs on DONNA_PAD_PACK_DAY, for the previous month."""
        today = today or datetime.now(_local_tz()).date()
        if today.day != PACK_DAY:
            return {"skipped": "not PAD pack day"}
        pdf = build_pdf(None, today)
        res: dict[str, Any] = {"bytes": len(pdf), "month": month_bounds(None, today)[0]}
        if send:
            from donna.channels import send_discord_file
            from jarvis.agents.personal import PERSONAL_TARGETS

            res["delivery"] = {t: send_discord_file(pdf, filename(None, today),
                                                    "Your monthly PAD self-review record, for your own file.",
                                                    webhook=t.partition(":")[2])
                               for t in PERSONAL_TARGETS if t.startswith("discord")}
        audit.log("pad", "monthly_pack", res)
        return res
