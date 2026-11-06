"""Lead Finder: one list of potential IBs (partners) and traders, found in public places.

Where it looks (each source is optional and switches on when its setting is there):
  YouTube    trading educators and academies per cleared country (the Partner Radar, JARVIS_YOUTUBE_API_KEY).
  Telegram   public channels and groups found with Telegram's own search, through the lead monitor's user
             session (TG_API_ID / TG_API_HASH). Also traders who ask for a broker in groups you are a member of.
  Web        open-web search (Brave Search API, JARVIS_BRAVE_API_KEY): academies, communities, IB websites,
             public t.me channels and LinkedIn COMPANY pages that a search engine already shows.
  LinkedIn   LinkedIn forbids automated collection, so DONNA never visits it. `!leads linkedin` gives you ready
             search links; you browse yourself and log anyone useful with `!leads add <link> <note>`.

Rules that keep this compliant:
  - Only countries you cleared for marketing (!radar countries ...). Promoting CFDs usually needs a local licence.
  - Business information only for partners (name, link, audience, short public description). Personal LinkedIn
    profiles that turn up in web results are dropped. Trader leads keep a handle and a 280-character snippet.
  - Nothing is ever sent. Every first message is a draft you read, change and send yourself, with the risk warning.
  - Red flags (promised returns, trading for clients, paid signals, prop-challenge passing) are shown and kept off
    the shortlist: an IB who markets like that puts the licence at risk.
  - Retention: rejected prospects are deleted after 90 days and never-actioned ones after 180
    (JARVIS_PROSPECT_RETENTION_DAYS). The do-not-contact list in DONNA is always respected.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import requests

from jarvis.agents import radar
from jarvis.core import audit
from jarvis.core.config import settings

BRAVE = "https://api.search.brave.com/res/v1/web/search"
RETENTION_DAYS = int(os.getenv("JARVIS_PROSPECT_RETENTION_DAYS", "180"))
REJECTED_DAYS = 90
STATUSES = ("new", "approved", "contacted", "partner", "rejected")
SOURCE_TAG = {"youtube": "YT", "telegram": "TG", "web": "WEB", "linkedin": "LI", "manual": "YOU",
              "telegram-group": "TG"}
IB_WORDS = re.compile(r"\b(introducing broker|\bIBs?\b|partner(?:ship)?s?|affiliate|academy|mentor\w*|education|"
                      r"course|school|community|trading room|webinar|coach\w*)\b", re.I)
# reference, news, regulators and big platforms: never a partner prospect themselves
SKIP_DOMAINS = re.compile(r"(^|\.)(wikipedia\.org|investopedia\.com|reddit\.com|quora\.com|babypips\.com|"
                          r"forexfactory\.com|tradingview\.com|myfxbook\.com|reuters\.com|bloomberg\.com|"
                          r"cnbc\.com|fsca\.co\.za|cma\.or\.ke|fscmauritius\.org|sca\.gov\.ae|dfsa\.ae|"
                          r"google\.com|apple\.com|play\.google\.com|youtube\.com|glassdoor\.\w+|indeed\.\w+)$", re.I)


# ------------------------------------------------------------------ storage
def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lead_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, ext_id TEXT,
            kind TEXT, country TEXT, name TEXT, url TEXT, about TEXT, audience INTEGER, last_active TEXT,
            score INTEGER, flags TEXT, first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'new', note TEXT,
            UNIQUE (source, ext_id));
    """)
    return c


