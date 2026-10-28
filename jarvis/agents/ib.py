"""IB control: what each introducing broker brings in, what they are paid, and whether the rebates are earned.

Data: the IB / partner commission report from the CRM (emailed or uploaded like the other exports).
Recognised by its columns - an IB / partner id, a client account, and a commission / rebate amount
(lots and volume when present). Joined to the cTrader account-activity export for deposits, trading and P&L.

Per IB: clients, funded clients, deposits and withdrawals, lots, rebates paid, what the broker earned from
those clients, and rebate as a % of that. Flags (each becomes a compliance case, category IB):
  IB_REBATE_ABOVE_REVENUE  paid more in rebates than the broker earned from the IB's clients
  IB_RATE_MISMATCH         paid differs from the agreed rate x lots (set rates with !ib rate IB123 6.5)
  IB_REBATE_FARMING        a client trades heavily for ~no profit or loss while the IB earns the rebate
  IB_SELF_REFERRAL         the IB is a client under their own code
  IB_IN_OUT_NO_TRADE       several clients deposit and withdraw without trading (bonus / pass-through)
  IB_SAME_DEPOSITS         several clients made the identical deposit (possible multi-accounting)
Revenue is an estimate: commission charged + swaps + client losses (the B-book view). Check it against
the dealing desk's own numbers before acting on a single IB.
Clients appear by account number only.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter
from typing import Any

import pandas as pd

FARM_MIN_DEALS = int(os.getenv("JARVIS_IB_FARM_MIN_DEALS", "100"))
RATE_TOLERANCE_PCT = float(os.getenv("JARVIS_IB_RATE_TOLERANCE_PCT", "5"))
SAME_DEPOSIT_MIN = int(os.getenv("JARVIS_IB_SAME_DEPOSIT_MIN", "3"))
IN_OUT_MIN = int(os.getenv("JARVIS_IB_IN_OUT_MIN", "2"))


def _pick(cols: dict[str, str], *names: str, exclude: tuple[str, ...] = ()) -> str | None:
    for n in names:
        for low, orig in cols.items():
            if n in low and not any(x in low for x in exclude):
                return orig
    return None


def columns(d: pd.DataFrame) -> dict[str, str | None] | None:
    """Column map if this export is an IB / partner commission report, else None."""
    cols = {str(c).lower().strip(): c for c in d.columns}
    ib = _pick(cols, "ib id", "ib_id", "ib no", "ib number", "ib code", "ib account", "partner id", "partner",
               "affiliate id", "affiliate", "introducing broker", "referrer", "agent id") or \
        next((orig for low, orig in cols.items() if "ib" in re.split(r"[^a-z0-9]+", low)), None)
    acc = _pick(cols, "trader account", "client account", "client login", "login", "account", "customer_no",
                "client id", "client", exclude=("ib", "partner", "affiliate", "referrer", "agent"))
    paid = _pick(cols, "rebate", "ib commission", "commission paid", "payout", "partner commission", "commission",
                 exclude=("rate",))
    if not (ib and acc and paid) or len({ib, acc, paid}) < 3:
        return None
    return {"ib": ib, "account": acc, "paid": paid, "lots": _pick(cols, "lots", "lot"),
            "volume": _pick(cols, "volume", exclude=("lot",))}


def store(c: sqlite3.Connection, d: pd.DataFrame, asof: str) -> int:
    from jarvis.agents.backoffice import _num

    m = columns(d)
    c.executescript(SCHEMA)
    clean = lambda s: s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True)  # noqa: E731
    rows = pd.DataFrame({"asof": asof, "ib": clean(d[m["ib"]]), "account": clean(d[m["account"]]),
                         "lots": _num(d[m["lots"]]) if m["lots"] else 0.0,
                         "volume": _num(d[m["volume"]]) if m["volume"] else 0.0, "paid": _num(d[m["paid"]])})
    rows = rows[(rows.ib != "") & (rows.ib.str.lower() != "nan") & (rows.account != "")]
    rows = rows.groupby(["asof", "ib", "account"], as_index=False)[["lots", "volume", "paid"]].sum()
    c.execute("DELETE FROM bo_ib WHERE asof = ?", (asof,))
    c.executemany("INSERT INTO bo_ib VALUES (?,?,?,?,?,?)", rows.itertuples(index=False, name=None))
    return len(rows)


SCHEMA = """
    CREATE TABLE IF NOT EXISTS bo_ib (asof TEXT, ib TEXT, account TEXT, lots REAL, volume REAL, paid REAL,
        PRIMARY KEY (asof, ib, account));
    CREATE TABLE IF NOT EXISTS ib_rates (ib TEXT PRIMARY KEY, per_lot REAL, note TEXT);
