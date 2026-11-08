"""When markets are open, and which data sources DONNA uses at all. Plain rules, no network.

Market hours (UTC, the broker's trading week):
  FX, metals, indices, energies, stocks: Sunday 21:00 -> Friday 22:00, 5 days a week, plus the closed
      holidays below (25 December, 1 January). Outside that a quiet price feed, a cTrader server in
      weekend maintenance or no new trade is normal: no alerts, no red flags, no "failing".
  Crypto (BTC, ETH, ...): 7 days a week, 24 hours.
Back-office reports are produced on working days, so their age is counted in working days (Mon-Fri):
a report from Friday is still current on Sunday.

Data sources switched off: Syntellicore (the CRM) is not available to SH, so everything built on its
exports (CRM KPIs, CRM leads, customer documents / KYC, IB commissions) shows as "not used" instead of
missing or red. Turn it back on with JARVIS_SYNTELLICORE=on in /etc/vertex1/vertex1.env.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

# ------------------------------------------------------------------ market hours
WEEK_OPEN_HOUR = int(os.getenv("DONNA_MARKET_OPEN_HOUR_SUN", "21"))     # Sunday, UTC
WEEK_CLOSE_HOUR = int(os.getenv("DONNA_MARKET_CLOSE_HOUR_FRI", "22"))   # Friday, UTC
HOLIDAYS = {(12, 25), (1, 1)}                                           # (month, day): FX / CFDs closed all day
CRYPTO = tuple(x.strip().upper() for x in os.getenv(
    "DONNA_CRYPTO_SYMBOLS", "BTC,ETH,XRP,LTC,SOL,DOGE,ADA,BNB,DOT,BCH,LINK,AVAX,XLM,TRX,MATIC,SHIB,UNI,TON,"
                            "XBT").split(",") if x.strip())


def _utc(now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def is_crypto(symbol: str | None) -> bool:
    s = (symbol or "").upper()
    return any(s.startswith(c) for c in CRYPTO)


def fx_open(now: datetime | None = None) -> bool:
    """True while the 5-day market (FX, metals, indices) trades."""
    now = _utc(now)
    if (now.month, now.day) in HOLIDAYS:
        return False
    wd, h = now.weekday(), now.hour
    return not ((wd == 4 and h >= WEEK_CLOSE_HOUR) or wd == 5 or (wd == 6 and h < WEEK_OPEN_HOUR))


def is_open(symbol: str | None = None, now: datetime | None = None) -> bool:
    """Crypto: always. Anything else: the 5-day week."""
    return True if is_crypto(symbol) else fx_open(now)


def open_hours_between(start: datetime, end: datetime | None = None, symbol: str | None = None) -> float:
    """Hours the market was open between two moments (crypto: plain hours), walked in 15-minute steps:
    precise enough for freshness checks and cheap over a few weeks."""
    start, end = _utc(start), _utc(end)
    if end <= start:
        return 0.0
    if is_crypto(symbol):
        return (end - start).total_seconds() / 3600
    step, t, mins = timedelta(minutes=15), start, 0.0
    while t < end:
        nxt = min(t + step, end)
        if fx_open(t):
            mins += (nxt - t).total_seconds() / 60
        t = nxt
    return mins / 60


def working_days_between(last: date, today: date) -> int:
    """Working days (Mon-Fri) after `last` up to and including `today`: Friday -> Monday is 1, Friday -> Sunday 0."""
    if today <= last:
        return 0
    n, d = 0, last
    span = (today - last).days
    if span > 400:                       # very old: no need to walk every day
        return span * 5 // 7
    while d < today:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def closed_note(now: datetime | None = None) -> str:
    """Plain words for why things are quiet, '' while the market is open."""
    now = _utc(now)
    if fx_open(now):
        return ""
    if (now.month, now.day) in HOLIDAYS:
        return "markets closed for the holiday (crypto still trades)"
    return "markets closed for the weekend (crypto still trades); FX, metals and indices reopen Sunday " \
           f"{WEEK_OPEN_HOUR:02d}:00 UTC"


# ------------------------------------------------------------------ data sources switched off
SYNTELLICORE_KINDS = ("docs", "kpi", "leads", "ib")      # export kinds that come from the Syntellicore CRM
NOT_USED = "not used (Syntellicore is switched off)"


def syntellicore_on() -> bool:
    return os.getenv("JARVIS_SYNTELLICORE", "off").strip().lower() in ("1", "on", "yes", "true")


def source_off(kind: str) -> bool:
    """True when this export kind comes from a source DONNA doesn't use, so it must never count as missing."""
    return kind in SYNTELLICORE_KINDS and not syntellicore_on()
