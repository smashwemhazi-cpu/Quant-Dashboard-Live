"""OPTIONAL futures hedge engine (off unless hedge_enabled = true). Kraken Futures linear perps (PF_ contracts).

What it does, hourly:
  for each coin in hedge_assets (default: PAXG, gold) held by the harvest ledger (+ hedge_extra coins you own):
    * while the perp trades below its 200-hour average, hold a SHORT of hedge_ratio x the coins held (net flat);
    * the short's stop-loss and take-profit REST ON KRAKEN as reduce-only orders (they work while this bot is off);
    * when one of them fills, the other is cancelled; after a stop-out the coin waits 24 h before a new hedge;
    * if you hold fewer coins than the short (sold, withdrawn), the excess short is bought back at once:
      this engine never leaves a net short bet open.

Hard guards (each one refuses to trade rather than guess):
  * refuses to run if your account is on a CONSUMER fee schedule (Kraken publishes 0.25%/0.25% for those; the
    backtest loses money at that cost) or if the account's taker fee exceeds hedge_max_taker_fee;
  * never moves money: if the futures wallet lacks margin it logs how much is needed and does nothing.
    Funding the wallet is a separate, explicit command (`futures-fund`), run by you;
  * every failure is logged with its traceback; nothing is swallowed.

Evidence (Kraken PF_ hourly data 2022-2026, funding real for the last 12 months and reconstructed from Kraken's own
mark/index prices before that, daily R2 0.69-0.87): with the pro fee schedule, hedging gold beat holding it in 85%
of 81 settings and cut the drawdown in 98%; hedging BTC lost in 84%. The gold result depends on shorts receiving
funding: with zero funding the hedge lags holding. Kraken's futures demo exchange has been retired (it redirects
to kraken.com), so there is no paper venue: `hedge` without --i-understand-the-risk only prints its plan.
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from .config import Config

log = logging.getLogger("krakenbot.hedge")

PERP_ID = {"BTC": "PF_XBTUSD", "DOGE": "PF_DOGEUSD"}   # everything else is PF_<COIN>USD (PAXG, SOL, ETH, ...)


def perp_id(coin: str) -> str:
    return PERP_ID.get(coin, f"PF_{coin}USD")


def wma(x, n: int) -> np.ndarray:
    w = np.arange(1, n + 1)
    return np.convolve(np.asarray(x, float), w[::-1], "valid") / w.sum()   # newest bar weighted most


def hma(closes, n: int = 14) -> np.ndarray:
    fast, slow = wma(closes, n // 2), wma(closes, n)
    return wma(2 * fast[-len(slow):] - slow, int(math.sqrt(n)))


def exit_levels(closes, entry: float, cfg: Config) -> tuple[float, float]:
    """Take-profit below and stop above a SHORT entry, scaled by the Hull MA's recent volatility (v33.1's idea)."""
    h = hma(closes)
    radius = float(np.std(h[-20:]) / closes[-1]) if len(h) >= 20 else 0.0
    return entry * (1 - max(cfg.hedge_tp_floor, 2.0 * radius)), entry * (1 + max(cfg.hedge_sl_floor, 1.5 * radius))


class FuturesBroker:
    """Thin, explicit wrapper over ccxt.krakenfutures. Every private call here touches your real Kraken account."""

    CONSUMER = "consumer"

    def __init__(self, cfg: Config, api_key: str = "", secret: str = ""):
        import ccxt
        opts = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 20000}
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            opts["https_proxy"] = proxy
        self.ccxt, self.cfg = ccxt, cfg
        self.ex = ccxt.krakenfutures(opts)
        self.ex.load_markets()
        self.by_id = {m["id"].upper(): m["symbol"] for m in self.ex.markets.values()}

    def symbol(self, coin: str) -> str:
        sym = self.by_id.get(perp_id(coin))
        if not sym:
            raise ValueError(f"{perp_id(coin)} is not listed on Kraken Futures")
        return sym

    # --- public (real exchange data, no keys) -----------------------------------------------------------
    def hourly_closes(self, sym: str, n: int = 260) -> list[float]:
        rows = self.ex.fetch_ohlcv(sym, "1h", limit=n)
        now = self.ex.milliseconds()
        return [r[4] for r in rows if r[0] + 3_600_000 <= now]          # drop the still-forming hour

    def last(self, sym: str) -> float:
        return float(self.ex.fetch_ticker(sym)["last"])

    def funding_now(self, sym: str) -> float:
        hist = self.ex.fetch_funding_rate_history(sym)
        return float(hist[-1]["fundingRate"]) if hist else float("nan")

    def schedules(self) -> dict[str, dict]:
        return {f["uid"]: f for f in self.ex.publicGetFeeschedules().get("feeSchedules", [])}

    def instrument_schedule(self, sym: str) -> dict | None:
        uid = self.ex.market(sym)["info"].get("feeScheduleUid")
        return self.schedules().get(uid)

    # --- private (needs your keys; touches your account) ------------------------------------------------
    def account_schedules(self) -> list[dict]:
        """Fee schedules your account has volume on (Kraken: GET /feeschedules/volumes)."""
        vols = self.ex.privateGetFeeschedulesVolumes().get("volumesByFeeSchedule", {}) or {}
        all_s = self.schedules()
        return [all_s[u] for u in vols if u in all_s]

    def available_margin_usd(self) -> float:
        info = self.ex.fetch_balance({"type": "flex"}).get("info", {}) or {}
        flex = (info.get("accounts") or {}).get("flex", info)
        return float(flex.get("availableMargin") or 0.0)

    def positions(self) -> dict[str, dict]:
        out = {}
        for p in self.ex.fetch_positions():
            if abs(float(p.get("contracts") or 0)) > 0:
                out[p["symbol"]] = p
        return out

    def exit_orders(self, sym: str) -> list[dict]:
        return [o for o in self.ex.fetch_open_orders(sym) if o.get("reduceOnly") and o.get("side") == "buy"]

    def cancel_all(self, sym: str) -> None:
        self.ex.cancel_all_orders(sym)

    def short(self, sym: str, qty: float) -> None:
        self.ex.create_order(sym, "market", "sell", qty, None, {"clientOrderId": str(uuid.uuid4())})

    def buy_back(self, sym: str, qty: float) -> None:
        self.ex.create_order(sym, "market", "buy", qty, None, {"reduceOnly": True, "clientOrderId": str(uuid.uuid4())})

    def place_exits(self, sym: str, qty: float, stop: float, tp: float) -> None:
        p = lambda x: float(self.ex.price_to_precision(sym, x))
        self.ex.create_order(sym, "market", "buy", qty, None,
                             {"stopLossPrice": p(stop), "reduceOnly": True, "triggerSignal": "mark"})
        self.ex.create_order(sym, "market", "buy", qty, None,
                             {"takeProfitPrice": p(tp), "reduceOnly": True, "triggerSignal": "mark"})

    def qty(self, sym: str, q: float) -> float:
        return float(self.ex.amount_to_precision(sym, q))


def fee_check(broker, sym: str, cfg: Config, private: bool) -> tuple[bool, str]:
    """True if fees are acceptable. Uses the account's own schedules when keys are present."""
    scheds = broker.account_schedules() if private else []
    source = "your account"
    if not scheds:
        inst = broker.instrument_schedule(sym)
        scheds, source = ([inst] if inst else []), "the contract's published schedule (account has no futures volume yet)"
    if not scheds:
        return False, "could not determine any fee schedule: refusing"
    for s in scheds:
        tiers = s.get("tiers") or [{}]
        taker = float(tiers[0].get("takerFee", 99)) / 100
        if FuturesBroker.CONSUMER in s.get("name", "").lower() or taker > cfg.hedge_max_taker_fee:
            return False, f"{source} is on '{s.get('name')}' (taker {taker:.2%}); backtest loses at that cost: refusing"
    return True, f"{source}: '{scheds[0].get('name')}' taker {float((scheds[0].get('tiers') or [{}])[0].get('takerFee', 0)):.3f}%"


