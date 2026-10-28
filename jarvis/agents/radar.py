"""Partner Radar: a shortlist of potential IBs (trading educators, academies, communities) per country.

Source: the YouTube Data API v3 (free key, 10,000 units a day; one weekly run uses about 2,000).
Public business information only: channel name, link, country, subscribers, videos, views, last upload.
DONNA never contacts anyone. You approve or reject each prospect, and outreach is a draft you send yourself.

Countries: only the ones you have cleared for marketing (!radar countries ZA,MU,KE). Until you set them,
the radar does not run - promoting CFDs in a country usually needs a licence there.

Fit score (0-100): audience size, views per video, posting in the last 60 days, trading-education words.
Red flags lower the score and are shown: promises of returns, "account management", paid signals,
prop-challenge passing. An IB who markets like that puts the firm's licence at risk.

Setting: JARVIS_YOUTUBE_API_KEY (vertex1-connect radar). Weekly on DONNA_RADAR_WEEKDAY (default 0 = Monday).
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from jarvis.core import audit
from jarvis.core.config import settings

API = "https://www.googleapis.com/youtube/v3"
RADAR_WEEKDAY = int(os.getenv("DONNA_RADAR_WEEKDAY", "0"))
COUNTRIES = {"ZA": ("South Africa", ["Johannesburg", "Cape Town", "Durban"]), "MU": ("Mauritius", ["Port Louis"]),
             "KE": ("Kenya", ["Nairobi", "Mombasa"]), "NG": ("Nigeria", ["Lagos", "Abuja"]), "GH": ("Ghana", ["Accra", "Kumasi"]),
             "AE": ("United Arab Emirates", ["Dubai", "Abu Dhabi"]), "SA": ("Saudi Arabia", ["Riyadh", "Jeddah"]),
             "QA": ("Qatar", ["Doha"]), "KW": ("Kuwait", ["Kuwait City"]), "BH": ("Bahrain", ["Manama"]),
             "OM": ("Oman", ["Muscat"]), "BW": ("Botswana", ["Gaborone"]), "ZM": ("Zambia", ["Lusaka"]),
             "NA": ("Namibia", ["Windhoek"]), "ZW": ("Zimbabwe", ["Harare"])}
FIT_WORDS = re.compile(r"\b(forex|fx|trading|trader|price action|smart money|smc|technical analysis|gold|xauusd|"
                       r"indices|nasdaq|mentor\w*|academy|education|course|school|community|market analysis)\b", re.I)
RED_FLAGS = [(re.compile(r"guarantee\w*|\b100% (?:win|profit)|risk.?free|double your|daily profit|passive income|"
                         r"get rich", re.I), "promises returns"),
             (re.compile(r"account management|manage your account|\bpamm\b|\bmam\b|we trade for you", re.I),
              "offers to trade for clients"),
             (re.compile(r"\b(?:vip )?signals?\b|copy my trades", re.I), "sells signals"),
             (re.compile(r"pass (?:your|the) (?:prop|funded|challenge)|challenge passing", re.I), "prop-challenge passing")]
STATUSES = ("new", "approved", "contacted", "partner", "rejected")


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS radar_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT UNIQUE,
            country TEXT, title TEXT, url TEXT, subscribers INTEGER, videos INTEGER, views INTEGER, last_upload TEXT,
            score INTEGER, flags TEXT, first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'new', note TEXT);
        CREATE TABLE IF NOT EXISTS radar_settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    return c


def permitted() -> list[str]:
    with _db() as c:
        r = c.execute("SELECT value FROM radar_settings WHERE key = 'countries'").fetchone()
    return [x for x in (r["value"].split(",") if r and r["value"] else []) if x in COUNTRIES]


def set_permitted(codes: list[str]) -> list[str]:
    ok = [x.upper() for x in codes if x.upper() in COUNTRIES]
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO radar_settings VALUES ('countries', ?)", (",".join(ok),))
    audit.log("officer", "radar_countries", {"countries": ok})
    return ok


def _get(path: str, **params: Any) -> dict[str, Any]:
    r = requests.get(f"{API}/{path}", params={**params, "key": os.getenv("JARVIS_YOUTUBE_API_KEY", "").strip()},
                     timeout=30)
    if r.status_code == 403 and "quota" in r.text.lower():
        raise RuntimeError("YouTube: daily quota used up - the radar tries again next run")
    if r.status_code in (400, 403):
        raise RuntimeError("YouTube: API key refused - check it at console.cloud.google.com (YouTube Data API v3 enabled)")
    r.raise_for_status()
    return r.json()


def queries(code: str) -> list[str]:
    name, cities = COUNTRIES[code]
    return [f"forex trading {name}", f"forex mentor {cities[0]}", f"trading academy {name}"]


def score(ch: dict[str, Any], last_upload: str | None, now: datetime | None = None) -> tuple[int, list[str]]:
    now = now or datetime.now(timezone.utc)
    st, sn = ch.get("statistics", {}), ch.get("snippet", {})
    subs, videos, views = int(st.get("subscriberCount", 0) or 0), int(st.get("videoCount", 0) or 0), int(st.get("viewCount", 0) or 0)
    text = f"{sn.get('title', '')} {sn.get('description', '')}"
    s = min(40.0, 10 * math.log10(max(subs, 1)) - 5) + min(20.0, 4 * math.log10(max(views / max(videos, 1), 1)))
    if last_upload:
        days = (now - datetime.fromisoformat(last_upload.replace("Z", "+00:00"))).days
        s += 20 if days <= 14 else 10 if days <= 60 else 0
    s += min(20, 5 * len({m.lower() for m in FIT_WORDS.findall(text)}))
    flags = [why for rx, why in RED_FLAGS if rx.search(text)]
    s -= 15 * len(flags)
    return max(0, min(100, round(s))), flags


def run(now: datetime | None = None) -> dict[str, Any]:
    """Search each permitted country, score the channels found, store new and updated prospects."""
    if not os.getenv("JARVIS_YOUTUBE_API_KEY", "").strip():
        return {"error": "NO DATA - Partner Radar not set up (YouTube API key: vertex1-connect radar)"}
    codes = permitted()
    if not codes:
        return {"error": "NO DATA - no countries cleared for marketing yet: !radar countries ZA,MU,KE"}
    now = now or datetime.now(timezone.utc)
    found: dict[str, str] = {}
    for code in codes:
        for q in queries(code):
            res = _get("search", part="snippet", type="channel", q=q, regionCode=code, relevanceLanguage="en",
                       maxResults=25)
            for it in res.get("items", []):
                cid = it.get("id", {}).get("channelId") or it.get("snippet", {}).get("channelId")
                if cid:
                    found.setdefault(cid, code)
    ids = list(found)
    stored = 0
    for i in range(0, len(ids), 50):
        chans = _get("channels", part="snippet,statistics,contentDetails", id=",".join(ids[i:i + 50]),
                     maxResults=50).get("items", [])
        for ch in chans:
            sn = ch.get("snippet", {})
            country = sn.get("country") or found[ch["id"]]
            if country not in codes:   # the channel says it is elsewhere: not a prospect for a cleared country
                continue
            uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            last = None
            if uploads:
                items = _get("playlistItems", part="contentDetails", playlistId=uploads, maxResults=1).get("items", [])
                last = items[0]["contentDetails"].get("videoPublishedAt") if items else None
            sc, flags = score(ch, last, now)
            st = ch.get("statistics", {})
            url = f"https://www.youtube.com/{sn['customUrl']}" if sn.get("customUrl") else f"https://www.youtube.com/channel/{ch['id']}"
            with _db() as c:
                c.execute("""INSERT INTO radar_prospects (channel_id, country, title, url, subscribers, videos, views,
                             last_upload, score, flags, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(channel_id) DO UPDATE SET subscribers=excluded.subscribers,
                             videos=excluded.videos, views=excluded.views, last_upload=excluded.last_upload,
                             score=excluded.score, flags=excluded.flags, last_seen=excluded.last_seen""",
                          (ch["id"], country, sn.get("title", "")[:120], url, int(st.get("subscriberCount", 0) or 0),
                           int(st.get("videoCount", 0) or 0), int(st.get("viewCount", 0) or 0), last, sc,
                           ", ".join(flags), now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
            stored += 1
    audit.log("radar", "run", {"countries": codes, "channels": stored})
    return {"countries": codes, "channels": stored}


def shortlist(per_country: int = 10, include_flagged: bool = False) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    with _db() as c:
        for code in permitted():
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM radar_prospects WHERE country = ? AND status IN ('new','approved','contacted') "
                + ("" if include_flagged else "AND (flags IS NULL OR flags = '') ")
                + "ORDER BY score DESC, subscribers DESC LIMIT ?", (code, per_country))]
            out[code] = rows
    return out


def line(r: dict[str, Any]) -> str:
    seen = f", last video {r['last_upload'][:10]}" if r["last_upload"] else ""
    return (f"#{r['id']:<4} [{r['score']:>3}] {r['title'][:50]} - {r['subscribers']:,} subscribers{seen}"
            f"{' (' + r['status'] + ')' if r['status'] != 'new' else ''}{'  !! ' + r['flags'] if r['flags'] else ''}\n      {r['url']}")


def shortlist_text() -> str:
    codes = permitted()
    if not codes:
        return "No countries cleared for marketing yet. Set them with !radar countries ZA,MU,KE (codes: " + \
            ", ".join(f"{k} {v[0]}" for k, v in COUNTRIES.items()) + ")"
    sl = shortlist()
    L = ["D.O.N.N.A | PARTNER RADAR - potential IBs (public channels; nobody has been contacted)"]
    for code in codes:
        L += ["", f"{COUNTRIES[code][0].upper()} ({code})"] + ([line(r) for r in sl.get(code, [])] or [" Nothing found yet."])
    L += ["", "!radar 12 approve / reject <why> / contacted / partner     !radar draft 12 for a first message"]
    return "\n".join(L)


def draft(pid: int) -> str:
    with _db() as c:
        r = c.execute("SELECT * FROM radar_prospects WHERE id = ?", (pid,)).fetchone()
    if not r:
        return f"No prospect #{pid}."
    firm = os.getenv("DONNA_FIRM_DISCLOSURE", "Vertex 1 Brokers")
    return (f"Draft for {r['title']} ({r['url']}) - check it with !review, then send it yourself:\n\n"
            f"Hi, I lead partnerships at {firm}. I've been following {r['title']} - your audience clearly values "
            f"careful, educational trading content.\n\nWe're building a small partner group in {COUNTRIES[r['country']][0]}: "
            f"transparent per-lot rebates paid weekly, a live partner dashboard and fast client withdrawals. "
            f"Would a 15-minute call next week make sense?\n\nKind regards,\nSH\n\n"
            f"CFDs are complex instruments and carry a high risk of losing money rapidly due to leverage.")


def update(pid: int, status: str, note: str = "") -> str:
    status = {"approve": "approved", "reject": "rejected"}.get(status, status)
    if status not in STATUSES:
        return "Use approve, reject <why>, contacted or partner."
    if status == "rejected" and not note.strip():
        return "Say why it was rejected - it keeps the list honest: !radar 12 reject <why>"
    with _db() as c:
        n = c.execute("UPDATE radar_prospects SET status = ?, note = COALESCE(NULLIF(?, ''), note) WHERE id = ?",
                      (status, note[:300], pid)).rowcount
    if n:
        audit.log("officer", f"radar_{status}", {"prospect": pid})
    return f"Prospect #{pid}: {status}." if n else f"No prospect #{pid}."


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!radar":
        return None
    parts = rest.split()
    if not parts:
        return shortlist_text()
    if parts[0] == "countries":
        if len(parts) == 1:
            return "Cleared for marketing: " + (", ".join(permitted()) or "none") + \
                ". Change with !radar countries ZA,MU,KE (only countries where Vertex 1 may market)."
        ok = set_permitted(re.split(r"[,\s]+", " ".join(parts[1:])))
        return "Cleared for marketing: " + (", ".join(f"{k} {COUNTRIES[k][0]}" for k in ok) or "none") + \
            ". The radar searches only these. Run it now with !radar run."
    if parts[0] == "run":
        r = run()
        return r["error"] if "error" in r else f"Radar searched {', '.join(r['countries'])}: {r['channels']} channels scored.\n\n" + shortlist_text()
    if parts[0] == "flagged":
        sl = shortlist(include_flagged=True)
        rows = [r for v in sl.values() for r in v if r["flags"]]
        return "\n".join(line(r) for r in rows) or "No flagged channels."
    if parts[0] == "draft" and len(parts) > 1 and parts[1].lstrip("#").isdigit():
        return draft(int(parts[1].lstrip("#")))
    if parts[0].lstrip("#").isdigit() and len(parts) > 1:
        return update(int(parts[0].lstrip("#")), parts[1].lower(), " ".join(parts[2:]))
    return "!radar   !radar run   !radar countries ZA,MU   !radar 12 approve|reject <why>|contacted|partner   !radar draft 12"


def weekly(send: bool = True, today: datetime | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc)
    if today.weekday() != RADAR_WEEKDAY:
        return {"skipped": "not radar day"}
    r = run()
    if "error" in r:
        return r
    if send:
        from jarvis.agents.personal import PERSONAL_TARGETS
        from donna.channels import broadcast

        r["delivery"] = broadcast(shortlist_text(), PERSONAL_TARGETS, title="D.O.N.N.A partner radar") \
            if PERSONAL_TARGETS else {"error": "JARVIS_PERSONAL_TARGETS not set"}
    return r
