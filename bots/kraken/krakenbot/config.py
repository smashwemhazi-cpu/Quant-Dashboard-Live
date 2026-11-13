"""All tunable inputs in one place. Every value can be overridden from a TOML file or KRAKENBOT_* env vars.

Defaults are deliberately conservative: long-only, no leverage, EUR-quoted (your account currency, so no
stablecoin/FX churn), daily bars (so fees are a rounding error, not the strategy's main expense).
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace

# Fiat and stablecoins. An order between two of these is a currency conversion, which the bot never makes unless
# you explicitly authorise it (allow_conversions = true AND the pair listed in authorised_conversions).
FIAT_AND_STABLES = frozenset({"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "USDC", "USDT", "USDG", "DAI",
                              "PYUSD", "EURC", "EURT", "TUSD", "FDUSD", "RLUSD", "USDQ", "EURQ", "ZEUR", "ZUSD"})

ENTRY_MODELS = ("trend", "range", "smc")


@dataclass(frozen=True)
class Config:
    # --- universe -------------------------------------------------------------------------------------
    quote: str = "EUR"
    assets: tuple[str, ...] = ("BTC", "ETH", "SOL")
    timeframe: str = "1d"

    # --- which entry models may open positions (any subset of ENTRY_MODELS) ---------------------------
    entry_models: tuple[str, ...] = ("trend",)

    # --- regime: up / down / range --------------------------------------------------------------------
    trend_ema: int = 100                          # price vs this EMA = side of the market
    slope_window: int = 10                        # EMA change over this many bars = direction
    slope_flat: float = 0.02                      # |EMA change| below this = flat = sideways
    er_window: int = 20                           # Kaufman efficiency ratio window
    er_range: float = 0.25                        # ER below this = choppy = sideways

    # --- trend model: multi-horizon time-series momentum -----------------------------------------------
    lookbacks: tuple[int, ...] = (20, 60, 120)
    vol_window: int = 30
    target_vol: float = 0.35                      # annualised vol target per asset sleeve
    max_weight: float = 0.45                      # cap per asset (fraction of equity)

    # --- swing structure (shared by stops, range and SMC) ---------------------------------------------
    swing_k: int = 3                              # pivot needs k lower highs/higher lows each side; confirmed k bars later
    stop_buffer_atr: float = 0.5                  # stops sit this many ATR beyond the swing point
    min_stop_atr: float = 1.0                     # a stop is never closer than this (noise)
    hard_stop_atr: float = 3.0                    # ...and never further than this (caps risk per unit)
    trail_atr: float = 3.5                        # chandelier: highest close since entry - k * ATR
    trail_swing_lows: bool = True                 # also trail under each new confirmed higher swing low
    atr_window: int = 14

    # --- range model: buy the swing-low zone of a sideways market, sell the swing-high zone -----------
    range_window: int = 20
    range_entry_zone: float = 0.25                # buy in the bottom 25% of the range...
    range_exit_zone: float = 0.80                 # ...take profit at 80% of it (below the swing-high liquidity)
    range_min_atr: float = 3.0                    # range must be wide enough to pay fees
    range_max_atr: float = 10.0
    range_min_rr: float = 1.5                     # reward:risk at entry
    range_risk: float = 0.01                      # smaller risk than trend trades
    range_max_weight: float = 0.30
    range_max_bars: int = 20                      # time stop

    # --- SMC model: liquidity sweep of a swing low + reclaim, inside bullish structure -----------------
    smc_require_bullish_structure: bool = True    # last break of structure was upward (HH after HL)
    smc_sweep_lookback: int = 3                   # the sweep happened within the last N bars
    smc_min_rr: float = 2.0                       # target = last swing high (buy-side liquidity)
    smc_risk: float = 0.01
    smc_max_weight: float = 0.30
    smc_max_bars: int = 30

    # --- risk -----------------------------------------------------------------------------------------
    max_gross: float = 1.0                        # total exposure / equity. >1.0 means margin. Keep 1.0.
    risk_per_trade: float = 0.02                  # trend trades: equity lost if the stop is hit
    max_drawdown_halt: float = 0.25               # drawdown from peak that flattens everything and halts
    cash_reserve: float = 0.05
    cooldown_bars: int = 5                        # no new entry on an asset for N bars after it was stopped out

    # --- execution ------------------------------------------------------------------------------------
    rebalance_band: float = 0.04
    min_order_quote: float = 15.0
    fee_rate: float = 0.0025
    max_slippage: float = 0.003
    post_only_wait_s: int = 90
    post_only_attempts: int = 4
    max_quote_age_s: int = 30
    max_spread: float = 0.004

    # --- currency conversion: OFF unless you authorise it ----------------------------------------------
    allow_conversions: bool = False
    authorised_conversions: tuple[str, ...] = ()  # e.g. ("USDC/EUR",) - exact pairs only

    # --- ops ------------------------------------------------------------------------------------------
    mode: str = "paper"                           # paper | validate | live
    state_file: str = "krakenbot_state.json"
    max_retries: int = 5
    allow_short: bool = False

    def validate(self) -> "Config":
        problems = []
        if self.mode not in {"paper", "validate", "live"}:
            problems.append(f"mode must be paper|validate|live, got {self.mode!r}")
        if not 0 < self.max_gross <= 2.0:
            problems.append("max_gross must be in (0, 2]; above 1.0 uses margin")
        for name in ("risk_per_trade", "range_risk", "smc_risk"):
            if not 0 < getattr(self, name) <= 0.05:
                problems.append(f"{name} must be in (0, 0.05]")
        for name in ("max_weight", "range_max_weight", "smc_max_weight"):
            if not 0 < getattr(self, name) <= 1:
                problems.append(f"{name} must be in (0, 1]")
        if not 0 < self.max_drawdown_halt < 1:
            problems.append("max_drawdown_halt must be in (0, 1)")
        if not 0 < self.min_stop_atr < self.hard_stop_atr:
            problems.append("need 0 < min_stop_atr < hard_stop_atr")
        if self.trail_atr <= 0 or self.stop_buffer_atr < 0:
            problems.append("trail_atr must be > 0 and stop_buffer_atr >= 0")
        if min(self.lookbacks) < 2 or max(self.lookbacks) >= 400:
            problems.append("lookbacks must be between 2 and 400 days")
        if not 0 < self.range_entry_zone < self.range_exit_zone <= 1:
            problems.append("need 0 < range_entry_zone < range_exit_zone <= 1")
        if self.swing_k < 1:
            problems.append("swing_k must be >= 1")
        bad = [m for m in self.entry_models if m not in ENTRY_MODELS]
        if bad or not self.entry_models:
            problems.append(f"entry_models must be a non-empty subset of {ENTRY_MODELS}, got {self.entry_models}")
        if self.allow_short and self.max_gross > 1.0:
            problems.append("refusing allow_short together with max_gross > 1.0")
        if self.quote not in FIAT_AND_STABLES:
            problems.append(f"quote {self.quote!r} must be a fiat/stable currency")
        conv = [a for a in self.assets if a in FIAT_AND_STABLES]
        if conv and not self.allow_conversions:
            problems.append(f"assets {conv} are currencies: trading them is a conversion; set allow_conversions")
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
