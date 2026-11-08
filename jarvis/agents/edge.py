"""Trading edge: where SH's own trades make and lose money, and which simple rules would have helped.

Reads only DONNA's own journal (pt_trades, filled by the read-only cTrader feed or uploaded statements) and,
where it exists, the opening time of each position from the PAD records (pad_positions / pad_closes).
Nothing here places, changes or closes a trade, and no Claude credit is needed.

What it answers, in plain words:
  1. Is there an edge?   win rate vs the break-even win rate that the average win / average loss demands
  2. Where is it?        by symbol, session (UTC), direction, weekday, trade size, hold time, trade number in the day
  3. Behaviour           trades right after a loss (tilt), after 2+ losses in a row, sizing up after a loss
  4. The loss tail       how much of the damage comes from the worst few losses
  5. Rule replay         "what if" each rule had been followed on the same history, and whether it helped in BOTH
                         halves of the period (a rule that only helps in one half is probably luck / curve-fitting)

Honesty rules built in:
  - a group needs MIN_GROUP trades before it is called better or worse; below that it says "too few to tell"
  - each group gets a t-score (mean / standard error); |t| >= 2 is "clear", 1-2 "maybe", below 1 "noise"
  - capping losses in the replay assumes the stop fills at the cap: it is the best case, real fills are worse
"""
from __future__ import annotations

import math
import os
from typing import Any, Callable

import numpy as np
import pandas as pd

from jarvis.core.registry import Agent, register, tool

DAYS = int(os.getenv("DONNA_EDGE_DAYS", "90"))
MIN_GROUP = int(os.getenv("DONNA_EDGE_MIN_GROUP", "30"))
COOLDOWN_MIN = float(os.getenv("DONNA_EDGE_COOLDOWN_MIN", "15"))
MAX_TRADES_DAY = int(os.getenv("DONNA_EDGE_MAX_TRADES_DAY", "10"))
DAILY_STOP_PCT = float(os.getenv("DONNA_EDGE_DAILY_STOP_PCT", "2"))
MAX_LOSS_STREAK = int(os.getenv("DONNA_EDGE_MAX_LOSS_STREAK", "3"))
LOSS_CAP_PCT = float(os.getenv("DONNA_EDGE_LOSS_CAP_PCT", "1"))
TILT_MIN = 5                     # same window the KPI scorecard uses for "re-entry after a loss"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOLD_BUCKETS = [(0, 1, "under 1 min"), (1, 5, "1-5 min"), (5, 30, "5-30 min"), (30, 240, "30 min-4 h"),
                (240, 1440, "4-24 h"), (1440, math.inf, "over a day")]


