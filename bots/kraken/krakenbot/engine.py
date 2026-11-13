"""One daily cycle. Run ONCE PER DAY just after the 00:00 UTC close (cron / systemd timer / Task Scheduler).

Guarantees:
  * idempotent: re-running on the same day resumes and re-checks protection; it never trades the day twice;
  * every position gets its Kraken stop-loss IMMEDIATELY after each fill (not at the end of the run), and again
    after any partial sell; a position that cannot get a stop after 3 tries is closed;
  * if price is already through the stop when we look, we exit instead of placing a stop that would fire at once;
  * a stop that filled while the bot was off is detected and booked (partial fills too);
  * fees charged in the coin (instead of EUR) reduce the coin quantity the ledger thinks it holds;
  * a position worth less than Kraken's minimum order is written off the ledger as dust, never left "protected"
    by a stop Kraken would reject;
  * sub-account ledger: only the EUR `capital` you gave it and the coins IT bought. Your other coins are not touched;
  * no currency conversions (EUR->USDC etc.) ever, unless authorised in the config (broker enforces it).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .broker import BelowMinimum, BrokerError, client_id
from .config import Config
from .risk import (DrawdownGuard, OrderPlan, PositionState, QuoteError, check_quote, limit_price, new_bar,
                   on_buy_fill, on_sell_fill, on_stopped_out, plan_rebalance)
from .signals import features, scale_portfolio, to_frame
from .strategy import Decision, decide

log = logging.getLogger("krakenbot.engine")


@dataclass
class State:
    cash: float = 0.0
    positions: dict[str, PositionState] = field(default_factory=dict)
    guard: DrawdownGuard = field(default_factory=DrawdownGuard)
    last_bar: str = ""
    history: list[dict] = field(default_factory=list)

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
        self.atr: dict[str, float] = {}
        self.decisions: dict[str, Decision] = {}
        self.run_tag = ""
        self._in_protect = False  # stops protect -> sell -> protect recursion when an exit keeps failing

    # --- helpers ---------------------------------------------------------------------------------------
    def _sym(self, a: str) -> str:
        return f"{a}/{self.cfg.quote}"

    def _commit(self) -> None:
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
                    p.stop = p.entry = p.high_close = p.take_profit = 0.0
                    p.stop_order_id, p.sleeve = None, ""

    def _book_sell(self, a: str, f) -> None:
        self.st.cash += f.qty * f.avg_px - f.fee_quote
        on_sell_fill(self.st.positions[a], f.qty + f.fee_base)

    def _check_stops(self) -> None:
        """Did an exchange-side stop fill since the last run? Book it (fully or partially)."""
        for a, p in self.st.positions.items():
            if not p.stop_order_id:
                continue
            sym = self._sym(a)
            o = self.b.fetch(p.stop_order_id, sym)
            filled = float(o.get("filled") or 0)
            status = o.get("status")
            if filled > 0 and status in {"closed", "canceled", "expired"}:
                f = self.b.to_fill(o, sym, p.stop_order_id)
                stop_level, high_close = p.stop, p.high_close
                full = filled >= p.qty - 1e-12
                self._book_sell(a, f)
                log.warning("%s STOP FILLED: %.8f @ %.4f (stop %.4f)%s", a, f.qty, f.avg_px, stop_level,
                            "" if full else " - partial; remainder will be re-protected")
                if full or p.qty <= 0:
                    on_stopped_out(p, self.cfg, high_close)
                else:
                    p.stop_order_id = None
            elif status in {"canceled", "expired", "rejected"}:
                log.error("%s protective stop %s is %s; it will be re-placed", a, p.stop_order_id, status)
                p.stop_order_id = None

    # --- protection --------------------------------------------------------------------------------------
    def _protect_one(self, a: str) -> None:
        """Make sure position `a` has a resting Kraken stop-loss at its current stop level (or is closed)."""
        if self._in_protect:
            return
        self._in_protect = True
        try:
            self._protect_inner(a)
        finally:
            self._in_protect = False

    def _protect_inner(self, a: str) -> None:
        p, sym = self.st.positions[a], self._sym(a)
        if p.qty <= 0:
            if p.stop_order_id:
                self.b.cancel(p.stop_order_id, sym)
                p.stop_order_id = None
            return
        if p.stop <= 0:
            log.critical("%s has a position but no stop level; flattening", a)
            self._execute(OrderPlan(a, "sell", p.qty, 0.0, "no stop level"), stop=0.0)
            return
        try:
            bid, ask, mid = self._mid(sym)
        except (QuoteError, BrokerError) as e:
            log.error("%s: cannot read price (%s); keeping existing stop as is", a, e)
            return
        if mid <= p.stop:
            log.warning("%s price %.4f already through stop %.4f: exiting now", a, mid, p.stop)
            high_close = p.high_close
            self._execute(OrderPlan(a, "sell", p.qty, p.qty * mid, "price through stop"), stop=0.0)
            if p.qty <= 0:
                on_stopped_out(p, self.cfg, high_close)
            else:
                log.critical("%s exit through stop did not complete; %.8f left UNPROTECTED - check Kraken now", a, p.qty)
            return
        if p.stop_order_id:
            o = self.b.fetch(p.stop_order_id, sym)
            resting = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
            same_qty = abs(float(o.get("amount") or 0) - p.qty) <= max(1e-8, p.qty * 1e-4)
            if o.get("status") == "open" and same_qty and abs(resting - p.stop) / p.stop < 0.002:
                return
            self.b.cancel(p.stop_order_id, sym)
            p.stop_order_id = None
        cid = client_id(self.run_tag, sym, "stop", f"{p.stop:.8f}", f"{p.qty:.8f}")
        for attempt in range(3):
            try:
                o = self.b.place_stop(sym, p.qty, p.stop, cid)
                p.stop_order_id = None if self.cfg.mode == "validate" else str(o["id"])
                log.info("STOP %s %.8f @ %.4f (%s)", sym, p.qty, p.stop,
                         "validated, not placed" if self.cfg.mode == "validate" else "resting on Kraken")
                self._commit()
                return
            except BelowMinimum as e:
                log.warning("%s position %.8f is below Kraken's minimum (%s): writing it off the ledger as dust",
                            a, p.qty, e)
                on_sell_fill(p, p.qty)
                self._commit()
                return
            except Exception as e:  # noqa: BLE001 - every other failure is handled the same way
                log.error("stop placement %s failed (%d/3): %s", sym, attempt + 1, e)
                self.sleep(5)
        log.critical("%s has NO protective stop after 3 attempts: flattening", sym)
        self._execute(OrderPlan(a, "sell", p.qty, p.qty * mid, "no-stop flatten"), stop=0.0)

    # --- execution ---------------------------------------------------------------------------------------
    def _execute(self, plan: OrderPlan, stop: float, sleeve: str = "", take_profit: float = 0.0) -> None:
        a, sym = plan.asset, self._sym(plan.asset)
        p = self.st.positions[a]
        remaining = plan.qty
        if plan.side == "sell" and p.stop_order_id:
            self.b.cancel(p.stop_order_id, sym)  # the stop reserves the coins on spot; release them first
            p.stop_order_id = None
        for attempt in range(self.cfg.post_only_attempts + 1):
            try:
                bid, ask, mid = self._mid(sym)
            except (QuoteError, BrokerError) as e:
                log.error("%s: no tradable quote (%s); aborting %s", sym, e, plan.side)
                break
            if remaining * mid < self.cfg.min_order_quote and plan.side == "buy":
                break
            if plan.side == "buy" and mid <= stop:
                log.warning("%s: price %.4f is at/below the planned stop %.4f; entry cancelled", sym, mid, stop)
                break
            price, post_only = limit_price(plan.side, bid, ask, mid, attempt, self.cfg)
            cid = client_id(self.run_tag, sym, plan.side, plan.reason, str(attempt))
            try:
                o = self.b.place_limit(sym, plan.side, remaining, price, post_only, cid)
            except BrokerError as e:  # below minimum / not authorised / retries exhausted
                log.error("%s %s not placed: %s", plan.side, sym, e)
                break
            except Exception as e:  # noqa: BLE001 - e.g. post-only rejected because the book moved: re-price
                log.error("%s %s attempt %d rejected: %s", plan.side, sym, attempt, e)
                continue
            if self.cfg.mode == "validate":
                log.info("VALIDATED (not placed) %s %s %.8f @ %.4f post_only=%s", plan.side, sym, remaining, price, post_only)
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
                    on_buy_fill(p, f.avg_px, f.qty - f.fee_base, stop, sleeve, take_profit)
                else:
                    self._book_sell(a, f)
                log.info("FILL %s %s %.8f @ %.4f fee %.4f %s (attempt %d)", plan.side, sym, f.qty, f.avg_px,
                         f.fee_quote, self.cfg.quote, attempt)
                remaining -= f.qty
                self._commit()  # persist after every fill: a crash now cannot double-buy on restart
                if plan.side == "buy":
                    self._protect_one(a)  # stop goes on Kraken right after EACH fill, not at the end
            if remaining <= 1e-12 or (plan.side == "sell" and p.qty <= 0):
                break
        if plan.side == "sell" and self.st.positions[a].qty > 0:
            self._protect_one(a)  # partial sell / trim: re-protect what is left at once

    # --- the cycle ---------------------------------------------------------------------------------------
    def run_once(self) -> dict:
        cfg, st = self.cfg, self.st
        feats = {a: features(to_frame(self.b.daily_bars(self._sym(a), 400)), cfg) for a in cfg.assets}
        last_bar = min(f.index[-1] for f in feats.values()).date().isoformat()
        self.run_tag = last_bar
        rows = {a: feats[a].iloc[-1] for a in cfg.assets}
        self.atr = {a: float(rows[a]["atr"]) for a in cfg.assets}

        bal = self.b.balances()
        self._reconcile_holdings(bal)
        self._check_stops()

        mids = {}
        for a in cfg.assets:
            try:
                mids[a] = self._mid(self._sym(a))[2]
            except (QuoteError, BrokerError) as e:
                log.error("no tradable quote for %s (%s): skipping this run, stops stay in place", a, e)
                for b in cfg.assets:
                    self._protect_one(b)
                self._commit()
                return {"status": "skipped", "reason": str(e)}
        equity = st.cash + sum(st.positions[a].qty * mids[a] for a in cfg.assets)

        if st.guard.update(equity, cfg):
            log.critical("HALTED: %s. Flattening. Delete the 'guard' block in %s to resume after review.",
                         st.guard.reason, self.path)
            self.decisions = {a: Decision(0.0, reason="drawdown halt") for a in cfg.assets}
            targets = {a: 0.0 for a in cfg.assets}
        elif st.last_bar == last_bar:
            log.info("bar %s already processed; only re-checking protection", last_bar)
            for a in cfg.assets:
                self._protect_one(a)
            self._commit()
            return {"status": "already-done", "bar": last_bar, "equity": equity}
        else:
            raw = {}
            for a in cfg.assets:
                p, row = st.positions[a], rows[a]
                new_bar(p, float(row["close"]), float(row["atr"]), float(row["swing_low"]), float(row["trend_w"]), cfg)
                d = decide(row, p, cfg)
                self.decisions[a] = d
                if p.qty > 0 and not d.is_exit:
                    p.sleeve = d.sleeve or p.sleeve
                    p.take_profit = d.take_profit
                    p.stop = max(p.stop, d.stop)
                raw[a] = (p.qty * mids[a] / equity) if d.weight is None else d.weight
                log.info("DECIDE %s regime=%s -> %s", a, row["regime"], d.as_dict())
            targets = scale_portfolio(raw, cfg)

        free_quote = bal.get(cfg.quote, {}).get("free", st.cash) if bal else st.cash
        plans = plan_rebalance(equity, mids, {a: st.positions[a].qty for a in cfg.assets}, targets, cfg,
                               free_quote=min(st.cash, free_quote),
                               reasons={a: d.reason for a, d in self.decisions.items()})
        for plan in plans:
            d = self.decisions.get(plan.asset, Decision(0.0))
            log.info("PLAN %s %s %.8f (~%.2f %s) %s", plan.side, plan.asset, plan.qty, plan.notional, cfg.quote, plan.reason)
            self._execute(plan, stop=d.stop, sleeve=d.sleeve, take_profit=d.take_profit)
        for a in cfg.assets:
            self._protect_one(a)  # ratcheted stops for positions that did not trade today
        st.last_bar = last_bar
        equity = st.cash + sum(st.positions[a].qty * mids[a] for a in cfg.assets)
        st.history.append({"bar": last_bar, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "equity": round(equity, 2), "cash": round(st.cash, 2),
                           "weights": {a: round(targets[a], 4) for a in cfg.assets},
                           "stops": {a: round(st.positions[a].stop, 6) for a in cfg.assets if st.positions[a].qty > 0},
                           "halted": st.guard.halted})
        self._commit()
        return {"status": "ok", "bar": last_bar, "equity": equity, "targets": targets, "orders": len(plans),
                "decisions": {a: d.as_dict() for a, d in self.decisions.items()}}
