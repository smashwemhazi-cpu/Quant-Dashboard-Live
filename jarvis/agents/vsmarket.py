"""Market vs me: what the market did each day, next to what SH did, and how the gap between the two hit the result.

For every day and symbol SH traded it puts side by side:
  the market   the day's move (close vs the previous close), how wide the day was against its usual range
               (calm / normal / wild), and the trend going INTO the day (yesterday's close vs its 20/50-day averages)
  SH           buys vs sells (count and size), net P&L of the buys and of the sells, re-entries within 5 min of a
               loss, trades bigger than usual
  the gap      "with the market" (net buyer on an up day / net seller on a down day), "against the market",
               "two-way" (no clear side) or "flat day" (the market barely moved), and a plain sentence on what it cost

Over the period it answers: do trades WITH the trend going in do better than trades against it (the trend is
known before you trade, so this is the actionable one), and do trades with the day's move do better than
trades against it (that is hindsight: it tells you whether you read the day, it is not a signal).

`!day [YYYY-MM-DD]` is the daily analysis: one day in detail, with the 4-week rules checked trade by trade.
`!vsmarket [days]` is the trend analysis over a period (default 30 days); the charts tab shows both.

Honesty rules built in (same as the edge report): a group needs MIN_GROUP trades before it is called better or
worse; daily bars come from the watchlist source (TradingView, else Yahoo futures such as GC=F), not the broker's
own feed, and trades are matched to the market day by their UTC date. Nothing here places, changes or closes a
trade, and no Claude credit is needed.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

from jarvis.agents.edge import MIN_GROUP, TILT_MIN, _m, stats, verdict
from jarvis.core.registry import Agent, register, tool

DAYS = int(os.getenv("DONNA_VSMARKET_DAYS", "30"))
STANCE = float(os.getenv("DONNA_VSMARKET_STANCE", "0.2"))     # size-weighted buy/sell lean needed to call a side
FLAT_SHARE = 0.2              # a move under 20% of the usual daily range is a "flat day"
CALM, WILD = 0.7, 1.5         # day range vs its usual range: below CALM is calm, above WILD is wild
BARS_TTL = float(os.getenv("DONNA_CHARTS_MARKET_TTL_MIN", "60")) * 60
MATCH_DAYS = 3                # a trade on a day with no bar (weekend open) counts on the next bar within 3 days

ALIGN = {"with": "with the market", "against": "against the market", "two-way": "two-way", "flat": "flat day"}
DAYS_LABEL = {"with": "Days with the market", "against": "Days against the market", "two-way": "Two-way days (no clear side)",
              "flat": "Flat days (the market barely moved)"}


# ------------------------------------------------------------------ market days
def market_days(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC -> per day: move %, range %, usual range, range vs usual, direction, trend going in."""
    c = df["close"].astype(float)
    prev = c.shift()
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["chg_pct"] = (c / prev - 1) * 100
    out["range_pct"] = (df["high"] - df["low"]) / prev * 100
    out["usual_range"] = out.range_pct.rolling(20, min_periods=5).mean().shift()
    out["range_x"] = out.range_pct / out.usual_range
    s20, s50 = c.rolling(20).mean(), c.rolling(50).mean()
    up = (c > s50) & (s20 > s50)
    down = (c < s50) & (s20 < s50)
    trend = pd.Series(np.where(up, "up", np.where(down, "down", "mixed")), index=df.index)
    trend[s50.isna()] = "unknown"
    out["trend_in"] = trend.shift().fillna("unknown")          # what the chart said BEFORE the day started
    flat = out.chg_pct.abs() < np.where(out.usual_range.notna(), FLAT_SHARE * out.usual_range, 0.1)
    out["direction"] = np.where(flat, "flat", np.where(out.chg_pct > 0, "up", "down"))
    out["mood"] = np.select([out.range_x < CALM, out.range_x > WILD], ["calm", "wild"], "normal")
    out.loc[out.range_x.isna(), "mood"] = "normal"
    return out.dropna(subset=["chg_pct"])