# ------------------------------------------------------------------ data
def load(days: int = DAYS, account: str = "") -> pd.DataFrame:
    """Closed trades, oldest first, with open time (when known), balance before, session and size class."""
    from jarvis.agents import personal

    with personal._db() as c:
        if not account:
            row = c.execute("SELECT account FROM pt_trades ORDER BY closed_utc DESC LIMIT 1").fetchone()
            account = os.getenv("JARVIS_PERSONAL_ACCOUNT") or (row[0] if row else "")
        d = pd.read_sql_query("SELECT * FROM pt_trades WHERE account = ? ORDER BY closed_utc", c, params=(account,))
        try:   # opening times come from the PAD records of the live feed; statements don't carry them
            opens = pd.read_sql_query(
                "SELECT k.closed_utc AS ck, k.net AS nk, p.opened_utc FROM pad_closes k JOIN pad_positions p "
                "ON p.login = k.login AND p.position_id = k.position_id WHERE p.opened_utc IS NOT NULL", c)
        except Exception:  # noqa: BLE001 - no PAD tables yet on a fresh server
            opens = pd.DataFrame(columns=["ck", "nk", "opened_utc"])
    if d.empty:
        return d
    d["t"] = pd.to_datetime(d.closed_utc, format="mixed")
    d = d[d.t >= d.t.max() - pd.Timedelta(days=days)].reset_index(drop=True)
    d["ck"], d["nk"] = d.closed_utc.str[:19], d.net.round(2)
    if "opened_utc" not in d:
        d["opened_utc"] = None
    if len(opens):     # trades stored before pt_trades kept the open time: take it from the PAD records
        opens = opens.assign(nk=opens.nk.round(2)).drop_duplicates(["ck", "nk"]).rename(columns={"opened_utc": "pad_open"})
        d = d.merge(opens, on=["ck", "nk"], how="left")
        d["opened_utc"] = d.opened_utc.fillna(d.pad_open)
        d = d.drop(columns=["pad_open"])
    d["opened"] = pd.to_datetime(d.opened_utc, format="mixed", errors="coerce")
    d["hold_min"] = (d.t - d.opened).dt.total_seconds() / 60
    d["start"] = d.opened.fillna(d.t)   # when the decision was made (close time if the open is unknown)
    d["bal_before"] = (d.balance - d.net).where(lambda s: s > 0)
    d["side"] = d.side.astype(str).str.lower().map(lambda s: "buy" if s.startswith("b") else "sell")
    d["session"] = d.start.dt.hour.map(_session)
    d["weekday"] = d.start.dt.dayofweek.map(lambda i: WEEKDAYS[i])
    d["day"] = d.start.dt.date
    med = d.groupby("symbol").qty.transform("median").replace(0, np.nan)
    rel = d.qty / med
    d["size"] = np.select([rel < 0.75, rel <= 1.5, rel <= 3], ["smaller than usual", "usual size", "1.5-3x usual"],
                          "over 3x usual")
    d["nth"] = d.groupby("day").cumcount() + 1
    return d.drop(columns=["ck", "nk"]).reset_index(drop=True)


def _session(h: int) -> str:
    from jarvis.agents.personal import _session as s

    return s(int(h))


# ------------------------------------------------------------------ statistics
def stats(n: pd.Series) -> dict[str, Any]:
    n = n.dropna()
    wins, losses = n[n > 0], n[n < 0]
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(losses.mean()) if len(losses) else 0.0
    payoff = avg_w / -avg_l if avg_l else None
    sd = float(n.std(ddof=1)) if len(n) > 1 else 0.0
    curve = n.cumsum()
    return {"trades": int(len(n)), "net": round(float(n.sum()), 2),
            "win_rate": round(float((n > 0).mean() * 100), 1) if len(n) else 0.0,
            "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "payoff": None if payoff is None else round(payoff, 2),
            "breakeven_win_rate": None if payoff is None else round(100 / (1 + payoff), 1),
            "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) else None,
            "expectancy": round(float(n.mean()), 3) if len(n) else 0.0,
            "t": round(float(n.mean() / (sd / math.sqrt(len(n)))), 2) if sd > 0 and len(n) > 1 else 0.0,
            "max_drawdown": round(float((curve - curve.cummax()).min()), 2) if len(n) else 0.0}


def verdict(s: dict[str, Any]) -> str:
    if s["trades"] < MIN_GROUP:
        return "too few to tell"
    t = s["t"]
    if t >= 2:
        return "clear edge"
    if t <= -2:
        return "clear leak"
    if abs(t) >= 1:
        return "maybe " + ("edge" if t > 0 else "leak")
    return "noise"


def by(d: pd.DataFrame, key: str, order: list[str] | None = None) -> list[dict[str, Any]]:
    out = []
    for k, g in d.groupby(key, sort=False):
        s = stats(g.net)
        out.append({"group": str(k), **s, "verdict": verdict(s)})
    if order:
        rank = {v: i for i, v in enumerate(order)}
        return sorted(out, key=lambda r: rank.get(r["group"], 99))
    return sorted(out, key=lambda r: r["net"])


