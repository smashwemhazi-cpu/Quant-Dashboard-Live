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
        "!ask <question>            e.g. !ask which clients are at risk this week?\n"
        "\nCOMPLIANCE & RISK\n"
        "!compliance                full compliance & risk report\n"
        "!cases   !case 12          case register / one case with history\n"
        "!investigate 12 <note>     !escalate 12 <why>   !close 12 <what you did>\n"
        "!kri                       key risk indicators (red/amber/green)\n"
        "!mi 30                     management info for the last 30 days\n"
        "!oblig                     regulatory calendar  (!oblig add <name> | YYYY-MM-DD | monthly, !oblig done 3)\n"
        "!review <text>             check marketing copy before it goes out\n"
        "\nGROWTH\n"
        "!leads                     lead backlog, sources, conversion\n"
        "!drafts                    today's compliant social-post drafts")


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
    reply = compliance_commands(cmd, rest)
    if reply is not None:
        return reply
    if cmd in ("!help", "!commands"):
        return HELP
    return None


def compliance_commands(cmd: str, rest: str) -> str | None:
    from jarvis.agents import compliance as co, growth

    num = re.match(r"\s*(\d+)\s*(.*)", rest, re.S)
    if cmd == "!compliance":
        return co.report_text()
    if cmd == "!cases":
        rows = co.list_cases(rest.strip() or "active")
        return "\n".join(f"#{c['id']:<4} {c['severity'][:1].upper()} {c['status'][:5]:<5} due {c['due']}  "
                         f"{c['category']} {c['subject']}: {c['finding'][:90]}" for c in rows) or "No cases."
    if cmd == "!case":
        c = co.get_case(int(num.group(1))) if num else None
        if not c:
            return "Usage: !case 12"
        hist = "\n".join(f" {h['ts'][:16]} {h['who']}: {h['action']} {h['note'] or ''}" for h in c["log"])
        return (f"Case #{c['id']} [{c['severity']}] {c['category']} / {c['rule']} - {c['status']}\n"
                f"Subject: {c['subject']}\nOpened: {c['opened'][:16]}  Due: {c['due']}  Owner: {c['owner']}\n"
                f"Finding: {c['finding']}\n" + (f"Resolution: {c['resolution']}\n" if c["resolution"] else "")
                + f"History:\n{hist}")
    if cmd in ("!investigate", "!close", "!escalate", "!reopen"):
        if not num:
            return f"Usage: {cmd} 12 <note>"
        if cmd == "!close" and not num.group(2).strip():
            return "A case can only be closed with a note of what you found or did: !close 12 <note>"
        status = {"!investigate": "investigating", "!close": "closed", "!escalate": "escalated", "!reopen": "open"}[cmd]
        ok = co.update_case(int(num.group(1)), status, num.group(2).strip(), who="discord")
        return f"Case #{num.group(1)} -> {status}" if ok else "No case with that number."
    if cmd == "!kri":
        return "\n".join(f"[{k['status']:<7}] {k['kri']}: {k['value']}" for k in co.kris())
    if cmd == "!mi":
        m = co.management_info(int(num.group(1)) if num else 30)
        lines = [f"Last {m['days']} days: {m['cases']} cases"] + [
            f" {cat}: opened {v['opened']}, closed {v['closed']}, open {v['open']}" for cat, v in m["by_category"].items()]
        if m["avg_hours_to_close"] is not None:
            lines.append(f" Average time to close: {m['avg_hours_to_close']:.1f} h")
        return "\n".join(lines)
    if cmd == "!oblig":
        sub, _, arg = rest.strip().partition(" ")
        if sub == "add":
            p = [x.strip() for x in arg.split("|")]
            try:
                oid = co.add_obligation(p[0], p[1] if len(p) > 1 else "", p[2].lower() if len(p) > 2 else "")
            except (ValueError, IndexError) as e:
                return f"Usage: !oblig add <name> | YYYY-MM-DD | monthly/quarterly/yearly/weekly ({e})"
            return f"Obligation #{oid} added"
        if sub == "done" and arg.strip().isdigit():
            return f"Obligation #{arg.strip()}: {co.complete_obligation(int(arg))}"
        return "\n".join(f"#{o['id']:<3} {o['due']}  {o['name']}" + (f"  ({o['repeat']})" if o["repeat"] else "")
                         for o in co.list_obligations()) or "No obligations yet - !oblig add <name> | YYYY-MM-DD | monthly"
    if cmd == "!review":
        if not rest.strip():
            return "Usage: !review <paste the marketing text>"
        r = co.review_promotion(rest.strip())
        out = [f"Verdict: {r['verdict'].replace('_', ' ').upper()}"]
        out += [f" - \"{i['phrase']}\": {i['problem']} -> {i['fix']}" for i in r["issues"]]
        if r["compliant_version"]:
            out += ["", "Compliant version:", r["compliant_version"]]
        return "\n".join(out)
    if cmd == "!leads":
        return "\n".join(growth.report_lines())
    if cmd == "!drafts":
        return growth.drafts_text(growth.draft_posts())
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
