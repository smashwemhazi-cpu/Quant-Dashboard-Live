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
  !morning  !evening  !risk      the two daily digests on demand, and live position risk
  !growth  /  !exp               growth review + next experiment / experiments; !exp start 3, !exp stop 3
  !audit                         what DONNA did today, and whether the audit trail is intact

Plain messages are only answered in DONNA_DISCORD_CHAT_CHANNELS (also private), and only from the
user IDs in DONNA_DISCORD_ALLOWED_USERS - free chat reaches every agent, so it is never left open.

Answers only in the channels named in DONNA_DISCORD_COMMAND_CHANNELS (keep them private - the
channel is the access control) and, if DONNA_DISCORD_ALLOWED_USERS is set, only to those user IDs.
Runs as its own bot (DONNA_DISCORD_BOT_TOKEN, Message Content intent on) so it never collides
with any other bot in the server.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from typing import Any

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
        "!drafts                    today's compliant social-post drafts\n"
        "\nThinking partner:\n"
        "just talk                  in the chat channel; she remembers (!reset clears)\n"
        "!dump <whatever>           sort a messy head into focus, tasks, ideas\n"
        "!idea <text>  !ideas       capture / list ideas\n"
        "!think <idea or #id>       stress-test an idea\n"
        "!park #id  !doing #id  !drop #id\n"
        "!remember <fact>  !memory  !forget <id>\n"
        "!shutdown  !week           close the day / weekly review\n"
        "!morning  !evening  !risk  today's digests / live position risk\n"
        "!growth  !exp              growth review / experiments (!exp start 3)\n"
        "!audit                     what DONNA did today; audit trail check\n"
        "!status                    running version and what is connected / broken\n"
        "!pack  !pack 30            management pack PDF (last 7 / 30 days) to forward to the directors")


def _list(v: str) -> set[str]:
    return {x.strip().lstrip("#").lower() for x in v.split(",") if x.strip()}


def allowed(author_id: int, channel_name: str) -> bool:
    channels = _list(os.getenv("DONNA_DISCORD_COMMAND_CHANNELS", ""))
    users = _list(os.getenv("DONNA_DISCORD_ALLOWED_USERS", ""))
    return (bool(channels) and channel_name.lower() in channels and (not users or str(author_id) in users)) \
        or chat_allowed(author_id, channel_name)


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
    if cmd in ("!morning", "!evening", "!risk"):
        from jarvis.agents import rhythm

        return {"!morning": rhythm.morning_text, "!evening": rhythm.evening_text,
                "!risk": lambda: "\n".join(rhythm.risk_lines())}[cmd]()
    if cmd == "!week":
        return mind.week_review()
    if cmd in ("!growth", "!exp"):
        from jarvis.agents import copilot

        if cmd == "!growth":
            return copilot.review()
        sub, _, arg = rest.strip().partition(" ")
        eid, note = mind.parse_idea_ref(arg)
        if sub.lower() in ("start", "stop") and eid is not None:
            return copilot.start(eid) if sub.lower() == "start" else copilot.stop(eid, note)
        if sub:
            return "Usage: !exp | !exp start 3 | !exp stop 3 [why]"
        return copilot.experiments_text()
    if cmd == "!audit":
        return audit_text()
    if cmd == "!status":
        return status_text()
    return None


def status_text(client: Any = None) -> str:
    """Which version is running, and the last error of every job that failed in the last 24h."""
    import json
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from jarvis.core import audit
    from jarvis.core.brain import claude_check

    rel = Path(__file__).resolve().parents[1] / "RELEASE"
    version = rel.read_text().strip() if rel.exists() else "dev"
    L = [f"DONNA version {version} - running", f"Claude:  {claude_check(client)}"]
    from jarvis.scheduler import waiting_jobs

    waiting = waiting_jobs()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    last: dict[str, tuple[str, str]] = {}
    for r in audit.since(since, limit=20000):
        if r["actor"] == "scheduler" and r["event"] == "job_error":
            try:
                p = json.loads(r["payload"])
            except (TypeError, ValueError):
                continue
            last[p.get("job", "?")] = (r["ts"][11:16], str(p.get("error", ""))[:140])
    last = {j: v for j, v in last.items() if j not in waiting}
    L += _waiting_lines()
    if last:
        L.append("Failing in the last 24h:")
        L += [f" {job}: {err} (last {ts} UTC)" for job, (ts, err) in sorted(last.items())]
    else:
        L.append("No job failures in the last 24h.")
    return "\n".join(L)


def _waiting_lines() -> list[str]:
    from jarvis.scheduler import waiting_jobs

    w = waiting_jobs()
    if not w:
        return []
    return ["Waiting for setup (paused, not broken - they start by themselves once connected):"] + [
        f" {job}: {why}" for job, (why, _) in w.items()]


def audit_text() -> str:
    """What DONNA did today, and whether the audit trail is intact."""
    from collections import Counter
    from datetime import datetime, timezone

    from jarvis.agents.mind import LOCAL_TZ
    from jarvis.core import audit

    start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = audit.since(start.isoformat(), limit=20000)
    v = audit.verify()
    L = [f"AUDIT TRAIL: {'INTACT' if v['ok'] else 'BROKEN at entry #' + str(v['first_bad_id'])} "
         f"({v['checked']} chained entries checked)", f"Today: {len(rows)} entries"]
    for (actor, event), n in Counter((r["actor"], r["event"]) for r in rows).most_common(15):
        L.append(f" {n:>4}  {actor}: {event}")
    L += _waiting_lines()
    from jarvis.scheduler import waiting_jobs

    waiting = waiting_jobs()
    errors = [r for r in rows if "error" in r["event"] and not any(f'"{j}"' in r["payload"] for j in waiting)]
    if errors:
        L.append(f"Last error: {errors[-1]['ts'][11:16]} UTC {errors[-1]['actor']} {errors[-1]['event']} "
                 f"{errors[-1]['payload'][:150]}")
    return "\n".join(L)


def tasks_text() -> str:
    rows = assistant.list_tasks()
    if not rows:
        return "No open tasks."
    return "\n".join(f"#{t['id']:<4} {t['priority'][:1].upper()} {t['due'] or '          '}  {t['title']}" for t in rows)


def handle(text: str, channel: str = "") -> str | None:
    """Turn one message into a reply; None means 'not a command - stay silent'."""
    slip = re.match(r"#([a-z]+)\b", text.strip(), re.I)
    if slip and (f"!{slip.group(1).lower()} " in HELP or slip.group(1).lower() == "help"):  # "#help" for "!help"
        text = "!" + text.strip()[1:]
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
    reply = compliance_commands(cmd, rest)
    if reply is not None:
        return reply
    if cmd in ("!help", "!commands"):
        return HELP
    return mind_command(cmd, rest, f"discord:{channel.lower()}")


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
        pack = re.fullmatch(r"!pack(?:\s+(\d{1,3}))?", message.content.strip().lower())
        if pack:
            try:
                from jarvis.agents import pack as pk

                async with message.channel.typing():
                    pdf = await asyncio.to_thread(pk.build_pdf, int(pack.group(1) or 7))
                await message.channel.send("Management pack:", file=discord.File(io.BytesIO(pdf), pk.filename()))
            except Exception as e:
                log.exception("pack failed")
                await message.channel.send(f"Sorry - the pack failed ({type(e).__name__}).")
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
