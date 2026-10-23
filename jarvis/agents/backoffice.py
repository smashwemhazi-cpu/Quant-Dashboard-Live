"""Back office: cTrader end-of-day account report + Syntellicore CRM exports.

Drop exports into JARVIS_INBOX_DIR (server: /var/lib/vertex1/inbox). `ingest()` recognises
each file by its columns, not its name, stores it in the state DB and moves it to
inbox/processed/. Supported:
  cTrader   "End of day report"      (.xlsx/.csv: Trader Account, Group, Balance, Credit, Equity, Margin, ...)
  Syntellicore "Performance Overview KPIs"  (.csv: caption/value pairs - Leads, FTDs, Deposits ...)
  Syntellicore "Customer Documents"         (.csv: customer_no, type, document_status, ...)
  cTrader   "Active Accounts"        (.xlsx: deposits, withdrawals, deals, rejected deals, volume, realised P&L)
  cTrader   "Price Streams <group>"  (.xlsx: symbol, enabled, spread, markups)

Snapshots accumulate, so day-over-day signals (new funded accounts, accounts emptied,
equity drawdowns) appear from the second end-of-day file onward.
Reports carry account / customer numbers only - never names.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

MARGIN_ALERT_PCT = float(os.getenv("JARVIS_MARGIN_ALERT_PCT", "150"))   # margin level below this = alert
CREDIT_HEAVY_PCT = float(os.getenv("JARVIS_CREDIT_HEAVY_PCT", "50"))    # credit as % of balance
DRAWDOWN_ALERT_PCT = float(os.getenv("JARVIS_DRAWDOWN_ALERT_PCT", "20"))
EXPIRY_WARN_DAYS = int(os.getenv("JARVIS_DOC_EXPIRY_WARN_DAYS", "60"))
TURNOVER_ALERT_X = float(os.getenv("JARVIS_TURNOVER_ALERT_X", "1000"))  # traded volume / equity
SPREAD_WIDEN_PCT = float(os.getenv("JARVIS_SPREAD_WIDEN_PCT", "50"))
WATCH_SYMBOLS = re.compile(os.getenv("JARVIS_SPREAD_WATCH", r"^(XAUUSD|XAGUSD|EURUSD|GBPUSD|US30|USOIL|BTCUSD|GER\d\d|NAS100|USTEC)\b"), re.I)
TEST_NAME = re.compile(r"test|\bqa\b|automation", re.I)
NULL_DATE = "1900-01-01"


def inbox() -> Path:
    return Path(os.getenv("JARVIS_INBOX_DIR", str(Path(settings.data_dir).parent / "inbox")))


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS bo_eod (asof TEXT, account TEXT, grp TEXT, balance REAL, credit REAL,
            equity REAL, margin REAL, upnl_a REAL, upnl_b REAL, PRIMARY KEY (asof, account));
        CREATE TABLE IF NOT EXISTS bo_kpi (asof TEXT, metric TEXT, value REAL, PRIMARY KEY (asof, metric));
        CREATE TABLE IF NOT EXISTS bo_docs (uploaded TEXT, customer TEXT, assigned TEXT, doc_type TEXT,
            status TEXT, verified TEXT, expires TEXT, is_test INTEGER);
        CREATE TABLE IF NOT EXISTS bo_activity (asof TEXT, account TEXT, equity REAL, deposit REAL, withdrawal REAL,
            closed INTEGER, deals INTEGER, rejected INTEGER, volume REAL, swaps REAL, commission REAL, net_pnl REAL,
            PRIMARY KEY (asof, account));
        CREATE TABLE IF NOT EXISTS bo_streams (asof TEXT, grp TEXT, symbol TEXT, asset TEXT, enabled INTEGER,
            spread REAL, markup_bid REAL, markup_ask REAL, PRIMARY KEY (asof, grp, symbol));
        CREATE TABLE IF NOT EXISTS bo_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return c


# ------------------------------------------------------------------ parsing
def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        d = pd.read_excel(path)
    else:
        for enc in ("utf-8-sig", "cp1252"):  # Syntellicore exports are Windows-1252
            try:
                d = pd.read_csv(path, dtype=str, encoding=enc)
                break
            except UnicodeDecodeError:
                continue
    # Syntellicore wraps headers as *|name|*
    d.columns = [re.sub(r"^\*\|(.*)\|\*$", r"\1", str(c).strip()).strip() for c in d.columns]
    return d


def _num(s: pd.Series) -> pd.Series:
    # handles "1,246.00", "9.93%", "USD 2 037.38", "USD -2.64"
    return pd.to_numeric(s.astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce").fillna(0.0)


def _asof(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()


def detect(d: pd.DataFrame) -> str | None:
    cols = {c.lower() for c in d.columns}
    if {"trader account", "equity", "balance"} <= cols:
        return "eod"
    if {"customer_no", "document_status"} <= cols:
        return "docs"
    if {"account", "traded volume (usd)", "total deals"} <= cols:
        return "activity"
    if {"symbol", "spread", "markup bid", "enabled"} <= cols:
        return "streams"
    if "value" in cols and any(c.startswith("caption") for c in cols):
        return "kpi"
    return None


def _store_eod(c: sqlite3.Connection, d: pd.DataFrame, asof: str) -> int:
    col = {k.lower(): k for k in d.columns}
    g = lambda name: _num(d[col[name]]) if name in col else pd.Series(0.0, index=d.index)  # noqa: E731
    rows = pd.DataFrame({
        "asof": asof, "account": d[col["trader account"]].astype(str).str.replace(r"\.0$", "", regex=True),
        "grp": d[col["group"]].astype(str) if "group" in col else "",
        "balance": g("balance"), "credit": g("credit"), "equity": g("equity"), "margin": g("margin"),
        "upnl_a": g("net unrealized pnl (a-book)"), "upnl_b": g("net unrealized pnl (b-book)")})
    c.execute("DELETE FROM bo_eod WHERE asof = ?", (asof,))
    c.executemany("INSERT INTO bo_eod VALUES (?,?,?,?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
    return len(rows)


def _store_kpi(c: sqlite3.Connection, d: pd.DataFrame, asof: str) -> int:
    cap = next(k for k in d.columns if k.lower().startswith("caption"))
    vals = list(zip(d[cap].astype(str).str.strip(), _num(d["value"])))
    c.executemany("INSERT OR REPLACE INTO bo_kpi VALUES (?,?,?)", [(asof, k, float(v)) for k, v in vals])
    return len(vals)


def _store_docs(c: sqlite3.Connection, d: pd.DataFrame) -> int:
    name = d.get("first_name", "").fillna("") + " " + d.get("last_name", "").fillna("")
    extra = {x.strip() for x in os.getenv("JARVIS_TEST_CUSTOMERS", "").split(",") if x.strip()}
    is_test = name.str.contains(TEST_NAME) | d["customer_no"].isin(extra)
    rows = pd.DataFrame({"uploaded": d["date"], "customer": d["customer_no"], "assigned": d.get("assigned_to", "0"),
                         "doc_type": d["type"], "status": d["document_status"],
                         "verified": d.get("verification_date", ""), "expires": d.get("expiration_date", ""),
                         "is_test": is_test.astype(int)}).fillna("")
    c.execute("DELETE FROM bo_docs")  # the export is the full current list, not a delta
    c.executemany("INSERT INTO bo_docs VALUES (?,?,?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
    return len(rows)


def _store_activity(c: sqlite3.Connection, d: pd.DataFrame, asof: str) -> int:
    col = {k.lower(): k for k in d.columns}
    g = lambda name: _num(d[col[name]]) if name in col else pd.Series(0.0, index=d.index)  # noqa: E731
    rows = pd.DataFrame({  # names in this export are deliberately not stored
        "asof": asof, "account": d[col["account"]].astype(str).str.replace(r"\.0$", "", regex=True),
        "equity": g("equity (usd)"), "deposit": g("deposit (usd)"), "withdrawal": g("withdrawal (usd)"),
        "closed": g("closed positions").astype(int), "deals": g("total deals").astype(int),
        "rejected": g("rejected deals").astype(int), "volume": g("traded volume (usd)"),
        "swaps": g("realized swaps (usd)"), "commission": g("realized commissions (usd)"),
        "net_pnl": g("realised net p&l (usd)")})
    c.execute("DELETE FROM bo_activity WHERE asof = ?", (asof,))
    c.executemany("INSERT INTO bo_activity VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
    return len(rows)


def _store_streams(c: sqlite3.Connection, d: pd.DataFrame, asof: str, grp: str) -> int:
    rows = pd.DataFrame({
        "asof": asof, "grp": grp, "symbol": d["Symbol"].astype(str), "asset": d.get("Asset class", "").astype(str),
        "enabled": d["Enabled"].astype(str).str.lower().isin(["true", "yes", "1"]).astype(int),
        "spread": pd.to_numeric(d["Spread"], errors="coerce"),  # "No Quotes" -> NULL
        "markup_bid": _num(d["Markup Bid"]), "markup_ask": _num(d.get("Markup Ask", d["Markup Bid"]))})
    rows = rows.drop_duplicates(["symbol"])
    c.execute("DELETE FROM bo_streams WHERE asof = ? AND grp = ?", (asof, grp))
    c.executemany("INSERT INTO bo_streams VALUES (?,?,?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
    return len(rows)


def _stream_group(path: Path) -> str:
    m = re.search(r"PriceStreams_(.+?)_+\d{4}-\d{2}-\d{2}", path.name)
    return m.group(1).replace("_", " ").strip() if m else "default"


def ingest_file(path: Path) -> dict[str, Any]:
    from jarvis.agents import personal  # personal journal + cBot logs share this inbox

    if personal.is_bot_log(path):
        who, n = personal.store_bot_log(path)
        return {"file": path.name, "status": "STORED", "kind": "botlog", "bot": who, "rows": n}
    d = _read(path)
    if personal.is_statement(d):
        acc, n = personal.store_statement(d, path)
        return {"file": path.name, "status": "STORED", "kind": "statement", "account": acc, "rows": n}
    kind = detect(d)
    if kind is None:
        from jarvis.agents import growth  # Syntellicore leads / customers list

        m = growth.lead_columns(d)
        if m:
            return {"file": path.name, "status": "STORED", "kind": "leads", "asof": _asof(path),
                    "rows": growth.store_leads(d, m, _asof(path))}
        return {"file": path.name, "status": "UNRECOGNISED", "columns": list(d.columns)[:12]}
    asof = _asof(path)
    with _db() as c:
        n = {"eod": lambda: _store_eod(c, d, asof), "kpi": lambda: _store_kpi(c, d, asof),
             "docs": lambda: _store_docs(c, d), "activity": lambda: _store_activity(c, d, asof),
             "streams": lambda: _store_streams(c, d, asof, _stream_group(path))}[kind]()
        c.execute("INSERT OR REPLACE INTO bo_meta VALUES (?, ?)", (f"last_{kind}", asof))
    return {"file": path.name, "status": "STORED", "kind": kind, "asof": asof, "rows": n}


# ------------------------------------------------------------------ analysis
def book_summary() -> dict[str, Any]:
    with _db() as c:
        dates = [r[0] for r in c.execute("SELECT DISTINCT asof FROM bo_eod ORDER BY asof DESC LIMIT 2")]
        if not dates:
            return {"error": "NO DATA - no cTrader end-of-day report ingested"}
        cur = pd.read_sql_query("SELECT * FROM bo_eod WHERE asof = ?", c, params=(dates[0],))
        prev = pd.read_sql_query("SELECT * FROM bo_eod WHERE asof = ?", c, params=(dates[1],)) if len(dates) > 1 else None
    funded = cur[(cur.balance > 0) | (cur.equity > 0)]
    eq = funded.equity.sum()
    top = funded.sort_values("equity", ascending=False)
    lvl = cur[cur.margin > 0].assign(level=lambda x: x.equity / x.margin * 100)
    out: dict[str, Any] = {
        "asof": dates[0], "accounts": len(cur), "funded": len(funded), "empty": len(cur) - len(funded),
        "balance": cur.balance.sum(), "equity": eq, "credit": cur.credit.sum(), "margin": cur.margin.sum(),
        "floating_a": cur.upnl_a.sum(), "floating_b": cur.upnl_b.sum(), "open_accounts": int((cur.margin > 0).sum()),
        "by_group": funded.groupby("grp").agg(funded=("account", "size"), equity=("equity", "sum"))
                          .sort_values("equity", ascending=False).reset_index().to_dict("records"),
        "top_account": {"account": top.account.iloc[0], "share_pct": top.equity.iloc[0] / eq * 100} if eq > 0 else None,
        "top5_share_pct": top.equity.head(5).sum() / eq * 100 if eq > 0 else None,
        "margin_alerts": lvl[lvl.level < MARGIN_ALERT_PCT].sort_values("level")[["account", "grp", "level", "equity"]]
                         .to_dict("records"),
        "credit_heavy": cur[(cur.credit > 0) & (cur.credit >= cur.balance.clip(lower=0.01) * CREDIT_HEAVY_PCT / 100)]
                        [["account", "grp", "balance", "credit"]].to_dict("records"),
    }
    if prev is not None:
        m = cur.merge(prev, on="account", how="outer", suffixes=("", "_p")).fillna(0.0)
        out["prev_asof"] = dates[1]
        out["newly_funded"] = m[(m.balance_p <= 0) & (m.balance > 0)].account.tolist()
        out["emptied"] = m[(m.balance_p > 0) & (m.balance <= 0)].account.tolist()
        dd = m[(m.equity_p > 0) & (m.equity < m.equity_p * (1 - DRAWDOWN_ALERT_PCT / 100)) & (m.equity > 0)]
        out["drawdowns"] = dd.assign(chg_pct=(dd.equity / dd.equity_p - 1) * 100)[["account", "chg_pct"]].to_dict("records")
        out["equity_change"] = eq - m[(m.balance_p > 0) | (m.equity_p > 0)].equity_p.sum()
    return out


def kpi_summary() -> dict[str, Any]:
    with _db() as c:
        dates = [r[0] for r in c.execute("SELECT DISTINCT asof FROM bo_kpi ORDER BY asof DESC LIMIT 2")]
        if not dates:
            return {"error": "NO DATA - no Syntellicore KPI export ingested"}
        get = lambda d: dict(c.execute("SELECT metric, value FROM bo_kpi WHERE asof = ?", (d,)).fetchall())  # noqa: E731
        cur, prev = get(dates[0]), (get(dates[1]) if len(dates) > 1 else {})
    return {"asof": dates[0], "prev_asof": dates[1] if prev else None, "kpis": cur, "prev": prev}


def kyc_summary(today: pd.Timestamp | None = None) -> dict[str, Any]:
    today = (today or pd.Timestamp.now()).normalize()
    with _db() as c:
        d = pd.read_sql_query("SELECT * FROM bo_docs", c)
    if d.empty:
        return {"error": "NO DATA - no Syntellicore Customer Documents export ingested"}
    d["uploaded"] = pd.to_datetime(d.uploaded, errors="coerce")
    d["expires"] = pd.to_datetime(d.expires.where(~d.expires.astype(str).str.startswith(NULL_DATE)), errors="coerce")
    test, real = d[d.is_test == 1], d[d.is_test == 0]
    pend = real[real.status.str.lower() == "pending"]
    # a customer still needs action on a doc type if its latest upload of that type was rejected
    latest = real.sort_values("uploaded").groupby(["customer", "doc_type"]).tail(1)
    rej = latest[latest.status.str.lower() == "rejected"]
    exp = real[(real.status.str.lower() == "verified") & real.expires.notna()
               & (real.expires <= today + pd.Timedelta(days=EXPIRY_WARN_DAYS))].drop_duplicates("customer")
    return {
        "pending_docs": len(pend), "pending_customers": pend.customer.nunique(),
        "oldest_pending_days": int((today - pend.uploaded.min()).days) if len(pend) else 0,
        "pending_over_2d": sorted(pend[pend.uploaded < today - pd.Timedelta(days=2)].customer.unique().tolist()),
        "pending_by_type": pend.doc_type.value_counts().to_dict(),
        "rejected_open": rej.groupby("customer").doc_type.apply(lambda s: ", ".join(sorted(set(s)))).to_dict(),
        "expiring": exp[["customer", "doc_type"]].assign(expires=exp.expires.dt.date.astype(str)).to_dict("records"),
        "unassigned_pct": float((real.assigned.astype(str).isin(["0", "", "nan"])).mean() * 100),
        "test_docs": len(test), "test_pending": int((test.status.str.lower() == "pending").sum()),
    }


def activity_summary() -> dict[str, Any]:
    with _db() as c:
        dates = [r[0] for r in c.execute("SELECT DISTINCT asof FROM bo_activity ORDER BY asof DESC LIMIT 1")]
        if not dates:
            return {"error": "NO DATA - no cTrader Active Accounts report ingested"}
        a = pd.read_sql_query("SELECT * FROM bo_activity WHERE asof = ?", c, params=dates)
    traded = a[a.deals > 0]
    turn = traded[traded.equity > 0].assign(x=lambda t: t.volume / t.equity)
    return {
        "asof": dates[0], "accounts": len(a), "trading": len(traded), "volume": a.volume.sum(), "deals": int(a.deals.sum()),
        "rejected": int(a.rejected.sum()), "deposits": a.deposit.sum(), "withdrawals": a.withdrawal.sum(),
        "commission": -a.commission.sum(), "swaps": -a.swaps.sum(), "client_net_pnl": a.net_pnl.sum(),
        "winners": int((traded.net_pnl > 0).sum()), "losers": int((traded.net_pnl < 0).sum()),
        "top_volume": traded.sort_values("volume", ascending=False)[["account", "volume", "net_pnl"]].head(3).to_dict("records"),
        "high_turnover": turn[turn.x >= TURNOVER_ALERT_X].sort_values("x", ascending=False)[["account", "x"]].to_dict("records"),
        # money in and straight back out with no trading is a standard AML review trigger
        "in_out_no_trading": a[(a.deposit > 0) & (a.withdrawal >= a.deposit * 0.9) & (a.deals == 0)].account.tolist(),
        "rejections": a[a.rejected > 0][["account", "rejected", "deals"]].to_dict("records"),
    }


def streams_summary() -> dict[str, Any]:
    with _db() as c:
        dates = [r[0] for r in c.execute("SELECT DISTINCT asof FROM bo_streams ORDER BY asof DESC LIMIT 2")]
        if not dates:
            return {"error": "NO DATA - no cTrader Price Streams export ingested"}
        cur = pd.read_sql_query("SELECT * FROM bo_streams WHERE asof = ?", c, params=(dates[0],))
        prev = pd.read_sql_query("SELECT * FROM bo_streams WHERE asof = ?", c, params=(dates[1],)) if len(dates) > 1 else None
    on = cur[cur.enabled == 1]
    out: dict[str, Any] = {"asof": dates[0], "groups": {}}
    for grp, g in on.groupby("grp"):
        w = g[g.symbol.str.match(WATCH_SYMBOLS)].sort_values("symbol")
        out["groups"][grp] = {
            "enabled": len(g), "no_quotes": sorted(g[g.spread.isna()].symbol.tolist()),
            "watch": [(r.symbol, r.spread, r.markup_bid, r.markup_ask) for r in w.itertuples()],
        }
    if prev is not None:
        m = on.merge(prev, on=["grp", "symbol"], suffixes=("", "_p"))
        wide = m[(m.spread_p > 0) & (m.spread > m.spread_p * (1 + SPREAD_WIDEN_PCT / 100))]
        out["prev_asof"] = dates[1]
        out["widened"] = [(r.grp, r.symbol, r.spread_p, r.spread) for r in wide.itertuples()]
        mk = m[(m.markup_bid != m.markup_bid_p) | (m.markup_ask != m.markup_ask_p)]
        out["markup_changed"] = [(r.grp, r.symbol) for r in mk.itertuples()]
    return out


# ------------------------------------------------------------------ report section
def _m(v: float) -> str:
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def report_lines() -> list[str]:
    """Plain-text BUSINESS section for the daily reports. Missing inputs say NO DATA."""
    L: list[str] = []
    k = kpi_summary()
    if "error" in k:
        L.append(f"CRM KPIs: {k['error']}")
    else:
        v, p = k["kpis"], k["prev"]
        delta = lambda m: f" ({v[m] - p[m]:+,.0f})" if m in p and m in v else ""  # noqa: E731
        L.append(f"CRM KPIs (Syntellicore, export {k['asof']}):")
        L.append(f" Leads {v.get('Leads', 0):,.0f}{delta('Leads')} | FTDs {v.get('FTDs', 0):,.0f}{delta('FTDs')} "
                 f"| conversion {v.get('Conversion Rate', 0):.2f}% | avg FTD {_m(v.get('AVG FTD Amount ($)', 0))}")
        L.append(f" Deposits {_m(v.get('Deposits ($)', 0))} | Withdrawals {_m(v.get('Withdrawals ($)', 0))} "
                 f"| Net {_m(v.get('Net Deposits ($)', 0))}{delta('Net Deposits ($)')}")
        if v.get("Volume", None) == 0:
            L.append(" Volume reported as 0 - check the CRM <-> cTrader volume sync.")
    b = book_summary()
    L.append("")
    if "error" in b:
        L.append(f"BOOK: {b['error']}")
    else:
        L.append(f"BOOK (cTrader end of day {b['asof']}):")
        L.append(f" {b['funded']} funded of {b['accounts']} accounts ({b['empty']} at zero) | equity {_m(b['equity'])} "
                 f"| credit {_m(b['credit'])} | {b['open_accounts']} with open positions")
        L.append(f" Floating P&L A-book {_m(b['floating_a'])} | B-book {_m(b['floating_b'])}")
        L.append(" Groups: " + ", ".join(f"{g['grp']} {g['funded']}/{_m(g['equity'])}" for g in b["by_group"]))
        if b["top_account"]:
            L.append(f" Concentration: #{b['top_account']['account']} holds {b['top_account']['share_pct']:.0f}% "
                     f"of equity, top 5 hold {b['top5_share_pct']:.0f}%")
        for a in b["margin_alerts"]:
            L.append(f" !! Margin level {a['level']:.0f}% on #{a['account']} ({a['grp']}) - near stop-out")
        if b["credit_heavy"]:
            L.append(" Bonus-heavy (credit >= {:.0f}% of balance): ".format(CREDIT_HEAVY_PCT)
                     + ", ".join(f"#{a['account']}" for a in b["credit_heavy"]))
        if "prev_asof" in b:
            L.append(f" Since {b['prev_asof']}: equity {b['equity_change']:+,.0f} | newly funded "
                     f"{', '.join('#' + a for a in b['newly_funded']) or 'none'} | emptied "
                     f"{', '.join('#' + a for a in b['emptied']) or 'none'}")
            L += [f" !! #{a['account']} equity {a['chg_pct']:+.0f}% day-on-day - retention call" for a in b["drawdowns"]]
        else:
            L.append(" Day-on-day changes start with the next end-of-day file.")
    t = activity_summary()
    L.append("")
    if "error" in t:
        L.append(f"TRADING ACTIVITY: {t['error']}")
    else:
        L.append(f"TRADING ACTIVITY (cTrader Active Accounts {t['asof']}, period as exported):")
        L.append(f" {t['trading']} trading accounts | {t['deals']} deals | volume {_m(t['volume'])} "
                 f"| rejected deals {t['rejected']}")
        L.append(f" Commission {_m(t['commission'])} | swaps {_m(t['swaps'])} | client net P&L {_m(t['client_net_pnl'])} "
                 f"({t['winners']} up, {t['losers']} down)")
        L.append(" Top volume: " + ", ".join(f"#{a['account']} {_m(a['volume'])} (P&L {_m(a['net_pnl'])})"
                                            for a in t["top_volume"]))
        L += [f" !! #{a['account']} traded {a['x']:,.0f}x its equity - leverage / risk review" for a in t["high_turnover"]]
        L += [f" !! #{a} deposited and withdrew with no trading - AML review" for a in t["in_out_no_trading"]]
        L += [f" !! #{a['account']} {a['rejected']} rejected of {a['deals']} deals - check execution" for a in t["rejections"]]
    s = streams_summary()
    L.append("")
    if "error" in s:
        L.append(f"PRICE STREAMS: {s['error']}")
    else:
        L.append(f"PRICE STREAMS ({s['asof']}):")
        for grp, g in s["groups"].items():
            L.append(f" {grp}: {g['enabled']} symbols enabled")
            if g["no_quotes"]:
                L.append(f"  !! Enabled but NO QUOTES ({len(g['no_quotes'])}): " + ", ".join(g["no_quotes"]))
            if g["watch"]:
                L.append("  Spreads: " + " | ".join(f"{sym} {sp:g}" if sp == sp else f"{sym} n/q" for sym, sp, _, _ in g["watch"]))
        if "prev_asof" in s:
            L += [f"  !! {sym} ({grp}) spread {a:g} -> {b:g} since {s['prev_asof']}" for grp, sym, a, b in s["widened"]]
            if s["markup_changed"]:
                L.append(f"  Markup changed on {len(s['markup_changed'])} symbols since {s['prev_asof']} - confirm intended")
    q = kyc_summary()
    L.append("")
    if "error" in q:
        L.append(f"KYC: {q['error']}")
    else:
        L.append(f"KYC QUEUE: {q['pending_docs']} docs pending for {q['pending_customers']} customers "
                 f"(oldest {q['oldest_pending_days']}d)")
        if q["pending_over_2d"]:
            L.append(" Waiting > 2 days: " + ", ".join(q["pending_over_2d"]))
        if q["rejected_open"]:
            L.append(" Re-request (latest upload rejected): "
                     + "; ".join(f"{c} {t}" for c, t in q["rejected_open"].items()))
        if q["expiring"]:
            L.append(f" ID expiring <= {EXPIRY_WARN_DAYS}d: " + ", ".join(f"{e['customer']} {e['expires']}" for e in q["expiring"]))
        if q["unassigned_pct"] > 50:
            L.append(f" {q['unassigned_pct']:.0f}% of documents have no assigned owner.")
        if q["test_pending"]:
            L.append(f" Excluded {q['test_docs']} test-account docs ({q['test_pending']} pending) - archive them in the CRM.")
    return L


@register
class BackOfficeAgent(Agent):
    name = "backoffice"
    description = "cTrader end-of-day book, Syntellicore CRM KPIs and KYC document queue."

    @tool()
    def ingest(self) -> dict[str, Any]:
        """Load every export waiting in the inbox (cTrader EOD, Syntellicore KPIs / documents)."""
        box = inbox()
        if not box.exists():
            return {"error": f"inbox {box} does not exist"}
        done = box / "processed"
        done.mkdir(exist_ok=True)
        results = []
        for p in sorted(x for x in box.iterdir() if x.suffix.lower() in (".csv", ".xlsx", ".xls", ".log", ".txt")):
            try:
                r = ingest_file(p)
            except Exception as e:  # one bad file must not block the others
                r = {"file": p.name, "status": "ERROR", "error": f"{type(e).__name__}: {e}"}
            if r["status"] == "STORED":
                shutil.move(str(p), done / p.name)
            results.append(r)
        if results:
            audit.log("backoffice", "ingest", {"results": results})
        return {"files": results}

    @tool()
    def book(self) -> dict[str, Any]:
        """Latest cTrader end-of-day book: funded accounts, equity, credit, floating P&L by book,
        concentration, margin-level alerts, bonus-heavy accounts and day-on-day changes."""
        return book_summary()

    @tool()
    def kpis(self) -> dict[str, Any]:
        """Latest Syntellicore performance KPIs (leads, FTDs, conversion, deposits, withdrawals)."""
        return kpi_summary()

    @tool()
    def activity(self) -> dict[str, Any]:
        """cTrader account activity: volume, deals, rejected deals, commission, client P&L, turnover and AML flags."""
        return activity_summary()

    @tool()
    def price_streams(self) -> dict[str, Any]:
        """cTrader price-stream config per group: enabled symbols without quotes, watchlist spreads, changes."""
        return streams_summary()

    @tool()
    def kyc_queue(self) -> dict[str, Any]:
        """KYC document queue: pending, stale, rejected-needing-re-request, expiring IDs (customer numbers only)."""
        return kyc_summary()