def _now(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def score(text: str, audience: int | None = None, last_active: str | None = None,
          now: datetime | None = None) -> tuple[int, list[str]]:
    """0-100: audience size (when known), recent activity, trading-education words, partner words; red flags cost."""
    now = now or datetime.now(timezone.utc)
    s = min(40.0, 10 * math.log10(max(audience, 1)) - 5) if audience else 15.0
    if last_active:
        try:
            days = (now - datetime.fromisoformat(last_active.replace("Z", "+00:00"))).days
            s += 15 if days <= 14 else 8 if days <= 60 else 0
        except ValueError:
            pass
    s += min(30, 6 * len({m.lower() for m in radar.FIT_WORDS.findall(text)}))
    s += 15 if IB_WORDS.search(text) else 0
    flags = [why for rx, why in radar.RED_FLAGS if rx.search(text)]
    s -= 15 * len(flags)
    return max(0, min(100, round(s))), flags


def add(c: dict[str, Any], now: datetime | None = None) -> int | None:
    """Store or refresh one candidate. Keys: source, ext_id, kind, country, name, url, about, audience,
    last_active. A prospect already approved, rejected or contacted keeps its status and note."""
    if c.get("country") and c["country"] not in radar.permitted():
        return None
    about = re.sub(r"\s+", " ", str(c.get("about") or ""))[:300]
    name = str(c.get("name") or "")[:120]
    sc, flags = score(f"{name} {about}", c.get("audience"), c.get("last_active"), now)
    ts = _now(now)
    with _db() as db:
        db.execute("""INSERT INTO lead_prospects (source, ext_id, kind, country, name, url, about, audience,
                      last_active, score, flags, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(source, ext_id) DO UPDATE SET name=COALESCE(NULLIF(name, ''), excluded.name),
                      about=CASE WHEN length(excluded.about) > length(about) THEN excluded.about ELSE about END,
                      audience=COALESCE(excluded.audience, audience), last_active=COALESCE(excluded.last_active,
                      last_active), score=MAX(score, excluded.score), flags=excluded.flags, last_seen=excluded.last_seen""",
                   (c["source"], str(c["ext_id"]).lower()[:300], c.get("kind", "ib"), c.get("country") or "",
                    name, c.get("url", ""), about, c.get("audience"), c.get("last_active"), sc, ", ".join(flags), ts, ts))
        row = db.execute("SELECT id FROM lead_prospects WHERE source = ? AND ext_id = ?",
                         (c["source"], str(c["ext_id"]).lower()[:300])).fetchone()
    return row["id"] if row else None


def add_many(cands: list[dict[str, Any]], now: datetime | None = None) -> int:
    return sum(1 for c in cands if add(c, now) is not None)


def purge(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    with _db() as db:
        n = db.execute("DELETE FROM lead_prospects WHERE status = 'rejected' AND last_seen < ?",
                       (_now(now - timedelta(days=REJECTED_DAYS)),)).rowcount
        n += db.execute("DELETE FROM lead_prospects WHERE status = 'new' AND last_seen < ?",
                        (_now(now - timedelta(days=RETENTION_DAYS)),)).rowcount
    return n


# ------------------------------------------------------------------ sources
def sources() -> dict[str, str]:
    """Which sources are on, in plain words."""
    yt = bool(os.getenv("JARVIS_YOUTUBE_API_KEY", "").strip())
    web = bool(os.getenv("JARVIS_BRAVE_API_KEY", "").strip())
    tg = bool(os.getenv("TG_API_ID", "").strip() and os.getenv("TG_API_HASH", "").strip())
    return {
        "YouTube": "on" if yt else "off - free key: vertex1-connect radar",
        "Telegram": "on (searched daily by the lead monitor)" if tg else "off - Telegram login: vertex1-connect telegram",
        "Web": "on" if web else "off - Brave Search key: vertex1-connect leads",
        "LinkedIn": "you search, I log it (!leads linkedin)",
    }


def web_queries(code: str) -> list[str]:
    name, cities = radar.COUNTRIES[code]
    return [f"forex trading academy {name}", f"forex mentor {cities[0]}", f"introducing broker forex {name}",
            f"forex trading community {name} telegram", f"site:t.me forex {name}",
            f"site:linkedin.com/company forex education {name}"]


def telegram_queries(code: str) -> list[str]:
    name, cities = radar.COUNTRIES[code]
    return [f"forex {name}", f"forex {cities[0]}", f"trading {name}", f"{name} traders"]


def classify_url(url: str) -> tuple[str, str] | None:
    """(source, ext_id) for a web result, or None when it must not be stored (personal LinkedIn, reference sites)."""
    u = urlparse(url)
    host = u.netloc.lower().removeprefix("www.")
    path = u.path.rstrip("/")
    if host.endswith("linkedin.com"):
        m = re.match(r"/company/([^/?#]+)", path)
        return ("linkedin", m.group(1)) if m else None      # /in/ (a person), posts, jobs: never stored
    if host in ("t.me", "telegram.me"):
        m = re.match(r"/(?:s/)?([A-Za-z0-9_]{5,32})$", path)
        return ("telegram", m.group(1)) if m and m.group(1).lower() not in ("joinchat", "share", "addstickers") else None
    if not host or SKIP_DOMAINS.search(host):
        return None
    return "web", host


def search_web(code: str, now: datetime | None = None) -> int:
    key = os.getenv("JARVIS_BRAVE_API_KEY", "").strip()
    stored = 0
    for q in web_queries(code):
        r = requests.get(BRAVE, params={"q": q, "count": 20}, timeout=30,
                         headers={"Accept": "application/json", "X-Subscription-Token": key})
        if r.status_code in (401, 403):
            raise RuntimeError("Web search: Brave refused the key - check it (vertex1-connect leads)")
        if r.status_code == 429:
            raise RuntimeError("Web search: Brave limit reached - tries again next run")
        r.raise_for_status()
        for it in (r.json().get("web") or {}).get("results", []):
            hit = classify_url(it.get("url", ""))
            if not hit:
                continue
            src, ext = hit
            title = re.sub(r"\s*[|\-–]\s*LinkedIn$", "", re.sub(r"<[^>]+>", "", it.get("title", "")))
            desc = re.sub(r"<[^>]+>", "", it.get("description", ""))
            if not radar.FIT_WORDS.search(f"{title} {desc}"):
                continue
            url = it["url"] if src != "web" else f"https://{ext}"
            if add({"source": src, "ext_id": ext, "kind": "ib", "country": code, "name": title, "url": url,
                    "about": desc}, now):
                stored += 1
    return stored


def sync_youtube() -> int:
    """Copy the Partner Radar's channels into the one list (status set with !radar carries over)."""
    n = 0
    with radar._db() as rc:
        rows = [dict(r) for r in rc.execute("SELECT * FROM radar_prospects")]
    for r in rows:
        pid = add({"source": "youtube", "ext_id": r["channel_id"], "kind": "ib", "country": r["country"],
                   "name": r["title"], "url": r["url"], "about": "",
                   "audience": r["subscribers"], "last_active": r["last_upload"]})
        if pid is None:
            continue
        n += 1
        with _db() as db:
            db.execute("UPDATE lead_prospects SET score = ?, flags = ?, status = CASE WHEN status = 'new' THEN ? "
                       "ELSE status END WHERE id = ?", (r["score"], r["flags"] or "", r["status"] or "new", pid))
    return n


def run(now: datetime | None = None) -> dict[str, Any]:
    """Search every source that is switched on, for every cleared country."""
    codes = radar.permitted()
    if not codes:
        return {"error": "NO DATA - no countries cleared for marketing yet: !radar countries ZA,MU,KE"}
    on = {k for k, v in sources().items() if v.startswith("on")}
    if not on:
        return {"error": "NO DATA - Lead Finder has no search source switched on yet. Countries are set ("
                + ", ".join(codes) + "). Next, on the server (one command each, any one is enough to start):\n"
                " 1. vertex1-connect telegram   free - api_id and api_hash from my.telegram.org, then a login code\n"
                " 2. vertex1-connect radar      free - YouTube Data API key from console.cloud.google.com\n"
                " 3. vertex1-connect leads      web search - Brave key (asks for a card, $5/month free credit)\n"
                "Meanwhile: !leads linkedin gives you LinkedIn searches to do yourself, and !leads add logs a find."}
    found, problems = {}, []
    if "YouTube" in on:
        try:
            radar.run(now)
        except Exception as e:  # one source down must not stop the others
            problems.append(str(e)[:160])
        found["youtube"] = sync_youtube()
    if "Web" in on:
        try:
            found["web"] = sum(search_web(code, now) for code in codes)
        except Exception as e:
            problems.append(str(e)[:160])
    removed = purge(now)
    audit.log("leadfinder", "run", {"countries": codes, "found": found, "problems": problems, "purged": removed})
    return {"countries": codes, "found": found, "problems": problems, "purged": removed}


# ------------------------------------------------------------------ the list
def shortlist(kind: str = "ib", per_country: int = 8, include_flagged: bool = False) -> list[dict[str, Any]]:
    with _db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM lead_prospects WHERE kind = ? AND status IN ('new','approved','contacted') "
            + ("" if include_flagged else "AND (flags IS NULL OR flags = '') ")
            + "ORDER BY score DESC, COALESCE(audience, 0) DESC", (kind,))]
    seen: dict[str, int] = {}
    out = []
    for r in rows:
        seen[r["country"]] = seen.get(r["country"], 0) + 1
        if seen[r["country"]] <= per_country:
            out.append(r)
    return out


