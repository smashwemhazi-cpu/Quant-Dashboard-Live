"""One rebalance cycle. Designed to run ONCE PER DAY just after the 00:00 UTC daily close (cron/systemd timer).

Why once a day and not every 15 seconds: the October statement shows the fast loop's real cost. 242 fills in
10 days, a median round trip of +0.30% gross against ~0.44% of fees, so 83% of "winning" round trips lost money.
A daily cycle trades ~100 times in two years instead of ~240 times in ten days.

The run is idempotent: re-running on the same day after a crash resumes instead of trading twice.

Sub-account ledger: the bot only manages the EUR `capital` you give it and the coins IT bought. Coins you already
hold (your BTC/DOGE/SOL bags) are never touched unless you transfer them into the bot's ledger yourself.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .broker import BrokerError, client_id
from .config import Config
from .risk import (DrawdownGuard, PositionState, QuoteError, check_quote, limit_price, on_buy_fill, on_sell_fill,
                   on_stopped_out, plan_rebalance, update_block, update_trail)
from .signals import scale_portfolio, target_weight, to_frame

log = logging.getLogger("krakenbot.engine")


@dataclass
class State:
    cash: float = 0.0                                   # EUR the bot may use (its ledger, not your whole account)
    positions: dict[str, PositionState] = field(default_factory=dict)
    guard: DrawdownGuard = field(default_factory=DrawdownGuard)
    last_bar: str = ""                                  # ISO date of the last daily bar fully processed
    history: list[dict] = field(default_factory=list)   # equity log, one row per run

    @classmethod
    def load(cls, path: str, cfg: Config, capital: float | None) -> "State":
        if os.path.exists(path):
            with open(path) as fh:
                d = json.load(fh)
            st = cls(cash=d["cash"], last_bar=d.get("last_bar", ""), history=d.get("history", [])[-1000:])
            st.positions = {a: PositionState.from_dict(p) for a, p in d.get("positions", {}).items()}
            st.guard = DrawdownGuard(**d.get("guard", {}))
        else:
            if capital is None or capital <= 0:
                raise SystemExit("First run: pass --capital <EUR> (the amount this bot may manage).")
            st = cls(cash=float(capital))
        for a in cfg.assets:
            st.positions.setdefault(a, PositionState())
        return st

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"cash": self.cash, "last_bar": self.last_bar, "history": self.history[-1000:],
                       "positions": {a: p.to_dict() for a, p in self.positions.items()},
                       "guard": {"peak": self.guard.peak, "halted": self.guard.halted, "reason": self.guard.reason}},
                      fh, indent=2)
        os.replace(tmp, path)  # atomic: a crash mid-write never corrupts the state


class Engine:
    def __init__(self, cfg: Config, broker, state: State, state_path: str, sleep=time.sleep):
        self.cfg, self.b, self.st, self.path, self.sleep = cfg, broker, state, state_path, sleep
        self.live = cfg.mode == "live"

    # --- helpers ---------------------------------------------------------------------------------------
    def _commit(self):
        if self.cfg.mode != "validate":
            self.st.save(self.path)

    def _mid(self, sym: str) -> tuple[float, float, float]:
        bid, ask, ts = self.b.top_of_book(sym)
        return bid, ask, check_quote(bid, ask, ts, self.cfg)

    def _reconcile_holdings(self, bal: dict) -> None:
        """If you sold or withdrew coins by hand, the ledger must not believe it still owns them."""
        if not bal:
            return
        for a, p in self.st.positions.items():
            have = bal.get(a, {}).get("total", 0.0)
            if p.qty > have + 1e-9:
                log.error("%s: ledger says %.8f but account holds %.8f; clamping ledger", a, p.qty, have)
                p.qty = have
                if have <= 0:
                    p.stop = p.entry = p.high_close = 0.0
                    p.stop_order_id = None

    def _check_stops(self) -> None:
        """Did an exchange-side stop fill since the last run? Book it."""
        for a, p in self.st.positions.items():
            if not p.stop_order_id:
                continue
            sym = f"{a}/{self.cfg.quote}"
            o = self.b.fetch(p.stop_order_id, sym)
            if o.get("status") == "closed" and float(o.get("filled") or 0) > 0:
                f = self.b.to_fill(o, sym, p.stop_order_id)
                self.st.cash += f.qty * f.avg_px - f.fee_quote
                log.warning("%s STOPPED OUT: %.8f @ %.2f (stop %.2f)", a, f.qty, f.avg_px, p.stop)
                on_stopped_out(p)
            elif o.get("status") in {"canceled", "expired", "rejected"} and p.qty > 0:
                log.error("%s protective stop %s is %s; it will be re-placed", a, p.stop_order_id, o.get("status"))
                p.stop_order_id = None

    def _execute(self, plan, run_tag: str) -> None:
        sym = f"{plan.asset}/{self.cfg.quote}"
        p = self.st.positions[plan.asset]
        remaining = plan.qty
        if plan.side == "sell" and p.stop_order_id:
            # the stop reserves the coins on spot; release them before selling
            self.b.cancel(p.stop_order_id, sym)
            p.stop_order_id = None
        for attempt in range(self.cfg.post_only_attempts + 1):
            if remaining * self._mid(sym)[2] < self.cfg.min_order_quote:
                break
            bid, ask, mid = self._mid(sym)
            price, post_only = limit_price(plan.side, bid, ask, mid, attempt, self.cfg)
            cid = client_id(run_tag, sym, plan.side, plan.reason, str(attempt))
            try:
                o = self.b.place_limit(sym, plan.side, remaining, price, post_only, cid)
            except BrokerError as e:  # below minimum / retries exhausted: nothing sensible left to try
                log.error("%s %s not placed: %s", plan.side, sym, e)
                return
            except Exception as e:  # noqa: BLE001 - e.g. post-only rejected because the book moved; re-price
                log.error("%s %s attempt %d rejected: %s", plan.side, sym, attempt, e)
                continue
            if self.cfg.mode == "validate":
                log.info("VALIDATED (not placed) %s %s %.8f @ %.2f post_only=%s", plan.side, sym, remaining, price, post_only)
                return
            if o.get("status") != "closed" and post_only:
                self.sleep(self.cfg.post_only_wait_s)
                o = self.b.fetch(o["id"], sym)
                if o.get("status") == "open":
                    self.b.cancel(o["id"], sym)
                    o = self.b.fetch(o["id"], sym)
            f = self.b.to_fill(o, sym, cid)
            if f.qty > 0:
                atr_now = self.atr[plan.asset]
                if plan.side == "buy":
                    self.st.cash -= f.qty * f.avg_px + f.fee_quote
                    on_buy_fill(p, f.avg_px, f.qty, atr_now, self.cfg)
                else:
                    self.st.cash += f.qty * f.avg_px - f.fee_quote
                    on_sell_fill(p, f.qty)
                log.info("FILL %s %s %.8f @ %.4f fee %.4f %s (attempt %d)", plan.side, sym, f.qty, f.avg_px,
                         f.fee_quote, self.cfg.quote, attempt)
                remaining -= f.qty
                self._commit()  # persist after every fill: a crash now cannot double-buy on restart
            if remaining <= 1e-12:
                break

    def _protect(self, run_tag: str) -> None:
        """Make sure every open position has an exchange-side stop at the current (ratcheted) level."""
        for a, p in self.st.positions.items():
            if p.qty <= 0 or p.stop <= 0:
                continue
            sym = f"{a}/{self.cfg.quote}"
            if p.stop_order_id:
                o = self.b.fetch(p.stop_order_id, sym)
                if o.get("status") == "open" and abs(float(o.get("stopPrice") or o.get("triggerPrice") or 0) - p.stop) / p.stop < 0.002:
                    continue
                self.b.cancel(p.stop_order_id, sym)
                p.stop_order_id = None
            cid = client_id(run_tag, sym, "stop", f"{p.stop:.8f}")
            for attempt in range(3):
                try:
                    o = self.b.place_stop(sym, p.qty, p.stop, cid)
                    p.stop_order_id = None if self.cfg.mode == "validate" else str(o["id"])
                    log.info("STOP %s %.8f @ %.2f (%s)", sym, p.qty, p.stop, "validated" if self.cfg.mode == "validate" else "resting")
                    break
                except Exception as e:  # noqa: BLE001 - every failure here is handled the same way
                    log.error("stop placement %s failed (%d/3): %s", sym, attempt + 1, e)
                    self.sleep(5)
            else:
                # A position without a stop is not allowed to exist. Flatten it.
                log.critical("%s has NO protective stop after 3 attempts: flattening", sym)
                from .risk import OrderPlan
                bid, ask, mid = self._mid(sym)
                self._execute(OrderPlan(a, "sell", p.qty, p.qty * mid, "no-stop flatten"), run_tag + ":flatten")
            self._commit()

    # --- the cycle ---------------------------------------------------------------------------------------
    def run_once(self) -> dict:
        cfg, st = self.cfg, self.st
        frames = {}
        for sym, a in zip(cfg.symbols, cfg.assets):
            frames[a] = to_frame(self.b.daily_bars(sym, 400))
        last_bar = min(f.index[-1] for f in frames.values()).date().isoformat()
        run_tag = f"{last_bar}"
        sig = {a: target_weight(frames[a], cfg).iloc[-1] for a in cfg.assets}
        self.atr = {a: float(sig[a]["atr"]) for a in cfg.assets}

        bal = self.b.balances()
        self._reconcile_holdings(bal)
        self._check_stops()

        mids = {}
        for sym, a in zip(cfg.symbols, cfg.assets):
            try:
                mids[a] = self._mid(sym)[2]
            except (QuoteError, BrokerError) as e:
                log.error("no tradable quote for %s (%s): skipping this run, stops stay in place", sym, e)
                self._protect(run_tag)
                return {"status": "skipped", "reason": str(e)}
        equity = st.cash + sum(st.positions[a].qty * mids[a] for a in cfg.assets)

        if st.guard.update(equity, cfg):
            log.critical("HALTED: %s. Flattening. Delete the 'guard' block in %s to resume after review.", st.guard.reason, self.path)
            targets = {a: 0.0 for a in cfg.assets}
        elif st.last_bar == last_bar:
            log.info("bar %s already processed; only re-checking protection", last_bar)
            self._protect(run_tag)
            self._commit()
            return {"status": "already-done", "bar": last_bar, "equity": equity}
        else:
            raw = {}
            for a in cfg.assets:
                close = float(frames[a]["close"].iloc[-1])
                update_trail(st.positions[a], close, self.atr[a], cfg)
                update_block(st.positions[a], close, float(sig[a]["weight"]))
                raw[a] = 0.0 if st.positions[a].blocked else float(sig[a]["weight"])
            targets = scale_portfolio(raw, cfg)

        free_quote = bal.get(cfg.quote, {}).get("free", st.cash) if bal else st.cash
        plans = plan_rebalance(equity, mids, {a: st.positions[a].qty for a in cfg.assets}, targets, cfg,
                               free_quote=min(st.cash, free_quote))
        for plan in plans:
            log.info("PLAN %s %s %.8f (~%.2f %s) %s", plan.side, plan.asset, plan.qty, plan.notional, cfg.quote, plan.reason)
            self._execute(plan, run_tag)
        self._protect(run_tag)
        st.last_bar = last_bar
        equity = st.cash + sum(st.positions[a].qty * mids[a] for a in cfg.assets)
        st.history.append({"bar": last_bar, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "equity": round(equity, 2), "cash": round(st.cash, 2),
                           "weights": {a: round(targets[a], 4) for a in cfg.assets}, "halted": st.guard.halted})
        self._commit()
        return {"status": "ok", "bar": last_bar, "equity": equity, "targets": targets, "orders": len(plans)}
