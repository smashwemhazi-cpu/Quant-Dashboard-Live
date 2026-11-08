"""D.O.N.N.A on your phone (Telegram): the same commands as Discord, ✓ buttons to tick tasks, and
Approve / Reject buttons for anything waiting on your OK. Her morning brief and urgent alerts reach the phone
when JARVIS_PERSONAL_TARGETS includes telegram:<your chat id> (vertex1-connect phone sets that up).

Runs as its own service (vertex1-phone) and asks Telegram for new messages every few seconds ("long polling"),
so the server needs no public web address. Only the owner's Telegram account is answered; anyone else who finds
the bot gets nothing.

  python -m donna.phone          run the bot
  python -m donna.phone pair     one-time setup: send /start to your bot, this saves your chat id
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from typing import Any

import requests

log = logging.getLogger("donna.phone")
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000                                  # Telegram allows 4096 characters per message
ENV_FILE = os.getenv("VERTEX1_ENV_FILE", "/etc/vertex1/vertex1.env")


def token() -> str:
    return os.getenv("DONNA_TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")


def owners() -> set[str]:
    """Telegram user ids allowed to use DONNA (the owner's own account)."""
    ids = {s.strip() for s in os.getenv("DONNA_TELEGRAM_APPROVER_IDS", "").split(",") if s.strip()}
    chat = os.getenv("DONNA_TELEGRAM_CHAT_ID", "").strip()
    return ids | ({chat} if chat and not chat.startswith("-") else set())   # a private chat id is the user id


def call(method: str, **params: Any) -> dict[str, Any]:
    """One Telegram Bot API call; returns the 'result' or {'error': ...}. Never raises."""
    try:
        r = requests.post(API.format(token=token(), method=method), json=params,
                          timeout=params.get("timeout", 0) + 15)
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        return {"error": f"Telegram unreachable: {type(e).__name__}"}
    if not body.get("ok"):
        return {"error": str(body.get("description") or r.status_code)}
    return body.get("result") if isinstance(body.get("result"), dict) else {"result": body.get("result")}


# ------------------------------------------------------------------ what she answers
def to_command(text: str) -> str:
    """Telegram commands start with '/', Discord ones with '!': '/tasks@DonnaBot 3' -> '!tasks 3'."""
    text = text.strip()
    m = re.match(r"/([A-Za-z_]+)(?:@\w+)?(.*)", text, re.S)
    return f"!{m.group(1).lower()}{m.group(2)}" if m else text


def answer(text: str) -> str:
    """Same answers as the console: commands, everyday questions (\"what's on today\"), or conversation."""
    from donna import console

    cmd = to_command(text)
    if cmd.lower() in ("!start", "!help"):
        from donna import discord_bot

        return ("D.O.N.N.A on your phone. Type the same commands as in Discord, with / or !, for example "
                "/tasks, /today, /waiting, /task 4, /done 4. Or just ask: \"what's on today?\"\n\n" + discord_bot.HELP)
    return console.reply(cmd) or "Done."


def keyboard(command: str) -> dict[str, Any] | None:
    """✓ buttons under a task list, four to a row."""
    from donna import discord_bot

    ids = discord_bot.tick_ids(to_command(command))
    if not ids:
        return None
    buttons = [{"text": f"✓ {i}", "callback_data": f"done:{i}"} for i in ids]
    return {"inline_keyboard": [buttons[i:i + 4] for i in range(0, len(buttons), 4)]}


def approval_keyboard(action_id: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"approve:{action_id}"},
                                 {"text": "✖ Reject", "callback_data": f"reject:{action_id}"}]]}


def decide(action_id: str, approve: bool, who: str) -> str:
    """Approve or reject a waiting action (the same as the console and /approve)."""
    from fastapi import HTTPException

    from donna.app import decide as app_decide

    try:
        r = app_decide(action_id, approve, f"telegram:{who}")
    except HTTPException as e:
        return f"Not changed: {e.detail}."
    return {"REJECTED": "Rejected - nothing was sent.", "APPROVED": "Approved - it's queued.",
            "EXECUTED": "Approved and done.", "FAILED": f"Approved, but it failed: {r.get('result')}"}.get(
        r["status"], r["status"])


