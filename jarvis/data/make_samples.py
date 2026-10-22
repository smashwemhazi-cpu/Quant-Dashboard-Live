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


if __name__ == "__main__":
    main()
