"""DONNA lead monitor (Telegram user session, Telethon).

Rewrite of the "Telegram Hunter". What changed and why:
  * Talks to the DONNA API (auth'd) instead of writing someone else's DB tables directly -
    the old code inserted into tables v3/v4 never create and skipped the approval alert.
  * Word-boundary intent scoring with weights. The old substring match flagged 'ib' in
    "possible"/"visible", 'lots' in "pilots", 'mam' in "mama".
  * Self-promoters, signal sellers and bots are skipped; one lead per handle (DB UNIQUE).
  * Draft-only by default: a trader who asks for a broker goes on the Lead Finder list (!leads) and
    nothing is sent. Only with DONNA_LEAD_AUTOSEND=1 does this process send the single, approved
    consent request (no pitch), with pacing and flood handling; STOP is honoured permanently.
  * Once a day it also searches Telegram's public channels and groups (Telegram's own search,
    contacts.Search) for trading communities in the countries cleared for marketing, and hands
    their public title, description and member count to the Lead Finder. It never joins them.

Env: TG_API_ID, TG_API_HASH, TG_SESSION (path), DONNA_URL, DONNA_API_KEY,
     LEAD_GROUPS (optional: comma list of @usernames or ids you are a legitimate member of),
     DONNA_TG_SEARCH_HOURS (default 24)
Run: python -m donna.lead_monitor   (first run asks for the phone login code)
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("donna.leads")

# (pattern, weight). Weighted toward signals of real volume / business need.
INTENT_TERMS: list[tuple[str, float]] = [
    (r"\bfix\s*api\b", 3), (r"\b(rebate|rebates)\b", 2), (r"\bintroducing broker\b|\bIB\b", 2),
    (r"\bpamm\b|\bmam\b|\bmulti[- ]account manager\b", 3), (r"\bslippage\b", 1.5),
    (r"\b(raw|tight|low) spreads?\b", 1.5), (r"\b\d{2,}\s*(lots|standard lots)\b", 2.5),
    (r"\bvolume\b", 1), (r"\bliquidity\b", 1), (r"\b(looking for|need|recommend)\b.{0,30}\bbroker\b", 2.5),
    (r"\bctrader\b|\bmt5\b|\bmt4\b", 0.5), (r"\bwhite ?label\b", 3), (r"\bprime\b|\bprime of prime\b", 2),
]
# Messages that are themselves promotions/signals are not leads.
NOISE = re.compile(r"(?i)\b(join (my|our)|dm me for|vip signals?|guaranteed|100% (profit|win)|"
                   r"free signals?|account management service|t\.me/|https?://)")
URL_OR_MENTION_ONLY = re.compile(r"^\s*(@\w+|https?://\S+)\s*$")


AUTOSEND = os.getenv("DONNA_LEAD_AUTOSEND", "0") == "1"
FIT = re.compile(r"(?i)forex|\bfx\b|trad(e|er|ers|ing)|gold|xau|indices|nasdaq|crypto|pips?|smc|"
                 r"price action|academy|mentor|signals?|invest")


def channel_fits(title: str) -> bool:
    """A search hit worth a closer look: trading words in its name (the Lead Finder scores it properly)."""
    return bool(FIT.search(title or ""))


@dataclass
class Score:
    value: float
    terms: list[str]


def score_message(text: str) -> Score:
    if NOISE.search(text) or URL_OR_MENTION_ONLY.match(text):
        return Score(0.0, [])
    terms, total = [], 0.0
    for pat, w in INTENT_TERMS:
        m = re.search(pat, text, flags=re.I)
        if m:
            terms.append(m.group(0).strip().lower())
            total += w
    if "?" in text and total:   # asking is stronger intent than stating
        total += 1
    return Score(round(total, 1), terms)


class DonnaAPI:
    def __init__(self, base: str, key: str) -> None:
        self.base, self.h = base.rstrip("/"), {"X-API-Key": key}
        self.s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def call(self, method: str, path: str, **kw):
        async with self.s.request(method, f"{self.base}{path}", headers=self.h, **kw) as r:
            if r.status >= 300:
                raise RuntimeError(f"DONNA {path} -> {r.status}: {(await r.text())[:200]}")
            return await r.json()


async def main() -> None:
    from telethon import TelegramClient, errors, events  # imported here so the scorer is testable alone

    logging.basicConfig(level=logging.INFO)
    groups = [g.strip() for g in os.getenv("LEAD_GROUPS", "").split(",") if g.strip()]
    api = DonnaAPI(os.environ.get("DONNA_URL", "http://127.0.0.1:8080"), os.environ["DONNA_API_KEY"])
    client = TelegramClient(os.environ.get("TG_SESSION", "donna_lead_monitor"),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    async def on_group_message(event) -> None:
        text = event.raw_text or ""
        sc = score_message(text)
        if sc.value <= 0:
            return
        sender = await event.get_sender()
        if not sender or getattr(sender, "bot", False) or not getattr(sender, "username", None):
            return
        chat = await event.get_chat()
        try:
            res = await api.call("POST", "/api/leads", json={
                "handle": sender.username, "source_chat": getattr(chat, "title", "") or "group",
                "snippet": text[:280], "matched_terms": sc.terms, "intent_score": sc.value,
                "display_name": (getattr(sender, "first_name", "") or "")[:64]})
            log.info("lead @%s score %.1f -> %s", sender.username, sc.value, res.get("status"))
        except Exception as e:  # API down must not kill the listener
            log.error("lead post failed: %s", e)

    @client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_private_reply(event) -> None:
        sender = await event.get_sender()
        if sender and getattr(sender, "username", None):  # the API ignores people we never contacted
            try:
                res = await api.call("POST", "/api/leads/reply",
                                     json={"handle": sender.username, "text": event.raw_text or ""})
                log.info("reply from @%s -> %s", sender.username, res.get("status"))
            except Exception as e:
                log.error("reply post failed: %s", e)

    async def outreach_worker() -> None:
        """Sends approved consent requests, slowly. Telegram restricts accounts that DM many strangers."""
        while True:
            try:
                for item in await api.call("GET", "/api/outreach/approved"):
                    handle = item["destination"].lstrip("@")
                    try:
                        await client.send_message(handle, item["preview_text"], link_preview=False)
                        await api.call("POST", f"/api/outreach/{item['action_id']}/result", json={"ok": True})
                    except errors.FloodWaitError as e:
                        log.warning("flood wait %ss", e.seconds)
                        await asyncio.sleep(e.seconds + 5)
                        break
                    except errors.PeerFloodError:
                        log.error("PeerFlood: Telegram is limiting this account - pausing outreach 24h")
                        await asyncio.sleep(86400)
                        break
                    except (errors.UserPrivacyRestrictedError, ValueError) as e:
                        await api.call("POST", f"/api/outreach/{item['action_id']}/result",
                                       json={"ok": False, "detail": type(e).__name__})
                    await asyncio.sleep(random.uniform(90, 240))  # pace: a handful per hour at most
            except Exception as e:
                log.error("outreach worker: %s", e)
            await asyncio.sleep(60)

    async def discovery_worker() -> None:
        """Daily: search Telegram's public directory for trading communities in the cleared countries."""
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.contacts import SearchRequest

        while True:  # DONNA decides when a search is due (daily, or now after !leads run); ask every 5 minutes
            try:
                plan = await api.call("GET", "/api/prospects/queries")
                for country, queries in plan.items():
                    found: dict[str, dict] = {}
                    for q in queries:
                        res = await client(SearchRequest(q=q, limit=30))
                        for ch in res.chats:
                            if not getattr(ch, "username", None) or getattr(ch, "scam", False) \
                                    or getattr(ch, "fake", False) or ch.username in found:
                                continue
                            found[ch.username] = {"username": ch.username, "title": (ch.title or "")[:200],
                                                  "members": getattr(ch, "participants_count", None),
                                                  "about": "", "country": country, "_entity": ch}
                        await asyncio.sleep(random.uniform(4, 9))  # gentle: a few searches a minute
                    hits = [f for f in found.values() if channel_fits(f["title"])]
                    for f in hits[:25]:  # description and member count for the likely ones only
                        try:
                            full = await client(GetFullChannelRequest(f["_entity"]))
                            f["about"] = (full.full_chat.about or "")[:600]
                            f["members"] = full.full_chat.participants_count or f["members"]
                        except errors.FloodWaitError as e:
                            await asyncio.sleep(e.seconds + 5)
                        except Exception as e:  # private, deleted or restricted: keep the title only
                            log.info("no details for @%s: %s", f["username"], type(e).__name__)
                        await asyncio.sleep(random.uniform(3, 6))
                    # reported even when empty, so !leads can say the search finished
                    res = await api.call("POST", "/api/prospects",
                                         json=[{k: v for k, v in f.items() if k != "_entity"} for f in hits])
                    log.info("telegram search %s: %d found, %d stored", country, len(hits), res.get("stored", 0))
            except errors.FloodWaitError as e:
                log.warning("telegram search flood wait %ss", e.seconds)
                await asyncio.sleep(e.seconds + 5)
                continue
            except Exception as e:  # DONNA down or Telegram hiccup: try again next round
                log.error("telegram search: %s", e)
            await asyncio.sleep(300)

    if groups:
        client.add_event_handler(on_group_message, events.NewMessage(chats=groups))
    await client.start()
    log.info("lead monitor online: groups %s; Telegram search when DONNA asks; outreach %s",
             ", ".join(groups) or "none",
             "auto-send after approval" if AUTOSEND else "draft-only")
    if AUTOSEND:
        client.loop.create_task(outreach_worker())
    client.loop.create_task(discovery_worker())
    await client.run_until_disconnected()


async def login() -> None:
    """One-time interactive login (phone number + code) that saves the session file, then exits."""
    from telethon import TelegramClient

    client = TelegramClient(os.environ.get("TG_SESSION", "donna_lead_monitor"),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.start()
    me = await client.get_me()
    print(f"Logged in to Telegram as {getattr(me, 'first_name', '') or 'you'} - the session is saved.")
    await client.disconnect()


if __name__ == "__main__":
    import sys

    asyncio.run(login() if sys.argv[1:] == ["login"] else main())
