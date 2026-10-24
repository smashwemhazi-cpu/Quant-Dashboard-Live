"""D.O.N.N.A's thinking desk: memory, conversation, brain dumps, ideas and the evening shutdown.

Memory       Facts DONNA keeps about you (goals, preferences, context) - added with !remember or by
             DONNA herself when you tell her something worth keeping. Listed with !memory, removed with !forget.
Conversation Every chat channel keeps its last turns, so a follow-up ("and what about the second one?")
             makes sense. !reset clears it.
Brain dump   Paste anything messy; DONNA sorts it into top-3 focus, tasks (added to the task list),
             ideas (added to the ideas board), worries with one next step each, and things to park.
Ideas        A board of business and trading ideas with a status (new, exploring, doing, parked, dropped).
             !think <idea> stress-tests one: the best case, the weakest assumptions, what the data says,
             the cheapest 7-day test and a verdict.
Shutdown     Evening close-out: what got done, what is still open, ideas captured, and tomorrow's top 3.

All of this lives in the JARVIS state DB on your server. What you say to DONNA, and your stored memory,
is sent to the Claude API to answer you - keep client names and account numbers out of it.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

LOCAL_TZ = ZoneInfo(os.getenv("JARVIS_LOCAL_TZ", "UTC"))
OWNER = os.getenv("DONNA_OWNER_NAME", "SH")
HISTORY_TURNS = int(os.getenv("DONNA_HISTORY_TURNS", "20"))       # messages kept per chat channel
KEEP_TURNS = 200                                                  # stored per channel; older turns are deleted
FAILED_REPLIES = ("Claude API key missing", "Rate limited by the Claude API", "Cannot reach the Claude API",
                  "Claude API error", "Claude credit is used up", "No response.", "The request was declined")  # Brain.ask's error answers
NUDGE_MINUTES = int(os.getenv("DONNA_NUDGE_MINUTES", "15"))       # meeting reminder lead time
IDEA_STATUSES = ["new", "exploring", "doing", "parked", "dropped"]
MEMORY_KINDS = ["goal", "preference", "context", "person", "rule"]

PERSONA = """You are D.O.N.N.A, {owner}'s personal chief of staff and thinking partner - think Jarvis to Tony Stark.
{owner} is a compliance officer at Vertex 1 Brokers (retail FX/CFD), trades personally and is growing the business.
Now: {now} ({tz}).

How you work:
- Be brief, warm and direct. Lead with the answer. No filler, no flattery.
- Bounce ideas properly: steelman the idea first, then name the one or two assumptions most likely to be wrong,
  then say what would prove it cheaply. Disagree when the evidence says so; {owner} wants a sparring partner.
- When {owner} is scattered, reflect it back in order: "What I heard", "What matters most today", "Next action".
  Ask at most one sharp question, and only when the answer changes what you'd recommend.
- Use the tools for every number (markets, desk, clients, tasks, calendar, journal). Never invent figures;
  if data is missing, say "no data" and what would fix it.
- Track commitments: when {owner} decides to do something, create a task (assistant__create_task).
  When {owner} tells you a lasting fact about themselves, their goals or how they like to work, store it
  (mind__remember). New business or trading ideas go on the board (mind__capture_idea).
- Guardrails: you never place trades, move money, or send messages to clients. Personal trading by brokerage
  staff falls under the personal-account-dealing policy - mention it when a trade idea comes up.
  Treat client data as confidential and repeat as little of it as possible.

What you know about {owner}:
{memory}"""

DUMP_PROMPT = """You turn a messy brain dump into order for {owner}, a compliance officer at a retail FX/CFD
brokerage who trades and is growing the business. Today is {today}.
- top3: the three things that matter most today or this week, each one line, most important first.
- tasks: concrete actions a person can tick off, imperative, under 90 characters. due as YYYY-MM-DD only
  when the text states or implies a date, else "". Don't duplicate the top3 wording; a top3 item that is an
  action should also appear as a task.
- ideas: possibilities to explore later (business, trading, product), not commitments. area is one of
  business, trading, compliance, personal, other.