def behaviour(d: pd.DataFrame) -> dict[str, Any]:
    """How trades go right after a loss, compared with all the others."""
    prev_loss = d.net.shift() < 0
    gap = (d.start - d.t.shift()).dt.total_seconds() / 60
    tilt = prev_loss & (gap < TILT_MIN)
    streak = (d.net < 0).astype(int)
    run = streak.groupby((streak == 0).cumsum()).cumsum().shift().fillna(0)   # losses in a row before this trade
    same = d.symbol == d.symbol.shift()
    size_up = prev_loss & same & (d.qty > d.qty.shift() * 1.2)
    rest = ~tilt
    return {"after_loss_fast": stats(d.net[tilt]), "others": stats(d.net[rest]),
            "after_2_losses": stats(d.net[run >= 2]), "after_a_win": stats(d.net[d.net.shift() > 0]),
            "size_up_after_loss": stats(d.net[size_up])}


def loss_tail(d: pd.DataFrame) -> dict[str, Any]:
    losses = d.net[d.net < 0].sort_values()
    if losses.empty:
        return {"losses": 0}
    k = max(1, int(math.ceil(len(losses) * 0.1)))
    worst = losses.iloc[:k]
    cap = (d.bal_before * LOSS_CAP_PCT / 100)
    over = d[(d.net < 0) & (-d.net > cap)]
    big = d.loc[losses.index[0]]
    pct = -big.net / big.bal_before * 100 if pd.notna(big.bal_before) else None
    return {"losses": int(len(losses)), "worst_10pct_count": k, "worst_10pct_sum": round(float(worst.sum()), 2),
            "worst_10pct_share": round(float(worst.sum() / losses.sum() * 100), 1),
            "largest": round(float(losses.iloc[0]), 2), "largest_when": big.t.strftime("%d %b"),
            "largest_symbol": str(big.symbol),
            "largest_balance_before": None if pct is None else round(float(big.bal_before), 2),
            "largest_pct_balance": None if pct is None else round(float(pct), 1),
            "over_cap": int(len(over)), "over_cap_cost": round(float((over.net + cap[over.index]).sum()), 2)}


def nth_buckets(n: int) -> str:
    return "1st-5th" if n <= 5 else "6th-10th" if n <= 10 else "11th-20th" if n <= 20 else "21st+"


# ------------------------------------------------------------------ rule replay
def _cooldown(d: pd.DataFrame, minutes: float = COOLDOWN_MIN) -> pd.Series:
    keep, block_until = [], pd.Timestamp.min
    for start, close, net in zip(d.start, d.t, d.net):
        ok = start >= block_until
        keep.append(ok)
        if ok and net < 0:
            block_until = close + pd.Timedelta(minutes=minutes)
    return pd.Series(keep, index=d.index)


def _per_day(d: pd.DataFrame, fn: Callable[[pd.DataFrame], list[bool]]) -> pd.Series:
    keep = pd.Series(True, index=d.index)
    for _, g in d.groupby("day"):
        keep[g.index] = fn(g)
    return keep


def _max_trades(d: pd.DataFrame, n: int = MAX_TRADES_DAY) -> pd.Series:
    return d.nth <= n


def _daily_stop(d: pd.DataFrame, pct: float = DAILY_STOP_PCT) -> pd.Series:
    def day(g: pd.DataFrame) -> list[bool]:
        base = g.bal_before.dropna()
        limit = (float(base.iloc[0]) if len(base) else 0.0) * pct / 100
        out, total = [], 0.0
        for net in g.net:
            ok = not (limit and total <= -limit)
            out.append(ok)
            total += net if ok else 0.0
        return out
    return _per_day(d, day)


def _loss_streak(d: pd.DataFrame, k: int = MAX_LOSS_STREAK) -> pd.Series:
    def day(g: pd.DataFrame) -> list[bool]:
        out, run = [], 0
        for net in g.net:
            ok = run < k
            out.append(ok)
            if ok:
                run = run + 1 if net < 0 else 0
        return out
    return _per_day(d, day)


