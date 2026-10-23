"""Central settings. Everything is env-driven so the same code runs locally,
on Streamlit Cloud (st.secrets are exported as env vars) or in a container."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _f(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- AI brain ---
    model: str = os.getenv("JARVIS_MODEL", "claude-opus-5-5")
    effort: str = os.getenv("JARVIS_EFFORT", "medium")
    max_tool_turns: int = int(os.getenv("JARVIS_MAX_TOOL_TURNS", "12"))

    # --- storage ---
    state_db: str = os.getenv("JARVIS_STATE_DB", str(_ROOT / "data" / "jarvis_state.db"))
    data_dir: str = os.getenv("JARVIS_DATA_DIR", str(_ROOT / "data" / "samples"))

    # --- personal trading risk limits (all adjustable) ---
    risk_per_trade_pct: float = _f("JARVIS_RISK_PER_TRADE_PCT", 0.5)
    max_daily_loss_pct: float = _f("JARVIS_MAX_DAILY_LOSS_PCT", 2.0)
    max_open_risk_pct: float = _f("JARVIS_MAX_OPEN_RISK_PCT", 3.0)
    max_slippage_pips: float = _f("JARVIS_MAX_SLIPPAGE_PIPS", 1.5)
    max_spread_pips: float = _f("JARVIS_MAX_SPREAD_PIPS", 2.5)
    trail_atr_mult: float = _f("JARVIS_TRAIL_ATR_MULT", 2.0)
    stop_atr_mult: float = _f("JARVIS_STOP_ATR_MULT", 1.5)

    # --- business thresholds ---
    churn_inactive_days: int = int(os.getenv("JARVIS_CHURN_INACTIVE_DAYS", "21"))
    stale_deal_days: int = int(os.getenv("JARVIS_STALE_DEAL_DAYS", "14"))

    # --- email (read-only IMAP; JARVIS drafts, a human sends) ---
    imap_host: str = os.getenv("JARVIS_IMAP_HOST", "")
    imap_user: str = os.getenv("JARVIS_IMAP_USER", "")
    imap_password: str = os.getenv("JARVIS_IMAP_PASSWORD", "")


settings = Settings()