- worries: concerns or open loops, each with the single smallest next step that would reduce it.
- park: things that can safely wait or be dropped.
- note: one or two sentences of honest perspective (what is noise, what is the real priority).
Return only the JSON object."""
DUMP_SCHEMA = {
    "type": "object",
    "properties": {
        "top3": {"type": "array", "items": {"type": "string"}},
        "tasks": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "due": {"type": "string"},
                           "priority": {"type": "string", "enum": ["high", "medium", "low"]}},
            "required": ["title", "due", "priority"], "additionalProperties": False}},
        "ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "area": {"type": "string", "enum": ["business", "trading", "compliance", "personal", "other"]}},
            "required": ["text", "area"], "additionalProperties": False}},
        "worries": {"type": "array", "items": {
            "type": "object",
            "properties": {"worry": {"type": "string"}, "next_step": {"type": "string"}},
            "required": ["worry", "next_step"], "additionalProperties": False}},
        "park": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["top3", "tasks", "ideas", "worries", "park", "note"],
    "additionalProperties": False,
}

THINK_PROMPT = """Stress-test this idea for {owner}. Use the tools where real data helps (market regime, desk,
clients, pipeline, journal). Answer in this shape, under 250 words:
1. Best case - the strongest version of the idea in two lines.
2. Weakest assumptions - the two most likely to be wrong, and why.
3. What the data says - only figures from tools, or "no data".
4. Cheapest test - something doable in 7 days, with a pass/fail number.
5. Compliance/risk - anything a regulator, the PAD policy or the risk limits would care about.
6. Verdict - pursue, test first, or park, in one line.

Idea: {idea}"""

WEEK_PROMPT = """Weekly review for {owner}. Use the tools: tasks (done and open), ideas board, calendar for the
next 7 days, personal trading journal for 7 days, and the business picture you can see. Under 300 words:
- Wins this week (from done tasks and data).
- What slipped and the likely reason.
- Trading: one behaviour to keep, one to stop (from the journal, not guesses).
- Ideas: which one deserves the next hour, which to park.
- Next week: three outcomes that would make it a good week."""


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL, kind TEXT,
            created TEXT, active INTEGER DEFAULT 1, UNIQUE (fact));
        CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, area TEXT,
            status TEXT DEFAULT 'new', note TEXT DEFAULT '', created TEXT, updated TEXT, UNIQUE (text));
        CREATE TABLE IF NOT EXISTS conversation (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT);
        CREATE TABLE IF NOT EXISTS dumps (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS nudges (key TEXT PRIMARY KEY, ts TEXT);
    """)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _local_day(ts: str | None) -> str:
    """UTC ISO timestamp from the DB -> the owner's local date, so 'today' means their day, not UTC's."""
    if not ts:
        return ""
    t = datetime.fromisoformat(ts)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ).date().isoformat()


# ------------------------------------------------------------------ memory
def remember(fact: str, kind: str = "context") -> int | None:
    fact = fact.strip()[:500]
    if not fact:
        return None
    kind = kind if kind in MEMORY_KINDS else "context"
    with _db() as c:
        row = c.execute("SELECT id FROM memory WHERE fact = ?", (fact,)).fetchone()
        if row:
            c.execute("UPDATE memory SET active = 1, kind = ? WHERE id = ?", (kind, row["id"]))
            return row["id"]
        return c.execute("INSERT INTO memory (fact, kind, created) VALUES (?,?,?)", (fact, kind, _now())).lastrowid


def forget(memory_id: int) -> bool:
    with _db() as c:
        return c.execute("UPDATE memory SET active = 0 WHERE id = ? AND active = 1", (memory_id,)).rowcount == 1


def memories() -> list[dict[str, Any]]:
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT id, fact, kind, created FROM memory WHERE active = 1 ORDER BY kind, id")]


def memory_text() -> str:
    m = memories()
    return "\n".join(f"- ({x['kind']}) {x['fact']}" for x in m) or "- nothing stored yet"


# ------------------------------------------------------------------ ideas
def add_idea(text: str, area: str = "other") -> int | None:
    text = text.strip()[:500]
    if not text:
        return None
    with _db() as c:
        cur = c.execute("INSERT OR IGNORE INTO ideas (text, area, created, updated) VALUES (?,?,?,?)",
                        (text, area, _now(), _now()))
        return cur.lastrowid if cur.rowcount else None


def list_ideas(include_closed: bool = False) -> list[dict[str, Any]]:
    q = "SELECT * FROM ideas" + ("" if include_closed else " WHERE status NOT IN ('dropped')")
    order = " ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'exploring' THEN 1 WHEN 'new' THEN 2 ELSE 3 END, id DESC"
    with _db() as c:
        return [dict(r) for r in c.execute(q + order)]


def get_idea(idea_id: int) -> dict[str, Any] | None:
    with _db() as c:
        r = c.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
    return dict(r) if r else None


