"""Outbound channels. One function per channel, configured from env, raising
ChannelError on any failure so callers can mark an action FAILED honestly.

Env:
  DONNA_TELEGRAM_BOT_TOKEN                   bot that posts to internal chats / your channels
  DONNA_SLACK_BOT_TOKEN                      xoxb-... with chat:write
  DONNA_DISCORD_WEBHOOK_URL                  default channel webhook (target "discord")
  DONNA_DISCORD_WEBHOOK_<NAME>               named webhooks (target "discord:<name>"), e.g.
                                             DONNA_DISCORD_WEBHOOK_REPORTS, DONNA_DISCORD_WEBHOOK_SIGNALS
  DONNA_TEAMS_WEBHOOK_URL                    Teams Workflows "post to a channel when a webhook request is received" URL
  DONNA_SMTP_HOST / _PORT / _USER / _PASSWORD / _FROM   e.g. smtp.gmail.com:465 + an app password

Note: a Telegram *bot* cannot start a conversation with a user; it can only post to chats
it was added to or users who pressed Start. Person-to-person outreach goes through the
lead monitor's user session (donna/lead_monitor.py), never through here.
"""
from __future__ import annotations

import os
import smtplib
import sys
import time
from email.message import EmailMessage
from typing import Optional

import requests

TIMEOUT = 15
TELEGRAM_LIMIT = 4096


class ChannelError(RuntimeError):
    pass


def _env(name: str) -> str:
    # tolerate copy-paste debris: surrounding spaces, quotes or <>
    v = os.getenv(name, "").strip().strip("\"'<>").strip()
    if not v:
        raise ChannelError(f"{name} not configured")
    return v


def _check(r: requests.Response, what: str) -> requests.Response:
    if r.status_code >= 300:
        raise ChannelError(f"{what} HTTP {r.status_code}: {r.text[:300]}")
    return r