def hedge_targets(cfg: Config, harvest_state_path: str, spot_balances: dict | None) -> dict[str, float]:
    """Coins to hedge = harvest ledger holdings of hedge_assets + hedge_extra, capped by what the account really holds."""
    want: dict[str, float] = {}
    if os.path.exists(harvest_state_path):
        with open(harvest_state_path) as fh:
            hold = json.load(fh).get("holdings", {})
        for c in cfg.hedge_assets:
            want[c] = want.get(c, 0.0) + float((hold.get(c) or {}).get("qty") or 0)
    for item in cfg.hedge_extra:
        c, _, q = item.partition("=")
        want[c.strip().upper()] = want.get(c.strip().upper(), 0.0) + float(q)
    if spot_balances is not None:   # never hedge more than you actually own
        want = {c: min(q, float((spot_balances.get(c) or {}).get("total") or 0)) for c, q in want.items()}
    return {c: q for c, q in want.items() if q > 0}


class HedgeEngine:
    def __init__(self, cfg: Config, broker: FuturesBroker, execute: bool, private: bool | None = None, now=None):
        """execute: may send orders. private: may READ the account (keys present). execute implies private."""
        self.cfg, self.b, self.execute = cfg, broker, execute
        self.private = execute if private is None else (private or execute)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.state = {"disabled": False, "reason": "", "coins": {}, "history": []}
        if os.path.exists(cfg.hedge_state_file):
            with open(cfg.hedge_state_file) as fh:
                self.state.update(json.load(fh))

    def _save(self) -> None:
        if not self.execute:
            return
        tmp = self.cfg.hedge_state_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.state, fh, indent=2, default=str)
        os.replace(tmp, self.cfg.hedge_state_file)

    def _act(self, what: str, fn, *a) -> None:
        log.info("%s %s", "DO" if self.execute else "PLAN (not sent)", what)
        if self.execute:
            fn(*a)

    def run_once(self, targets: dict[str, float]) -> dict:
        cfg, out = self.cfg, {}
        if not cfg.hedge_enabled:
            return {"status": "disabled in config (hedge_enabled = false)"}
        if self.state.get("disabled"):
            return {"status": "halted", "reason": self.state.get("reason")}
        coins = sorted(set(cfg.hedge_assets) | set(targets))
        positions = self.b.positions() if self.private else {}
        for coin in coins:
            try:
                out[coin] = self._coin(coin, targets.get(coin, 0.0), positions)
            except Exception as e:  # noqa: BLE001 - logged with traceback, next coin still runs
                log.exception("%s hedge step failed: check positions and orders on Kraken Futures", coin)
                out[coin] = {"status": "FAILED", "error": repr(e)}
        self.state["history"] = (self.state.get("history") or [])[-500:] + [{"ts": self.now().isoformat(), **out}]
        self._save()
        return out

    def _coin(self, coin: str, held: float, positions: dict) -> dict:
        cfg, b = self.cfg, self.b
        sym = b.symbol(coin)
        st = self.state["coins"].setdefault(coin, {})
        pos = positions.get(sym)
        short_qty = abs(float(pos.get("contracts") or 0)) if pos else 0.0
        closes = b.hourly_closes(sym)
        if len(closes) < cfg.hedge_sma_hours:
            return {"status": "not enough hourly data"}
        px, sma = closes[-1], float(np.mean(closes[-cfg.hedge_sma_hours:]))
        bear = px < sma
        info = {"price": px, f"sma{cfg.hedge_sma_hours}h": round(sma, 6), "downtrend": bear, "held": held,
                "short": short_qty, "funding_now": b.funding_now(sym)}

        if pos is not None and pos.get("side") != "short":
            return {**info, "status": "REFUSED: a LONG position exists on this perp; this engine only manages hedges"}

        if short_qty > 0:
            want = b.qty(sym, held * cfg.hedge_ratio)
            if short_qty > want + 1e-12:                                   # coins sold/withdrawn: no naked short
                self._act(f"buy back {short_qty - want} {sym} (hedge larger than coins held)", b.buy_back, sym,
                          b.qty(sym, short_qty - want))
                short_qty = want
                if short_qty <= 0:
                    self._act(f"cancel leftover exits on {sym}", b.cancel_all, sym)
                    self.state["coins"][coin] = {}
                    return {**info, "status": "hedge closed: no coins left to protect"}
            if len(b.exit_orders(sym)) < 2 and st.get("stop") and st.get("tp"):
                self._act(f"re-place exits on {sym}", self._replace_exits, sym, short_qty, st["stop"], st["tp"])
            return {**info, "status": "hedged", "stop": st.get("stop"), "take_profit": st.get("tp")}

        # flat on this perp
        if st.get("stop"):                                                 # a hedge closed since the last run
            hit_stop = px >= st["stop"] * 0.995
            if hit_stop:
                st["cooldown_until"] = (self.now() + timedelta(hours=cfg.hedge_cooldown_hours)).isoformat()
            st.pop("stop", None), st.pop("tp", None)
            self._act(f"cancel the leftover exit on {sym}", b.cancel_all, sym)
        cool = st.get("cooldown_until")
        if cool and self.now() < datetime.fromisoformat(cool):
            return {**info, "status": f"cooldown after stop-out until {cool}"}
        if not bear or held <= 0:
            return {**info, "status": "no hedge needed" if held > 0 else "nothing held to hedge"}

        qty = b.qty(sym, held * cfg.hedge_ratio)
        if qty <= 0:
            return {**info, "status": "position too small for the contract's minimum size"}
        ok, why = fee_check(b, sym, cfg, private=self.private)
        info["fees"] = why
        if not ok:
            if self.execute:
                self.state.update(disabled=True, reason=why)
            return {**info, "status": "REFUSED: " + why}
        need = qty * px * cfg.hedge_margin_per_notional
        if self.private:
            have = b.available_margin_usd()
            if have < need:
                return {**info, "status": f"NOT FUNDED: need ${need:.2f} free margin in the futures wallet, have "
                                          f"${have:.2f}. Fund it yourself with `futures-fund`; this never moves money."}
        stop_tp = exit_levels(closes, px, cfg)
        self._act(f"short {qty} {sym} at market", b.short, sym, qty)
        entry = px
        if self.execute:
            pos = b.positions().get(sym) or {}
            entry = float(pos.get("entryPrice") or px)
            qty = abs(float(pos.get("contracts") or qty))
            stop_tp = exit_levels(closes, entry, cfg)
        tp, stop = stop_tp
        self._act(f"rest reduce-only STOP {stop:.6f} and TAKE-PROFIT {tp:.6f} on {sym}", b.place_exits, sym, qty, stop, tp)
        st.update(stop=stop, tp=tp, entry=entry, opened=self.now().isoformat())
        if self.execute:   # fees confirmed on the account's real schedule once it has volume
            ok, why = fee_check(b, sym, cfg, private=True)
            if not ok:
                self._act(f"close {sym}: {why}", self._close, sym, qty)
                self.state.update(disabled=True, reason=why)
                return {**info, "status": "CLOSED AND HALTED: " + why}
        return {**info, "status": "hedge opened" if self.execute else "would open hedge", "qty": qty,
                "entry": entry, "stop": stop, "take_profit": tp, "margin_needed_usd": round(need, 2)}

    def _replace_exits(self, sym, qty, stop, tp):
        self.b.cancel_all(sym)
        self.b.place_exits(sym, qty, stop, tp)

    def _close(self, sym, qty):
        self.b.buy_back(sym, qty)
        self.b.cancel_all(sym)


