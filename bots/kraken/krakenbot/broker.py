"""Exchange access. KrakenBroker = real Kraken via ccxt (sync). PaperBroker = real public data, simulated fills.

Resilience rules implemented here (each one maps to a failure seen in the legacy bot or the October statement):
  * transient errors (network, timeout, 5xx, rate limit, nonce) are retried with exponential backoff + jitter;
  * permanent errors (bad order, insufficient funds, auth) are NOT retried and are raised loudly;
  * every order carries a deterministic client order id (Kraken `cl_ord_id`). Before re-sending after an
    ambiguous failure we look the id up first, so a timeout can never turn one order into three
    (October had 49 duplicate fills, ~4,042 USDC of unintended volume);
  * amounts/prices go through the exchange's own precision and minimums (never hard-coded decimals);
  * mode=validate sends Kraken's `validate=true` flag: the order is checked by Kraken but never placed;
  * NO CURRENCY CONVERSIONS: an order between two fiat/stable currencies (EUR->USDC, USD->USDC, USDT->USDC...)
    is refused unless you set allow_conversions=true AND list that exact pair in authorised_conversions.
    Orders in any quote currency other than cfg.quote (EUR) are refused too. Enforced on every order path.
"""
from __future__ import annotations

import logging
import os
import random
import time
import uuid
from dataclasses import dataclass

from .config import FIAT_AND_STABLES, Config

log = logging.getLogger("krakenbot.broker")


@dataclass
class Fill:
    order_id: str
    client_id: str
    side: str
    qty: float          # base filled
    avg_px: float
    fee_quote: float    # fee in quote currency (0 if Kraken charged it in the coin)
    status: str         # closed | canceled | open | validated
    fee_base: float = 0.0  # fee charged in the coin itself: reduces the coins you actually received


