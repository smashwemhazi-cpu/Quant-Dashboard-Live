"""DONNA lead monitor (Telegram user session, Telethon).

Rewrite of the "Telegram Hunter". What changed and why:
  * Talks to the DONNA API (auth'd) instead of writing someone else's DB tables directly -
    the old code inserted into tables v3/v4 never create and skipped the approval alert.
  * Word-boundary intent scoring with weights. The old substring match flagged 'ib' in
    "possible"/"visible", 'lots' in "pilots", 'mam' in "mama".
  * Self-promoters, signal sellers and bots are skipped; one lead per handle (DB UNIQUE).
  * Outreach is a single, approved consent request (no pitch), sent by this process with
    pacing and flood handling; replies are classified so STOP is honoured permanently.

Env: TG_API_ID, TG_API_HASH, TG_SESSION (path), DONNA_URL, DONNA_API_KEY,
     LEAD_GROUPS (comma list of @usernames or ids you are a legitimate member of)
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
    groups = [g.strip() for g in os.environ["LEAD_GROUPS"].split(",") if g.strip()]
    api = DonnaAPI(os.environ.get("DONNA_URL", "http://127.0.0.1:8080"), os.environ["DONNA_API_KEY"])
    client = TelegramClient(os.environ.get("TG_SESSION", "donna_lead_monitor"),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    @client.on(events.NewMessage(chats=groups))
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

    await client.start()
    log.info("lead monitor online: %s", ", ".join(groups))
    client.loop.create_task(outreach_worker())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
