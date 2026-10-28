"""Regional campaign engine: one command builds a ready-to-review campaign for a cleared country.

  !campaign kenya ib        IB recruitment: outreach message, follow-up, partner one-pager, posts, 2-week plan
  !campaign ghana clients   client acquisition: posts, landing copy, email, 2-week plan
  !campaign za retention    win-back / retention: email, WhatsApp follow-up, posts

Every campaign:
  - runs only for a country cleared with !radar countries (promotion usually needs a local licence)
  - carries the CFD risk warning and the firm disclosure (DONNA_FIRM_DISCLOSURE, e.g. "Vertex 1 Brokers, FSP 12345")
  - is checked against the same red-flag words the Partner Radar uses (guarantees, risk-free, signals...)
  - gets a tracking tag (utm_campaign) so leads -> funded clients can be counted per campaign: !campaign 3
Deterministic templates - no Claude credit needed. Nothing is published or sent; you post it yourself.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jarvis.agents import radar
from jarvis.core import audit
from jarvis.core.config import settings

FIRM = os.getenv("DONNA_FIRM_DISCLOSURE", "Vertex 1 Brokers")
SITE = os.getenv("DONNA_SITE_URL", "https://vertex1brokers.com")
RISK = os.getenv("DONNA_RISK_WARNING", "CFDs are complex instruments and come with a high risk of losing money rapidly "
                 "due to leverage. Make sure you understand how they work and whether you can afford to lose your money.")
NAMES = {**{k: k for k in radar.COUNTRIES}, **{v[0].lower(): k for k, v in radar.COUNTRIES.items()},
         "uae": "AE", "dubai": "AE", "saudi": "SA", "ksa": "SA", "south africa": "ZA"}
KINDS = ("ib", "clients", "retention")
# what lands in each market - the angle, the local proof points, the channels people actually use
ANGLES = {
    "ZA": ("rand-friendly funding and quick EFT withdrawals", "WhatsApp, YouTube, Instagram, LinkedIn"),
    "MU": ("a local, regulated home market and in-person support", "LinkedIn, Facebook, local events"),
    "KE": ("mobile-money funding, a small starting deposit and Swahili/English support", "WhatsApp, Telegram, TikTok, YouTube"),
    "NG": ("reliable, on-time withdrawals and strong education", "Telegram, X, YouTube, WhatsApp"),
    "GH": ("mobile-money funding and a local partner manager", "WhatsApp, Telegram, YouTube"),
    "AE": ("swap-free accounts, Arabic support and premium service", "Instagram, X, LinkedIn, WhatsApp"),
    "SA": ("swap-free accounts and Arabic support", "X, Snapchat, Instagram"),
}
DEFAULT_ANGLE = ("fast withdrawals, transparent pricing and real education", "WhatsApp, YouTube, LinkedIn")


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, country TEXT, "
              "kind TEXT, tag TEXT UNIQUE, status TEXT DEFAULT 'draft', note TEXT)")
    return c


def country_code(word: str) -> str | None:
    return NAMES.get(word.strip().lower()) or NAMES.get(word.strip().upper())


def red_flags(text: str) -> list[str]:
    return sorted({why for rx, why in radar.RED_FLAGS if rx.search(text)})


def _link(tag: str, medium: str) -> str:
    return f"{SITE}/?utm_source={medium}&utm_medium=social&utm_campaign={tag}"


def build(country: str, kind: str, now: datetime | None = None) -> dict[str, Any]:
    code = country_code(country)
    if not code:
        return {"error": f"Unknown country '{country}'. Use a name or code: " + ", ".join(radar.COUNTRIES)}
    if kind not in KINDS:
        return {"error": "Campaign type must be ib, clients or retention"}
    if code not in radar.permitted():
        return {"error": f"{radar.COUNTRIES[code][0]} isn't cleared for marketing. If Vertex 1 may market there, "
                         f"clear it first: !radar countries {','.join(radar.permitted() + [code])}"}
    now = now or datetime.now(timezone.utc)
    name, cities = radar.COUNTRIES[code]
    angle, channels = ANGLES.get(code, DEFAULT_ANGLE)
    y, w, _ = now.isocalendar()
    tag = f"{code.lower()}-{kind}-{y}w{w:02d}"
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM campaigns WHERE tag LIKE ?", (tag + "%",)).fetchone()[0]
        tag = tag if not n else f"{tag}-{n + 1}"
        cid = c.execute("INSERT INTO campaigns (created, country, kind, tag) VALUES (?,?,?,?)",
                        (now.isoformat(timespec="seconds"), code, kind, tag)).lastrowid
    sections = _content(kind, name, cities[0], angle, channels, tag, now)
    body = "\n".join(t for _, t in sections)
    audit.log("growth", "campaign_built", {"campaign": cid, "country": code, "kind": kind})
    return {"id": cid, "tag": tag, "country": code, "kind": kind, "sections": sections, "red_flags": red_flags(body)}


def _content(kind: str, name: str, city: str, angle: str, channels: str, tag: str, now: datetime) -> list[tuple[str, str]]:
    start = now.date() + timedelta(days=(7 - now.weekday()) % 7 or 7)   # next Monday
    day = lambda n: (start + timedelta(days=n)).strftime("%a %d %b")  # noqa: E731
    foot = f"\n\n{FIRM}. {RISK}"
    if kind == "ib":
        return [
            ("Who to approach", f"Trading educators, academies and community leads in {name} - start with your approved "
                                f"Partner Radar list (!radar). Channels that work there: {channels}."),
            ("First message (LinkedIn / email)",
             f"Hi [name], I lead partnerships at {FIRM}. I've followed [channel] - your audience in {name} clearly "
             f"values careful, educational trading content.\n\nWe're building a small partner group in {city}: "
             f"transparent per-lot rebates paid weekly, a live partner dashboard, and {angle} for the clients you "
             f"introduce. Would a 15-minute call next week make sense?\n\nKind regards,\nSH" + foot),
            ("Follow-up (only after they replied)",
             f"Thanks for coming back to me. Here is our one-page partner overview: {_link(tag, 'partner')}\n"
             f"It covers how rebates are calculated and paid, and the marketing rules every partner follows "
             f"(approved copy only, the risk warning on every post, no promises of profit).\nReply STOP and we won't message again."),
            ("Partner one-pager",
             f"Partner with {FIRM} in {name}\n- Per-lot rebates, paid weekly, with a live dashboard\n"
             f"- What your clients get: {angle}\n- A named partner manager and an approved marketing kit\n"
             f"- Rules: IB agreement and KYC first; approved copy only; no rebate on abusive trading; clawback for "
             f"chargebacks and bonus abuse" + foot),
            ("Two-week plan", f"{day(0)}: shortlist 15 prospects from !radar and approve them\n{day(1)}-{day(3)}: send "
                              f"first messages (5 a day, personal, never bulk)\n{day(7)}: follow up once with replies "
                              f"only\n{day(8)}-{day(10)}: calls; IB agreement and KYC for those who say yes\n{day(11)}: "
                              f"review - who signed, set their rate with !ib rate"),
        ]
    if kind == "clients":
        link = _link(tag, "social")
        return [
            ("Posts (3)",
             f"1) Trading in {name}? Learn how leverage really works before you risk a cent - our free market "
             f"basics series starts this week. {link}" + foot + "\n\n"
             f"2) What matters most when you choose a broker: how fast you get your money back. With {FIRM}: "
             f"{angle}. {link}" + foot + "\n\n"
             f"3) Gold, indices and FX - this week's key levels, explained simply. Education, not advice. {link}" + foot),
            ("Landing page copy",
             f"Headline: Trade with a broker built for {name}\nThree points: {angle}; transparent pricing with no hidden "
             f"fees; real education and a demo account to practise first\nButton: Open a free demo account\n"
             f"Footer: {FIRM}. {RISK}"),
            ("Email to new leads",
             f"Subject: Your demo account and a 10-minute start guide\n\nHi [first name],\nThanks for your interest "
             f"in {FIRM}. Start with the demo account - it uses virtual money so you can learn safely: {link}\n"
             f"When you're ready, our team in {city} can help you with verification and funding.\n\nKind regards,\n"
             f"The {FIRM} team" + foot + "\nUnsubscribe: reply STOP."),
            ("Two-week plan", f"{day(0)}: post 1 on {channels.split(', ')[0]}\n{day(2)}: post 2\n{day(4)}: post 3\n"
                              f"{day(7)}-{day(10)}: repeat the best performer; answer every comment within a day\n"
                              f"{day(11)}: review leads and funded clients for tag {tag} (!campaign <id>)"),
        ]
    return [
        ("Win-back email",
         f"Subject: We've improved - here's what's new\n\nHi [first name],\nIt's been a while since you traded "
         f"with {FIRM}. Since then we've focused on {angle}. Your account is still there if you'd like to come back: "
         f"{_link(tag, 'email')}\nIf you'd rather not hear from us, reply STOP.\n\nKind regards,\nThe {FIRM} team" + foot),
        ("WhatsApp follow-up (only to clients who opted in)",
         f"Hi [first name], it's [agent] from {FIRM}. Is there anything we could have done better? Your feedback "
         f"goes straight to our team. Reply STOP to opt out."),
        ("Post", f"Thank you to our traders in {name}. This month we've focused on {angle}. Questions? Our {city} "
                 f"team is here to help. {_link(tag, 'social')}" + foot),
        ("Two-week plan", f"{day(0)}: send the win-back email to clients inactive 30-90 days\n{day(3)}: WhatsApp "
                          f"follow-up to opted-in clients who opened it\n{day(10)}: post\n{day(11)}: review returns "
                          f"for tag {tag}; log any complaints raised (!complaint)"),
    ]


def text(c: dict[str, Any]) -> str:
    if "error" in c:
        return c["error"]
    L = [f"CAMPAIGN #{c['id']} - {radar.COUNTRIES[c['country']][0]} {c['kind']} - tracking tag {c['tag']}",
         "Nothing has been posted or sent. Run each piece through !review, then publish it yourself."]
    if c["red_flags"]:
        L.append("!! Red-flag wording found: " + ", ".join(c["red_flags"]) + " - fix before use")
    for title, body in c["sections"]:
        L += ["", title.upper(), body]
    return "\n".join(L)


def results(cid: int) -> str:
    with _db() as c:
        r = c.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()
    if not r:
        return f"No campaign #{cid}."
    from jarvis.agents import growth

    with growth._db() as c:  # noqa: SLF001
        last = c.execute("SELECT MAX(asof) FROM bo_leads").fetchone()[0]
        leads = ftd = 0
        if last:
            leads, ftd = c.execute("SELECT COUNT(*), COALESCE(SUM(ftd), 0) FROM bo_leads WHERE asof = ? AND "
                                   "LOWER(source) LIKE ?", (last, f"%{r['tag']}%")).fetchone()
    return (f"Campaign #{cid} {radar.COUNTRIES[r['country']][0]} {r['kind']} (tag {r['tag']}, {r['status']}, "
            f"created {r['created'][:10]}): {leads} lead(s), {ftd} funded"
            + ("" if last else " - no CRM leads export yet") + ".\nLeads are counted when the CRM keeps the "
            "utm_campaign tag in the lead source.")


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!campaigns":
        with _db() as c:
            rows = c.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 20").fetchall()
        return "\n".join(f"#{r['id']:<4} {r['created'][:10]} {r['country']} {r['kind']:<9} {r['tag']} ({r['status']})"
                         for r in rows) or "No campaigns yet - !campaign kenya ib"
    if cmd != "!campaign":
        return None
    parts = rest.split()
    if parts and parts[0].lstrip("#").isdigit():
        cid = int(parts[0].lstrip("#"))
        if len(parts) > 1 and parts[1].lower() in ("live", "done", "draft", "stopped"):
            with _db() as c:
                ok = c.execute("UPDATE campaigns SET status = ? WHERE id = ?", (parts[1].lower(), cid)).rowcount
            return f"Campaign #{cid}: {parts[1].lower()}." if ok else f"No campaign #{cid}."
        return results(cid)
    if len(parts) < 2:
        return ("!campaign <country> ib|clients|retention   e.g. !campaign kenya ib\n"
                "!campaigns   !campaign 3 (results)   !campaign 3 live|done|stopped")
    kind = parts[-1].lower()
    return text(build(" ".join(parts[:-1]), kind))
