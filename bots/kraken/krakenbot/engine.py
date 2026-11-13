"""The live cycle. Run ONCE PER DAY just after 00:00 UTC (cron / systemd timer / Windows Task Scheduler).

Each run:
  1. book any disaster stop that filled while the bot was off (partial fills included);
  2. on the weekly rebalance bar (or if a rebalance was missed for 7+ days): raise stops, rank, trade to the
     two-sleeve target weights;
  3. every day: make sure each holding has its stop resting on Kraken (and sell if price is already through it).

Guarantees (each covered by a test): idempotent per daily bar; stop placed straight after every fill; a holding
that cannot get a stop after 3 tries is sold; dust below Kraken's minimum is written off the ledger; fees charged
in the coin reduce the ledger quantity; manual sales are detected; no currency conversions (broker gate);
validate mode never changes the ledger.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .broker import BelowMinimum, BrokerError, client_id
from .config import Config
from .risk import (DrawdownGuard, Holding, OrderPlan, QuoteError, check_quote, limit_price, on_buy, on_sell,
                   plan_rebalance, ratchet, stop_level)
from .signals import features, target_weights, to_frame

log = logging.getLogger("krakenbot.engine")


@dataclass
class State:
    cash: float = 0.0
    holdings: dict[str, Holding] = field(default_factory=dict)
    guard: DrawdownGuard = field(default_factory=DrawdownGuard)
    last_bar: str = ""
    last_rebalance: str = ""
    history: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: str, cfg: Config, capital: float | None) -> "State":
        if os.path.exists(path):
            with open(path) as fh:
                d = json.load(fh)
            st = cls(cash=d["cash"], last_bar=d.get("last_bar", ""), last_rebalance=d.get("last_rebalance", ""),
                     history=d.get("history", [])[-1000:])
            st.holdings = {a: Holding.from_dict(h) for a, h in d.get("holdings", {}).items()}
            st.guard = DrawdownGuard(**d.get("guard", {}))
        else:
            if capital is None or capital <= 0:
                raise SystemExit("First run: pass --capital <EUR> (or use `rebuild` to adopt your current holdings).")
            st = cls(cash=float(capital))
        for a in cfg.assets:
            st.holdings.setdefault(a, Holding())
        return st

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"cash": self.cash, "last_bar": self.last_bar, "last_rebalance": self.last_rebalance,
                       "history": self.history[-1000:], "holdings": {a: h.to_dict() for a, h in self.holdings.items()},
                       "guard": {"peak": self.guard.peak, "halted": self.guard.halted, "reason": self.guard.reason}},
                      fh, indent=2)
        os.replace(tmp, path)  # atomic


class Engine:
    def __init__(self, cfg: Config, broker, state: State, state_path: str, sleep=time.sleep):
        self.cfg, self.b, self.st, self.path, self.sleep = cfg, broker, state, state_path, sleep
        self.vol: dict[str, float] = {}
        self.run_tag = ""
        self._in_protect = False
        self.reserved: dict[str, float] = {}  # coins owned by ANOTHER ledger on the same account (e.g. harvest)

    # --- helpers ---------------------------------------------------------------------------------------
    def _sym(self, a: str) -> str:
        return f"{a}/{self.cfg.quote}"

    def _commit(self) -> None:
        if self.cfg.mode != "validate":
            self.st.save(self.path)

    def _mid(self, sym: str) -> tuple[float, float, float]:
        bid, ask, ts = self.b.top_of_book(sym)
        return bid, ask, check_quote(bid, ask, ts, self.cfg)

    def _h(self, a: str) -> Holding:
        return self.st.holdings.setdefault(a, Holding())

    def reconcile(self, bal: dict) -> None:
        """If coins were sold or withdrawn by hand, the ledger must not believe it still owns them."""
        if not bal:
            return
        for a, h in self.st.holdings.items():
            have = max(0.0, bal.get(a, {}).get("total", 0.0) - self.reserved.get(a, 0.0))
            if h.qty > have + 1e-9:
                log.error("%s: ledger %.8f > account %.8f; clamping the ledger", a, h.qty, have)
                h.qty = have
                if have <= 0:
                    on_sell(h, 0.0)

    def _book_sell(self, a: str, f) -> None:
        self.st.cash += f.qty * f.avg_px - f.fee_quote
        on_sell(self._h(a), f.qty + f.fee_base)

    def check_stops(self) -> None:
        for a, h in self.st.holdings.items():
            if not h.stop_order_id:
                continue
            o = self.b.fetch(h.stop_order_id, self._sym(a))
            filled, status = float(o.get("filled") or 0), o.get("status")
            if filled > 0 and status in {"closed", "canceled", "expired"}:
                f = self.b.to_fill(o, self._sym(a), h.stop_order_id)
                log.warning("%s DISASTER STOP FILLED: %.8f @ %.4f (stop %.4f)", a, f.qty, f.avg_px, h.stop)
                h.stop_order_id = None
                self._book_sell(a, f)
            elif status in {"canceled", "expired", "rejected"}:
                log.error("%s stop %s is %s; it will be re-placed", a, h.stop_order_id, status)
                h.stop_order_id = None

    # --- protection --------------------------------------------------------------------------------------
    def protect(self, a: str) -> None:
        if self._in_protect:
            return
        self._in_protect = True
        try:
            self._protect(a)
        finally:
            self._in_protect = False

    def _protect(self, a: str) -> None:
        h, sym = self._h(a), self._sym(a)
        if h.qty <= 0:
            if h.stop_order_id:
                self.b.cancel(h.stop_order_id, sym)
                h.stop_order_id = None
            return
        try:
            _, _, mid = self._mid(sym)
        except (QuoteError, BrokerError) as e:
            log.error("%s: no usable price (%s); existing stop left as is", a, e)
            return
        if h.stop <= 0:
            h.stop = stop_level(mid, self.vol.get(a, float("nan")), self.cfg)
        if mid <= h.stop:
            log.warning("%s price %.4f already through stop %.4f: selling now", a, mid, h.stop)
            self.execute(OrderPlan(a, "sell", h.qty, h.qty * mid, "price through stop"))
            if h.qty > 0:
                log.critical("%s: exit through stop incomplete, %.8f UNPROTECTED - check Kraken now", a, h.qty)
            return
        if h.stop_order_id:
            o = self.b.fetch(h.stop_order_id, sym)
            resting = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
            same_qty = abs(float(o.get("amount") or 0) - h.qty) <= max(1e-8, h.qty * 1e-4)
            if o.get("status") == "open" and same_qty and resting > 0 and abs(resting - h.stop) / h.stop < 0.002:
                return
            self.b.cancel(h.stop_order_id, sym)
            h.stop_order_id = None
        cid = client_id(self.run_tag, sym, "stop", f"{h.stop:.8f}", f"{h.qty:.8f}")
        for attempt in range(3):
            try:
                o = self.b.place_stop(sym, h.qty, h.stop, cid)
                h.stop_order_id = None if self.cfg.mode == "validate" else str(o["id"])
                log.info("STOP %s %.8f @ %.4f (%s)", sym, h.qty, h.stop,
                         {"validate": "validated, not placed", "paper": "simulated"}.get(self.cfg.mode, "resting on Kraken"))
                self._commit()
                return
            except BelowMinimum as e:
                log.warning("%s %.8f is below Kraken's minimum (%s): written off the ledger as dust", a, h.qty, e)
                on_sell(h, h.qty)
                self._commit()
                return
            except Exception as e:  # noqa: BLE001
                log.error("stop placement %s failed (%d/3): %s", sym, attempt + 1, e)
                self.sleep(5)
        log.critical("%s has NO stop after 3 attempts: selling it", sym)
        self.execute(OrderPlan(a, "sell", h.qty, h.qty * mid, "no-stop exit"))

    # --- execution ---------------------------------------------------------------------------------------
    def execute(self, plan: OrderPlan) -> None:
        a, sym = plan.asset, self._sym(plan.asset)
        h = self._h(a)
        remaining = plan.qty
        if plan.side == "sell" and h.stop_order_id:
            self.b.cancel(h.stop_order_id, sym)  # a resting stop reserves the coins; release them first
            h.stop_order_id = None
        for attempt in range(self.cfg.post_only_attempts + 1):
            try:
                bid, ask, mid = self._mid(sym)
            except (QuoteError, BrokerError) as e:
                log.error("%s: no tradable quote (%s); %s aborted", sym, e, plan.side)
                break
            if plan.side == "buy" and remaining * mid < self.cfg.min_order_quote:
                break
            price, post_only = limit_price(plan.side, bid, ask, mid, attempt, self.cfg)
            cid = client_id(self.run_tag, sym, plan.side, plan.reason, str(attempt))
            try:
                o = self.b.place_limit(sym, plan.side, remaining, price, post_only, cid)
            except BrokerError as e:
                log.error("%s %s not placed: %s", plan.side, sym, e)
                break
            except Exception as e:  # noqa: BLE001 - e.g. post-only rejected because the book moved
                log.error("%s %s attempt %d rejected: %s", plan.side, sym, attempt, e)
                continue
            if self.cfg.mode == "validate":
                log.info("VALIDATED (not placed) %s %s %.8f @ %.6f", plan.side, sym, remaining, price)
                break
            if o.get("status") != "closed" and post_only:
                self.sleep(self.cfg.post_only_wait_s)
                o = self.b.fetch(o["id"], sym)
                if o.get("status") == "open":
                    self.b.cancel(o["id"], sym)
                    o = self.b.fetch(o["id"], sym)
            f = self.b.to_fill(o, sym, cid)
            if f.qty > 0:
                if plan.side == "buy":
                    self.st.cash -= f.qty * f.avg_px + f.fee_quote
                    on_buy(h, f.avg_px, f.qty - f.fee_base, self.vol.get(a, float("nan")), self.cfg)
                else:
                    self._book_sell(a, f)
                log.info("FILL %s %s %.8f @ %.6f fee %.4f (attempt %d)", plan.side, sym, f.qty, f.avg_px, f.fee_quote, attempt)
                remaining -= f.qty
                self._commit()
                if plan.side == "buy":
                    self.protect(a)  # stop on Kraken straight after EACH fill
            if remaining <= 1e-12 or (plan.side == "sell" and h.qty <= 0):
                break
        if plan.side == "sell" and h.qty > 0:
            self.protect(a)  # trims / partial sells: re-protect what is left at once

    # --- the cycle ---------------------------------------------------------------------------------------
    def run_once(self, force_rebalance: bool = False) -> dict:
        cfg, st = self.cfg, self.st
        feats = {a: features(to_frame(self.b.daily_bars(self._sym(a), 400)), cfg) for a in cfg.assets}
        bar = min(f.index[-1] for f in feats.values())
        self.run_tag = bar.date().isoformat()
        rows = {a: f.loc[bar] for a, f in feats.items() if bar in f.index}
        self.vol = {a: float(r["vol"]) for a, r in rows.items()}

        bal = self.b.balances()
        self.reconcile(bal)
        self.check_stops()

        mids = {}
        for a in cfg.assets:
            try:
                mids[a] = self._mid(self._sym(a))[2]
            except (QuoteError, BrokerError) as e:
                log.error("%s: no tradable quote (%s); it will not be traded this run", a, e)
        equity = st.cash + sum(h.qty * mids.get(a, h.entry) for a, h in st.holdings.items())

        if st.guard.update(equity, cfg):
            log.critical("HALTED: %s. Selling everything. Delete the 'guard' block in %s to resume.", st.guard.reason, self.path)
            targets, tables, reason = {a: 0.0 for a in cfg.assets}, {}, "halt"
        elif st.last_bar == self.run_tag and not force_rebalance:
            for a in list(st.holdings):
                self.protect(a)
            self._commit()
            return {"status": "already-done", "bar": self.run_tag, "equity": equity}
        else:
            due = bar.dayofweek == cfg.rebalance_weekday
            stale = not st.last_rebalance or (bar.date() - date.fromisoformat(st.last_rebalance)).days >= 7
            if not (due or stale or force_rebalance):
                for a in list(st.holdings):
                    self.protect(a)
                st.last_bar = self.run_tag
                self._log_history(equity, None)
                self._commit()
                return {"status": "hold", "bar": self.run_tag, "equity": equity,
                        "next_rebalance": "on the next weekly rebalance bar"}
            for a, h in st.holdings.items():
                if a in rows:
                    ratchet(h, float(rows[a]["close"]), float(rows[a]["vol"]), cfg)
            targets, tables = target_weights(rows, cfg)
            reason = "forced" if force_rebalance else ("weekly" if due else "catch-up (missed rebalance)")

        tradable = {a: w for a, w in targets.items() if a in mids}
        free_eur = bal.get(cfg.quote, {}).get("free", st.cash) if bal else st.cash
        plans = plan_rebalance(equity, mids, {a: st.holdings[a].qty for a in tradable}, tradable, cfg,
                               free_quote=min(st.cash, free_eur))
        log.info("REBALANCE (%s) bar %s equity %.2f %s targets %s", reason, self.run_tag, equity, cfg.quote,
                 {a: round(w, 3) for a, w in targets.items() if w})
        for p in plans:
            log.info("PLAN %s %s %.8f (~%.2f %s) %s", p.side, p.asset, p.qty, p.notional, cfg.quote, p.reason)
            self.execute(p)
        for a in list(st.holdings):
            self.protect(a)
        st.last_bar = self.run_tag
        if reason != "halt":
            st.last_rebalance = self.run_tag
        equity = st.cash + sum(h.qty * mids.get(a, h.entry) for a, h in st.holdings.items())
        self._log_history(equity, targets)
        self._commit()
        return {"status": "rebalanced", "reason": reason, "bar": self.run_tag, "equity": equity,
                "targets": {a: w for a, w in targets.items() if w}, "orders": len(plans),
                "rankings": {k: v.round(4).to_dict("index") for k, v in tables.items()}}

    def _log_history(self, equity: float, targets: dict | None) -> None:
        self.st.history.append({"bar": self.run_tag, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "equity": round(equity, 2), "cash": round(self.st.cash, 2),
                                "holdings": {a: round(h.qty, 8) for a, h in self.st.holdings.items() if h.qty > 0},
                                "stops": {a: round(h.stop, 6) for a, h in self.st.holdings.items() if h.qty > 0},
                                "targets": None if targets is None else {a: round(w, 4) for a, w in targets.items() if w},
                                "halted": self.st.guard.halted})
