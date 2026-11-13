"""All tunable inputs in one place. Every value can be overridden from a TOML file or KRAKENBOT_* env vars.

Defaults are deliberately conservative: long-only, no leverage, EUR-quoted (your account currency, so no
stablecoin/FX churn), daily bars (so fees are a rounding error, not the strategy's main expense).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Config:
    # --- universe -------------------------------------------------------------------------------------
    quote: str = "EUR"
    assets: tuple[str, ...] = ("BTC", "ETH", "SOL")
    timeframe: str = "1d"

    # --- signal: multi-horizon time-series momentum, long-only ----------------------------------------
    lookbacks: tuple[int, ...] = (20, 60, 120)    # days; signal = share of lookbacks with a positive return
    trend_ema: int = 100                          # extra filter: no exposure below this EMA
    vol_window: int = 30                          # days for realised volatility
    target_vol: float = 0.35                      # annualised vol target PER ASSET SLEEVE
    max_weight: float = 0.45                      # cap per asset (fraction of equity)

    # --- risk -----------------------------------------------------------------------------------------
    max_gross: float = 1.0                        # total exposure / equity. >1.0 means margin. Keep 1.0.
    risk_per_trade: float = 0.02                  # max equity lost if the hard stop is hit (sizes the position)
    hard_stop_atr: float = 3.0                    # exchange-side stop-loss = entry - k * ATR(14)
    trail_atr: float = 3.5                        # chandelier trailing stop = highest close since entry - k * ATR
    atr_window: int = 14
    max_drawdown_halt: float = 0.25               # equity drawdown from peak that flattens everything and halts
    cash_reserve: float = 0.05                    # fraction of equity never deployed (fees, rounding, withdrawals)

    # --- execution ------------------------------------------------------------------------------------
    rebalance_band: float = 0.04                  # only trade when |target - current| > this fraction of equity
    min_order_quote: float = 15.0                 # skip dust orders (Kraken costmin is ~0.45-0.5, but fees/rounding)
    fee_rate: float = 0.0025                      # per side; your measured October rate was 0.22%
    max_slippage: float = 0.003                   # limit price never worse than mid * (1 +/- this)
    post_only_wait_s: int = 90                    # how long a post-only order may rest before it is re-priced
    post_only_attempts: int = 4                   # re-price attempts before falling back to a capped IOC limit
    max_quote_age_s: int = 30                     # reject ticker/book data older than this
    max_spread: float = 0.004                     # reject a market whose bid/ask spread is wider than this

    # --- ops ------------------------------------------------------------------------------------------
    mode: str = "paper"                           # paper | validate | live
    state_file: str = "krakenbot_state.json"
    max_retries: int = 5
    allow_short: bool = False                     # margin shorts; off. See REVIEW.md before ever enabling.

    def validate(self) -> "Config":
        problems = []
        if self.mode not in {"paper", "validate", "live"}:
            problems.append(f"mode must be paper|validate|live, got {self.mode!r}")
        if not 0 < self.max_gross <= 2.0:
            problems.append("max_gross must be in (0, 2]; above 1.0 uses margin")
        if not 0 < self.risk_per_trade <= 0.05:
            problems.append("risk_per_trade must be in (0, 0.05]")
        if not 0 < self.max_weight <= 1:
            problems.append("max_weight must be in (0, 1]")
        if not 0 < self.max_drawdown_halt < 1:
            problems.append("max_drawdown_halt must be in (0, 1)")
        if self.hard_stop_atr <= 0 or self.trail_atr <= 0:
            problems.append("stop multiples must be positive")
        if min(self.lookbacks) < 2 or max(self.lookbacks) >= 400:
            problems.append("lookbacks must be between 2 and 400 days")
        if self.allow_short and self.max_gross > 1.0:
            problems.append("refusing allow_short together with max_gross > 1.0")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def symbols(self) -> list[str]:
        return [f"{a}/{self.quote}" for a in self.assets]


def _coerce(value: str, like):
    if isinstance(like, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    if isinstance(like, tuple):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return tuple(int(p) for p in parts) if like and isinstance(like[0], int) else tuple(parts)
    return value


def load_config(path: str | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = Config()
    overrides: dict = {}
    if path:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for f in fields(Config):
            if f.name in data:
                v = data[f.name]
                overrides[f.name] = tuple(v) if isinstance(v, list) else v
    for f in fields(Config):
        key = f"KRAKENBOT_{f.name.upper()}"
        if key in env:
            overrides[f.name] = _coerce(env[key], getattr(cfg, f.name))
    return replace(cfg, **overrides).validate()