def _cap_losses(d: pd.DataFrame, pct: float = LOSS_CAP_PCT) -> pd.Series:
    cap = d.bal_before * pct / 100
    return d.net.where(~((d.net < 0) & cap.notna() & (-d.net > cap)), -cap)


RULES: list[tuple[str, str]] = [
    ("cooldown", f"Wait {COOLDOWN_MIN:g} min after a losing trade before the next one"),
    ("max_trades", f"At most {MAX_TRADES_DAY} trades a day"),
    ("daily_stop", f"Stop for the day once down {DAILY_STOP_PCT:g}% of the balance"),
    ("loss_streak", f"Stop for the day after {MAX_LOSS_STREAK} losses in a row"),
    ("cap_losses", f"Hard stop: no single loss bigger than {LOSS_CAP_PCT:g}% of the balance"),
    ("combined", "All five together"),
]


def apply_rule(d: pd.DataFrame, rule: str) -> pd.Series:
    """The net of each trade under the rule; NaN = the trade would not have been taken."""
    if rule == "cap_losses":
        return _cap_losses(d)
    if rule == "combined":
        keep = _cooldown(d) & _max_trades(d)
        kept = d[keep].copy()
        kept["net"] = _cap_losses(kept)
        kept = kept[_loss_streak(kept)]
        kept = kept[_daily_stop(kept)]
        return kept.net.reindex(d.index)
    keep = {"cooldown": _cooldown, "max_trades": _max_trades, "daily_stop": _daily_stop,
            "loss_streak": _loss_streak}[rule](d)
    return d.net.where(keep)


def _stop_penalty(d: pd.DataFrame, net: pd.Series) -> float:
    """What a hard stop would also cost: a stop tight enough to cut those big losses would knock out winners too.
    Assume one typical (median) winning trade becomes a loss at the cap for every big loss the cap cut."""
    capped = (net.notna()) & (net > d.net + 1e-9)            # trades whose loss the cap made smaller
    n = int(capped.sum())
    if not n:
        return 0.0
    wins = d.net[d.net > 0]
    median_win = float(wins.median()) if len(wins) else 0.0
    cap = float((-net[capped]).median())
    return n * (median_win + cap)


def replay(d: pd.DataFrame) -> list[dict[str, Any]]:
    base = stats(d.net)
    half = len(d) // 2
    h1, h2 = d.iloc[:half], d.iloc[half:]
    out = []
    for key, label in RULES:
        net = apply_rule(d, key)
        s = stats(net)
        # robustness: does the rule help in each half on its own?
        def gain(h: pd.DataFrame) -> float:
            h = h.reset_index(drop=True)
            r = apply_rule(h, key)
            pen = _stop_penalty(h, r) if key in ("cap_losses", "combined") else 0.0
            return float(r.sum() - h.net.sum() - pen)
        a = gain(h1) if half else 0.0
        b = gain(h2) if half else 0.0
        change = round(s["net"] - base["net"], 2)
        careful = round(change - _stop_penalty(d, net), 2) if key in ("cap_losses", "combined") else change
        out.append({"rule": key, "label": label, "trades": s["trades"], "skipped": base["trades"] - s["trades"],
                    "net": s["net"], "change": change, "conservative": careful, "best_case_only": careful != change,
                    "profit_factor": s["profit_factor"],
                    "max_drawdown": s["max_drawdown"], "first_half": round(float(a), 2),
                    "second_half": round(float(b), 2), "both_halves": bool(a > 0 and b > 0)})
    return out