def chunks(text: str, limit: int) -> list[str]:
    """Split on line boundaries so each part fits the channel limit."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single over-long line
            out.append(cur) if cur else None
            cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out


def send_telegram(chat_id: str, text: str) -> str:
    url = f"https://api.telegram.org/bot{_env('DONNA_TELEGRAM_BOT_TOKEN')}/sendMessage"
    ids = []
    for part in chunks(text, TELEGRAM_LIMIT):
        # plain text: Markdown parse modes fail on stray '_' or '*'
        r = _check(requests.post(url, json={"chat_id": chat_id, "text": part,
                                            "disable_web_page_preview": True}, timeout=TIMEOUT), "telegram")
        body = r.json()
        if not body.get("ok"):
            raise ChannelError(f"telegram: {body.get('description')}")
        ids.append(str(body["result"]["message_id"]))
    return f"telegram {chat_id} msg {','.join(ids)}"


def send_slack(channel: str, text: str, thread_ts: Optional[str] = None) -> str:
    payload = {"channel": channel, "text": text, "unfurl_links": False}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    r = _check(requests.post("https://slack.com/api/chat.postMessage", json=payload, timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {_env('DONNA_SLACK_BOT_TOKEN')}"}), "slack")
    body = r.json()  # Slack returns HTTP 200 with ok=false on errors
    if not body.get("ok"):
        raise ChannelError(f"slack: {body.get('error')}")
    return f"slack {channel} ts {body.get('ts')}"


DISCORD_EMBED_LIMIT = 4096 - 8   # embed description limit minus the code-block fences
DISCORD_COLOURS = {"default": 0x00E676, "alert": 0xFF5252, "info": 0x5865F2}
FENCE = "`" * 3


def _discord_post(url: str, payload: dict) -> dict:
    for _ in range(3):
        r = requests.post(url, params={"wait": "true"}, json=payload, timeout=TIMEOUT)
        if r.status_code == 429:  # rate limited: Discord says how long to wait
            time.sleep(float(r.json().get("retry_after", 1)) + 0.25)
            continue
        return _check(r, "discord").json()
    raise ChannelError("discord: still rate limited after 3 attempts")


def send_discord(text: str, title: str = "DONNA", webhook: str = "", colour: str = "default") -> str:
    """Post as embeds with a monospace body so report columns line up. `webhook` picks a named
    webhook (DONNA_DISCORD_WEBHOOK_<NAME>); blank uses DONNA_DISCORD_WEBHOOK_URL."""
    name = f"DONNA_DISCORD_WEBHOOK_{webhook.upper()}" if webhook else "DONNA_DISCORD_WEBHOOK_URL"
    url = _env(name)
    if not url.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise ChannelError(f"{name} is not a Discord webhook URL (must start with https://discord.com/api/webhooks/)")
    body = text.replace(FENCE, "\u02cb" * 3)  # look-alike so text can't break out of the code block
    parts = chunks(body, DISCORD_EMBED_LIMIT)
    ids = []
    for n, part in enumerate(parts, 1):
        embed = {"description": f"{FENCE}\n{part}\n{FENCE}",
                 "color": DISCORD_COLOURS.get(colour, DISCORD_COLOURS["default"])}
        if n == 1:
            embed["title"] = title[:256]
        if n == len(parts):
            embed["footer"] = {"text": "DONNA | internal"}
            embed["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # one embed per message keeps every message under Discord's 6000-character total
        ids.append(_discord_post(url, {"username": "DONNA", "embeds": [embed],
                                       "allowed_mentions": {"parse": []}}).get("id", "?"))
    return f"discord{':' + webhook if webhook else ''} msg {','.join(ids)}"


def send_teams(text: str, title: str = "DONNA") -> str:
    card = {"type": "message", "attachments": [{
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {"type": "AdaptiveCard", "version": "1.4",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
                             {"type": "TextBlock", "text": text, "wrap": True, "fontType": "Monospace"}]}}]}
    _check(requests.post(_env("DONNA_TEAMS_WEBHOOK_URL"), json=card, timeout=TIMEOUT), "teams")
    return "teams ok"


def send_email(to: str, subject: str, body: str, html: Optional[str] = None,
               in_reply_to: Optional[str] = None) -> str:
    msg = EmailMessage()
    msg["From"] = os.getenv("DONNA_SMTP_FROM") or _env("DONNA_SMTP_USER")
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = msg["References"] = in_reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    host, port = _env("DONNA_SMTP_HOST"), int(os.getenv("DONNA_SMTP_PORT", "465"))
    try:
        with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT) as s:
            s.login(_env("DONNA_SMTP_USER"), _env("DONNA_SMTP_PASSWORD"))
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise ChannelError(f"email: {e}") from e
    return f"email to {to}"


def broadcast(text: str, targets: list[str], title: str = "DONNA") -> dict[str, str]:
    """Send to several targets like ['telegram:-100123', 'slack:#desk', 'discord', 'teams', 'email:a@b.com'].
    Returns per-target result; never raises, so one dead channel can't block the rest."""
    results: dict[str, str] = {}
    for t in targets:
        kind, _, arg = t.partition(":")
        try:
            if kind == "telegram":
                results[t] = send_telegram(arg, text)
            elif kind == "slack":
                results[t] = send_slack(arg, text)
            elif kind == "discord":
                results[t] = send_discord(text, title, webhook=arg)
            elif kind == "teams":
                results[t] = send_teams(text, title)
            elif kind == "email":
                results[t] = send_email(arg, title, text)
            else:
                results[t] = f"ERROR unknown channel '{kind}'"
        except ChannelError as e:
            results[t] = f"ERROR {e}"
        except requests.RequestException as e:  # DNS, proxy, TLS, timeout - report, don't crash
            results[t] = f"ERROR {kind} unreachable: {type(e).__name__}"
    return results


if __name__ == "__main__":
    # python -m donna.channels test discord:reports   -> sends a test message, prints the result
    if len(sys.argv) == 3 and sys.argv[1] == "test":
        print(broadcast("DONNA channel test - if you can read this, delivery works.\n"
                        "Columns   | line up | in a code block", [sys.argv[2]], title="DONNA test"))
    else:
        print("usage: python -m donna.channels test <target>   e.g. discord, discord:reports, telegram:-100123")
