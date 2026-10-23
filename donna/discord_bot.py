"""D.O.N.N.A task commands in Discord.

  !tasks                         open tasks, overdue first
  !done 12 [13 ...]              mark tasks done
  !add Call LP about XAU spreads | 2026-10-24 | high     (date and priority optional)
  !today                         the my-day brief (calendar, tasks, inbox, trading, bots)
  !ask <question>                ask D.O.N.N.A anything; she answers from the live agents (read-only)
  !help / !commands

Thinking partner (jarvis/agents/mind.py):
  plain message in a chat channel   talk to D.O.N.N.A; she remembers the conversation (!reset clears it)
  !dump <anything on your mind>  sorted into focus, tasks, ideas, worries and parked items
  !idea <text>  /  !ideas        capture an idea / see the ideas board
  !think <idea or #id>           stress-test an idea against the data
  !park #id  !doing #id  !drop #id   move an idea on the board
  !remember <fact>  !memory  !forget <id>   what D.O.N.N.A knows about you
  !shutdown                      close the day; !week for the weekly review

Plain messages are only answered in DONNA_DISCORD_CHAT_CHANNELS (also private), and only from the
user IDs in DONNA_DISCORD_ALLOWED_USERS - free chat reaches every agent, so it is never left open.

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
        "!ask <question>            e.g. !ask which clients are at risk this week?\n"
        "\nThinking partner:\n"
        "just talk                  in the chat channel; she remembers (!reset clears)\n"
        "!dump <whatever>           sort a messy head into focus, tasks, ideas\n"
        "!idea <text>  !ideas       capture / list ideas\n"
        "!think <idea or #id>       stress-test an idea\n"
        "!park #id  !doing #id  !drop #id\n"
        "!remember <fact>  !memory  !forget <id>\n"
        "!shutdown  !week           close the day / weekly review")


def _list(v: str) -> set[str]:
    return {x.strip().lstrip("#").lower() for x in v.split(",") if x.strip()}


def allowed(author_id: int, channel_name: str) -> bool:
    channels = _list(os.getenv("DONNA_DISCORD_COMMAND_CHANNELS", "")) | chat_channels()
    users = _list(os.getenv("DONNA_DISCORD_ALLOWED_USERS", ""))
    return bool(channels) and channel_name.lower() in channels and (not users or str(author_id) in users)


def chat_channels() -> set[str]:
    return _list(os.getenv("DONNA_DISCORD_CHAT_CHANNELS", ""))


def chat_allowed(author_id: int, channel_name: str) -> bool:
    """Free chat reaches every agent, so it needs a named user list, not just a private channel."""
    users = _list(os.getenv("DONNA_DISCORD_ALLOWED_USERS", ""))
    return channel_name.lower() in chat_channels() and str(author_id) in users


def mind_command(cmd: str, rest: str, channel: str) -> str | None:
    """The thinking-partner commands; None if cmd isn't one of them."""
    from jarvis.agents import mind

    if cmd == "!dump":
        return mind.dump_text(mind.brain_dump(rest)) if rest.strip() else "Usage: !dump <everything on your mind>"
    if cmd == "!idea":
        if not rest.strip():
            return "Usage: !idea <text>"
        iid = mind.add_idea(rest)
        return f"Idea #{iid} on the board." if iid else "That idea is already on the board."
    if cmd == "!ideas":
        rows = mind.list_ideas()
        return "\n".join(f"#{i['id']:<4} {i['status']:<9} ({i['area']}) {i['text'][:100]}" for i in rows) \
            or "The ideas board is empty. !idea <text> to add one."
    if cmd in ("!park", "!doing", "!drop"):
        iid, note = mind.parse_idea_ref(rest)
        if iid is None:
            return f"Usage: {cmd} #12 [note]"
        status = {"!park": "parked", "!doing": "doing", "!drop": "dropped"}[cmd]
        return f"Idea #{iid} is now {status}." if mind.set_idea(iid, status, note) else f"No idea #{iid}."
    if cmd == "!think":
        iid, text = mind.parse_idea_ref(rest)
        if iid is not None:
            idea = mind.get_idea(iid)
            if not idea:
                return f"No idea #{iid}."
            answer = mind.think(idea["text"] + (f"\nNotes so far: {idea['note']}" if idea["note"] else ""))
            mind.set_idea(iid, "exploring" if idea["status"] == "new" else "", f"[think {mind._today()}] {answer[:1500]}")
            return answer
        return mind.think(text) if text else "Usage: !think <idea> or !think #12"
    if cmd == "!remember":
        mid = mind.remember(rest)
        return f"Noted (#{mid})." if mid else "Usage: !remember <fact about you, your goals or how you work>"
    if cmd == "!memory":
        rows = mind.memories()
        return "\n".join(f"#{m['id']:<4} ({m['kind']}) {m['fact']}" for m in rows) or "Nothing stored yet. !remember <fact>"
    if cmd == "!forget":
        ids = [int(x) for x in re.findall(r"\d+", rest)]
        return "\n".join(f"#{i} {'forgotten' if mind.forget(i) else 'not found'}" for i in ids) or "Usage: !forget 3"
    if cmd == "!reset":
        mind.reset(channel)
        return "Fresh page. What's on your mind?"
    if cmd == "!shutdown":
        return mind.shutdown_text()
    if cmd == "!week":
        return mind.week_review()
    return None


def tasks_text() -> str:
    rows = assistant.list_tasks()
    if not rows:
        return "No open tasks."
    return "\n".join(f"#{t['id']:<4} {t['priority'][:1].upper()} {t['due'] or '          '}  {t['title']}" for t in rows)


def handle(text: str, channel: str = "") -> str | None:
    """Turn one message into a reply; None means 'not a command - stay silent'."""
    if not text.strip().startswith("!"):
        if channel.lower() in chat_channels():
            from jarvis.agents import mind

            return mind.chat(text, channel=f"discord:{channel.lower()}")
        return None
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
        from jarvis.agents import mind

        # read-only: anything that would write or send still needs approval in the console.
        # Goes through mind.chat so a follow-up !ask remembers the previous one.
        return mind.chat(rest, channel=f"discord:{channel.lower()}", allow_writes=False)
    if cmd in ("!help", "!commands"):
        return HELP
    return mind_command(cmd, rest, f"discord:{channel.lower()}")


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
        log.info("logged in as %s; command channels: %s; chat channels: %s", client.user,
                 os.getenv("DONNA_DISCORD_COMMAND_CHANNELS"), os.getenv("DONNA_DISCORD_CHAT_CHANNELS"))

    @client.event
    async def on_message(message: "discord.Message") -> None:
        if message.author.bot or message.webhook_id or not message.content.strip():
            return
        channel = getattr(message.channel, "name", "")
        if not message.content.startswith("!") and not chat_allowed(message.author.id, channel):
            return
        if not allowed(message.author.id, channel):
            return
        try:
            async with message.channel.typing():
                reply = await asyncio.to_thread(handle, message.content, channel)  # DB / Claude calls off the event loop
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