# ------------------------------------------------------------------ report
def report(days: int = DAYS) -> dict[str, Any]:
    d = load(days)
    if d.empty:
        return {"error": "NO DATA - no closed trades yet (they come from your cTrader account by themselves once it is connected - !status)"}
    s = stats(d.net)
    held = d[d.hold_min.notna()].copy()
    if len(held):
        held["hold"] = held.hold_min.map(lambda m: next(n for lo, hi, n in HOLD_BUCKETS if lo <= m < hi))
    d["nth_bucket"] = d.nth.map(nth_buckets)
    from jarvis.agents.personal import SESSIONS

    top = d.symbol.value_counts()
    x = {
        "from": d.t.min().date().isoformat(), "to": d.t.max().date().isoformat(), "days": days,
        "overall": s, "trades_per_day": round(len(d) / max(1, d.day.nunique()), 1),
        "gap_to_breakeven": None if s["breakeven_win_rate"] is None else round(s["win_rate"] - s["breakeven_win_rate"], 1),
        "loss_to_breakeven": _loss_to_breakeven(s),
        "symbol": by(d, "symbol"), "session": by(d, "session", [n for _, _, n in SESSIONS]),
        "direction": by(d, "side", ["buy", "sell"]), "weekday": by(d, "weekday", WEEKDAYS),
        "size": by(d, "size", ["smaller than usual", "usual size", "1.5-3x usual", "over 3x usual"]),
        "nth": by(d, "nth_bucket", ["1st-5th", "6th-10th", "11th-20th", "21st+"]),
        "hold": by(held, "hold", [n for _, _, n in HOLD_BUCKETS]) if len(held) else [],
        "hold_known": int(len(held)),
        "behaviour": behaviour(d), "tail": loss_tail(d), "replay": replay(d), "min_group": MIN_GROUP,
        "main_symbol": str(top.index[0]), "main_share": round(float(top.iloc[0] / len(d) * 100), 1),
    }
    x["bottom_line"] = bottom_line(x)
    return x


def bottom_line(x: dict[str, Any]) -> dict[str, str]:
    """Three plain lines: the biggest leak, what to do first, what is still unproven."""
    b = x["behaviour"]
    cands: list[tuple[float, str, str]] = []
    f, o = b["after_loss_fast"], b["others"]
    if f["trades"] >= MIN_GROUP and f["net"] < 0:
        cands.append((f["net"], f"trades opened within {TILT_MIN} min of a loss: {f['trades']} trades, {_m(f['net'])} "
                                f"(all your other trades: {_m(o['net'])})",
                      f"wait {COOLDOWN_MIN:g} minutes after every losing trade"))
    for r in x["size"]:
        if r["group"] in ("1.5-3x usual", "over 3x usual") and r["trades"] >= MIN_GROUP and r["net"] < 0:
            cands.append((r["net"], f"trades {r['group'].replace('usual', 'your usual size')}: {r['trades']} trades, "
                                    f"{_m(r['net'])} ({_m(r['expectancy'])} each)",
                          "keep every trade at your usual size: no doubling up"))
    for key, word in (("session", "the {g} session (UTC)"), ("symbol", "{g}")):
        for r in x[key]:
            if r["verdict"] in ("clear leak", "maybe leak"):
                cands.append((r["net"], f"{word.format(g=r['group'])}: {r['trades']} trades, {_m(r['net'])}",
                              f"stop trading {word.format(g=r['group'])}"))
    t = x["tail"]
    if not cands and t.get("losses") and t["worst_10pct_share"] >= 40:   # only when nothing more specific stands out
        cands.append((t["worst_10pct_sum"], f"a few big losses: your worst {t['worst_10pct_count']} losses are "
                                                f"{t['worst_10pct_share']:.0f}% of all money lost",
                      "put a hard stop on every trade before you open it"))
    cands.sort(key=lambda c: c[0])
    leak = "; then ".join(c[1] for c in cands[:2]) if cands else "nothing stands out yet"
    first = " and ".join(dict.fromkeys(c[2] for c in cands[:2])) if cands else "keep trading as you are and recheck in 4 weeks"
    robust = [r for r in x["replay"] if r["both_halves"] and r["conservative"] > 0 and r["rule"] != "combined"]
    if robust:
        best = max(robust, key=lambda r: r["conservative"])
        first += f". Of the rules replayed, \"{best['label'].lower()}\" helped most and in both halves ({_m(best['conservative'])})"
    unproven = []
    if verdict(x["overall"]) not in ("clear edge",):
        unproven.append(f"whether your setups themselves make money (overall result is {verdict(x['overall'])})")
    if x["main_share"] >= 80:
        unproven.append(f"anything outside {x['main_symbol']}: it is {x['main_share']:.0f}% of your trades, "
                        "the other symbols have too few to judge")
    if not x["hold_known"]:
        unproven.append("hold time (open times are recorded from now on)")
    return {"leak": leak[0].upper() + leak[1:], "first": first[0].upper() + first[1:],
            "unproven": ("; ".join(unproven) or "nothing major")[0].upper() + ("; ".join(unproven) or "nothing major")[1:]}


