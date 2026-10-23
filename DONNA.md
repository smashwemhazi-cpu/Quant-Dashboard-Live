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
| Close the day | Evening shutdown: what got done, what's still open, ideas captured today, tomorrow's top 3. | Evening, automatic, or `!shutdown` |
| Step back weekly | Wins, what slipped, one trading habit to keep and one to stop (from the journal), and next week's three outcomes. | `!week` |
| Daily market and business analysis | Already built: morning dispatch, daily wrap, desk report, back office. Important alerts now reach Discord instead of only the log. | Automatic |

## Phases

**Phase 1 (this PR): her mind.** Memory, conversation, brain dump, ideas board, `!think`, shutdown, `!week`, alerts to Discord, and the trade-plan sizing fix for pairs not quoted in USD (USDJPY was sized about 150 times too small; crosses like EURGBP now ask for the conversion rate instead of guessing).

**Phase 2: fewer, better messages.** One morning message and one evening message instead of up to five reports. Each one is ranked by what needs you, with everything else one command away.

**Phase 3: growth co-pilot.** Builds on the compliance and growth agents in PR #2: a weekly growth review (leads, funnel, IB performance, retention), with DONNA proposing the one experiment to run next and tracking whether it worked.

**Phase 4: personal-account-dealing guard.** Before any trade plan, DONNA checks the instrument against your restricted list and holding-period rules, and logs the pre-clearance. It needs your firm's PAD policy to set up.

**Phase 5: reach.** Voice notes in Telegram, and a phone-friendly page. Only if you want them.

## Switching phase 1 on

1. In Discord, create a private channel called `donna`.
2. In the server's `/etc/vertex1/vertex1.env`, add:
   ```
   DONNA_DISCORD_CHAT_CHANNELS=donna
   DONNA_OWNER_NAME=SH
   JARVIS_ALERT_TARGETS=discord:personal
   ```
3. Restart: `sudo systemctl restart vertex1-discord vertex1-scheduler`.
4. In `#donna`, say hello. Then try `!dump` with whatever is on your mind.

The DONNA Discord bot you already have is reused, so there's no new account, key or cost beyond the Claude API usage per message.

## Guardrails

- DONNA never trades, moves money or messages clients. In chat she can add tasks, ideas and memories, and nothing else.
- What you type, and what she remembers about you, is sent to the Claude API to answer you. Keep client names and account numbers out of chat; use the desk tools for client work.
- Everything she keeps is in the JARVIS database on your server. `!memory` shows it, `!forget` removes it, and `!reset` clears a conversation.