def client_id(*parts: str) -> str:
    """Deterministic UUID for (run date, symbol, side, purpose, attempt). Same inputs -> same id -> idempotent."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "krakenbot:" + ":".join(parts)))


class BrokerError(Exception):
    pass


class BelowMinimum(BrokerError):
    pass


class ConversionNotAuthorised(BrokerError):
    pass


def assert_tradeable(symbol: str, cfg: Config) -> None:
    """The single gate every order passes through. Raises instead of trading anything you did not authorise."""
    base, quote = symbol.split("/")
    if base in FIAT_AND_STABLES and quote in FIAT_AND_STABLES:
        if not (cfg.allow_conversions and symbol in cfg.authorised_conversions):
            raise ConversionNotAuthorised(
                f"{symbol} is a currency conversion. Refused: set allow_conversions=true and add "
                f"{symbol!r} to authorised_conversions to authorise it.")
        return
    if quote != cfg.quote:
        raise ConversionNotAuthorised(f"{symbol}: bot only trades against {cfg.quote}; refusing {quote} pair")


class KrakenBroker:
    def __init__(self, cfg: Config, api_key: str = "", secret: str = ""):
        import ccxt  # lazy: unit tests and paper backtests do not need ccxt installed

        self.ccxt = ccxt
        self.cfg = cfg
        opts = {"apiKey": api_key, "secret": secret, "enableRateLimit": True, "timeout": 20000}
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        if proxy:
            opts["https_proxy"] = proxy
        self.ex = ccxt.kraken(opts)
        self.transient = (ccxt.NetworkError, ccxt.InvalidNonce)  # NetworkError covers timeouts, DDoS/rate limit, 5xx
        self.permanent = (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.InsufficientFunds,
                          ccxt.InvalidOrder, ccxt.BadSymbol, ccxt.BadRequest)
        self._call(self.ex.load_markets)

    # --- plumbing ---------------------------------------------------------------------------------------
    def _call(self, fn, *a, **kw):
        delay = 1.0
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                return fn(*a, **kw)
            except self.permanent:
                raise
            except self.transient as e:
                if attempt == self.cfg.max_retries:
                    raise BrokerError(f"{fn.__name__} failed after {attempt} attempts: {e}") from e
                sleep = delay * (1 + random.random() * 0.25)
                log.warning("transient error in %s (attempt %d/%d): %s; retrying in %.1fs",
                            getattr(fn, "__name__", fn), attempt, self.cfg.max_retries, e, sleep)
                time.sleep(sleep)
                delay = min(delay * 2, 30)

    def market(self, symbol: str) -> dict:
        if symbol not in self.ex.markets:
            raise BrokerError(f"{symbol} is not listed on Kraken spot")
        return self.ex.markets[symbol]

    # --- market data ------------------------------------------------------------------------------------
    def daily_bars(self, symbol: str, limit: int = 400) -> list[list[float]]:
        rows = self._call(self.ex.fetch_ohlcv, symbol, self.cfg.timeframe, None, min(limit, 720))
        # Kraken returns the still-forming bar last. Trading on it is look-ahead, so drop it.
        tf_ms = self.ex.parse_timeframe(self.cfg.timeframe) * 1000
        now_ms = self.ex.milliseconds()
        return [r for r in rows if r[0] + tf_ms <= now_ms]

    def top_of_book(self, symbol: str) -> tuple[float, float, float]:
        ob = self._call(self.ex.fetch_order_book, symbol, 10)
        if not ob["bids"] or not ob["asks"]:
            raise BrokerError(f"empty order book for {symbol}")
        ts = ob.get("timestamp") or self.ex.milliseconds()
        return ob["bids"][0][0], ob["asks"][0][0], ts

    # --- account ----------------------------------------------------------------------------------------
    def balances(self) -> dict[str, dict[str, float]]:
        b = self._call(self.ex.fetch_balance)
        return {k: {"free": float(v.get("free") or 0), "total": float(v.get("total") or 0)}
                for k, v in b.items() if isinstance(v, dict) and "free" in v}

    # --- orders -----------------------------------------------------------------------------------------
    def normalise(self, symbol: str, qty: float, price: float) -> tuple[float, float]:
        q = float(self.ex.amount_to_precision(symbol, qty))
        p = float(self.ex.price_to_precision(symbol, price))
        lim = self.market(symbol)["limits"]
        min_amt = (lim.get("amount") or {}).get("min") or 0
        min_cost = (lim.get("cost") or {}).get("min") or 0
        if q <= 0 or q < min_amt or q * p < min_cost:
            raise BelowMinimum(f"{symbol} order {q}@{p} below Kraken minimum (amount {min_amt}, cost {min_cost})")
        return q, p

    def find_by_client_id(self, symbol: str, cid: str) -> dict | None:
        for fetch in (self.ex.fetch_open_orders, self.ex.fetch_closed_orders):
            try:
                for o in self._call(fetch, symbol, None, 50):
                    if o.get("clientOrderId") == cid:
                        return o
            except self.ccxt.NotSupported:
                continue
        return None

    def place_limit(self, symbol: str, side: str, qty: float, price: float, post_only: bool, cid: str) -> dict:
        assert_tradeable(symbol, self.cfg)
        q, p = self.normalise(symbol, qty, price)
        params = {"clientOrderId": cid}
        if post_only:
            params["postOnly"] = True
        else:
            params["timeInForce"] = "IOC"  # crossing order: fill what is available at <= cap, cancel the rest
        if self.cfg.mode == "validate":
            params["validate"] = True
        try:
            o = self.ex.create_order(symbol, "limit", side, q, p, params)
        except self.permanent:
            raise
        except self.transient as e:
            # Ambiguous: the order may or may not be live. Look before re-sending.
            log.warning("ambiguous create_order failure (%s); checking client id %s", e, cid)
            time.sleep(2)
            existing = self.find_by_client_id(symbol, cid)
            if existing:
                return existing
            o = self._call(self.ex.create_order, symbol, "limit", side, q, p, params)
        return o

    def place_stop(self, symbol: str, qty: float, trigger: float, cid: str) -> dict:
        """Exchange-side stop-loss (Kraken ordertype `stop-loss`): protects the position even if this bot is down."""
        assert_tradeable(symbol, self.cfg)
        q, p = self.normalise(symbol, qty, trigger)
        params = {"stopLossPrice": p, "clientOrderId": cid}
        if self.cfg.mode == "validate":
            params["validate"] = True
        existing = self.find_by_client_id(symbol, cid)
        if existing and existing.get("status") == "open":
            return existing
        return self._call(self.ex.create_order, symbol, "market", "sell", q, None, params)

    def cancel(self, order_id: str, symbol: str) -> None:
        try:
            self._call(self.ex.cancel_order, order_id, symbol)
        except self.ccxt.OrderNotFound:
            pass  # already filled or cancelled; caller re-reads the order

    def fetch(self, order_id: str, symbol: str) -> dict:
        return self._call(self.ex.fetch_order, order_id, symbol)

    def to_fill(self, o: dict, symbol: str, cid: str) -> Fill:
        filled = float(o.get("filled") or 0)
        avg = float(o.get("average") or o.get("price") or 0)
        fee = o.get("fee") or {}
        fee_cost = float(fee.get("cost") or 0)
        base, _quote = symbol.split("/")
        in_base = fee.get("currency") == base
        status = "validated" if self.cfg.mode == "validate" else (o.get("status") or "open")
        return Fill(str(o.get("id")), cid, o.get("side", ""), filled, avg, 0.0 if in_base else fee_cost, status,
                    fee_cost if in_base else 0.0)


class PaperBroker:
    """Public Kraken data (via an optional KrakenBroker without keys) + simulated, pessimistic fills."""

    def __init__(self, cfg: Config, market_data=None, slippage: float = 0.0005):
        self.cfg, self.md, self.slip = cfg, market_data, slippage
        self.orders: dict[str, dict] = {}

    def daily_bars(self, symbol, limit=400):
        return self.md.daily_bars(symbol, limit)

    def top_of_book(self, symbol):
        return self.md.top_of_book(symbol)

    def balances(self):
        return {}

    def normalise(self, symbol, qty, price):
        return (self.md.normalise(symbol, qty, price) if hasattr(self.md, "normalise") else (qty, price))

    def place_limit(self, symbol, side, qty, price, post_only, cid):
        assert_tradeable(symbol, self.cfg)
        qty, price = self.normalise(symbol, qty, price)
        bid, ask, _ = self.top_of_book(symbol)
        # Approximation: fills at once at the limit price plus slippage. Real post-only orders can miss;
        # the backtest and paper runs therefore overstate fill certainty slightly, never the price.
        px = price * (1 + self.slip if side == "buy" else 1 - self.slip)
        o = {"id": cid, "clientOrderId": cid, "side": side, "amount": qty, "filled": qty, "average": px,
             "status": "closed", "fee": {"cost": qty * px * self.cfg.fee_rate, "currency": symbol.split("/")[1]}}
        self.orders[cid] = o
        return o

    def place_stop(self, symbol, qty, trigger, cid):
        assert_tradeable(symbol, self.cfg)
        qty, trigger = self.normalise(symbol, qty, trigger)
        o = {"id": cid, "clientOrderId": cid, "side": "sell", "amount": qty, "filled": 0.0, "status": "open",
             "stopPrice": trigger, "symbol": symbol}
        self.orders[cid] = o
        return o

    def cancel(self, order_id, symbol):
        if order_id in self.orders and self.orders[order_id]["status"] == "open":
            self.orders[order_id]["status"] = "canceled"

    def fetch(self, order_id, symbol):
        o = self.orders.get(order_id)
        if o is None:
            return {"id": order_id, "status": "canceled", "filled": 0}
        if o.get("stopPrice") and o["status"] == "open":
            bars = self.daily_bars(symbol, 3)
            if bars and bars[-1][3] <= o["stopPrice"]:  # last closed bar's low traded through the stop
                px = min(bars[-1][1], o["stopPrice"]) * (1 - self.slip)
                o.update(status="closed", filled=o["amount"], average=px,
                         fee={"cost": o["amount"] * px * self.cfg.fee_rate, "currency": symbol.split("/")[1]})
        return o

    def to_fill(self, o, symbol, cid):
        fee = o.get("fee") or {}
        return Fill(str(o["id"]), cid, o.get("side", ""), float(o.get("filled") or 0), float(o.get("average") or 0),
                    float(fee.get("cost") or 0), o.get("status", "open"))