def _loss_to_breakeven(s: dict[str, Any]) -> float | None:
    """How much smaller the average loss must be, at today's win rate and average win, to break even."""
    w = s["win_rate"] / 100
    if not s["avg_loss"] or w >= 1 or s["expectancy"] >= 0:
        return None
    needed = w * s["avg_win"] / (1 - w)      # |avg loss| at which expectancy = 0
    return round(-s["avg_loss"] - needed, 2)


def _m(v: float | None) -> str:
    if v is None:
        return "-"
    return f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def _row(r: dict[str, Any]) -> str:
    return (f"  {r['group']:<18} {r['trades']:>4} trades  {_m(r['net']):>10}  won {r['win_rate']:>4.0f}%  "
            f"per trade {_m(r['expectancy'])}  - {r['verdict']}")


def report_lines(days: int = DAYS) -> list[str]:
    x = report(days)
    if "error" in x:
        return [f"MY EDGE: {x['error']}"]
    s = x["overall"]
    bl = x["bottom_line"]
    L = [f"MY EDGE - {x['from']} to {x['to']} UTC, {s['trades']} trades ({x['trades_per_day']:g} a day)", "",
         "BOTTOM LINE", f" Biggest leak: {bl['leak']}.", f" Do this first: {bl['first']}.",
         f" Still unproven: {bl['unproven']}.", ""]
    if x["main_share"] >= 80:
        L += [f" Note: {x['main_share']:.0f}% of these trades are {x['main_symbol']}, so this report is really about your "
              f"{x['main_symbol']} trading.", ""]
    L.append("1. IS THERE AN EDGE?")
    L.append(f" Net {_m(s['net'])}. You win {s['win_rate']:.0f}% of trades. Average win {_m(s['avg_win'])}, "
             f"average loss {_m(s['avg_loss'])} (each win is {s['payoff'] or 0:.2f}x a loss).")
    if s["breakeven_win_rate"] is not None:
        L.append(f" With wins that size you need to win {s['breakeven_win_rate']:.0f}% just to break even "
                 f"-> you are {abs(x['gap_to_breakeven']):.1f} points {'above' if x['gap_to_breakeven'] >= 0 else 'below'} it.")
    if x["loss_to_breakeven"]:
        L.append(f" Cutting the average loss by just {_m(x['loss_to_breakeven'])} would put you at break-even "
                 "without winning a single extra trade.")
    L.append(f" Verdict on the whole period: {verdict(s)} (t-score {s['t']:+.1f}; "
             f"{'2 or more' if abs(s['t']) >= 2 else 'under 2'} means {'clear' if abs(s['t']) >= 2 else 'not proven'}).")
    b = x["behaviour"]
    L += ["", "2. BEHAVIOUR (the part you control)"]
    f, o = b["after_loss_fast"], b["others"]
    if f["trades"]:
        L.append(f" Trades opened within {TILT_MIN} min of a loss: {f['trades']}, net {_m(f['net'])}, "
                 f"{_m(f['expectancy'])} per trade vs {_m(o['expectancy'])} for all other trades.")
        if f["net"] < 0 <= o["net"]:
            L.append(f" Without those trades the period would be {_m(o['net'])} instead of {_m(s['net'])}.")
    if b["after_2_losses"]["trades"]:
        a2 = b["after_2_losses"]
        L.append(f" After 2+ losses in a row: {a2['trades']} trades, won {a2['win_rate']:.0f}%, net {_m(a2['net'])}.")
    if b["size_up_after_loss"]["trades"]:
        su = b["size_up_after_loss"]
        L.append(f" Bigger size straight after a loss (same symbol): {su['trades']} times, net {_m(su['net'])}.")
    t = x["tail"]
    if t.get("losses"):
        L.append(f" Your worst {t['worst_10pct_count']} losses (10%) cost {_m(t['worst_10pct_sum'])} = "
                 f"{t['worst_10pct_share']:.0f}% of all money lost.")
        L.append(f" Largest single loss {_m(t['largest'])} on {t['largest_symbol']}, {t['largest_when']}"
                 + (f": {t['largest_pct_balance']:.1f}% of the {_m(t['largest_balance_before'])} balance at that moment"
                    if t["largest_pct_balance"] is not None else "")
                 + f" (whole {x['days']} days; the PAD & KPIs tab shows the last 30 days only, so its figure can differ).")
        if t["over_cap"]:
            L.append(f" {t['over_cap']} losses were bigger than {LOSS_CAP_PCT:g}% of the balance at the time.")
    L += ["", f"3. WHERE IT WORKS AND WHERE IT LEAKS (needs {x['min_group']}+ trades to call it)"]
    for title, key in (("Symbols", "symbol"), ("Sessions (UTC)", "session"), ("Direction", "direction"),
                       ("Trade size vs your usual", "size"), ("Trade number in the day", "nth"),
                       ("Weekday", "weekday"), ("Hold time", "hold")):
        rows = x[key]
        if not rows:
            if key == "hold":
                L.append(" Hold time: not known yet. DONNA now records the open time of every new trade the cTrader feed sees.")
            continue
        if key == "symbol" and len(rows) > 8:
            rows = rows[-4:][::-1] + rows[:4]      # best 4, then worst 4
            L.append(f" {title} (best 4, worst 4):")
        else:
            L.append(f" {title}:")
        L += [_row(r) for r in rows]
    L += ["", "4. RULE REPLAY - same trades, as if you had followed the rule (careful estimate first)"]
    for r in x["replay"]:
        est = (f"{_m(r['conservative'])} careful estimate, {_m(r['change'])} at best" if r["best_case_only"]
               else f"{_m(r['change'])}")
        L.append(f"  {r['label']}: {est} vs actual ({r['skipped']} trades skipped). "
                 + ("Helped in both halves - robust." if r["both_halves"] else "Not consistent across both halves."))
    L += ["", " How to read it: the skip rules assume you simply don't take those trades. The hard-stop lines can't see",
          " inside a trade, so 'at best' assumes perfect fills and no winner ever touched the stop; the careful",
          " estimate also assumes one typical winner is stopped out for every big loss the stop cuts.",
          " Test any rule for 4 weeks before trusting it."]
    return L


def report_text(days: int = DAYS) -> str:
    return "\n".join(report_lines(days))


def command(cmd: str, rest: str) -> str | None:
    if cmd in ("!edge", "!trades", "!myedge"):
        arg = rest.strip()
        return report_text(int(arg) if arg.isdigit() and 7 <= int(arg) <= 365 else DAYS)
    return None


@register
class EdgeAgent(Agent):
    name = "edge"
    description = "Where SH's own trades make and lose money, behaviour after losses, and a what-if rule replay."

    @tool()
    def trading_edge(self, days: int = DAYS) -> dict[str, Any]:
        """Edge report on SH's closed trades: win rate vs break-even win rate, results by symbol / session /
        direction / size / hold time / trade number in the day, behaviour after losses, the loss tail, and a
        replay of simple rules (cool-down, max trades a day, daily stop, loss-streak stop, hard stop)."""
        return report(days)
