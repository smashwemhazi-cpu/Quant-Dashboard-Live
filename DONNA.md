# D.O.N.N.A: the plan

DONNA is the voice and personality on top of JARVIS. JARVIS is the engine: the agents that read the markets, the desk, clients, compliance, email, calendar and your trading. DONNA is who you talk to. She remembers you, helps you think, keeps your day in order and tells you what matters.

Nothing is rewritten. Every new piece is one more agent file in `jarvis/agents/`, so it is picked up by the chat, the Discord bot and the scheduler automatically.

## What she does for you

| You want | DONNA does | Where |
|---|---|---|
| Bounce ideas | Talks with you in a private Discord channel and remembers the conversation. Pushes back when the data says so. | Just type in `#donna` |
| Untangle a busy head | `!dump` anything messy. She returns your top 3, adds the tasks to your list, puts ideas on the board, gives one next step per worry, and parks the rest. | `!dump` |
| Test an idea before spending on it | `!think` gives the best case, the weakest assumptions, the real numbers from the agents, a 7-day test with a pass/fail number, the compliance angle, and a verdict. | `!think <idea>` or `!think #3` |
| Keep ideas from getting lost | An ideas board with statuses: new, exploring, doing, parked, dropped. | `!idea`, `!ideas`, `!doing #3`, `!park #3` |
| Know you | She remembers your goals, preferences and rules. You can see and delete everything she keeps. | `!remember`, `!memory`, `!forget` |
| Start the day focused | The my-day brief now opens with your focus from the last `!dump` and the ideas in play. | Morning, automatic |
| Not miss things | A reminder 15 minutes before each meeting, and one midday nudge if high-priority work due today is still open. Each is sent once, even after a restart. | Automatic |
| Close the day | Evening shutdown: what got done, what's still open, ideas captured today, tomorrow's top 3. | Evening, automatic, or `!shutdown` |
| Step back weekly | Wins, what slipped, one trading habit to keep and one to stop (from the journal), and next week's three outcomes. | `!week` |
| Daily market and business analysis | Already built: morning dispatch, daily wrap, desk report, back office. Important alerts now reach Discord instead of only the log. | Automatic |

## Phase 1 (this PR): her mind

Memory, conversation, brain dump, ideas board, `!think`, shutdown, `!week`, alerts to Discord, and the trade-plan sizing fix for pairs not quoted in USD (USDJPY was sized about 150 times too small; crosses like EURGBP now ask for the conversion rate instead of guessing).

Also in phase 1, after a second review:
- Free chat in `#donna` only answers the Discord user IDs you list. A private channel alone isn't enough, because chat can reach every agent.
- The scheduler remembers when each job last ran, so a restart doesn't send the daily reports twice. A failed job now tells you in Discord instead of failing silently.
- "Today" in the shutdown is your local day, not the UTC day.
- An outage reply ("rate limited", "can't reach Claude") is no longer saved into the conversation.

## Roadmap, ranked

Ranked by how much each step moves DONNA toward "runs my day, thinks with me, grows the business", against the effort. Each step is its own PR, and nothing is merged without your go-ahead.

| # | Step | Why it's ranked here | What it needs from you |
|---|---|---|---|
| 1 | **Connect your calendar and email** | Already built (read-only email triage into tasks, Google Calendar in the my-day brief and the meeting reminders). Switching it on is what makes her run your day rather than only your task list. | About 10 minutes with `vertex1-connect`: a Google app password and your calendar's private iCal link. No cost. |
| 2 | **One morning and one evening message, with live position risk** | Up to five reports a day is noise. One morning message and one evening message, ranked by what needs you. The morning one includes your open trades, risk at stop and distance to the daily-loss limit. | Nothing. |
| 3 | **Growth co-pilot** | Uses the growth and compliance agents from PR #2 (now merged). A weekly growth review covering leads, funnel, IB performance and retention, plus one experiment to run next, and DONNA tracks whether it worked. | Nothing beyond the Syntellicore exports you already upload. |
| 4 | **Proactive nudges, round 2** | Meeting reminders and the midday check are done. Next is to speak up when it matters: a watchlist move beyond its normal range, a compliance case or obligation about to go overdue, a hot lead left untouched, a bot that stopped. | Nothing. |
| 5 | **Trading guardrails and the personal-account-dealing check** | Weekly and monthly drawdown limits, a daily trade cap, a cool-off after a loss streak, and a news blackout. Before any trade plan she checks your restricted list and holding period, and logs the pre-clearance. | Your firm's PAD policy: restricted list, holding period, pre-clearance rule. |
| 6 | **Security and audit hardening** | You'd expect this as a compliance officer. Client names and numbers are masked in tool results before they reach Claude. The audit log becomes tamper-evident (each entry chained to the last), and `!audit` shows what DONNA did today. | Nothing. |
| 7 | **Reliable hosting** | Services already restart on a crash, and as of this PR the scheduler survives a restart without repeating reports and tells you when a job fails. Still to add: nightly database backups, and an outside health check that messages you if a service is down. | Possibly a free uptime monitor account. I'll ask before adding anything. |
| 8 | **Voice** | Talking to her on your phone like Tony and Jarvis. Telegram voice notes go to speech-to-text, she answers as text and optionally voice. Ranked last because it needs a paid speech service and steps 1–4 deliver more day-to-day value. | Your OK on a speech service and its cost (I'll price the options first). |

## Switching phase 1 on

1. In Discord, create a private channel called `donna`.
2. In the server's `/etc/vertex1/vertex1.env`, add:
   ```
   DONNA_DISCORD_CHAT_CHANNELS=donna
   DONNA_DISCORD_ALLOWED_USERS=<your Discord user ID>
   DONNA_OWNER_NAME=SH
   JARVIS_ALERT_TARGETS=discord:personal
   ```
   To find your user ID, go to Discord → Settings → Advanced → turn on Developer Mode, then right-click your name and choose Copy User ID.
   For meeting reminders and email triage, the calendar link and mailbox must be connected too (`vertex1-connect` asks for them).
3. Restart: `sudo systemctl restart vertex1-discord vertex1-scheduler`.
4. In `#donna`, say hello. Then try `!dump` with whatever is on your mind.

The DONNA Discord bot you already have is reused, so there's no new account, key or cost beyond the Claude API usage per message.

## Guardrails

- DONNA never trades, moves money or messages clients. In chat she can add tasks, ideas and memories, and nothing else.
- What you type, and what she remembers about you, is sent to the Claude API to answer you. Keep client names and account numbers out of chat; use the desk tools for client work.
- Everything she keeps is in the JARVIS database on your server. `!memory` shows it, `!forget` removes it, and `!reset` clears a conversation.