_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def bars(symbol: str) -> tuple[pd.DataFrame, str, str]:
    """(market days, source, display name) for a traded symbol; empty frame when it can't be priced. Cached 1 h."""
    from jarvis.adapters.market import daily_bars, instrument_for

    inst = instrument_for(symbol)
    if inst is None:
        return pd.DataFrame(), "no market data for this symbol", symbol
    name = inst.label.split(" (")[0]
    with _lock:
        hit = _cache.get(inst.key)
        if hit and time.time() - hit[0] < BARS_TTL:
            return hit[1][0], hit[1][1], name
    try:
        df, src = daily_bars(inst, period="2y")
    except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the report
        df, src = pd.DataFrame(), f"unavailable: {type(e).__name__}"
    md = market_days(df) if len(df) > 2 else pd.DataFrame()
    with _lock:   # a failed fetch is retried after 5 minutes, not an hour
        _cache[inst.key] = (time.time() - (BARS_TTL - 300 if md.empty else 0), (md, src))
    return md, src, name


# ------------------------------------------------------------------ joining trades to market days
def joined(days: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Closed trades (from the edge loader) with the market day they belong to. Returns (frame, info)."""
    from jarvis.agents import edge

    d = edge.load(days)
    if d.empty:
        return d, {"error": "NO DATA - no closed trades yet (cTrader feed not connected and no statement uploaded)"}
    gap = (d.start - d.t.shift()).dt.total_seconds() / 60
    d["tilt"] = (d.net.shift() < 0) & (gap < TILT_MIN)
    d["big"] = d["size"].isin(["1.5-3x usual", "over 3x usual"])
    d["date"] = pd.to_datetime(d.start.dt.date)
    parts, sources, names, missing = [], {}, {}, {}
    for sym, g in d.groupby("symbol", sort=False):
        md, src, name = bars(str(sym))
        if md.empty:
            missing[str(sym)] = int(len(g))
            continue
        sources[str(sym)], names[str(sym)] = src, name
        idx = md.index.searchsorted(g.date)                   # same day, or the next bar (weekend opens)
        ok = idx < len(md)
        bar_dates = pd.Series(pd.NaT, index=g.index, dtype="datetime64[ns]")
        bar_dates[ok] = md.index.values[idx[ok]]
        close_enough = (bar_dates - g.date).dt.days <= MATCH_DAYS
        g = g[close_enough].assign(mday=bar_dates[close_enough])
        if not len(g):
            missing[str(sym)] = missing.get(str(sym), 0) + int((~close_enough).sum())
            continue
        g = g.join(md, on="mday")
        g["name"] = name
        parts.append(g)
    info = {"sources": sources, "names": names, "missing": missing, "all_trades": int(len(d))}
    if not parts:
        return pd.DataFrame(), info | {"error": "NO MARKET DATA - none of your traded symbols could be priced right now"}
    j = pd.concat(parts).sort_values("t").reset_index(drop=True)
    j["with_day"] = np.where(j.direction == "flat", "flat day",
                             np.where((j.side == "buy") == (j.direction == "up"), "with the day's move", "against the day's move"))
    j["with_trend"] = np.where(~j.trend_in.isin(["up", "down"]), "no clear trend",
                               np.where((j.side == "buy") == (j.trend_in == "up"), "with the trend", "against the trend"))
    j["trend_side"] = j.apply(lambda r: f"{r.side}s in {_trend_words(r.trend_in)}", axis=1)
    return j, info


def _trend_words(t: str) -> str:
    return {"up": "an uptrend", "down": "a downtrend", "mixed": "a mixed market"}.get(t, "an unknown trend")


# ------------------------------------------------------------------ one day, one symbol
def day_rows(j: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for (mday, sym), g in j.groupby(["mday", "symbol"], sort=True):
        first = g.iloc[0]
        buys, sells = g[g.side == "buy"], g[g.side == "sell"]
        q = g.qty.abs().fillna(0)
        lean = float(((g.side == "buy") * 2 - 1).mul(q).sum() / q.sum()) if q.sum() else 0.0
        stance = "buyer" if lean >= STANCE else "seller" if lean <= -STANCE else "two-way"
        direction = str(first.direction)
        if direction == "flat":
            align = "flat"
        elif stance == "two-way":
            align = "two-way"
        else:
            align = "with" if (stance == "buyer") == (direction == "up") else "against"
        against = g[g.with_day == "against the day's move"]
        r = {"date": pd.Timestamp(mday).date().isoformat(), "symbol": str(sym), "name": str(first["name"]),
             "chg_pct": round(float(first.chg_pct), 2), "range_x": None if pd.isna(first.range_x) else round(float(first.range_x), 2),
             "mood": str(first.mood), "trend_in": str(first.trend_in), "direction": direction,
             "trades": int(len(g)), "buys": int(len(buys)), "sells": int(len(sells)),
             "buy_share": round(len(buys) / len(g) * 100), "lean": round(lean, 2), "stance": stance,
             "net": round(float(g.net.sum()), 2), "net_buys": round(float(buys.net.sum()), 2),
             "net_sells": round(float(sells.net.sum()), 2), "net_against": round(float(against.net.sum()), 2),
             "won": round(float((g.net > 0).mean() * 100)), "tilt": int(g.tilt.sum()),
             "net_tilt": round(float(g.net[g.tilt].sum()), 2), "big": int(g.big.sum()),
             "net_big": round(float(g.net[g.big].sum()), 2), "alignment": align, "alignment_text": ALIGN[align]}
        r["explanation"] = explain(r)
        out.append(r)
    return out


def explain(r: dict[str, Any]) -> str:
    """One plain paragraph: what the market did, what SH did, and how the gap hit the result."""
    mv = ("barely moved" if r["direction"] == "flat" else
          f"{'rose' if r['chg_pct'] > 0 else 'fell'} {abs(r['chg_pct']):.1f}%")
    width = (f", a {r['mood']} day ({r['range_x']:.1f}x its usual range)" if r["range_x"] is not None else "")
    trend = {"up": "the trend going in was up", "down": "the trend going in was down",
             "mixed": "there was no clear trend going in"}.get(r["trend_in"], "")
    s = f"{r['name']} {mv}{width}{'; ' + trend if trend else ''}. "
    side = (f"{r['buys']} buy{'s' if r['buys'] != 1 else ''} and {r['sells']} sell{'s' if r['sells'] != 1 else ''} "
            f"({r['buy_share']}% buys)")
    s += f"You took {r['trades']} trade{'s' if r['trades'] != 1 else ''}: {side}"
    a = r["alignment"]
    if a == "with":
        s += f", so you were {'buying' if r['stance'] == 'buyer' else 'selling'} with the market. "
    elif a == "against":
        s += f", so you were {'buying into a falling' if r['stance'] == 'buyer' else 'selling into a rising'} market. "
    elif a == "two-way":
        s += ", no clear side. "
    else:
        s += ". "
    s += f"Buys {_m(r['net_buys'])}, sells {_m(r['net_sells'])}, net {_m(r['net'])}. "
    if a == "against":
        s += (f"Trading against the move cost {_m(r['net_against'])}." if r["net_against"] < 0 else
              "You went against the move and still came out ahead: quick timing worked, but it is the harder way to win.")
    elif a == "with":
        s += ("Right direction, and it paid." if r["net"] >= 0 else
              "You read the direction right and still lost, so the loss came from entries, exits or size, not the call.")
    elif a == "two-way":
        if r["direction"] != "flat" and r["net_against"] < 0:
            s += f"The trades against the day's move cost {_m(r['net_against'])}."
    else:
        s += "Direction hardly mattered on a flat day; the result came from timing and costs."
    extra = []
    if r["tilt"]:
        extra.append(f"{r['tilt']} trade{'s were' if r['tilt'] != 1 else ' was'} opened within {TILT_MIN} min of a loss "
                     f"({_m(r['net_tilt'])})")
    if r["big"]:
        extra.append(f"{r['big']} {'were' if r['big'] != 1 else 'was'} bigger than your usual size ({_m(r['net_big'])})")
    if extra:
        s += " " + "; ".join(extra)[0].upper() + "; ".join(extra)[1:] + "."
    return s.strip()


# ------------------------------------------------------------------ the period
def _groups(j: pd.DataFrame, key: str, order: list[str]) -> list[dict[str, Any]]:
    out = []
    for k in order:
        g = j[j[key] == k]
        if len(g):
            s = stats(g.net)
            out.append({"group": k, **{x: s[x] for x in ("trades", "net", "win_rate", "expectancy", "t")},
                        "verdict": verdict(s)})
    return out


def weekly(rows: list[dict[str, Any]], j: pd.DataFrame) -> list[dict[str, Any]]:
    """Per week, for the main symbol: the market's week, your buy share and your net."""
    if j.empty:
        return []
    main = j.symbol.value_counts().index[0]
    md, _src, name = bars(str(main))
    g = j[j.symbol == main]
    out = []
    for wk, w in g.groupby(g.mday.dt.to_period("W-SUN")):
        bars_w = md[(md.index >= wk.start_time) & (md.index <= wk.end_time)]
        before = md[md.index < wk.start_time]
        if bars_w.empty or before.empty:
            continue
        chg = (float(bars_w.close.iloc[-1]) / float(before.close.iloc[-1]) - 1) * 100
        buys = int((w.side == "buy").sum())
        share = round(buys / len(w) * 100)
        lean = "buyer" if share >= 60 else "seller" if share <= 40 else "two-way"
        mkt = "up" if chg > 0.3 else "down" if chg < -0.3 else "flat"
        align = ("flat" if mkt == "flat" else "two-way" if lean == "two-way" else
                 "with" if (lean == "buyer") == (mkt == "up") else "against")
        out.append({"week": wk.start_time.date().isoformat(), "symbol": str(main), "name": name,
                    "chg_pct": round(chg, 2), "trades": int(len(w)), "buy_share": share,
                    "net": round(float(w.net.sum()), 2), "alignment": align, "alignment_text": ALIGN[align]})
    return out


def report(days: int = DAYS) -> dict[str, Any]:
    j, info = joined(days)
    if "error" in info:
        return info
    rows = day_rows(j)
    main = str(j.symbol.value_counts().index[0])
    md, src, name = bars(main)
    first = pd.Timestamp(j.mday.min())
    span = md[md.index >= first]
    before = md[md.index < first]
    start_close = float(before.close.iloc[-1]) if len(before) else float(span.close.iloc[0])
    period_chg = (float(span.close.iloc[-1]) / start_close - 1) * 100 if len(span) else None
    mj = j[j.symbol == main]
    by_align = []
    for a in ("with", "against", "two-way", "flat"):
        rs = [r for r in rows if r["alignment"] == a]
        if rs:
            by_align.append({"group": ALIGN[a], "key": a, "days": len(rs), "trades": sum(r["trades"] for r in rs),
                             "net": round(sum(r["net"] for r in rs), 2),
                             "per_day": round(sum(r["net"] for r in rs) / len(rs), 2)})
    x: dict[str, Any] = {
        "from": rows[0]["date"], "to": rows[-1]["date"], "days": days, "min_group": MIN_GROUP,
        "trades": int(len(j)), "all_trades": info["all_trades"], "missing": info["missing"], "sources": info["sources"],
        "main": {"symbol": main, "name": name, "source": src, "chg_pct": None if period_chg is None else round(period_chg, 2),
                 "trend_now": str(md.trend_in.iloc[-1]) if len(md) else "unknown",
                 "up_days": int((span.direction == "up").sum()), "down_days": int((span.direction == "down").sum()),
                 "flat_days": int((span.direction == "flat").sum()),
                 "buy_share": round(float((mj.side == "buy").mean() * 100)), "trades": int(len(mj)),
                 "t": [d.date().isoformat() for d in span.index], "close": [round(float(v), 4) for v in span.close]},
        "by_alignment": by_align,
        "day_move": _groups(j, "with_day", ["with the day's move", "against the day's move", "flat day"]),
        "trend": _groups(j, "with_trend", ["with the trend", "against the trend", "no clear trend"]),
        "trend_side": _groups(j, "trend_side", [f"{s}s in {_trend_words(t)}" for t in ("up", "down", "mixed", "unknown")
                                                for s in ("buy", "sell")]),
        "mood": _groups(j.assign(mood_g=j["mood"] + " days"), "mood_g", ["calm days", "normal days", "wild days"]),
        "rows": rows, "weekly": weekly(rows, j),
    }
    x["bottom_line"] = bottom_line(x)
    return x


def bottom_line(x: dict[str, Any]) -> dict[str, str]:
    m = x["main"]
    if m["chg_pct"] is not None:
        mkt = (f"{m['name']} {'rose' if m['chg_pct'] > 0 else 'fell'} {abs(m['chg_pct']):.1f}% from {x['from']} to {x['to']} "
               f"({m['up_days']} up days, {m['down_days']} down, {m['flat_days']} flat); the trend now is "
               f"{ {'up': 'up', 'down': 'down', 'mixed': 'mixed'}.get(m['trend_now'], 'not clear')}")
    else:
        mkt = f"Not enough {m['name']} prices for the period"
    al = {a["key"]: a for a in x["by_alignment"]}
    me = f"{m['buy_share']}% of your {m['trades']} {m['symbol']} trades were buys"
    w, a = al.get("with"), al.get("against")
    if w or a:
        me += ". " + "; ".join(f"{v['days']} day{'s' if v['days'] != 1 else ''} {v['group']}: {_m(v['net'])}"
                               for v in (w, a) if v)
    tr = {g["group"]: g for g in x["trend"]}
    ws, ag = tr.get("with the trend"), tr.get("against the trend")
    effect, todo = "", ""
    if ws and ag:
        effect = (f"trades with the trend going in: {ws['trades']} trades, {_m(ws['net'])} ({_m(ws['expectancy'])} each); "
                  f"against it: {ag['trades']} trades, {_m(ag['net'])} ({_m(ag['expectancy'])} each)")
        worse = ag if ag["expectancy"] < ws["expectancy"] else ws
        if worse is ag and ag["trades"] >= MIN_GROUP and ag["net"] < 0:
            todo = ("before the first trade, look at the trend label on the Markets tab: in a downtrend only sell or stand "
                    "aside, in an uptrend only buy")
        elif worse is ws and ws["trades"] >= MIN_GROUP and ws["net"] < 0:
            todo = ("fighting the trend is not what costs you: going with it lost more, so look at entries, size and "
                    "re-entries (the My edge tab) before direction")
    dm = {g["group"]: g for g in x["day_move"]}
    agd = dm.get("against the day's move")
    if agd and agd["net"] < 0:
        effect += ("; " if effect else "") + f"trades against the day's move cost {_m(agd['net'])} in total"
    if not todo:
        todo = ("no clear direction problem yet: keep the trend label in view and recheck with more trades"
                if x["trades"] < 4 * MIN_GROUP else "direction is not the main leak: the rules on the My edge tab come first")
    caveat = ("the trend going in is known before you trade, the day's move only after it closes (hindsight). "
              f"Prices are daily bars from {m['source']}, matched to your trades by UTC date: a guide, not a promise")
    cap = lambda s: s[:1].upper() + s[1:] if s else s  # noqa: E731
    return {"market": cap(mkt), "me": cap(me), "effect": cap(effect or "too few trades on each side to compare yet"),
            "todo": cap(todo), "caveat": cap(caveat)}


# ------------------------------------------------------------------ one day in detail (!day)
def day_detail(j: pd.DataFrame, day: str) -> dict[str, Any]:
    """Every symbol traded that day vs its market, plus the 4-week rules checked trade by trade."""
    from jarvis.agents import edge

    g = j[j.mday.dt.date.astype(str) == day].copy()
    if g.empty:
        return {"error": f"no trades on {day}"}
    rows = day_rows(g)
    g = g.sort_values("t").reset_index(drop=True)
    g["day"] = g.mday.dt.date
    g["nth"] = range(1, len(g) + 1)
    rules = []
    if len(g) > edge.MAX_TRADES_DAY:
        over = g.net[g.nth > edge.MAX_TRADES_DAY]
        rules.append({"rule": f"At most {edge.MAX_TRADES_DAY} trades a day", "kept": False,
                      "text": f"{len(g)} trades: the {len(over)} after number {edge.MAX_TRADES_DAY} made {_m(float(over.sum()))}"})
    else:
        rules.append({"rule": f"At most {edge.MAX_TRADES_DAY} trades a day", "kept": True, "text": f"{len(g)} trades"})
    keep = edge._cooldown(g)
    broke = g.net[~keep]
    rules.append({"rule": f"Wait {edge.COOLDOWN_MIN:g} min after a loss", "kept": not len(broke),
                  "text": (f"broken {len(broke)} time{'s' if len(broke) != 1 else ''}; those trades made {_m(float(broke.sum()))}"
                           if len(broke) else "kept")})
    ks = edge._loss_streak(g)
    after = g.net[~ks]
    rules.append({"rule": f"Stop after {edge.MAX_LOSS_STREAK} losses in a row", "kept": not len(after),
                  "text": (f"the stop came after trade {int(g.nth[~ks].iloc[0]) - 1}; the {len(after)} trades after it made "
                           f"{_m(float(after.sum()))}" if len(after) else f"never {edge.MAX_LOSS_STREAK} losses in a row")})
    kd = edge._daily_stop(g)
    afterd = g.net[~kd]
    rules.append({"rule": f"Stop once down {edge.DAILY_STOP_PCT:g}% of the balance", "kept": not len(afterd),
                  "text": (f"reached at trade {int(g.nth[~kd].iloc[0]) - 1}; the {len(afterd)} trades after it made "
                           f"{_m(float(afterd.sum()))}" if len(afterd) else "not reached")})
    cap = g.bal_before * edge.LOSS_CAP_PCT / 100
    big_loss = g[(g.net < 0) & cap.notna() & (-g.net > cap)]
    rules.append({"rule": f"No single loss over {edge.LOSS_CAP_PCT:g}% of the balance", "kept": big_loss.empty,
                  "text": (f"{len(big_loss)} loss{'es' if len(big_loss) != 1 else ''} over the cap, worst {_m(float(big_loss.net.min()))}"
                           if len(big_loss) else "kept")})
    s = stats(g.net)
    return {"date": day, "trades": s["trades"], "net": s["net"], "win_rate": s["win_rate"],
            "largest_loss": round(float(g.net.min()), 2) if (g.net < 0).any() else 0.0,
            "symbols": rows, "rules": rules, "rules_kept": sum(r["kept"] for r in rules), "rules_total": len(rules)}


def day_report(day: str = "", days: int = 120) -> dict[str, Any]:
    j, info = joined(days)
    if "error" in info:
        return info
    have = sorted(j.mday.dt.date.astype(str).unique())
    if not day:
        day = have[-1]
    if day not in have:
        earlier = [d for d in have if d <= day]
        return {"error": f"no trades on {day}" + (f"; the nearest earlier day with trades is {earlier[-1]}" if earlier else "")}
    return day_detail(j, day) | {"days_with_trades": have[-60:]}


# ------------------------------------------------------------------ text (Discord)
def report_lines(days: int = DAYS) -> list[str]:
    x = report(days)
    if "error" in x:
        return [f"MARKET VS ME: {x['error']}"]
    bl = x["bottom_line"]
    L = [f"MARKET VS ME - {x['from']} to {x['to']}, {x['trades']} trades", "",
         "BOTTOM LINE", f" The market: {bl['market']}.", f" You: {bl['me']}.", f" How it hit you: {bl['effect']}.",
         f" Try this: {bl['todo']}.", ""]
    L.append(f"1. TREND GOING IN (known before you trade; needs {x['min_group']}+ trades to call it)")
    L += [f"  {g['group']:<24} {g['trades']:>4} trades  {_m(g['net']):>10}  won {g['win_rate']:>3.0f}%  "
          f"per trade {_m(g['expectancy'])} - {g['verdict']}" for g in x["trend_side"]]
    L += ["", "2. THE DAY'S MOVE (hindsight: did you read the day?)"]
    L += [f"  {g['group']:<24} {g['trades']:>4} trades  {_m(g['net']):>10}  won {g['win_rate']:>3.0f}%  "
          f"per trade {_m(g['expectancy'])} - {g['verdict']}" for g in x["day_move"]]
    L += [f"  {DAYS_LABEL[a['key']]}: {a['days']}, net {_m(a['net'])} ({_m(a['per_day'])} a day)" for a in x["by_alignment"]]
    L += ["", "3. CALM, NORMAL AND WILD DAYS (day range vs its usual range)"]
    L += [f"  {g['group']:<24} {g['trades']:>4} trades  {_m(g['net']):>10}  per trade {_m(g['expectancy'])} - {g['verdict']}"
          for g in x["mood"]]
    if x["weekly"]:
        L += ["", f"4. WEEK BY WEEK ({x['main']['name']})"]
        L += [f"  week of {w['week']}: market {w['chg_pct']:+.1f}%, you {w['buy_share']}% buys, {w['trades']} trades, "
              f"{_m(w['net'])} - {w['alignment_text']}" for w in x["weekly"][-6:]]
    L += ["", "5. LAST 5 DAYS"]
    for r in x["rows"][-5:]:
        L.append(f"  {r['date']} {r['symbol']}: {r['explanation']}")
    if x["missing"]:
        L += ["", " Not compared (no market prices): " + ", ".join(f"{k} ({v} trades)" for k, v in x["missing"].items())]
    L += ["", f" {bl['caveat']}.", " One day in detail with your rules checked: !day or !day 2026-11-06"]
    return L


def day_lines(day: str = "") -> list[str]:
    x = day_report(day)
    if "error" in x:
        return [f"MY DAY VS THE MARKET: {x['error']}"]
    L = [f"MY DAY VS THE MARKET - {x['date']} (UTC)",
         f" {x['trades']} trades, net {_m(x['net'])}, won {x['win_rate']:.0f}%, largest loss {_m(x['largest_loss'])}", ""]
    for r in x["symbols"]:
        L += [f"{r['symbol']} - {r['alignment_text'].upper()}", f" {r['explanation']}", ""]
    L.append(f"YOUR RULES TODAY ({x['rules_kept']} of {x['rules_total']} kept)")
    L += [f" {'OK ' if r['rule'] and r['kept'] else 'X  '}{r['rule']}: {r['text']}" for r in x["rules"]]
    L += ["", " Prices are daily bars, matched by UTC date: a guide, not a promise. The period view: !vsmarket"]
    return L


def evening_lines() -> list[str]:
    """Two lines for the evening digest: today's trading vs the market, when SH traded today."""
    x = day_report()
    if "error" in x or x["date"] != pd.Timestamp.now(tz="UTC").date().isoformat():
        return []
    r = max(x["symbols"], key=lambda r: r["trades"])
    return [f" Vs the market: {r['name']} {r['chg_pct']:+.1f}%, you {r['buy_share']}% buys -> {r['alignment_text']}; "
            f"rules kept {x['rules_kept']}/{x['rules_total']}. Detail: !day"]


def command(cmd: str, rest: str) -> str | None:
    arg = rest.strip().lower()
    if cmd in ("!day", "!myday", "!dayvs"):
        if arg in ("", "today"):
            day = "" if not arg else pd.Timestamp.now(tz="UTC").date().isoformat()
        elif arg == "yesterday":
            day = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)).date().isoformat()
        else:
            try:
                day = pd.Timestamp(arg).date().isoformat()
            except ValueError:
                return "Use !day, !day yesterday or !day 2026-11-06"
        return "\n".join(day_lines(day))
    if cmd in ("!vsmarket", "!divergence", "!mvm", "!marketvsme"):
        return "\n".join(report_lines(int(arg) if arg.isdigit() and 5 <= int(arg) <= 365 else DAYS))
    return None


def _json(v: Any) -> Any:
    """NaN/inf -> None so the charts API always returns valid JSON."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json(x) for x in v]
    return v


def charts_payload(days: int = 90) -> dict[str, Any]:
    """The 'Market vs me' tab: the period report plus each day's rule check."""
    x = report(days)
    if "error" in x:
        return x
    j, _ = joined(days)
    x["detail"] = {r["date"]: day_detail(j, r["date"]) for r in x["rows"][-45:]}
    return _json(x)


@register
class VsMarketAgent(Agent):
    name = "vsmarket"
    description = "What the market did each day next to what SH did (buys vs sells), the divergence, and what it cost."

    @tool()
    def market_vs_me(self, days: int = DAYS) -> dict[str, Any]:
        """Per day and symbol: the market's move, range and trend going in vs SH's buy/sell mix and P&L, whether SH
        traded with or against the market, and over the period how trades with vs against the trend did."""
        return _json(report(days))

    @tool()
    def my_day(self, day: str = "") -> dict[str, Any]:
        """One trading day (YYYY-MM-DD, default the latest with trades): each symbol vs its market, plus the
        4-week rules (max trades, cool-down, loss streak, daily stop, 1% loss cap) checked trade by trade."""
        return _json(day_report(day))
