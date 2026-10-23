"""D.O.N.N.A task commands in Discord.

  !tasks                         open tasks, overdue first
  !done 12 [13 ...]              mark tasks done
  !add Call LP about XAU spreads | 2026-10-24 | high     (date and priority optional)
  !today                         the my-day brief (calendar, tasks, inbox, trading, bots)
  !ask <question>                ask D.O.N.N.A anything; she answers from the live agents (read-only)
  !help / !commands

Answers only in the channels named in DONNA_DISCORD_COMMAND_CHANNELS (keep them private - the
channel is the access control) and, if DONNA_DISCORD_ALLOWED_USERS is set, only to those user IDs.
Runs as its own bot (DONNA_DISCORD_BOT_TOKEN, Message Content intent on) so it never collides
with any other bot in the server.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

from jarvis.agents import assistant

log = logging.getLogger("donna.discord")
LIMIT = 1900  # Discord allows 2000 characters per message; leave room for the code fence
HELP = ("D.O.N.N.A commands:\n"
        "!tasks                     open tasks\n"
        "!done 12 13                mark tasks done\n"
        "!add <title> | YYYY-MM-DD | high/medium/low\n"
        "!today                     my-day brief\n"
        "!ask <question>            e.g. !ask which clients are at risk this week?")


def _list(v: str) -> set[str]:
    return {x.strip().lstrip("#").lower() for x in v.split(",") if x.strip()}


def allowed(author_id: int, channel_name: str) -> bool:
    channels = _list(os.getenv("DONNA_DISCORD_COMMAND_CHANNELS", ""))
    users = _list(os.getenv("DONNA_DISCORD_ALLOWED_USERS", ""))
    return bool(channels) and channel_name.lower() in channels and (not users or str(author_id) in users)


def tasks_text() -> str:
    rows = assistant.list_tasks()
    if not rows:
        return "No open tasks."
    return "\n".join(f"#{t['id']:<4} {t['priority'][:1].upper()} {t['due'] or '          '}  {t['title']}" for t in rows)


def handle(text: str) -> str | None:
    """Turn one message into a reply; None means 'not a command - stay silent'."""
    cmd, _, rest = text.strip().partition(" ")
    cmd = cmd.lower()
    if cmd == "!tasks":
        return tasks_text()
    if cmd == "!done":
        ids = [int(x) for x in re.findall(r"\d+", rest)]
        if not ids:
            return "Usage: !done 12 13"
        res = [f"#{i} {'done' if assistant.complete_task(i) else 'not found or already done'}" for i in ids]
        return "\n".join(res) + "\n\n" + tasks_text()
    if cmd == "!add":
        parts = [p.strip() for p in rest.split("|")]
        if not parts[0]:
            return "Usage: !add <title> | YYYY-MM-DD | high"
        due = next((p for p in parts[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)), None)
        prio = next((p.lower() for p in parts[1:] if p.lower() in ("high", "medium", "low")), "medium")
        tid = assistant.add_task(parts[0], due, prio, source="discord")
        return f"Added #{tid}: {parts[0]}" + (f" (due {due})" if due else "") + f" [{prio}]"
    if cmd == "!today":
        return assistant.my_day_text()
    if cmd == "!ask":
        if not rest.strip():
            return "Usage: !ask <question>"
        from jarvis.core.brain import Brain

        # read-only: anything that would write or send still needs approval in the console
        return Brain().ask([{"role": "user", "content": rest.strip()}], user="discord", allow_writes=False)
    if cmd in ("!help", "!commands"):
        return HELP
    return None


def chunks(text: str) -> list[str]:
    from donna.channels import chunks as split

    return [f"```\n{p.replace('```', 'ˋˋˋ')}\n```" for p in split(text, LIMIT)]


def main() -> None:
    import discord

    logging.basicConfig(level=logging.INFO)
    token = os.getenv("DONNA_DISCORD_BOT_TOKEN", "").strip().strip("\"'")
    if not token:
        raise SystemExit("DONNA_DISCORD_BOT_TOKEN not set")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())

    @client.event
    async def on_ready() -> None:
        log.info("logged in as %s; command channels: %s", client.user, os.getenv("DONNA_DISCORD_COMMAND_CHANNELS"))

    @client.event
    async def on_message(message: "discord.Message") -> None:
        if message.author.bot or message.webhook_id or not message.content.startswith("!"):
            return
        if not allowed(message.author.id, getattr(message.channel, "name", "")):
            return
        try:
            reply = await asyncio.to_thread(handle, message.content)  # DB / calendar calls off the event loop
        except Exception as e:  # never crash the bot on one bad command
            log.exception("command failed")
            reply = f"Sorry - that failed ({type(e).__name__}). Details are in the server log."
        if reply is None:
            return
        for part in chunks(reply):
            await message.channel.send(part)

    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
