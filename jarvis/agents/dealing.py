"""AI dealing desk: LP accountability, trader classification, A/B book, daily desk report.

Input tables (see adapters/sources.py for the column contract):
  fills   one row per client trade: request/fill times and prices, LP, quote age,
          mid-price markouts at +1s/+5s/+60s, revenue and the trade's closed P&L.
  events  economic calendar (ts, event, impact).

Beyond a typical risk SaaS this adds:
  * slippage *episodes* per LP+symbol with start, end, duration, volume affected,
    cost by order type and an evidence-based cause (LP-specific vs stale feed vs
    news vs fast market) - the "what caused it, when, how long" question;
  * LP asymmetry: an LP that only ever passes on negative slippage is visible;
  * markout-based toxicity (normally a premium feature) and a significance test
    before any account is proposed for B-book, so the desk never flips on noise.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core.registry import Agent, register, tool

SLIP_EPISODE_PIPS = 0.5      # bucket mean adverse slippage that counts as "bad"
EPISODE_BUCKET_MIN = 15      # sized for a desk doing a few hundred fills a day; shrink for busier books
EPISODE_MIN_TRADES = 2
EPISODE_GAP_MIN = 15         # merge bad buckets separated by <= this many minutes
STALE_QUOTE_MS = 400
NEWS_WINDOW_MIN = 5
MIN_TRADES_FOR_VERDICT = 30


def prepare(fills: pd.DataFrame) -> pd.DataFrame:
    d = fills.copy()
    d["ts_request"] = pd.to_datetime(d["ts_request"])
    d["ts_fill"] = pd.to_datetime(d["ts_fill"])
    d["sign"] = d["side"].str.lower().map({"buy": 1, "sell": -1})
    # + = adverse to the client (paid more on a buy / received less on a sell)
    d["slip_pips"] = (d["fill_price"] - d["request_price"]) * d["sign"] / d["pip_size"]
    d["slip_cost_usd"] = (d["fill_price"] - d["request_price"]) * d["sign"] * d["units"] * d["quote_to_usd"]
    d["exec_ms"] = (d["ts_fill"] - d["ts_request"]).dt.total_seconds() * 1000
    for h in ("1s", "5s", "60s"):
        # + = market moved in the client's favour right after the fill (toxic for a B-book)
        d[f"markout_{h}_pips"] = (d[f"mid_{h}"] - d["mid_fill"]) * d["sign"] / d["pip_size"]
    d["notional_usd"] = d["units"] * d["mid_fill"] * d["quote_to_usd"]
    d["revenue_usd"] = d["markup_usd"] + d["commission_usd"]
    return d


def _near_event(ts: pd.Series, events: pd.DataFrame, minutes: int = NEWS_WINDOW_MIN) -> pd.Series:
    if events.empty:
        return pd.Series("", index=ts.index)
    ev = events.assign(ts=pd.to_datetime(events["ts"]).astype("datetime64[ns]")).sort_values("ts")
    s = pd.DataFrame({"ts": pd.to_datetime(ts).astype("datetime64[ns]").values, "i": ts.index}).sort_values("ts")
    m = pd.merge_asof(s, ev[["ts", "event"]], on="ts", direction="nearest",
                      tolerance=pd.Timedelta(minutes=minutes))
    return m.set_index("i")["event"].fillna("").reindex(ts.index)


# ---------------------------------------------------------------- slippage
def slippage_episodes(d: pd.DataFrame, events: pd.DataFrame) -> list[dict[str, Any]]:
    d = d.copy()
    d["bucket"] = d["ts_fill"].dt.floor(f"{EPISODE_BUCKET_MIN}min")
    g = d.groupby(["lp", "symbol", "bucket"]).agg(n=("slip_pips", "size"), slip=("slip_pips", "mean")).reset_index()
    bad = g[(g["n"] >= EPISODE_MIN_TRADES) & (g["slip"] > SLIP_EPISODE_PIPS)].sort_values(["lp", "symbol", "bucket"])
    if bad.empty:
        return []
    new = (bad["bucket"].diff() > pd.Timedelta(minutes=EPISODE_GAP_MIN)) | \
          (bad["lp"] != bad["lp"].shift()) | (bad["symbol"] != bad["symbol"].shift())
    bad = bad.assign(ep=new.cumsum())

    base_age = d["quote_age_ms"].median()
    base_move = d["markout_60s_pips"].abs().median()
    out = []
    for _, b in bad.groupby("ep"):
        lp, sym = b["lp"].iloc[0], b["symbol"].iloc[0]
        start, end = b["bucket"].min(), b["bucket"].max() + pd.Timedelta(minutes=EPISODE_BUCKET_MIN)
        win = d[(d["symbol"] == sym) & (d["ts_fill"] >= start) & (d["ts_fill"] < end)]
        mine, others = win[win["lp"] == lp], win[win["lp"] != lp]
        bad_fills = mine[mine["slip_pips"] > SLIP_EPISODE_PIPS]
        if len(bad_fills):  # tighten the reported window to the first/last slipped fill
            start, end = bad_fills["ts_fill"].min().floor("1min"), bad_fills["ts_fill"].max().ceil("1min")

        causes = []
        if len(others) >= 1 and others["slip_pips"].mean() < 0.5 * mine["slip_pips"].mean():
            causes.append(f"LP-specific: other LPs averaged {others['slip_pips'].mean():.2f} pips in the same window")
        if mine["quote_age_ms"].median() > max(STALE_QUOTE_MS, 3 * base_age):
            causes.append(f"stale price feed: median quote age {mine['quote_age_ms'].median():.0f} ms")
        ev = _near_event(pd.Series([start + (end - start) / 2]), events,
                         NEWS_WINDOW_MIN + int((end - start).total_seconds() // 120)).iloc[0]
        if ev:
            causes.append(f"news: {ev}")
        if base_move > 0 and win["markout_60s_pips"].abs().mean() > 3 * base_move:
            causes.append("fast market: 60s price moves > 3x normal")
        out.append({
            "lp": lp, "symbol": sym, "start": str(start), "end": str(end),
            "duration_min": int((end - start).total_seconds() // 60),
            "trades": int(len(mine)), "volume_usd": round(float(mine["notional_usd"].sum())),
            "avg_slip_pips": round(float(mine["slip_pips"].mean()), 2),
            "cost_to_clients_usd": round(float(mine["slip_cost_usd"].sum()), 2),
            "by_order_type": mine.groupby("order_type")["slip_cost_usd"].sum().round(2).to_dict(),
            "probable_cause": causes or ["unexplained - raise with LP with this evidence"],
            # News/fast-market slippage is the market; LP-specific, stale-feed or unexplained is a claim.
            "raise_with_lp": not any(c.startswith(("news", "fast market")) for c in causes),
        })
    return sorted(out, key=lambda x: (not x["raise_with_lp"], -x["cost_to_clients_usd"]))


def lp_scorecard(d: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for lp, x in d.groupby("lp"):
        slipped = x[x["slip_pips"].abs() > 1e-9]
        rows.append({
            "lp": lp, "fills": int(len(x)), "volume_usd": round(float(x["notional_usd"].sum())),
            "avg_slip_pips": round(float(x["slip_pips"].mean()), 3),
            "p95_slip_pips": round(float(x["slip_pips"].quantile(.95)), 3),
            "slip_cost_usd": round(float(x["slip_cost_usd"].sum()), 2),
            # Symmetric slippage ~50%. Well above = LP keeps positive slippage, passes on negative.
            "adverse_share_of_slipped_pct": round(float((slipped["slip_pips"] > 0).mean() * 100), 1) if len(slipped) else None,
            "p95_exec_ms": round(float(x["exec_ms"].quantile(.95))),
            "median_quote_age_ms": round(float(x["quote_age_ms"].median())),
        })
    return sorted(rows, key=lambda r: -r["slip_cost_usd"])


# ---------------------------------------------------------------- traders
def classify_traders(d: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["news"] = _near_event(d["ts_request"], events) != ""
    a = d.groupby("account").agg(
        group=("group", "last"), equity=("equity", "last"), trades=("account", "size"),
        volume_usd=("notional_usd", "sum"), client_pnl=("client_pnl_usd", "sum"),
        pnl_mean=("client_pnl_usd", "mean"), pnl_std=("client_pnl_usd", "std"),
        win_rate=("client_pnl_usd", lambda s: (s > 0).mean() * 100),
        revenue=("revenue_usd", "sum"), hold_med_s=("hold_seconds", "median"),
        short_hold_pct=("hold_seconds", lambda s: (s < 60).mean() * 100),
        news_pct=("news", lambda s: s.mean() * 100),
        mk1=("markout_1s_pips", "mean"), mk5=("markout_5s_pips", "mean"), mk60=("markout_60s_pips", "mean"),
    )
    # t-stat of mean P&L per trade: only call someone a consistent winner/loser if |t| > 2
    a["pnl_t"] = a["pnl_mean"] / (a["pnl_std"] / np.sqrt(a["trades"])).replace(0, np.nan)

    def label(r: pd.Series) -> tuple[str, list[str]]:
        flags = []
        if r.mk1 > 0.3 and r.short_hold_pct > 50:
            flags.append("latency arbitrage (profits within 1s of fill)")
        if r.news_pct > 40:
            flags.append(f"news trading ({r.news_pct:.0f}% of trades near releases)")
        if r.short_hold_pct > 60 and not flags:
            flags.append("scalper")
        if r.trades < MIN_TRADES_FOR_VERDICT:
            return "insufficient data", flags
        if any(f.startswith("latency") for f in flags):
            return "toxic", flags
        if r.pnl_t > 2:
            return "consistent winner", flags
        if r.pnl_t < -2:
            return "consistent loser", flags
        return "neutral", flags

    lab = a.apply(label, axis=1, result_type="expand")
    a["profile"], a["flags"] = lab[0], lab[1]
    a["toxicity"] = (40 * (a["mk1"] / 0.5).clip(0, 1) + 20 * (a["mk60"] / 2).clip(0, 1)
                     + 20 * (a["short_hold_pct"] / 100) + 20 * (a["news_pct"] / 100)).round(1)

    def book(r: pd.Series) -> str:
        if r.profile == "toxic":
            return "A-book (hedge) + review execution terms"
        if r.profile == "consistent winner":
            return "A-book"
        if r.profile == "consistent loser" and r.toxicity < 30:
            return "B-book candidate"
        if r.profile == "neutral" and r.toxicity < 20:
            return "hybrid 50/50"
        return "A-book (default)"

    a["recommendation"] = a.apply(book, axis=1)
    # B-book P&L = -client P&L + revenue (spread markup & commission still earned); A-book = revenue.
    a["b_minus_a_usd"] = (-a["client_pnl"]).round(2)
    return a.round(3).sort_values("toxicity", ascending=False)


def _session(d: pd.DataFrame, day: str) -> pd.DataFrame:
    if not day:
        day = str(d["ts_fill"].dt.date.max())
    return d[d["ts_fill"].dt.date.astype(str) == day]


@register
class DealingAgent(Agent):
    name = "dealing"
    description = "AI dealing desk: LP slippage root cause, trader toxicity, A/B book, daily desk report."

    def _data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        src = default_source()
        try:
            events = src.load("events")
        except FileNotFoundError:
            events = pd.DataFrame(columns=["ts", "event", "impact"])
        return prepare(src.load("fills")), events

    @tool()
    def slippage(self, day: str = "", lp: str = "") -> dict[str, Any]:
        """Slippage root-cause report for one day ('YYYY-MM-DD', default latest) or
        all data if day='all'. Returns an LP scorecard (including whether an LP passes
        on only adverse slippage) and slippage episodes with start/end/duration,
        affected volume, cost by order type and probable cause."""
        d, ev = self._data()
        if day != "all":
            d = _session(d, day)
        if lp:
            d = d[d["lp"].str.lower() == lp.lower()]
        return {"lp_scorecard": lp_scorecard(d), "episodes": slippage_episodes(d, ev)[:20]}

    @tool()
    def slippage_week_compare(self) -> list[dict[str, Any]]:
        """Daily slippage cost and average adverse slippage per LP for the last 7 days."""
        d, _ = self._data()
        d = d[d["ts_fill"] >= d["ts_fill"].max().normalize() - pd.Timedelta(days=6)]
        t = d.groupby([d["ts_fill"].dt.date.astype(str), "lp"]).agg(
            cost_usd=("slip_cost_usd", "sum"), avg_slip=("slip_pips", "mean"), fills=("lp", "size"))
        return t.round(3).reset_index().rename(columns={"ts_fill": "day"}).to_dict("records")

    @tool()
    def traders(self, min_toxicity: float = 0, recommendation: str = "", limit: int = 30) -> list[dict[str, Any]]:
        """Classify traders (toxic / consistent winner / consistent loser / neutral) with
        markouts, holding time, news share, toxicity 0-100 and an A/B/hybrid book
        recommendation plus what B-booking would have earned vs A-booking."""
        d, ev = self._data()
        t = classify_traders(d, ev)
        t = t[t["toxicity"] >= min_toxicity]
        if recommendation:
            t = t[t["recommendation"].str.lower().str.contains(recommendation.lower())]
        cols = ["group", "equity", "trades", "client_pnl", "win_rate", "hold_med_s", "mk1", "mk60",
                "news_pct", "pnl_t", "toxicity", "profile", "flags", "recommendation", "b_minus_a_usd"]
        return t[cols].head(limit).reset_index().to_dict("records")

    @tool()
    def account_report(self, account: str) -> dict[str, Any]:
        """One-click trader report (e.g. before approving a withdrawal): activity,
        P&L, revenue, behaviour flags, toxicity and book recommendation."""
        d, ev = self._data()
        t = classify_traders(d, ev)
        if account not in t.index:
            return {"error": f"no trades for account {account}"}
        r = t.loc[account].to_dict()
        x = d[d["account"] == account]
        r["symbols"] = x.groupby("symbol")["client_pnl_usd"].sum().round(2).to_dict()
        r["first_trade"], r["last_trade"] = str(x["ts_fill"].min()), str(x["ts_fill"].max())
        r["withdrawal_flags"] = [f for f in r["flags"]] + (
            ["profit concentrated near news"] if r["news_pct"] > 40 and r["client_pnl"] > 0 else [])
        return {"account": account, **{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}}

    @tool()
    def desk_report(self, day: str = "") -> dict[str, Any]:
        """Morning desk report for the previous session: revenue, active traders, volume,
        net client exposure per symbol, slippage episodes, newly toxic accounts and
        recommended actions."""
        d, ev = self._data()
        s = _session(d, day)
        exp = s.assign(net=s["sign"] * s["notional_usd"]).groupby("symbol")["net"].sum().round(0)
        eps = slippage_episodes(s, ev)
        tr = classify_traders(d, ev)
        toxic_today = tr[(tr["profile"] == "toxic") & tr.index.isin(s["account"].unique())]
        actions = [f"Raise {e['lp']} {e['symbol']} {e['start'][11:16]}-{e['end'][11:16]}: "
                   f"{e['avg_slip_pips']} pips avg, ${e['cost_to_clients_usd']:,.2f} cost; {e['probable_cause'][0]}"
                   for e in eps if e["raise_with_lp"]][:5]
        actions += [f"Review {a}: {', '.join(r['flags']) or 'toxic'} -> {r['recommendation']}"
                    for a, r in toxic_today.head(5).iterrows()]
        bb = tr[tr["recommendation"] == "B-book candidate"]
        if len(bb):
            actions.append(f"{len(bb)} B-book candidates; switching would have added ${bb['b_minus_a_usd'].sum():,.0f} "
                           "over the period - compliance sign-off required before any move")
        return {
            "session": str(s["ts_fill"].dt.date.max()) if len(s) else day,
            "revenue_usd": round(float(s["revenue_usd"].sum()), 2),
            "client_pnl_usd": round(float(s["client_pnl_usd"].sum()), 2),
            "active_traders": int(s["account"].nunique()),
            "volume_usd": round(float(s["notional_usd"].sum())),
            "net_client_exposure_usd": exp.to_dict(),
            "slippage_episodes": len(eps),
            "slippage_cost_usd": round(float(s["slip_cost_usd"].sum()), 2),
            "recommended_actions": actions,
        }