def on_callback(cb: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """A button press -> (short answer, new buttons for that message or None to leave them)."""
    from donna import discord_bot

    kind, _, arg = str(cb.get("data", "")).partition(":")
    who = str((cb.get("from") or {}).get("id", ""))
    if kind == "done" and arg.isdigit():
        msg = discord_bot.tick(int(arg))
        rows = (((cb.get("message") or {}).get("reply_markup") or {}).get("inline_keyboard") or [])
        new = [[{"text": f"✅ {arg}", "callback_data": "noop"} if b.get("callback_data") == f"done:{arg}" else b
                for b in row] for row in rows]
        return msg, {"inline_keyboard": new}
    if kind in ("approve", "reject") and re.fullmatch(r"[0-9a-f]{32}", arg):
        return decide(arg, kind == "approve", who), {"inline_keyboard": []}
    return "", None


def send(chat_id: str, text: str, markup: dict[str, Any] | None = None) -> None:
    from donna.channels import chunks

    parts = chunks(text, LIMIT) or [""]
    for n, part in enumerate(parts):
        extra = {"reply_markup": markup} if markup and n == len(parts) - 1 else {}
        r = call("sendMessage", chat_id=chat_id, text=part, disable_web_page_preview=True, **extra)
        if "error" in r:
            log.error("sendMessage failed: %s", r["error"])


def handle_update(u: dict[str, Any]) -> None:
    allowed = owners()
    if "callback_query" in u:
        cb = u["callback_query"]
        if str((cb.get("from") or {}).get("id", "")) not in allowed:
            call("answerCallbackQuery", callback_query_id=cb["id"], text="Not allowed.")
            return
        msg, markup = on_callback(cb)
        call("answerCallbackQuery", callback_query_id=cb["id"], text=msg[:190])
        m = cb.get("message") or {}
        if markup is not None and m:
            call("editMessageReplyMarkup", chat_id=m["chat"]["id"], message_id=m["message_id"], reply_markup=markup)
        if msg:
            send(str(m.get("chat", {}).get("id", "")), msg)
        return
    m = u.get("message") or {}
    text, user = (m.get("text") or "").strip(), str((m.get("from") or {}).get("id", ""))
    if not text or not m.get("chat"):
        return
    chat = str(m["chat"]["id"])
    if user not in allowed:
        log.warning("ignored a message from Telegram user %s", user)   # strangers get no answer at all
        return
    if to_command(text).lower().strip() == "!weekly":      # the one-page PDF, as a file on the phone
        try:
            from donna.channels import send_telegram_file
            from jarvis.agents import weekly

            send_telegram_file(chat, weekly.build_pdf(), weekly.filename(), weekly.text()[:1000])
        except Exception as e:  # noqa: BLE001
            log.exception("weekly page failed")
            send(chat, f"Sorry - the weekly page failed ({type(e).__name__}).")
        return
    try:
        reply = answer(text)
    except Exception as e:  # noqa: BLE001 - one bad command must never stop the bot
        log.exception("command failed")
        reply = f"Sorry - that failed ({type(e).__name__})."
    send(chat, reply, keyboard(text))


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    if not token():
        raise SystemExit("DONNA_TELEGRAM_BOT_TOKEN not set - run: vertex1-connect phone")
    if not owners():
        raise SystemExit("No owner set - run: vertex1-connect phone (it asks you to send /start to your bot)")
    call("deleteWebhook")              # polling and a webhook can't both be on
    offset = 0
    while True:
        r = call("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        if "error" in r:
            log.warning("getUpdates: %s", r["error"])
            time.sleep(10)
            continue
        for u in r.get("result") or []:
            offset = max(offset, int(u["update_id"]) + 1)
            try:
                handle_update(u)
            except Exception:  # noqa: BLE001
                log.exception("update failed")


# ------------------------------------------------------------------ one-time pairing
def set_env(values: dict[str, str], path: str = ENV_FILE) -> None:
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    done, out = set(), []
    for line in lines:
        k = line.split("=", 1)[0]
        if k in values and not line.lstrip().startswith("#"):
            out.append(f"{k}={values[k]}")
            done.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in done]
    open(path, "w").write("\n".join(out) + "\n")


def pair(wait_seconds: int = 180, path: str = ENV_FILE) -> str:
    """Wait for the owner to send /start to the bot, then save their chat id and add the phone to the
    personal targets (morning brief, urgent alerts)."""
    if not token():
        return "No bot token yet."
    me = call("getMe")
    if "error" in me:
        return f"The bot token was refused by Telegram: {me['error']}"
    call("deleteWebhook")
    print(f"Now open Telegram on your phone, search for @{me.get('username')}, tap Start (or send /start).")
    end, offset = time.time() + wait_seconds, 0
    while time.time() < end:
        r = call("getUpdates", offset=offset, timeout=20, allowed_updates=["message"])
        for u in r.get("result") or []:
            offset = max(offset, int(u["update_id"]) + 1)
            m = u.get("message") or {}
            if (m.get("text") or "").startswith("/start") and (m.get("chat") or {}).get("type") == "private":
                chat, user = str(m["chat"]["id"]), str(m["from"]["id"])
                current = dict(line.split("=", 1) for line in open(path).read().splitlines()
                               if "=" in line and not line.lstrip().startswith("#")) if os.path.exists(path) else {}
                targets = [t for t in current.get("JARVIS_PERSONAL_TARGETS", "discord:personal").strip('"').split(",")
                           if t and not t.startswith("telegram:")] + [f"telegram:{chat}"]
                set_env({"DONNA_TELEGRAM_CHAT_ID": chat, "DONNA_TELEGRAM_APPROVER_IDS": user,
                         "JARVIS_PERSONAL_TARGETS": ",".join(targets)}, path)
                call("getUpdates", offset=offset, timeout=0)     # mark /start as read
                call("sendMessage", chat_id=chat, text="Paired. I'm D.O.N.N.A - your morning brief and urgent "
                     "alerts will come here too. Try /tasks or /today.")
                return f"PHONE     OK - paired with Telegram user {user}"
    return "PHONE     not paired - no /start arrived in time. Run: vertex1-connect phone"


if __name__ == "__main__":
    if sys.argv[1:] == ["pair"]:
        print(pair())
    else:
        run()
