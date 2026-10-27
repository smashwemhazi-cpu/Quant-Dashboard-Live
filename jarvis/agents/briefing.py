"""Vertex 1 daily reports.

morning_dispatch  forward-looking, sent before London open (~06:00 UTC):
                  system & data health, live tickers vs previous close, predictor outlook,
                  overnight macro/crypto headlines, CRM, desk priorities.
daily_wrap        backward-looking, sent after the NY close (~22:00 UTC):
                  closes, daily/5-day change, range vs ATR (unusual-move flag), top movers,
                  24h news recap ranked by market relevance, predictor scorecard.

Rule: a section with no data says NO DATA. It never says "normal".
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import pandas as pd
import requests

from jarvis.adapters.market import WATCHLIST, Instrument, daily_bars
from jarvis.agents import predictor
from jarvis.core import audit
from jarvis.core.registry import Agent, agents, register, tool

FEEDS = {
    "macro": os.getenv("JARVIS_MACRO_FEEDS",
                       "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258,"
                       "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664").split(","),
    "crypto": os.getenv("JARVIS_CRYPTO_FEEDS",
                        "https://www.coindesk.com/arc/outboundfeeds/rss/,https://cointelegraph.com/rss").split(","),
}
RELEVANCE = {  # term -> weight; headlines that move our instruments rank first
    r"\b(fed|fomc|powell|rate (cut|hike)s?|interest rates?)\b": 3, r"\b(cpi|pce|inflation)\b": 3,
    r"\b(payrolls?|nfp|jobs report|unemployment)\b": 3, r"\b(ecb|lagarde|boe|bank of england)\b": 2,
    r"\b(treasur(y|ies)|yields?|dollar|dxy)\b": 2, r"\b(gold|silver|bullion)\b": 2,
    r"\b(oil|opec|crude|brent|wti)\b": 2, r"\b(tariffs?|recession|gdp|china)\b": 1.5,
    r"\b(bitcoin|btc|ether(eum)?|etf inflows?|etf outflows?)\b": 2, r"\b(sec|stablecoins?|regulat\w+)\b": 1,
    r"\b(dow|nasdaq|s&p|dax|stocks?)\b": 0.5,
}
TARGETS = [t for t in os.getenv("JARVIS_REPORT_TARGETS", "").split(",") if t]
LINE = "-" * 34


def fmt(inst: Instrument, v: float) -> str:
    s = f"{v:,.{inst.decimals}f}"
    return f"${s}" if inst.unit == "$" else f"{s} {inst.unit}".strip()


def pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else float("nan")


def sp(v: float | None, spec: str = "+.2f", suffix: str = "%") -> str:
    return "n/a" if v is None or v != v else f"{v:{spec}}{suffix}"


# ------------------------------------------------------------------ data gathering
def market_table(asof: pd.Timestamp | None = None) -> list[dict[str, Any]]:
    rows = []
    for inst in WATCHLIST:
        df, src = daily_bars(inst)
        if df.empty:
            rows.append({"inst": inst, "source": src, "error": True})
            continue
        if asof is not None:
            df = df[df.index <= asof]
        c, h, l = df["close"], df["high"], df["low"]
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-2]) if len(df) > 15 else float("nan")
        last_date = df.index[-1]
        stale_days = (pd.Timestamp.now().normalize() - last_date).days
        rows.append({
            "inst": inst, "source": src, "df": df, "date": last_date.date().isoformat(),
            "close": float(c.iloc[-1]), "chg_pct": pct(c.iloc[-1], c.iloc[-2]) if len(c) > 1 else float("nan"),
            "chg_5d_pct": pct(c.iloc[-1], c.iloc[-6]) if len(c) > 5 else float("nan"),
            "range": float(h.iloc[-1] - l.iloc[-1]), "range_atr": float((h.iloc[-1] - l.iloc[-1]) / atr) if atr else None,
            "stale": stale_days > (2 if inst.kind == "crypto" else 4),
        })
    return rows


def news(hours: int = 24, per_cat: int = 4) -> dict[str, list[dict[str, Any]]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: dict[str, list[dict[str, Any]]] = {}
    for cat, urls in FEEDS.items():
        items, seen, errors = [], set(), []
        for url in [u.strip() for u in urls if u.strip()]:
            try:
                r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 (Vertex1 desk report)"})
                r.raise_for_status()
                for it in ET.fromstring(r.content).iter("item"):
                    title = (it.findtext("title") or "").strip()
                    try:
                        ts = parsedate_to_datetime(it.findtext("pubDate") or "")
                        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                    except (TypeError, ValueError):
                        continue
                    key = re.sub(r"\W+", "", title.lower())[:60]
                    if not title or ts < cutoff or key in seen:
                        continue
                    seen.add(key)
                    score = sum(w for rx, w in RELEVANCE.items() if re.search(rx, title, re.I))
                    items.append({"title": title, "ts": ts.isoformat(), "score": score, "link": it.findtext("link")})
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {type(e).__name__}")
        items.sort(key=lambda x: x["ts"], reverse=True)   # newest first...
        items.sort(key=lambda x: -x["score"])             # ...then most relevant (stable sort)
        out[cat] = [i for i in items if i["score"] > 0][:per_cat] or items[:per_cat]
        if errors and not items:
            out[cat] = [{"title": f"NO DATA - feeds unreachable ({'; '.join(errors)})", "score": 0}]
    return out


def telemetry() -> dict[str, Any]:
    try:
        import psutil

        t = {"cpu_pct": psutil.cpu_percent(interval=0.5), "ram_pct": psutil.virtual_memory().percent,
             "disk_pct": psutil.disk_usage("/").percent}
    except ImportError:
        t = {"error": "psutil not installed"}
    url = os.getenv("DONNA_URL")
    if url:
        try:
            t["donna"] = requests.get(f"{url.rstrip('/')}/health", timeout=5).json().get("status", "?")
        except Exception as e:
            t["donna"] = f"DOWN ({type(e).__name__})"
    return t


def crm() -> list[str]:
    lines = []
    db = os.getenv("DONNA_DB_PATH")
    if db and os.path.exists(db):
        import sqlite3

        with sqlite3.connect(db) as c:
            by = dict(c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
        lines.append("Leads: " + (", ".join(f"{k.lower()} {v}" for k, v in sorted(by.items())) or "none"))
    else:
        lines.append("Leads: NO DATA (DONNA_DB_PATH not set)")
    try:
        p = agents()["pipeline"].analyse()
        lines.append(f"Pipeline: {p['open_deals']} open deals, weighted forecast {p['weighted_forecast']:,.0f}, "
                     f"{len(p['stale_deals'])} stale")
    except Exception as e:
        lines.append(f"Pipeline: NO DATA ({type(e).__name__})")
    return lines


def desk() -> list[str]:
    from jarvis.adapters.sources import is_demo

    if is_demo():
        return ["NO LIVE DATA - dealing analytics are running on synthetic demo data; "
                "toxic-flow and slippage checks NOT performed on real flow."]
    try:
        d = agents()["dealing"].desk_report()
    except FileNotFoundError:
        return ["NO DATA - no fills/statements ingested; toxic-flow and slippage checks NOT performed."]
    out = [f"Session {d['session']}: revenue ${d['revenue_usd']:,.0f}, client P&L ${d['client_pnl_usd']:,.0f}, "
           f"{d['active_traders']} active traders, {d['slippage_episodes']} slippage episodes"]
    return out + (d["recommended_actions"] or ["No toxic flow or LP slippage episodes found in the data received."])


def business() -> list[str]:
    from jarvis.agents import backoffice

    try:
        from jarvis.agents import growth

        backoffice.BackOfficeAgent().ingest()  # pick up anything dropped since the last run
        return backoffice.report_lines() + [""] + growth.report_lines()
    except Exception as e:
        return [f"NO DATA - back office section failed ({type(e).__name__}: {e})"]


def outlook(rows: list[dict[str, Any]], log: bool) -> list[dict[str, Any]]:
    res = []
    for r in rows:
        if r.get("error") or len(r["df"]) < 800:
            res.append({"label": r["inst"].label, "text": "NO DATA - need 800+ daily bars"})
            continue
        done = predictor.completed(r["df"])  # finished days only - today's bar is still forming
        wf = predictor.walk_forward(done)
        nxt = predictor.predict_next(done)
        if log and "error" not in wf:
            predictor.log_prediction(r["inst"].key, nxt, wf["edge"])
        tag = f"EDGE (BSS {wf['brier_skill']:+.3f})" if wf.get("edge") else "no proven edge, info only"
        res.append({"label": r["inst"].label, "p_up": nxt["p_up"], "edge": wf.get("edge"),
                    "text": f"P(up) {nxt['p_up']:.2f} | {tag} | OOS hit {wf.get('hit_rate', 0):.1%} "
                            f"vs naive {wf.get('naive_hit_rate', 0):.1%}"})
    return res


# ------------------------------------------------------------------ rendering
def render_morning() -> tuple[str, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    t, rows = telemetry(), market_table()
    nw, ol = news(hours=12, per_cat=3), outlook(rows, log=True)
    L = [f"VERTEX 1 | MORNING DISPATCH | {now:%a %d %b %Y %H:%M} UTC", LINE, "SYSTEM & DATA HEALTH"]
    L.append(f"CPU {t.get('cpu_pct', '?')}% | RAM {t.get('ram_pct', '?')}% | Disk {t.get('disk_pct', '?')}%"
             + (f" | DONNA {t['donna']}" if "donna" in t else ""))
    stale = [r["inst"].label for r in rows if r.get("error") or r.get("stale")]
    L.append("Market data: " + ("all current" if not stale else "STALE/MISSING: " + ", ".join(stale)))
    L += ["", "MARKETS (last vs previous close)"]
    for r in rows:
        i = r["inst"]
        L.append(f"{i.label}: {r['source']}" if r.get("error") else
                 f"{i.label}: {fmt(i, r['close'])} ({sp(r['chg_pct'])})  [{r['date']}, {r['source'].split(' ')[0]}]")
    L += ["", "PREDICTOR OUTLOOK (next session, daily close direction)"]
    L += [f"{o['label']}: {o['text']}" for o in ol]
    L += ["", "OVERNIGHT HEADLINES"]
    for cat, items in nw.items():
        L.append(f"{cat.title()}:")
        L += [f" - {x['title']}" for x in items] or [" - none in the last 12h"]
    L += ["", "CRM"] + crm() + ["", "BUSINESS (back office)"] + business() + ["", "DESK PRIORITIES"] + desk()
    L += ["", LINE, "Internal use only. Predictor output is statistical and not a trading instruction."]
    return "\n".join(L), {"telemetry": t, "outlook": ol, "news": nw}


def render_wrap() -> tuple[str, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows = market_table()
    for r in rows:
        if not r.get("error"):
            predictor.score_predictions(r["inst"].key, predictor.completed(r["df"]))
    nw, rec = news(hours=24, per_cat=5), predictor.track_record(60)
    L = [f"VERTEX 1 | DAILY WRAP | {now:%a %d %b %Y} (data to last close)", LINE, "CLOSES"]
    ok = [r for r in rows if not r.get("error")]
    for r in rows:
        i = r["inst"]
        if r.get("error"):
            L.append(f"{i.label}: {r['source']}")
            continue
        ra = r["range_atr"]
        flag = "  << unusual range" if ra is not None and ra == ra and ra > 1.5 else ""
        L.append(f"{i.label}: {fmt(i, r['close'])} | day {sp(r['chg_pct'])} | 5d {sp(r['chg_5d_pct'])} "
                 f"| range {sp(ra, '.1f', 'x ATR')}{flag}" + ("  [STALE]" if r["stale"] else ""))
    if ok:
        movers = sorted([r for r in ok if r["chg_pct"] == r["chg_pct"]], key=lambda r: -abs(r["chg_pct"]))[:3]
        L += ["", "TOP MOVERS: " + ", ".join(f"{r['inst'].label} {r['chg_pct']:+.2f}%" for r in movers)]
    L += ["", "24H NEWS RECAP (ranked by market relevance)"]
    for cat, items in nw.items():
        L.append(f"{cat.title()}:")
        L += [f" - {x['title']}" for x in items] or [" - none"]
    L += ["", "BUSINESS (back office)"] + business()
    L += ["", "PREDICTOR SCORECARD (last 60 days, live predictions only)"]
    L += [f"{x['symbol']}: {x['scored']} scored | hit {x['hit_rate']:.0%} | Brier {x['brier']:.3f} (coin flip = 0.250)"
          for x in rec] or ["No scored predictions yet - the track record starts with the first morning dispatch."]
    L += ["", LINE, "Internal use only."]
    return "\n".join(L), {"news": nw, "track_record": rec}


@register
class BriefingAgent(Agent):
    name = "briefing"
    description = "Vertex 1 morning dispatch and end-of-day wrap; optional delivery to channels."

    def _out(self, kind: str, text: str, send: bool) -> dict[str, Any]:
        res: dict[str, Any] = {"text": text}
        if send:
            if not TARGETS:
                res["delivery"] = {"error": "JARVIS_REPORT_TARGETS not set"}
            else:
                from donna.channels import broadcast

                res["delivery"] = broadcast(text, TARGETS, title=f"Vertex 1 {kind}")
        audit.log("briefing", kind, {"chars": len(text), "delivery": res.get("delivery")})
        return res

    @tool()
    def morning_dispatch(self, send: bool = False) -> dict[str, Any]:
        """Forward-looking morning report (health, markets vs prior close, predictor outlook,
        overnight headlines, CRM, desk priorities). send=True delivers to JARVIS_REPORT_TARGETS."""
        return self._out("morning_dispatch", render_morning()[0], send)

    @tool()
    def daily_wrap(self, send: bool = False) -> dict[str, Any]:
        """Backward-looking end-of-day wrap (closes, daily/5d change, range vs ATR, movers,
        24h news recap, predictor scorecard). send=True delivers to JARVIS_REPORT_TARGETS."""
        return self._out("daily_wrap", render_wrap()[0], send)