def futures_check(broker: FuturesBroker, cfg: Config, private: bool) -> dict:
    """Read-only report from the REAL exchange: fees, margin, positions, signal and funding for each hedge coin."""
    out = {"fee_schedules_published": {s["name"]: (s.get("tiers") or [{}])[0] for s in broker.schedules().values()}}
    if private:
        out["account_fee_schedules"] = [s["name"] for s in broker.account_schedules()] or "no futures volume yet"
        out["available_margin_usd"] = broker.available_margin_usd()
        out["open_positions"] = {k: {"side": v.get("side"), "contracts": v.get("contracts"),
                                     "entry": v.get("entryPrice")} for k, v in broker.positions().items()}
    for c in cfg.hedge_assets:
        sym = broker.symbol(c)
        closes = broker.hourly_closes(sym)
        sma = float(np.mean(closes[-cfg.hedge_sma_hours:]))
        out[c] = {"perp": perp_id(c), "price": closes[-1], f"sma{cfg.hedge_sma_hours}h": round(sma, 4),
                  "downtrend_now": closes[-1] < sma, "funding_rate_now": broker.funding_now(sym),
                  "contract_fee_schedule": (broker.instrument_schedule(sym) or {}).get("name")}
    return out


def fund_futures(spot_exchange, asset: str, amount: float, execute: bool) -> dict:
    """Spot -> Futures wallet via Kraken's WalletTransfer (the only direction that endpoint supports)."""
    if not execute:
        return {"plan": f"transfer {amount} {asset} from Spot Wallet to Futures Wallet", "sent": False}
    r = spot_exchange.transfer(asset, amount, "spot", "swap")
    return {"sent": True, "response": r}


def withdraw_futures(fut_exchange, asset: str, amount: float, execute: bool) -> dict:
    """Futures -> Spot uses the FUTURES API (Kraken docs: the spot endpoint cannot do this direction)."""
    if not execute:
        return {"plan": f"transfer {amount} {asset} from Futures (flex) to Spot", "sent": False}
    return {"sent": True, "response": fut_exchange.transfer(asset, amount, "flex", "spot")}


