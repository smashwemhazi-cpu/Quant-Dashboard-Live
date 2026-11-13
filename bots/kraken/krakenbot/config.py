"""Every tunable input in one place. Override with a TOML file (--config) or KRAKENBOT_<NAME> env vars.

Strategy: two momentum sleeves on Kraken SPOT, EUR pairs, rebalanced weekly.
  * DUAL MOMENTUM (60%): hold whichever of BTC or gold (PAXG) has the stronger risk-adjusted momentum, if it is
    in an uptrend; otherwise EUR. The anchor: positive in every year tested, max drawdown ~-16%.
  * ROTATION (40%): hold the top 2 of six liquid coins plus gold by the same score, each only if in an uptrend.
    The growth engine: captures crypto bull runs, gold when crypto bleeds.
No perps, no futures, no margin, no shorts, no currency conversions: see bots/kraken/REVIEW.md for why.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace

# An order between two of these is a currency conversion: refused unless explicitly authorised.
FIAT_AND_STABLES = frozenset({"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "USDC", "USDT", "USDG", "DAI",
                              "PYUSD", "EURC", "EURT", "TUSD", "FDUSD", "RLUSD", "USDQ", "EURQ", "ZEUR", "ZUSD"})



@dataclass(frozen=True)
class Config:
    quote: str = "EUR"
    timeframe: str = "1d"

    # --- sleeves (weights are shares of the portfolio; they need not sum to 1, the rest stays EUR) --------
    dual_weight: float = 0.60
    dual_assets: tuple[str, ...] = ("BTC", "PAXG")
    dual_top_k: int = 1
    rotation_weight: float = 0.40
    rotation_assets: tuple[str, ...] = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "PAXG")
    rotation_top_k: int = 2

    # --- ranking (shared by both sleeves) ----------------------------------------------------------------
    rank_lookbacks: tuple[int, ...] = (30, 60, 90)  # score = mean(return over L / volatility) across L
    trend_lookbacks: tuple[int, ...] = (10, 20, 40, 80, 160)  # asset must be up over most of these to qualify
    vol_window: int = 30
    target_vol: float = 0.40                      # annualised vol per holding -> weight = target_vol / vol / top_k
    max_weight: float = 0.50                      # cap per asset within a sleeve
    rebalance_weekday: int = 0                    # 0 = rebalance on Monday's daily close (acted on just after)

    # --- risk -----------------------------------------------------------------------------------------
    stop_sigma: float = 4.0                       # disaster stop: price x (1 - k x one-week volatility), on Kraken,
                                                  # reset only at each weekly rebalance, up only
    max_drawdown_halt: float = 0.35               # flatten and halt: beyond anything seen in the backtest (-20%)
    cash_reserve: float = 0.03                    # never invested (fees, rounding)

    # --- execution ------------------------------------------------------------------------------------
    rebalance_band: float = 0.03                  # skip trades smaller than this fraction of equity
    min_order_quote: float = 10.0
    fee_rate: float = 0.0025                      # per side; your measured October rate was 0.22%
    max_slippage: float = 0.003                   # limit price never worse than mid * (1 +/- this)
    post_only_wait_s: int = 90
    post_only_attempts: int = 4
    max_quote_age_s: int = 30
    max_spread: float = 0.006                     # skip an asset whose bid/ask spread is wider than this

    # --- currency conversion: OFF unless you authorise it ----------------------------------------------
    allow_conversions: bool = False
    authorised_conversions: tuple[str, ...] = ()  # exact pairs, e.g. ("USDC/EUR",)

    # --- ops ------------------------------------------------------------------------------------------
    mode: str = "paper"                           # paper | validate | live
    state_file: str = "krakenbot_state.json"
    max_retries: int = 5

    def validate(self) -> "Config":
        p = []
        if self.mode not in {"paper", "validate", "live"}:
            p.append(f"mode must be paper|validate|live, got {self.mode!r}")
        for name, w, assets, k in self.sleeves:
            if not 0 <= w <= 1:
                p.append(f"{name}_weight must be in [0, 1]")
            if w > 0 and not 1 <= k <= len(assets):
                p.append(f"{name}_top_k must be between 1 and the number of {name}_assets")
        if self.dual_weight + self.rotation_weight > 1 + 1e-9:
            p.append("dual_weight + rotation_weight must not exceed 1 (no leverage)")
        if not 0 < self.max_weight <= 1 or not 0 < self.target_vol <= 2:
            p.append("max_weight must be in (0, 1] and target_vol in (0, 2]")
        if not 0 < self.max_drawdown_halt < 1 or not 0 <= self.cash_reserve < 0.5:
            p.append("max_drawdown_halt must be in (0, 1) and cash_reserve in [0, 0.5)")
        if not 1.0 <= self.stop_sigma <= 10.0:
            p.append("stop_sigma must be in [1, 10]")
        if not 0 <= self.rebalance_weekday <= 6:
            p.append("rebalance_weekday must be 0..6")
        if min(self.rank_lookbacks + self.trend_lookbacks) < 2 or max(self.rank_lookbacks + self.trend_lookbacks) > 300:
            p.append("lookbacks must be between 2 and 300 days")
        if self.quote not in FIAT_AND_STABLES:
            p.append(f"quote {self.quote!r} must be a fiat/stable currency")
        conv = [a for a in self.assets if a in FIAT_AND_STABLES]
        if conv and not self.allow_conversions:
            p.append(f"assets {conv} are currencies: trading them is a conversion; set allow_conversions")
        for name, _, assets, _ in self.sleeves:
            if len(set(assets)) != len(assets):
                p.append(f"duplicate entries in {name}_assets")
        if p:
            raise ValueError("; ".join(p))
        return self

    @property
    def sleeves(self) -> list[tuple[str, float, tuple[str, ...], int]]:
        return [("dual", self.dual_weight, self.dual_assets, self.dual_top_k),
                ("rotation", self.rotation_weight, self.rotation_assets, self.rotation_top_k)]

    @property
    def assets(self) -> tuple[str, ...]:
        seen: list[str] = []
        for _, w, assets, _ in self.sleeves:
            seen += [a for a in assets if w > 0 and a not in seen]
        return tuple(seen)

    @property
    def symbols(self) -> list[str]:
        return [f"{a}/{self.quote}" for a in self.assets]

    @property
    def warmup(self) -> int:
        return max(self.rank_lookbacks + self.trend_lookbacks + (self.vol_window,)) + 1


def _coerce(value: str, like):
    if isinstance(like, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    if isinstance(like, tuple):
        parts = [x.strip() for x in value.split(",") if x.strip()]
        return tuple(int(x) for x in parts) if like and isinstance(like[0], int) else tuple(parts)
    return value


def load_config(path: str | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    cfg, over = Config(), {}
    if path:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for f in fields(Config):
            if f.name in data:
                v = data[f.name]
                over[f.name] = tuple(v) if isinstance(v, list) else v
    for f in fields(Config):
        key = f"KRAKENBOT_{f.name.upper()}"
        if key in env:
            over[f.name] = _coerce(env[key], getattr(cfg, f.name))
    return replace(cfg, **over).validate()
