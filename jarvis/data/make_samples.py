"""Generate synthetic demo data (no real clients). Run: python -m jarvis.data.make_samples"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "samples"


def main(seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    today = pd.Timestamp.now().normalize()
    OUT.mkdir(parents=True, exist_ok=True)
    n = 120
    managers = ["Thandi", "Marco", "Aisha", "Liam"]
    prev_dep = rng.gamma(2, 1500, n).round(2)
    pd.DataFrame({
        "client_id": [f"C{1000 + i}" for i in range(n)],
        "name": [f"Demo Client {i}" for i in range(n)],
        "country": rng.choice(["ZA", "GB", "AE", "NG", "KE", "DE"], n),
        "segment": rng.choice(["retail", "pro", "ib_referred"], n, p=[.6, .25, .15]),
        "account_manager": rng.choice(managers, n),
        "signup_date": [(today - pd.Timedelta(days=int(d))).date() for d in rng.integers(30, 900, n)],
        "last_login": [(today - pd.Timedelta(days=int(d))).date() for d in rng.exponential(8, n)],
        "last_trade": [(today - pd.Timedelta(days=int(d))).date() for d in rng.exponential(12, n)],
        "equity": rng.lognormal(8.5, 1.1, n).round(2),
        "deposits_30d": (prev_dep * rng.uniform(0, 1.6, n)).round(2),
        "deposits_prev_30d": prev_dep,
        "withdrawals_30d": rng.gamma(1.2, 900, n).round(2),
        "trades_30d": rng.poisson(25, n) * rng.binomial(1, .8, n),
        "trades_prev_30d": rng.poisson(28, n),
        "open_tickets": rng.binomial(2, .1, n),
    }).to_csv(OUT / "clients.csv", index=False)

    m = 80
    pd.DataFrame({
        "deal_id": [f"D{i}" for i in range(m)],
        "name": [f"Prospect {i}" for i in range(m)],
        "source": rng.choice(["ib_partner", "webinar", "seo", "referral", "paid_social", "event"], m),
        "stage": rng.choice(["lead", "qualified", "kyc", "funded", "active", "lost"], m, p=[.3, .2, .15, .1, .1, .15]),
        "value": rng.choice([500, 1000, 2500, 5000, 10000, 50000], m),
        "owner": rng.choice(managers, m),
        "created": [(today - pd.Timedelta(days=int(d))).date() for d in rng.integers(5, 120, m)],
        "last_activity": [(today - pd.Timedelta(days=int(d))).date() for d in rng.integers(0, 40, m)],
    }).to_csv(OUT / "deals.csv", index=False)

    k = 600
    syms = {"EURUSD": (1.08, .0001), "GBPUSD": (1.27, .0001), "USDJPY": (150.0, .01), "XAUUSD": (2400.0, .01)}
    sym = rng.choice(list(syms), k)
    req = np.array([syms[s][0] for s in sym]) * rng.uniform(.99, 1.01, k)
    pip = np.array([syms[s][1] for s in sym])
    side = rng.choice(["buy", "sell"], k)
    status = rng.choice(["filled", "rejected"], k, p=[.96, .04])
    slip = rng.normal(.2, .6, k) * pip * np.where(side == "buy", 1, -1)
    pd.DataFrame({
        "ts": [today - pd.Timedelta(minutes=int(x)) for x in rng.integers(0, 60 * 24 * 7, k)],
        "account": rng.choice(["BOT-1", "BOT-2", "PERSONAL"], k),
        "symbol": sym, "side": side, "requested_price": req.round(5),
        "fill_price": np.where(status == "filled", req + slip, np.nan).round(5), "pip_size": pip,
        "status": status,
        "error_code": np.where(status == "filled", "", rng.choice(["requote", "off_quotes", "timeout", "invalid_stops", "no_money"], k)),
        "latency_ms": rng.gamma(2, 40, k).round(0),
        "platform": rng.choice(["mt5", "ctrader"], k),
    }).to_csv(OUT / "executions.csv", index=False)

    pd.DataFrame({
        "task_id": [f"T{i}" for i in range(25)],
        "title": [f"Task {i}" for i in range(25)],
        "owner": rng.choice(managers, 25),
        "due": [(today + pd.Timedelta(days=int(d))).date() for d in rng.integers(-10, 30, 25)],
        "status": rng.choice(["todo", "doing", "done"], 25, p=[.5, .3, .2]),
        "priority": rng.choice(["high", "med", "low"], 25),
        "project": rng.choice(["IB programme", "MT5 migration", "KYC revamp", "Q4 campaign"], 25),
    }).to_csv(OUT / "tasks.csv", index=False)


    make_dealing(rng, today)
    make_bot_log(rng, today)


def make_dealing(rng: np.random.Generator, today: pd.Timestamp) -> None:
    """Fills with planted problems: LP-Beta XAUUSD slips for an hour (LP-specific),
    LP-Gamma has a stale feed window, and a news release causes a market-wide spike."""
    days = 7
    start = today - pd.Timedelta(days=days)
    syms = {"EURUSD": (1.08, 1e-4, 1.0), "GBPUSD": (1.27, 1e-4, 1.0),
            "XAUUSD": (2400.0, 0.01, 1.0), "USDJPY": (150.0, 0.01, 1 / 150)}
    accounts = [f"A{100 + i}" for i in range(60)]
    kind = {a: ("latency" if i < 3 else "news" if i < 8 else "winner" if i < 16 else "retail")
            for i, a in enumerate(accounts)}
    equity = {a: float(rng.choice([300, 800, 2500, 10000, 50000], p=[.3, .3, .2, .15, .05])) for a in accounts}
    events = pd.DataFrame({
        "ts": [start + pd.Timedelta(days=d, hours=12, minutes=30) for d in (1, 3, 5)],
        "event": ["DEMO US CPI", "DEMO US Retail Sales", "DEMO US NFP"], "impact": ["high"] * 3})
    rows = []
    n = 4000
    ts = start + pd.to_timedelta(np.sort(rng.uniform(0, days * 86400, n)), unit="s")
    beta_bad = (start + pd.Timedelta(days=4, hours=9), start + pd.Timedelta(days=4, hours=10))
    gamma_stale = (start + pd.Timedelta(days=2, hours=14), start + pd.Timedelta(days=2, hours=14, minutes=40))
    for t in ts:
        acc = accounts[int(rng.integers(0, len(accounts)))]
        k = kind[acc]
        sym = str(rng.choice(list(syms), p=[.35, .2, .3, .15]))
        mid, pip, q2usd = syms[sym]
        mid *= float(rng.uniform(.995, 1.005))
        lp = str(rng.choice(["LP-Alpha", "LP-Beta", "LP-Gamma"]))
        side = str(rng.choice(["buy", "sell"]))
        sign = 1 if side == "buy" else -1
        near_news = any(abs((t - e).total_seconds()) < 300 for e in events["ts"])
        if k == "news" and rng.random() < .5:  # news traders cluster around releases
            t = events["ts"].iloc[int(rng.integers(0, 3))] + pd.Timedelta(seconds=float(rng.uniform(-120, 120)))
            near_news = True
        quote_age = float(rng.gamma(2, 40))
        slip = float(rng.normal(0.05, 0.15))
        if beta_bad[0] <= t < beta_bad[1] and lp == "LP-Beta" and sym == "XAUUSD":
            slip += float(rng.uniform(1.0, 3.0))
        if gamma_stale[0] <= t < gamma_stale[1] and lp == "LP-Gamma":
            quote_age += 900
            slip += float(rng.uniform(.5, 1.5))
        if near_news:
            slip += float(rng.uniform(.5, 2.5))
        vol_mult = 4 if near_news else 1
        drift = {"latency": 0.8, "winner": 0.6, "news": 0.4, "retail": -0.3}[k]
        # markouts in pips from the client's side (+ = moved their way after the fill)
        mk1 = (drift if k == "latency" else 0) + float(rng.normal(0, .3)) * vol_mult
        mk5 = mk1 + float(rng.normal(0, .5)) * vol_mult
        mk60 = mk5 + float(rng.normal(drift * (k != "latency"), 2)) * vol_mult
        hold = float(rng.uniform(2, 40) if k == "latency" else rng.exponential(1800 if k != "retail" else 3600))
        units = float(rng.choice([1000, 5000, 10000, 50000, 100000])) if sym != "XAUUSD" else float(rng.choice([10, 50, 100, 500]))
        req = mid
        fill = req + sign * slip * pip
        pnl_pips = {"latency": mk1 * 0.9, "winner": float(rng.normal(3, 15)), "news": float(rng.normal(2, 25)),
                    "retail": float(rng.normal(-4, 15))}[k]
        rows.append({
            "ts_request": t, "ts_fill": t + pd.Timedelta(milliseconds=float(rng.gamma(2, 25) + quote_age / 10)),
            "account": acc, "group": "standard" if equity[acc] < 5000 else "pro", "equity": equity[acc],
            "symbol": sym, "side": side, "order_type": str(rng.choice(["market", "limit", "stop"], p=[.7, .2, .1])),
            "units": units, "request_price": round(req, 5), "fill_price": round(fill, 5), "pip_size": pip,
            "quote_to_usd": q2usd, "lp": lp, "quote_age_ms": round(quote_age),
            "mid_fill": round(fill, 6), "mid_1s": round(fill + mk1 * pip * sign, 6),
            "mid_5s": round(fill + mk5 * pip * sign, 6), "mid_60s": round(fill + mk60 * pip * sign, 6),
            "markup_usd": round(units * pip * q2usd * 0.3, 2), "commission_usd": round(units * mid * q2usd * 3e-6, 2),
            "client_pnl_usd": round(pnl_pips * pip * units * q2usd, 2), "hold_seconds": round(hold, 1),
        })
    pd.DataFrame(rows).to_csv(OUT / "fills.csv", index=False)
    events.to_csv(OUT / "events.csv", index=False)


def make_bot_log(rng: np.random.Generator, today: pd.Timestamp, n: int = 160) -> None:
    """Synthetic RLOG2 lines shaped like the silver scalper's output."""
    lines = ["RLOG2,id,side,entryUtc,exitUtc,hours,R,mfeR,maeR,exit,hour,stopPips,spreadPips,slipPips,costPips,net"]
    t = today - pd.Timedelta(days=240)
    for i in range(n):
        t += pd.Timedelta(days=float(rng.uniform(.8, 2.2)))
        hour = int(rng.choice([7, 8, 9, 10, 11], p=[.25, .25, .2, .15, .15]))
        entry = t.normalize() + pd.Timedelta(hours=hour, minutes=int(rng.integers(0, 12)) * 5)
        mfe = float(rng.exponential(1.3 if hour < 10 else .8))
        if mfe >= 1.5:
            r, ex = 1.5, "TakeProfit"
        elif mfe >= 1.0:
            r, ex = float(rng.choice([-0.05, 0.4], p=[.7, .3])), "StopLoss"
        else:
            r, ex = (-1.0, "StopLoss") if rng.random() < .75 else (float(rng.uniform(-.5, .5)), "Closed")
        mae = 1.0 if r <= -1 else float(rng.uniform(0, .9))
        hrs = float(rng.uniform(.1, 3))
        stop = float(rng.uniform(6, 16))
        lines.append(f"RLOG2,{1000 + i},{rng.choice(['Buy', 'Sell'])},{entry:%Y-%m-%d %H:%M},"
                     f"{entry + pd.Timedelta(hours=hrs):%Y-%m-%d %H:%M},{hrs:.2f},{r:.2f},{max(mfe, r):.2f},{mae:.2f},"
                     f"{ex},{hour},{stop:.1f},{rng.uniform(1.5, 3.5):.1f},{rng.normal(.2, .4):.1f},0.6,{r * 8:.2f}")
    (OUT / "silver_scalper_log.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