def _ago(iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    try:
        d = (now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).days
    except ValueError:
        return ""
    return "active today" if d < 1 else f"active {d}d ago" if d < 60 else f"quiet {d // 30} months"


def line(r: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    bits = [f"{r['audience']:,} followers" if r.get("audience") else "", _ago(r.get("last_active"), now),
            r["status"] if r["status"] != "new" else ""]
    meta = " · ".join(b for b in bits if b)
    head = f"#{r['id']:<4} {r['score']:>3}  {SOURCE_TAG.get(r['source'], r['source'][:3].upper()):<3}  {r['name'][:48]}"
    tail = f"\n            {r['url']}" + (f"  ({meta})" if meta else "")
    if r["kind"] == "trader" and r.get("about"):
        tail += f'\n            "{r["about"][:110]}"'
    return head + tail + (f"\n            !! {r['flags']}" if r.get("flags") else "")


def shortlist_text(now: datetime | None = None) -> str:
    codes = radar.permitted()
    if not codes:
        return ("LEAD FINDER: no countries cleared for marketing yet. Set them with !radar countries ZA,MU,KE "
                "(codes: " + ", ".join(f"{k} {v[0]}" for k, v in radar.COUNTRIES.items()) + ")")
    ibs, traders = shortlist("ib"), shortlist("trader", per_country=10)
    L = ["D.O.N.N.A | LEAD FINDER - public sources only, nobody has been contacted",
         "Sources: " + " | ".join(f"{k} {v.split(' ')[0]}" for k, v in sources().items() if k != "LinkedIn")
         + " | LinkedIn: !leads linkedin", "Score 0-100 · YT YouTube · TG Telegram · WEB website · LI LinkedIn company"]
    for code in codes:
        rows = [r for r in ibs if r["country"] == code]
        L += ["", f"POSSIBLE IBs / PARTNERS - {radar.COUNTRIES[code][0].upper()}"]
        L += [line(r, now) for r in rows] or ["  Nothing found yet."]
    L += ["", "TRADERS ASKING FOR A BROKER (Telegram groups you are in)"]
    L += [line(r, now) for r in traders] or ["  None yet."]
    L += ["", "!leads 12 approve | reject <why> | contacted | partner    !leads draft 12    !leads add <link> <note>",
          "!leads run   !leads sources   !leads linkedin   !leads flagged   !crm (CRM lead backlog)"]
    return "\n".join(L)


def linkedin_links(code: str | None = None) -> str:
    codes = [code] if code else (radar.permitted() or ["ZA"])
    L = ["LINKEDIN - search it yourself (LinkedIn does not allow automated collection). Open a link, and for anyone",
         "useful: !leads add <their link> <country code> <note>. Prefer company pages; keep notes to business facts."]
    for c in codes:
        name, cities = radar.COUNTRIES[c]
        L += ["", name.upper()]
        for label, kind, kw in (("Academies and communities", "companies", f"forex academy {name}"),
                                ("Introducing brokers", "companies", f"introducing broker forex {name}"),
                                ("People: IB managers", "people", f"introducing broker forex {cities[0]}"),
                                ("People: trading educators", "people", f"forex mentor {cities[0]}")):
            L.append(f" {label}: https://www.linkedin.com/search/results/{kind}/?keywords={quote_plus(kw)}")
    return "\n".join(L)


def add_manual(rest: str) -> str:
    m = re.match(r"\s*(?:(trader)\s+)?(https?://\S+)\s*(?:\b([A-Za-z]{2})\b)?\s*(.*)", rest, re.S)
    if not m:
        return "Usage: !leads add <link> [country code] <note>   e.g. !leads add https://linkedin.com/company/x KE runs a 4k academy"
    kind, url, code, note = m.group(1) or "ib", m.group(2), (m.group(3) or "").upper(), m.group(4).strip()
    codes = radar.permitted()
    if code and code not in radar.COUNTRIES:
        note, code = f"{m.group(3)} {note}".strip(), ""
    code = code or (codes[0] if len(codes) == 1 else "")
    if not code:
        return "Which country? Add the code after the link: !leads add <link> KE <note>"
    if code not in codes:
        return f"{radar.COUNTRIES[code][0]} isn't cleared for marketing (!radar countries)."
    host = urlparse(url).netloc.removeprefix("www.")
    pid = add({"source": "manual", "ext_id": url.rstrip("/"), "kind": kind, "country": code,
               "name": note[:60] or host, "url": url, "about": note})
    with _db() as db:
        db.execute("UPDATE lead_prospects SET note = ? WHERE id = ?", (note[:300], pid))
    audit.log("officer", "lead_added", {"prospect": pid})
    return f"Added #{pid} ({radar.COUNTRIES[code][0]}). !leads draft {pid} for a first message."


def draft(pid: int) -> str:
    with _db() as db:
        r = db.execute("SELECT * FROM lead_prospects WHERE id = ?", (pid,)).fetchone()
    if not r:
        return f"No prospect #{pid}."
    firm = os.getenv("DONNA_FIRM_DISCLOSURE", "Vertex 1 Brokers")
    rep = os.getenv("DONNA_REP_NAME", "SH")
    country = radar.COUNTRIES.get(r["country"], ("your market",))[0]
    head = f"DRAFT for {r['name']} ({r['url']}) - not sent. Check it with !review, then send it yourself.\n\n"
    if r["kind"] == "trader":
        # an unsolicited first message may only ask for consent: no pitch, no prices, a clear way out
        return head + (f"Hi, this is {rep} from {firm}. I saw your post asking about brokers. Would you like us to "
                       f"send you information about our trading accounts? Reply YES if so. If not, reply STOP and we "
                       f"won't contact you again.")
    return head + (f"Hi, I lead partnerships at {firm}. I came across {r['name']} - your audience clearly values "
                   f"careful, educational trading content.\n\nWe're building a small partner group in {country}: "
                   f"transparent per-lot rebates paid weekly, a live partner dashboard and fast client withdrawals. "
                   f"Would a 15-minute call next week make sense? If this isn't of interest, just say so and I won't "
                   f"follow up.\n\nKind regards,\n{rep}\n\n"
                   f"CFDs are complex instruments and carry a high risk of losing money rapidly due to leverage.")


def update(pid: int, status: str, note: str = "") -> str:
    status = {"approve": "approved", "reject": "rejected"}.get(status, status)
    if status not in STATUSES:
        return "Use approve, reject <why>, contacted or partner."
    if status == "rejected" and not note.strip():
        return "Say why it was rejected - it keeps the list honest: !leads 12 reject <why>"
    with _db() as db:
        n = db.execute("UPDATE lead_prospects SET status = ?, note = COALESCE(NULLIF(?, ''), note) WHERE id = ?",
                       (status, note[:300], pid)).rowcount
    if n:
        audit.log("officer", f"lead_{status}", {"prospect": pid})
    return f"Prospect #{pid}: {status}." if n else f"No prospect #{pid}."


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!crm":
        from jarvis.agents import growth

        return "\n".join(growth.report_lines())
    if cmd not in ("!leads", "!lead", "!prospects"):
        return None
    parts = rest.split()
    if not parts:
        return shortlist_text()
    sub = parts[0].lower()
    if sub == "crm":
        return command("!crm", "")
    if sub == "sources":
        return "LEAD FINDER SOURCES\n" + "\n".join(f" {k}: {v}" for k, v in sources().items()) + \
            "\nCountries: " + (", ".join(radar.permitted()) or "none - !radar countries ZA,MU,KE")
    if sub == "run":
        r = run()
        if "error" in r:
            return r["error"]
        found = ", ".join(f"{k} {v}" for k, v in r["found"].items()) or "no search source on yet"
        warn = ("\nProblems: " + "; ".join(r["problems"])) if r["problems"] else ""
        return f"Searched {', '.join(r['countries'])}: {found}.{warn}\n\n" + shortlist_text()
    if sub == "linkedin":
        code = parts[1].upper() if len(parts) > 1 and parts[1].upper() in radar.COUNTRIES else None
        return linkedin_links(code)
    if sub == "add":
        return add_manual(rest[rest.lower().index("add") + 3:])
    if sub == "flagged":
        rows = [r for k in ("ib", "trader") for r in shortlist(k, 50, include_flagged=True) if r["flags"]]
        return "\n".join(line(r) for r in rows) or "No flagged prospects."
    if sub == "draft" and len(parts) > 1 and parts[1].lstrip("#").isdigit():
        return draft(int(parts[1].lstrip("#")))
    if sub.lstrip("#").isdigit() and len(parts) > 1:
        return update(int(sub.lstrip("#")), parts[1].lower(), " ".join(parts[2:]))
    return ("!leads   !leads run   !leads sources   !leads linkedin [KE]   !leads add <link> [KE] <note>\n"
            "!leads 12 approve|reject <why>|contacted|partner   !leads draft 12   !leads flagged   !crm")


def weekly(send: bool = True, today: datetime | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc)
    if today.weekday() != radar.RADAR_WEEKDAY:
        return {"skipped": "not lead finder day"}
    r = run(today)
    if "error" in r:
        return r
    if send:
        from jarvis.agents.personal import PERSONAL_TARGETS
        from donna.channels import broadcast

        r["delivery"] = broadcast(shortlist_text(today), PERSONAL_TARGETS, title="D.O.N.N.A lead finder") \
            if PERSONAL_TARGETS else {"error": "JARVIS_PERSONAL_TARGETS not set"}
    return r