def set_idea(idea_id: int, status: str = "", note: str = "") -> bool:
    if status and status not in IDEA_STATUSES:
        raise ValueError(f"status must be one of {', '.join(IDEA_STATUSES)}")
    with _db() as c:
        row = c.execute("SELECT status, note FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        if not row:
            return False
        new_note = (row["note"] + "\n" if row["note"] and note else row["note"] or "") + note
        c.execute("UPDATE ideas SET status = ?, note = ?, updated = ? WHERE id = ?",
                  (status or row["status"], new_note[-4000:], _now(), idea_id))
        return True


# ------------------------------------------------------------------ conversation
def history(channel: str) -> list[dict[str, str]]:
    with _db() as c:
        rows = c.execute("SELECT role, content FROM conversation WHERE channel = ? ORDER BY id DESC LIMIT ?",
                         (channel, HISTORY_TURNS)).fetchall()
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    while msgs and msgs[0]["role"] != "user":  # the API needs the history to open with the user
        msgs.pop(0)
    return msgs


def reset(channel: str) -> int:
    with _db() as c:
        return c.execute("DELETE FROM conversation WHERE channel = ?", (channel,)).rowcount


def persona() -> str:
    now = datetime.now(LOCAL_TZ)
    return PERSONA.format(owner=OWNER, now=f"{now:%A %d %B %Y, %H:%M}", tz=str(LOCAL_TZ), memory=memory_text())


def chat(text: str, channel: str = "default", brain: Any = None, allow_writes: bool = False) -> str:
    """One turn of conversation with memory of the last turns in this channel."""
    text = text.strip()
    if not text:
        return "I'm listening."
    if brain is None:
        from jarvis.core.brain import Brain

        brain = Brain()
    msgs = history(channel) + [{"role": "user", "content": text}]
    reply = brain.ask(msgs, user=f"donna:{channel}", allow_writes=allow_writes, system=persona())
    if reply.startswith(FAILED_REPLIES):  # an outage message isn't conversation - don't make it context
        return reply
    with _db() as c:
        c.executemany("INSERT INTO conversation (channel, role, content, ts) VALUES (?,?,?,?)",
                      [(channel, "user", text[:8000], _now()), (channel, "assistant", reply[:8000], _now())])
        c.execute("DELETE FROM conversation WHERE channel = ? AND id NOT IN (SELECT id FROM conversation "
                  "WHERE channel = ? ORDER BY id DESC LIMIT ?)", (channel, channel, KEEP_TURNS))
    return reply


# ------------------------------------------------------------------ brain dump
def brain_dump(text: str, client: Any = None) -> dict[str, Any]:
    """Sort a messy dump into top3 / tasks / ideas / worries / park, then store the tasks and ideas."""
    from jarvis.agents import assistant

    text = text.strip()
    if not text:
        return {"error": "nothing to sort - write what's on your mind after !dump"}
    if client is None and not os.getenv("ANTHROPIC_API_KEY"):
        return {"error": "ANTHROPIC_API_KEY not set - sorting a brain dump needs the Claude API"}
    import anthropic

    client = client or anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=settings.model, max_tokens=8000,
            system=DUMP_PROMPT.format(owner=OWNER, today=_today()),
            messages=[{"role": "user", "content": text[:20000]}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": DUMP_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.APIError as e:
        return {"error": f"Claude API error: {type(e).__name__}"}
    if resp.stop_reason == "refusal":
        return {"error": "declined by the model's safety system - rephrase and try again"}
    if resp.stop_reason == "max_tokens":
        return {"error": "the dump was too long to sort in one go - split it in two"}
    r = json.loads(next(b.text for b in resp.content if b.type == "text"))
    ref = f"dump:{_now()}"
    r["task_ids"] = [tid for t in r["tasks"]
                     if (tid := assistant.add_task(t["title"], t["due"] or None, t["priority"],
                                                   source="brain_dump", source_ref=ref))]
    r["idea_ids"] = [iid for i in r["ideas"] if (iid := add_idea(i["text"], i["area"]))]
    with _db() as c:
        c.execute("INSERT INTO dumps (ts, day, result) VALUES (?,?,?)", (_now(), _today(), json.dumps(r)))
    audit.log("mind", "brain_dump", {"tasks": len(r["task_ids"]), "ideas": len(r["idea_ids"])})
    return r


def dump_text(r: dict[str, Any]) -> str:
    if "error" in r:
        return r["error"]
    L = ["FOCUS"] + [f" {i}. {t}" for i, t in enumerate(r["top3"], 1)]
    if r["tasks"]:
        L += ["", f"TASKS ADDED ({len(r['task_ids'])})"]
        L += [f" [{t['priority']}] {t['title']}" + (f" (due {t['due']})" if t["due"] else "") for t in r["tasks"]]
    if r["ideas"]:
        L += ["", "IDEAS BOARD"] + [f" ({i['area']}) {i['text']}" for i in r["ideas"]]
    if r["worries"]:
        L += ["", "ON YOUR MIND"] + [f" {w['worry']}\n   -> {w['next_step']}" for w in r["worries"]]
    if r["park"]:
        L += ["", "PARKED"] + [f" {p}" for p in r["park"]]
    if r["note"]:
        L += ["", r["note"]]
    return "\n".join(L)


def latest_focus(day: str | None = None) -> list[str]:
    """Top 3 from the most recent brain dump of the day (or yesterday's if none today)."""
    with _db() as c:
        row = c.execute("SELECT result FROM dumps WHERE day <= ? ORDER BY id DESC LIMIT 1",
                        (day or _today(),)).fetchone()
    return json.loads(row["result"]).get("top3", []) if row else []


# ------------------------------------------------------------------ evening shutdown
def shutdown_text(day: str | None = None) -> str:
    """Close the day: done, still open, ideas captured, tomorrow's top 3. No AI call - free to run daily."""
    from jarvis.agents import assistant

    day = day or _today()
    with assistant._db() as c:
        done = [dict(r) for r in c.execute("SELECT id, title, done_at FROM tasks WHERE status = 'done'")
                if _local_day(r["done_at"]) == day]
    open_ = assistant.list_tasks()
    overdue = [t for t in open_ if t["due"] and t["due"] < day]
    with _db() as c:
        new_ideas = [dict(r) for r in c.execute("SELECT id, text, created FROM ideas") if _local_day(r["created"]) == day]
    rank = {"high": 0, "medium": 1, "low": 2}
    tomorrow = sorted(open_, key=lambda t: (t["due"] is None, t["due"] or "", rank.get(t["priority"], 3), t["id"]))[:3]
    L = [f"D.O.N.N.A | SHUTDOWN | {day}", "-" * 34, f"DONE TODAY ({len(done)})"]
    L += [f" #{t['id']} {t['title']}" for t in done] or [" Nothing ticked off - was the day reactive?"]
    L += ["", f"STILL OPEN ({len(open_)}, {len(overdue)} overdue)"]
    L += [f" !! #{t['id']} {t['title']} (due {t['due']})" for t in overdue[:5]]
    if new_ideas:
        L += ["", "IDEAS CAPTURED TODAY"] + [f" #{i['id']} {i['text'][:120]}" for i in new_ideas]
    L += ["", "TOMORROW'S TOP 3 (from your list - reply !dump to reshuffle)"]
    L += [f" {n}. #{t['id']} {t['title']}" + (f" (due {t['due']})" if t["due"] else "") for n, t in enumerate(tomorrow, 1)] \
        or [" Nothing open. Use the space for the business."]
    L += ["", "Anything still in your head? Dump it now so it's out of your head tonight: !dump <text>"]
    return "\n".join(L)


# ------------------------------------------------------------------ proactive nudges
def _once(key: str) -> bool:
    """True the first time a nudge key is seen, so a restart or a 5-minute loop never repeats a nudge."""
    with _db() as c:
        return c.execute("INSERT OR IGNORE INTO nudges (key, ts) VALUES (?,?)", (key, _now())).rowcount == 1


def meeting_nudges(now: datetime | None = None, events: list[dict[str, Any]] | None = None) -> list[str]:
    """Reminders for meetings starting in the next NUDGE_MINUTES, each sent once."""
    from jarvis.agents import assistant

    now = now or datetime.now(LOCAL_TZ)
    if events is None:
        cal = assistant.calendar_events(now.date())
        events = cal.get("events", [])
    out = []
    for e in events:
        if e["all_day"]:
            continue
        start = datetime.strptime(e["start"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
        mins = (start - now).total_seconds() / 60
        if 0 <= mins <= NUDGE_MINUTES and _once(f"meeting:{e['start']}:{e['title']}"):
            where = f" ({e['location']})" if e["location"] else ""
            out.append(f"In {mins:.0f} min: {e['title']}{where}, {e['start'][11:]}-{e['end']}")
    return out


def midday_check(day: str | None = None) -> str | None:
    """One midday nudge when high-priority work due today (or overdue) is still open; None when all clear."""
    from jarvis.agents import assistant

    day = day or _today()
    late = [t for t in assistant.list_tasks() if t["priority"] == "high" and t["due"] and t["due"] <= day]
    if not late or not _once(f"midday:{day}"):
        return None
    L = [f"Halfway through the day. {len(late)} high-priority item(s) still open:"]
    L += [f" #{t['id']} {t['title']}" + (" (overdue)" if t["due"] < day else "") for t in late[:5]]
    focus = latest_focus(day)
    if focus:
        L.append(f"Your focus today was: {focus[0]}")
    L.append("Block 45 minutes for the first one, or !done it if it's handled.")
    return "\n".join(L)


def focus_lines() -> list[str]:
    """For the my-day brief: today's focus from the last brain dump and the active ideas."""
    f = latest_focus()
    L = ["FOCUS (from your last !dump)"] + ([f" {i}. {t}" for i, t in enumerate(f, 1)] or [" Not set - try !dump"])
    doing = [i for i in list_ideas() if i["status"] in ("doing", "exploring")]
    if doing:
        L += ["IDEAS IN PLAY: " + "; ".join(f"#{i['id']} {i['text'][:60]}" for i in doing[:3])]
    return L


@register
class MindAgent(Agent):
    name = "mind"
    description = "DONNA's memory of the owner, the ideas board, brain-dump sorting and the evening shutdown."

    @tool()
    def remember(self, fact: str, kind: str = "context") -> dict[str, Any]:
        """Store a lasting fact about the owner (their goals, preferences, people, rules they set).
        kind: goal, preference, context, person or rule. Don't store client data or one-off details."""
        return {"memory_id": remember(fact, kind)}

    @tool()
    def memory(self) -> list[dict[str, Any]]:
        """Everything DONNA currently remembers about the owner."""
        return memories()

    @tool()
    def capture_idea(self, text: str, area: str = "other") -> dict[str, Any]:
        """Put a business/trading/compliance idea on the ideas board. area: business, trading, compliance, personal, other."""
        return {"idea_id": add_idea(text, area)}

    @tool()
    def ideas(self, include_closed: bool = False) -> list[dict[str, Any]]:
        """The ideas board (id, text, area, status, notes), ideas in play first."""
        return list_ideas(include_closed)

    @tool()
    def update_idea(self, idea_id: int, status: str = "", note: str = "") -> dict[str, Any]:
        """Change an idea's status (new, exploring, doing, parked, dropped) and/or append a note."""
        return {"updated": set_idea(idea_id, status, note)}

    def nudge(self, midday: bool = False) -> dict[str, Any]:
        """Scheduler hook (not a Brain tool): meeting reminders, plus the midday check when midday=True.
        Delivers to JARVIS_PERSONAL_TARGETS."""
        msgs = meeting_nudges() + ([m] if midday and (m := midday_check()) else [])
        res: dict[str, Any] = {"nudges": msgs}
        if msgs:
            from jarvis.agents.personal import PERSONAL_TARGETS

            if PERSONAL_TARGETS:
                from donna.channels import broadcast

                res["delivery"] = broadcast("\n\n".join(msgs), PERSONAL_TARGETS, title="D.O.N.N.A heads-up")
        return res

    @tool()
    def shutdown(self, send: bool = False) -> dict[str, Any]:
        """Evening close-out: done today, still open, ideas captured, tomorrow's top 3.
        send=True delivers to JARVIS_PERSONAL_TARGETS."""
        text = shutdown_text()
        res: dict[str, Any] = {"text": text}
        if send:
            from jarvis.agents.personal import PERSONAL_TARGETS

            if not PERSONAL_TARGETS:
                res["delivery"] = {"error": "JARVIS_PERSONAL_TARGETS not set"}
            else:
                from donna.channels import broadcast

                res["delivery"] = broadcast(text, PERSONAL_TARGETS, title="D.O.N.N.A shutdown")
        audit.log("mind", "shutdown", {"delivery": res.get("delivery")})
        return res


def think(idea: str, brain: Any = None) -> str:
    """Stress-test an idea with the live tools (read-only)."""
    if brain is None:
        from jarvis.core.brain import Brain

        brain = Brain()
    return brain.ask([{"role": "user", "content": THINK_PROMPT.format(owner=OWNER, idea=idea)}],
                     user="donna:think", system=persona())


def week_review(brain: Any = None) -> str:
    if brain is None:
        from jarvis.core.brain import Brain

        brain = Brain()
    return brain.ask([{"role": "user", "content": WEEK_PROMPT.format(owner=OWNER)}], user="donna:week", system=persona())


_ID = re.compile(r"^#?(\d+)\b\s*")


def parse_idea_ref(rest: str) -> tuple[int | None, str]:
    """'#12 extra words' -> (12, 'extra words'); anything else -> (None, rest)."""
    m = _ID.match(rest.strip())
    return (int(m.group(1)), rest.strip()[m.end():]) if m else (None, rest.strip())


if __name__ == "__main__":
    # python -m jarvis.agents.mind            prints tonight's shutdown
    print(shutdown_text())
