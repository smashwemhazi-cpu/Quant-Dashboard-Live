"""Daily bars for the report watchlist, TradingView first.

Priority per symbol:
  1. TradingView CSV export (history)   data/tradingview/<KEY>.csv   (chart -> Export chart data)
  2. TradingView webhook bars (daily)   DONNA's tv_bars table         (donna_daily_snapshot.pine)
  3. Yahoo Finance via yfinance          fallback, labelled as such
1+2 are merged so history comes from the export and new days arrive by webhook.
Every result carries its `source` and last bar date so reports can flag stale data.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
MIN_TV_BARS = int(os.getenv("JARVIS_MIN_TV_BARS", "260"))
TV_EXPORT_DIR = Path(os.getenv("JARVIS_TV_EXPORT_DIR", str(_ROOT / "data" / "tradingview")))


@dataclass(frozen=True)
class Instrument:
    key: str          # stable id used in files/DB
    label: str        # what the report prints
    tv: str           # TradingView symbol (must match the Pine inputs)
    yahoo: str        # fallback ticker (futures where Yahoo has no spot)
    kind: str         # fx | metal | energy | index | crypto
    unit: str         # "$", "pts", "EUR pts" ...
    decimals: int


WATCHLIST: list[Instrument] = [
    Instrument("XAUUSD", "Gold (XAU)", "OANDA:XAUUSD", "GC=F", "metal", "$", 2),
    Instrument("XAGUSD", "Silver (XAG)", "OANDA:XAGUSD", "SI=F", "metal", "$", 3),
    Instrument("WTI", "Crude Oil (WTI)", "TVC:USOIL", "CL=F", "energy", "$", 2),
    Instrument("US30", "US30 (Dow)", "TVC:DJI", "^DJI", "index", "pts", 0),
    Instrument("NAS100", "NAS100 (Nasdaq)", "NASDAQ:NDX", "^NDX", "index", "pts", 1),
    Instrument("GER40", "GER40 (DAX)", "XETR:DAX", "^GDAXI", "index", "EUR pts", 1),
    Instrument("BTCUSD", "BTC/USD", "BITSTAMP:BTCUSD", "BTC-USD", "crypto", "$", 0),
    Instrument("EURUSD", "EUR/USD", "OANDA:EURUSD", "EURUSD=X", "fx", "", 5),
    Instrument("GBPUSD", "GBP/USD", "OANDA:GBPUSD", "GBPUSD=X", "fx", "", 5),
]


def _from_export(inst: Instrument) -> pd.DataFrame:
    p = TV_EXPORT_DIR / f"{inst.key}.csv"
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_csv(p)
    d.columns = [c.strip().lower() for c in d.columns]
    if pd.api.types.is_numeric_dtype(d["time"]):  # TradingView exports unix seconds; accept ms too
        t = pd.to_datetime(d["time"], unit="ms" if d["time"].max() > 1e11 else "s", errors="coerce")
    else:
        t = pd.to_datetime(d["time"], errors="coerce", utc=True).dt.tz_localize(None)
    return d.assign(date=t.dt.normalize())[["date", "open", "high", "low", "close"]].dropna()


def _from_webhook(inst: Instrument) -> pd.DataFrame:
    db = os.getenv("DONNA_DB_PATH")
    if not db or not Path(db).exists():
        return pd.DataFrame()
    with sqlite3.connect(db) as c:
        d = pd.read_sql_query("SELECT bar_time, open, high, low, close FROM tv_bars WHERE symbol = ?", c,
                              params=(inst.tv,))
    return d.assign(date=pd.to_datetime(d.pop("bar_time"))) if len(d) else d


def _from_yahoo(inst: Instrument, period: str) -> pd.DataFrame:
    import yfinance as yf

    h = yf.Ticker(inst.yahoo).history(period=period, auto_adjust=False)
    if h.empty:
        return pd.DataFrame()
    h.index = h.index.tz_localize(None) if h.index.tz is not None else h.index
    return pd.DataFrame({"date": h.index.normalize(), "open": h["Open"], "high": h["High"],
                         "low": h["Low"], "close": h["Close"]}).reset_index(drop=True)


def daily_bars(inst: Instrument, period: str = "6y") -> tuple[pd.DataFrame, str]:
    """Returns (date-indexed OHLC, source label)."""
    tv = pd.concat([_from_export(inst), _from_webhook(inst)], ignore_index=True)
    # Only trust TradingView once it has real history (CSV export); a few webhook days alone
    # would starve the predictor and 5-day stats, so fall back to Yahoo until then.
    if len(tv) >= MIN_TV_BARS:
        df, src = tv, "TradingView"
    else:
        try:
            df, src = _from_yahoo(inst, period), f"Yahoo ({inst.yahoo})"
        except Exception as e:  # network/ratelimit
            return pd.DataFrame(), f"unavailable: {type(e).__name__}"
    if df.empty:
        return df, "unavailable: no data"
    df = df.drop_duplicates("date", keep="last").sort_values("date").set_index("date")
    return df.astype(float), src