"""


def _db() -> sqlite3.Connection:
    from jarvis.agents import backoffice as bo

    c = bo._db()  # noqa: SLF001 - the IB data lives with the other exports
    c.executescript(SCHEMA)
    return c


def set_rate(ib: str, per_lot: float, note: str = "") -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO ib_rates VALUES (?,?,?)", (ib.strip(), per_lot, note[:200]))


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, str | None]:
    with _db() as c:
        last = c.execute("SELECT MAX(asof) FROM bo_ib").fetchone()[0]
        if not last:
            return pd.DataFrame(), pd.DataFrame(), None
        ibr = pd.read_sql_query("SELECT * FROM bo_ib WHERE asof = ?", c, params=(last,))
        act_asof = c.execute("SELECT MAX(asof) FROM bo_activity").fetchone()[0]
        act = pd.read_sql_query("SELECT * FROM bo_activity WHERE asof = ?", c, params=(act_asof,)) if act_asof \
            else pd.DataFrame(columns=["account", "deposit", "withdrawal", "deals", "volume", "commission", "swaps", "net_pnl"])
    return ibr, act, last


def summary() -> dict[str, Any]:
    """Per-IB economics and flags from the latest IB report."""
    ibr, act, asof = _frames()
    if asof is None:
        return {"error": "NO DATA - no IB / partner commission report received yet"}
    with _db() as c:
        rates = {r[0]: r[1] for r in c.execute("SELECT ib, per_lot FROM ib_rates")}
    act = act.drop(columns=[x for x in ("volume",) if x in act.columns]).rename(columns={"commission": "charged"})
    m = ibr.merge(act, on="account", how="left").fillna(0)
    out = []
    for ib, g in m.groupby("ib"):
        revenue = float(g.charged.abs().sum() + g.swaps.clip(upper=0).abs().sum() + (-g.net_pnl).clip(lower=0).sum())
        paid = float(g.paid.sum())
        lots = float(g.lots.sum())
        flags: list[dict[str, Any]] = []
        if paid > 0 and paid > revenue:
            flags.append({"rule": "IB_REBATE_ABOVE_REVENUE", "severity": "high",
                          "finding": f"IB {ib} was paid {paid:,.2f} but its clients earned the broker about {revenue:,.2f}"})
        if ib in rates and lots > 0:
            expected = rates[ib] * lots
            if abs(paid - expected) > max(1.0, expected * RATE_TOLERANCE_PCT / 100):
                flags.append({"rule": "IB_RATE_MISMATCH", "severity": "medium",
                              "finding": f"IB {ib} was paid {paid:,.2f}; agreed {rates[ib]:g}/lot x {lots:,.2f} lots = {expected:,.2f}"})
        farm = g[(g.deals >= FARM_MIN_DEALS) & (g.net_pnl.abs() < 0.5 * g.paid) & (g.paid > 0)]
        if len(farm):
            flags.append({"rule": "IB_REBATE_FARMING", "severity": "high", "accounts": farm.account.tolist(),
                          "finding": f"IB {ib}: {len(farm)} client(s) traded {int(farm.deals.sum())} deals for almost no "
                                     f"profit or loss while the IB was paid {farm.paid.sum():,.2f} on them"})
        if (g.account == ib).any():
            flags.append({"rule": "IB_SELF_REFERRAL", "severity": "medium",
                          "finding": f"IB {ib} trades as a client under its own code"})
        inout = g[(g.deposit > 0) & (g.withdrawal >= 0.9 * g.deposit) & (g.deals == 0)]
        if len(inout) >= IN_OUT_MIN:
            flags.append({"rule": "IB_IN_OUT_NO_TRADE", "severity": "high", "accounts": inout.account.tolist(),
                          "finding": f"IB {ib}: {len(inout)} clients deposited and withdrew without trading"})
        same = [(amt, n) for amt, n in Counter(round(x, 2) for x in g.deposit if x > 0).items() if n >= SAME_DEPOSIT_MIN]
        if same:
            amt, n = max(same, key=lambda x: x[1])
            flags.append({"rule": "IB_SAME_DEPOSITS", "severity": "medium",
                          "finding": f"IB {ib}: {n} clients each deposited exactly {amt:,.2f}"})
        out.append({"ib": ib, "clients": int(g.account.nunique()), "funded": int((g.deposit > 0).sum()),
                    "deposits": float(g.deposit.sum()), "withdrawals": float(g.withdrawal.sum()), "lots": lots,
                    "paid": paid, "revenue": revenue, "rebate_pct": round(100 * paid / revenue, 1) if revenue else None,
                    "client_pnl": float(g.net_pnl.sum()), "rate": rates.get(ib), "flags": flags})
    out.sort(key=lambda x: (-len(x["flags"]), -x["paid"]))
    return {"asof": asof, "activity_joined": not act.empty, "ibs": out,
            "paid": sum(x["paid"] for x in out), "revenue": sum(x["revenue"] for x in out)}


def scan_flags(add: Any) -> None:
    """Called by compliance.scan(): one case per flag per IB per report month."""
    s = summary()
    if "error" in s:
        return
    for x in s["ibs"]:
        for f in x["flags"]:
            add("IB", f["rule"], f["severity"], f"IB {x['ib']}", f["finding"],
                {"accounts": f.get("accounts", []), "paid": x["paid"], "revenue": x["revenue"]}, s["asof"][:7])


def report_lines() -> list[str]:
    s = summary()
    if "error" in s:
        return ["IB CONTROL", " " + s["error"]]
    L = [f"IB CONTROL (report {s['asof']}): rebates {s['paid']:,.2f} vs est. revenue {s['revenue']:,.2f}"
         + ("" if s["activity_joined"] else " - no account-activity export to join yet")]
    for x in s["ibs"][:8]:
        pct = f"{x['rebate_pct']:.0f}%" if x["rebate_pct"] is not None else "n/a"
        L.append(f" {x['ib']:<12} clients {x['clients']:>3} (funded {x['funded']:>3})  lots {x['lots']:>8,.1f}  "
                 f"paid {x['paid']:>10,.2f}  rebate/revenue {pct:>5}" + (f"  !! {len(x['flags'])} flag(s)" if x["flags"] else ""))
    return L


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!ib":
        return None
    parts = rest.split()
    if parts[:1] == ["rate"]:
        if len(parts) < 3:
            return "Usage: !ib rate IB123 6.5   (agreed rebate per lot)"
        try:
            set_rate(parts[1], float(parts[2]), " ".join(parts[3:]))
        except ValueError:
            return "The rate must be a number, e.g. !ib rate IB123 6.5"
        return f"Agreed rate for {parts[1]}: {float(parts[2]):g} per lot. Payments are checked against it from the next report."
    s = summary()
    if "error" in s:
        return s["error"] + ". Schedule the CRM's IB / partner commission report to your email, or upload it."
    if parts:
        x = next((i for i in s["ibs"] if i["ib"].lower() == parts[0].lower()), None)
        if not x:
            return f"No IB {parts[0]} in the {s['asof']} report."
        L = [f"IB {x['ib']} - report {s['asof']}",
             f" Clients {x['clients']} (funded {x['funded']}), deposits {x['deposits']:,.2f}, withdrawals {x['withdrawals']:,.2f}",
             f" Lots {x['lots']:,.2f}, rebates paid {x['paid']:,.2f}, est. revenue {x['revenue']:,.2f}"
             + (f" ({x['rebate_pct']:.0f}% of revenue)" if x["rebate_pct"] is not None else ""),
             f" Clients' net P&L {x['client_pnl']:,.2f}" + (f", agreed rate {x['rate']:g}/lot" if x["rate"] else ", no agreed rate set")]
        L += [f" !! {f['rule']}: {f['finding']}" + (f" (accounts {', '.join(f['accounts'][:8])})" if f.get("accounts") else "")
              for f in x["flags"]] or [" No flags."]
        return "\n".join(L)
    return "\n".join(report_lines() + ["", "!ib IB123 for one IB   !ib rate IB123 6.5 to set the agreed rebate"])
