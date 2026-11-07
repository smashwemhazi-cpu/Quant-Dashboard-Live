# D.O.N.N.A: the plan

DONNA is the voice and personality on top of JARVIS. JARVIS is the engine: the agents that read the markets, the desk, clients, compliance, email, calendar and your trading. DONNA is who you talk to. She remembers you, helps you think, keeps your day in order and tells you what matters.

Nothing is rewritten. Every new piece is one more agent file in `jarvis/agents/`, so it is picked up by the chat, the Discord bot and the scheduler automatically.

## DONNA's mandate: what she does to make your life better

**Her purpose:** Give SH back time and certainty. Nothing important slips, every day starts with a plan and ends closed, the routine work of the firm runs without SH, and every decision is made with the facts in front of SH.

She has seven duties. Each one is carried out by agents she coordinates, and each has a test you can check yourself.

| Duty | What she does | Commands | How you can tell |
|---|---|---|---|
| **Run your day** | Sorts your inbox, keeps your task list and diary, reminds you before meetings, and opens and closes each day with one message. | !today !tasks !add !done !draft !waiting !diary !workflow !morning !evening | Morning message by 08:00 your time; urgent unread email reaches you within 15 minutes. |
| **Keep the firm compliant** | Runs the AML/KYC and execution rules on every back-office report, keeps the case and complaints registers, screens names against sanctions and PEP lists, tracks regulatory deadlines and builds the weekly pack and STR evidence packs. | !compliance !cases !kri !oblig !complaints !screen !str !ib !pack | No regulatory deadline reaches its due date without a reminder 2 days before; the Monday pack arrives on its own. |
| **Watch the desk and the markets** | Watches execution quality and LP slippage, writes the desk report, the morning dispatch and the daily wrap. | !mi  (and the automatic dispatch and wrap) | Daily wrap at 21:00 your time; an LP slippage episode is flagged within the hour. |
| **Protect your own trading** | Reads your cTrader account (read-only), watches open risk, stops and the daily-loss limit, keeps your trading journal and watches your cBots, and checks your own trades against the PAD policy with a monthly pack for the MLRO. | !risk !journal !pad | A position without a stop or a hit daily-loss limit is flagged within 15 minutes. |
| **Grow the business** | Finds possible IBs and traders, drafts first messages for your OK, builds country campaigns and compliant post drafts, tracks which source brings funded clients, flags clients at risk of leaving and runs one growth experiment a week. | !leads !crm !campaign !drafts !review !attribution !growth !exp | A fresh lead shortlist each week; nothing is sent to a lead without your confirm. |
| **Think with you** | Talks ideas through with you, sorts a messy head into a plan, stress-tests ideas against the real numbers, remembers your goals and rules, and runs the weekly review. | chat in #donna, !dump !think !idea !ideas !remember !memory !week | Every decision you make in chat becomes a task; every idea lands on the board. |
| **Keep herself honest and running** | Keeps a tamper-evident log of everything she does, checks in every 10 minutes so you are emailed if she goes offline, backs herself up nightly, and tells you plainly what is waiting on you. | !status !audit !agents !hq | !status is green, or names exactly what is waiting on you. |

**What she never does:**
- Never places, changes or closes a trade, and never moves money.
- Never messages a client. Leads only get a first message after you type !leads confirm.
- Never sends email for you: replies go to your Gmail Drafts for you to check and send.
- Never invites anyone to a meeting, and never changes or deletes an existing calendar event.
- Passwords and keys only go into vertex1-connect on the server, never into chat.
- Anything that costs money waits for your yes.

**She coordinates the agents from one place:**
- `!mandate` gives this answer in Discord. Ask her in chat ("what can you do for me?") and she answers from the same mandate.
- `!hq` gives one view across all agents: what needs you first, then each duty in one line.
- `!agents` lists every agent job under its duty: OK, waiting for setup, failing (since when and why) or not run yet, and when it last ran.
- `!run <job>` runs one agent now, for example `!run daily_wrap` or `!run partner_radar`.

The mandate lives in `jarvis/agents/hq.py`. A test fails if a new agent job isn't given a duty, so this section can't fall out of date.

## KPIs: how DONNA scores every agent and your trading

Every agent and every trader has KPIs with a target, scored GREEN, AMBER, RED or NO DATA (the source isn't connected yet, so it is never shown as green). Each KPI is either **grow** (the business gets bigger) or **protect** (it stays safe while it grows). The full list with the starter targets is in the doc "DONNA KPI Scorecard: agents, leads and traders".

- `!kpi` the whole scorecard. `!kpi leads`, `!kpi marketing`, `!kpi growth`, `!kpi compliance`, `!kpi admin`, `!kpi desk`, `!kpi self`, `!kpi trading`, `!kpi book` one department.
- `!kpi targets` every target. `!kpi set leads_qualified_week 20 10` changes one (target 20, amber at 10). `!kpi reset leads_qualified_week` puts it back.
- `!kpi lead 12` runs the lead quality bar on one lead. A lead only counts if it is in a cleared country, has no red flags, has a public link (never a personal LinkedIn profile), was active recently (60 days for IBs, 14 for traders), is about trading, has 1,000+ followers when shown (IBs) and scores 60+ (IBs) or 50+ (traders). `!leads` tags each lead QUALIFIED or says why it doesn't count, and `!leads stats` shows the qualified count per source and the lead KPIs against target.
- `!hq` shows the KPI counts and the worst reds. The Monday management pack has a KPI section.
- **Growth brake:** if red-flag leads were contacted, a sanctions match is open, a complaint or regulatory deadline is late, a lead was messaged without your confirm, or the audit trail breaks, `!kpi` and `!hq` say "growth brake" and name what to fix first.
- Trader KPIs only read the cTrader feed and back-office exports. DONNA never places, changes or closes a trade.

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
| 2 | ✅ **One morning and one evening message, with live position risk** (step 2 PR) | Up to five reports a day is noise. One morning message and one evening message, ranked by what needs you. The morning one includes your open trades, risk at stop and distance to the daily-loss limit. | Nothing. |
| 3 | ✅ **Growth co-pilot** | Uses the growth and compliance agents from PR #2 (now merged). A weekly growth review covering leads, funnel, IB performance and retention, plus one experiment to run next, and DONNA tracks whether it worked. | Nothing beyond the Syntellicore exports you already upload. |
| 4 | ✅ **Proactive nudges, round 2** | Meeting reminders and the midday check are done. Next is to speak up when it matters: a watchlist move beyond its normal range, a compliance case or obligation about to go overdue, a hot lead left untouched, a bot that stopped. | Nothing. |
| 5 | **Trading guardrails and the personal-account-dealing check** | Weekly and monthly drawdown limits, a daily trade cap, a cool-off after a loss streak, and a news blackout. Before any trade plan she checks your restricted list and holding period, and logs the pre-clearance. | Your firm's PAD policy: restricted list, holding period, pre-clearance rule. |
| 6 | ✅ **Security and audit hardening** | You'd expect this as a compliance officer. Client names and numbers are masked in tool results before they reach Claude. The audit log becomes tamper-evident (each entry chained to the last), and `!audit` shows what DONNA did today. | Nothing. |
| 7 | **Reliable hosting** | Services already restart on a crash, and as of this PR the scheduler survives a restart without repeating reports and tells you when a job fails. Still to add: nightly database backups, and an outside health check that messages you if a service is down. | Possibly a free uptime monitor account. I'll ask before adding anything. |
| 8 | **Voice** | Talking to her on your phone like Tony and Jarvis. Telegram voice notes go to speech-to-text, she answers as text and optionally voice. Ranked last because it needs a paid speech service and steps 1–4 deliver more day-to-day value. | Your OK on a speech service and its cost (I'll price the options first). |

## Step 2: one morning, one evening

With `DONNA_DIGEST=1` (the default) your private channel gets two scheduled messages a day instead of five or six:

- **Morning** (at `JARVIS_MYDAY_UTC_HOUR`):
  - **Needs you:** a ranked list of overdue or due-today high-priority tasks, urgent emails, red compliance KRIs, overdue cases and due regulatory obligations, positions without a stop, the daily-loss limit if hit, and stopped bots.
  - Your focus, today's calendar, position risk, and the three biggest market moves.
- **Evening** (at `JARVIS_SHUTDOWN_UTC_HOUR`): the shutdown, your trading day, position risk, and markets at the close.
- **Replaced:** the separate my-day brief, compliance report, marketing drafts, shutdown and the paid AI morning brief. Each is still one command away (`!today`, `!compliance`, `!drafts`, `!shutdown`, or just ask), and `!morning`, `!evening` and `!risk` show the digests on demand.
- **Unchanged:** urgent emails, meeting reminders, the midday check and alerts still arrive straight away. The team's morning dispatch and daily wrap still go to the team channel.

**Position risk** needs a live feed of your open positions. DONNA accepts them at `POST /api/positions`, with equity, start-of-day equity, and for each position the symbol, side, units, entry, stop and current price. From that she works out:
- the risk at stop for each position, with the yen and other quote currencies converted;
- total open risk against your limit;
- how much room is left before the daily-loss stop;
- any position without a hard stop.

Until a feed is connected, the section says NO DATA.

## Step 3: growth co-pilot

Every Monday (`DONNA_GROWTH_WEEKDAY`, at `JARVIS_GROWTH_UTC_HOUR`) and whenever you type `!growth`, DONNA writes a growth review from the Syntellicore exports you already upload: a headline, up to three wins and three worries, each tied to a number, and **one experiment** for next week.

An experiment is a concrete action (for example "round-robin new leads to sales every hour"), with a target on a number DONNA measures herself from the next exports:
- new leads in the last 7 days
- conversion (leads that funded, %)
- stale leads (untouched for 3+ days)
- leads with no owner
- funded accounts

`!exp start 3` records today's value as the baseline. When the end date passes, the next review marks the experiment **won** or **lost** against its target. `!exp` lists them all, and `!exp stop 3` drops one. Proposals stay within what a regulated broker may do: no bonuses or trading incentives, no pressure selling, no profit claims.

Only counts and source or IB names are sent to Claude, never client names, account numbers or customer ids. With no leads export uploaded, the review says NO DATA and doesn't call Claude.

## Step 4: daytime heads-ups

Every 15 minutes DONNA checks for things that shouldn't wait for the evening message, and tells you **once** in your private channel:
- **Market:** a watchlist instrument whose range today is 1.5 times its normal day or more (`DONNA_MOVE_ATR`).
- **Compliance:** a regulatory obligation due within 2 days (`DONNA_OBLIGATION_WARN_DAYS`), or a case due today or tomorrow, before it becomes overdue.
- **Leads:** new leads still without an owner 2 hours after DONNA first saw the export (`DONNA_LEAD_HOT_HOURS`).
- **Bots:** a cBot that crashed or went quiet.
- **Positions:** a live position without a stop, or the daily-loss limit hit.

Each heads-up is remembered, so a restart never repeats it. If one source is down, the others still run.

## Step 6: security and audit

- **Client data masked before Claude sees it.** When DONNA uses a tool in chat, client names in the result become initials, emails become `j***@domain`, and phone numbers, addresses, dates of birth and ID or bank numbers become `***`. Client ids, account numbers and figures pass through, so answers still point at the right record. Turn it off with `DONNA_MASK_PII=0` (not recommended).
- **Tamper-evident audit trail.** Every audit entry carries a fingerprint (SHA-256) of itself and of the entry before it. Changing or deleting an entry breaks the chain from that point. Entries written before this release stay as they were, and the chain starts after them.
- **`!audit`** in Discord shows what DONNA did today, the last error, and whether the trail is intact.
- **Nightly backups** (step 7, started here): at 01:30 UTC every database is copied to `/var/lib/vertex1/backups/<date>/`, and 14 days are kept (`VERTEX1_BACKUP_DAYS`). The restore steps are at the top of `deploy/vertex1-backup`.

## Email and calendar without a password (the Google bridge)

Company Google accounts can have app passwords switched off by the admin, so Google refuses them however carefully they are pasted. The bridge avoids passwords altogether: a small script runs inside your own Google account and hands DONNA your new unread emails and your calendar.
- It only reads. It never sends, deletes, labels or marks anything as read.
- DONNA holds only the bridge link and a random key. You can switch it off at any time from script.google.com.
- The script is `deploy/google-bridge.gs` in this repository.

Set it up once, in the browser, signed in as **simba@vertex1brokers.com**:
1. Open https://script.google.com and click **New project**. Name it "DONNA bridge".
2. Delete what is in the editor. Paste the whole of `google-bridge.gs`, then click the **Save** icon.
3. Pick **setup** in the function box at the top, then click **Run**.
   - Click **Review permissions**, choose your work account, then **Allow**.
   - If Google shows "Google hasn't verified this app", click **Advanced**, then **Go to DONNA bridge**. It is your own script.
   - The log at the bottom shows `DONNA bridge key - ...`. Copy the 64-character key.
4. Click **Deploy**, then **New deployment**. Click the cog, then **Web app**.
   - **Execute as:** Me.
   - **Who has access:** Anyone. Pick the plain "Anyone", not "Anyone with Google account" or "Anyone within Vertex 1 Brokers": those make Google answer with a sign-in page.
   - Click **Deploy** and copy the **Web app URL**, which ends in `/exec`.
5. Double-click **Vertex1-Update-Now.bat**.
   - Press Enter at 5 and 6.
   - Paste the URL at **6a** and the key at **6b**.
   - It should say `EMAIL OK via Google bridge`.

The calendar comes through the same bridge, so question 7 can be skipped.

If step 4 offers only "Anyone within Vertex 1 Brokers", the company admin has blocked public web apps. Send Claude a screenshot.

### Updating the bridge script (when Claude ships a new version)
The link and key stay the same, so no setup questions are asked again.
1. Open script.google.com, then **DONNA bridge**.
2. Select everything in the editor and delete it. Paste the new `google-bridge.gs`, then click **Save**.
3. Click **Deploy**, then **Manage deployments**, then the ✏️ pencil. Set **Version** to **New version** and click **Deploy**.
4. If Google asks for permission again (new abilities need it), click **Allow**.

Version 2 adds two things:
- **Reports from email:** cTrader and Syntellicore reports that arrive as attachments are loaded automatically.
- **Draft replies:** `!draft 12` writes a reply to task 12's email and saves it in your Gmail **Drafts**. It is never sent; you review it and press send yourself.

Version 4 also reads **all new inbox mail, not just unread**, so mail you opened on your phone first still counts. Mail you've opened or answered is counted for `!workflow` but isn't chased. Version 4 adds **your diary** too: `!diary add <title> | YYYY-MM-DD HH:MM | 45m` and `!diary block 12` put events in your own Google Calendar. They are created with no guests, so nobody is invited, and DONNA never changes or deletes an event that is already there. After pasting version 4, choose **setup** in the function list and click **Run** once, then **Allow**: Google needs your OK for DONNA to add calendar events. `!diary` (reading) and `!workflow` work without this update.

Version 5 (optional) adds two read-only things. It lists **sent emails nobody has answered**, by subject and recipient only, for the second half of `!waiting`. It also shows **who a meeting is with**, so the 15-minute reminder can show your recent emails with them. No new permissions are needed, so you don't have to run setup again. Paste it, then go to **Manage deployments → ✏️ pencil → New version → Deploy**. Don't use "New deployment", because that makes a new link DONNA doesn't know.

## Follow-ups and meeting prep

- `!waiting` lists the emails that asked you something and that you haven't answered yet, oldest first. Each one shows its task number, so `!draft 12` writes the reply. Anything over 5 days old is marked. A reply you send from your phone takes the email off the list by itself. If you dealt with one another way, use `!waiting done E12`.
- The second half of `!waiting` lists emails you sent that nobody has answered after 3 working days. This part needs bridge version 5. You can change the number of days with `JARVIS_CHASE_DAYS`.
- The meeting reminder 15 minutes before a meeting now adds who it's with (bridge version 5), your last 3 emails that match the people or the meeting title, and open tasks that mention them. All of this works without Claude credit.

## Reports by email (no more uploading)

Schedule the cTrader and Syntellicore reports to be emailed to simba@vertex1brokers.com. Every 30 minutes DONNA looks for new spreadsheet attachments (.csv, .xlsx, .xls) from the last 3 days.
- Files she recognises by their columns (end-of-day, account activity, price streams, KPIs, documents, leads, statements) are loaded. The compliance checks then run straight away, and you get a "Picked up from your email" message.
- Every other spreadsheet is deleted from the server at once, so a client's file never stays there.
- To accept reports only from certain senders, set `JARVIS_REPORT_SENDERS=ctrader.com,syntellicore.com` in the settings file.

## DONNA Console (her face and voice)

Double-click **DONNA-Console.bat** on your PC and type your server password. A window opens with:
- her animated orb;
- your calendar, inbox highlights, tasks and risk panels, refreshed every minute;
- **Your agents**: the seven duties from her mandate, each with a light (green running, amber waiting on setup, red needs a fix). Click a duty for the `!hq` view, or **details** for every agent. Ask "what can you do?" for her mandate.

How to use it:
- **Hold the button or the space bar** to talk, and let go when you are done. She answers on screen and out loud.
- Type commands exactly as in Discord, e.g. `!tasks`, `!done 12`, `!draft 12`, `!kri`.
- The 🔊 button mutes her. The menu next to it picks her voice; the choice is remembered.

Privacy and limits:
- The console is not on the internet. It travels inside the same private SSH link as your server login, and it closes when you close the black window.
- Speech recognition in Edge and Chrome sends your voice to Microsoft or Google to turn into text. If that is not acceptable for a conversation, type instead.
- She needs Claude API credit to talk. Commands like `!tasks` work without it.

## Complaints register

Every client complaint gets a number, deadlines and an evidence trail: received, acknowledged, decided and why, and the root cause. That is what an ombud or regulator asks for.

**Getting complaints in:**
- Log one yourself with `!complaint add CU142 | withdrawal | what the client says`. Use the account or customer number only, never the name; leave it blank if you don't know it.
- Emails that read like complaints ("complaint", "ombud", "refund", "still not received", "legal action"…) are logged automatically as **new – from email**. Confirm with `!complaint 3 ack`, or `!complaint 3 notcomplaint <why>`; the reason is kept.

**Working a complaint:**
- `!complaint 3 ack <note>`, then `!complaint 3 investigate <note>`.
- `!complaint 3 resolve upheld | <root cause> | <what was done> | <redress>`. Use `partly` or `rejected` instead of `upheld` as needed. An outcome and a root cause are required.
- `!complaint 3 close`. Use `!complaint 3 escalate <note>` if the client goes to the ombud.

**Deadlines:** acknowledge within 2 days and give the final answer within 42 days (six weeks). Change them to match your complaints policy with `JARVIS_COMPLAINT_ACK_DAYS` and `JARVIS_COMPLAINT_RESOLVE_DAYS`.
- Overdue items go to the top of the morning list and appear as risk indicators.
- You get a reminder on the day an acknowledgement is due, and in the last week before an answer is due.

**Management information:** `!complaint mi 90` shows volumes by category, the upheld rate, average days to answer, top root causes and redress paid. The daily compliance report has a complaints section.

## STR evidence pack (suspicious transaction reports)

`!str 12` turns compliance case 12 into everything you need to decide on, and file, a suspicious transaction report. In Discord it comes as text plus a PDF for the file; in the Console, open `localhost:8765/console/str/12.pdf`. It contains:
- the case and why it may be suspicious, in plain words;
- the account's deposits, withdrawals, deals and volume from the reports on file, and its balances;
- KYC documents on file, plus other cases and complaints on the same account;
- the **reporting deadline**: 15 business days after the suspicion was formed, the FIC Act regulation 24(3) period. Change it with `JARVIS_STR_DEADLINE_DAYS` if your FIU's rule differs;
- the goAML checklist of what the report needs (identity details come from the CRM, never from DONNA);
- the tipping-off warning.

**Record the decision either way.** "Why we did not report" is evidence too.
- `!str 12 filed <FIC reference>` marks the case escalated.
- `!str 12 nofile <why it is not reportable>`.

Both are kept with the case history. The pack is built from DONNA's own records, so it works without Claude credit.

## IB control

Schedule your CRM's **IB / partner commission report** to your email, or upload it. DONNA recognises it by its columns: an IB or partner id, the client account, and the commission or rebate paid, plus lots when present. She joins it to the cTrader account-activity report.

`!ib` shows, per IB:
- clients and funded clients;
- deposits and withdrawals;
- lots and rebates paid;
- what the broker earned from those clients (an estimate: commission + swaps + client losses);
- rebates as a % of those earnings.

Each of these becomes a compliance case (category IB) automatically:

| Flag | What it means |
|---|---|
| Paid more than earned | Rebates exceed what the IB's clients brought in |
| Rate mismatch | Paid ≠ agreed rate × lots. Set the agreed rate with `!ib rate IB123 6.5` |
| Rebate farming | A client trades heavily (100+ deals) for almost no profit or loss while the IB earns the rebate |
| Self-referral | The IB trades as a client under its own code |
| In and out, no trading | Several of the IB's clients deposit and withdraw without trading |
| Identical deposits | Several clients made exactly the same deposit (possible multi-accounting) |

`!ib IB123` shows one IB with the accounts behind each flag. The thresholds are settings (`JARVIS_IB_FARM_MIN_DEALS`, `JARVIS_IB_RATE_TOLERANCE_PCT`, `JARVIS_IB_SAME_DEPOSIT_MIN`, `JARVIS_IB_IN_OUT_MIN`). The revenue figure is an estimate, so check it with the dealing desk before acting on one IB.

## Partner Radar (finding IBs)

Every Monday DONNA searches YouTube for trading educators, academies and communities in the countries you have cleared for marketing. She scores each one and posts a shortlist to your private channel. She **never contacts anyone**.

**Set up, once:**
1. **Clear your countries:** `!radar countries ZA,MU,KE`. List only countries where Vertex 1 may market; the radar searches nothing else. Codes: ZA, MU, KE, NG, GH, AE, SA, QA, KW, BH, OM, BW, ZM, NA, ZW.
2. **Get a free YouTube key:**
   - At **console.cloud.google.com**, create a project called "DONNA radar".
   - Under **APIs & Services → Library**, search **YouTube Data API v3** and click **Enable**.
   - Under **Credentials → Create credentials → API key**, copy the key. Restricting it to "YouTube Data API v3" is recommended.
3. On the server, run `vertex1-connect radar` and paste the key. It should say `RADAR OK`.

**Using it:**
- `!radar run` searches now; `!radar` shows the shortlist.
- Each prospect shows a fit score: audience, views per video, posting in the last 60 days, and trading-education focus.
- Channels that promise returns, offer to trade for clients, sell signals or pass prop challenges are kept **off** the shortlist. `!radar flagged` shows them.
- Work the list with `!radar 12 approve`, `!radar 12 reject <why>`, `!radar 12 contacted`, `!radar 12 partner`.
- `!radar draft 12` writes a first message with the risk warning. Check it with `!review`, then send it yourself.

## Regional campaigns

`!campaign kenya ib` builds a complete, ready-to-review campaign for a country you have cleared (`!radar countries`). Each campaign is built from templates, so it works without Claude credit, and nothing is ever posted or sent.

The three types:
- **ib** (partner recruitment): who to approach, the first message, the follow-up, a partner one-pager and a two-week plan.
- **clients:** three posts, landing-page copy, an email to new leads and a two-week plan.
- **retention:** a win-back email, a WhatsApp follow-up for opted-in clients, a post and a plan.

**What every piece includes:**
- The angle that works in that market: mobile money in Kenya, Ghana and Nigeria; swap-free accounts and Arabic support in the GCC; rand funding and fast EFT withdrawals in South Africa.
- The firm disclosure and the CFD risk warning. Set `DONNA_FIRM_DISCLOSURE` to include your licence number, e.g. "Vertex 1 Brokers, FSP 12345".
- A red-flag check for wording like guarantees, risk-free or signals.

**Tracking:**
- Each campaign gets a tracking tag such as `ke-ib-2026w44`, built into its links.
- `!campaign 3` counts the leads and funded clients whose CRM lead source contains the tag.
- `!campaigns` lists them all; `!campaign 3 live|done|stopped` sets the status.
- Run each piece through `!review` before you publish it yourself.

## Marketing attribution

`!attribution` shows where your funded clients come from, for leads created in the last 90 days (`!attribution 30` for 30 days). For each source it gives:
- leads, funded clients and conversion %;
- net deposits;
- estimated revenue: commission + swaps + client losses, for comparing channels, not for the accounts;
- cost per funded client and ROI.

Sources are grouped: your `!campaign` tags (ke-clients-2026w44), IBs and partners, and the CRM's own sources such as facebook and google. `!attribution country` shows the same figures by country. When an IB report is on file, an IB section shows each IB's funded clients, deposits, revenue, rebates and the net.

**For the value columns**, the Syntellicore leads export needs the client's **trading account** column alongside the customer number. Without it you still get leads, funded clients and conversion.

**For cost:**
- Record what you spend with `!spend facebook 1200 Oct ads` or `!spend ke-clients-2026w44 250`. The source name must match the CRM source, or the campaign tag.
- `!spend` on its own lists what has been recorded.

## Sanctions and PEP screening

**Lists.** Every night DONNA downloads the official lists:
- the **UN Security Council Consolidated List**, which the FIC's targeted financial sanctions follow;
- the **US OFAC SDN** list, with its aliases.

Add others, such as the UK or EU consolidated lists or a licensed PEP list, as a CSV (columns: name, dob, list, ref) with `JARVIS_SANCTIONS_EXTRA_CSV`. If a download fails she keeps yesterday's copy. If none have ever loaded, `!status` says that screening is not running.

**Clients.** Schedule the Syntellicore **customer list** to your email daily, with customer number, first and last name, date of birth and the PEP field. The KYC documents export is screened too. Each client is compared against the lists:
- **Strong match:** the name matches at 92% or more and the date of birth agrees, or the list gives none.
- **Possible match:** the name matches at 85% or more (`JARVIS_SCREEN_THRESHOLD`).
- A near-identical name with a different birth year is dropped. An identical name with a different birth year is still reported as possible.
- **PEP:** the CRM marks the client as a PEP, which calls for enhanced due diligence.

Each one opens a compliance case and turns the "Open sanctions / PEP matches" indicator red. Clear a false positive with the reason, for example `!close 12 false positive - different date of birth and nationality`; the decision stays on file.

**Names are never stored.** They are compared in memory and discarded. DONNA keeps only the customer number, the list entry and the score, so screening repeats with each daily customer export.

**Commands:**
- `!screening` shows the lists loaded, the last screening and the open matches.
- `!screen <full name> [YYYY-MM-DD]` checks one person before onboarding. The name you type stays in Discord's history, so use the Console for this if you prefer.

**PEP lists:** there is no free official PEP list. OpenSanctions has one, but its free licence is non-commercial, so a broker needs their paid licence or another vendor.

## Uptime alerts and backups

**Uptime alert (free).**
- Every 10 minutes DONNA's server checks in with your Google bridge.
- If it stays silent for 30 minutes, the bridge emails **you**, from and to your own address: "DONNA is offline". That covers the server being off, the network being down, or DONNA having stopped.
- When it checks in again you get "DONNA is back online".
- This needs bridge script **version 3**. After pasting it, choose **setup** and click **Run** once: that switches on the 15-minute watchdog and asks permission for Drive and to email you.

**Off-site backup (free).**
- The server already backs up every database nightly and keeps 14 days on the server.
- An **encrypted** copy of each backup now also goes to **Google Drive → DONNA backups**, keeping the newest 14.
- One-time set-up: run `vertex1-connect backup` on the server. It makes the encryption key and shows it once. **Save it in your password manager**: without it nobody can open the Drive copies, including Claude.
- To open a copy on any computer: `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in donna-backup-<day>.tgz.enc -out backup.tgz`. It asks for the key.

**Whole-server snapshots (DigitalOcean, about 20% of the droplet price per month).**
- These protect everything, including the operating system and settings.
- In the DigitalOcean dashboard: **Droplets → your droplet → Backups → Enable backups**.
- They are weekly and kept for four weeks, and you restore with one click.

## Connecting your live cTrader account (read-only)

DONNA reads your balance, open positions, stops and current prices every 5 minutes through cTrader's Open API. **She can't trade, close or change anything.** The feed only sends read requests, and it refuses to start if the token it was given has trading permission.

**On the cTrader side (about 10 minutes, then a wait for approval):**
1. Go to **openapi.ctrader.com** and sign in with the cTrader ID you trade with.
2. Open **Applications**, choose **Add new app**, name it `DONNA`, and describe it as "Personal read-only risk view". Submit it.
3. Wait for the status to change to **Active**. Spotware reviews new apps, which can take a day or two.
4. Open the app's **Credentials** and keep the **Client ID** and **Secret** ready (don't copy them anywhere else).
5. Open the app's **Playground** and generate a token:
   - Choose the **Account info** scope (read-only), **not Trading**.
   - Sign in, and tick only your personal account when cTrader asks which accounts to grant.
   - You'll get an **Access token** and a **Refresh token**.

I couldn't open the portal from here, so the exact button names may differ slightly.

**On your server:**
6. Run `sudo vertex1-connect ctrader` (only the cTrader questions; plain `vertex1-connect` asks everything). Questions 9–12 ask for the four values, and your typing stays hidden. Question 8 is your account number, if it isn't saved yet. The script then checks the connection and prints `CTRADER OK - read-only, N open position(s)`.
7. In Discord, `!risk` shows the live view, and the morning and evening messages include it.

**Never paste these four values into chat, Discord, email or a document.** They only go into the server's env file through `vertex1-connect`. If one ever leaks, delete the token in the Open API portal (or the whole app) and issue a new one.

**What happens over time:**
- Tokens last about a month. DONNA refreshes them herself and keeps the new pair in her database on the server.
- If the connection fails, you get one Discord message, not one every 5 minutes. The risk view then marks the last snapshot as old.

**Compliance:** this is your personal account at a brokerage you work for, so your personal-account-dealing policy applies to the data flow as well as the trading. DONNA keeps the data on your server. Position details are only sent to Claude when you ask her about them in chat.

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

## Hands-off updates

From this release the server updates itself. Every 30 minutes `vertex1-update.timer` checks GitHub for a newer commit on `main`, and installs it only if:
- the automatic tests (`.github/workflows/tests.yml`) passed on GitHub for that exact commit, and
- the code contains nothing that looks like a password or key.

After installing it restarts whichever services were running, waits, and checks they are all up (and that `/health` answers). If anything is down it goes back to the previous version by itself. Either way DONNA posts one line in your private channel: "Updated to abc1234: ..." or what went wrong. Your settings and databases are never touched.

- What's installed and what's waiting: `sudo vertex1-update --status`
- Update now instead of waiting: `sudo vertex1-update`
- Pause updates: `sudo systemctl disable --now vertex1-update.timer` (resume with `enable --now`)
- If the GitHub repository is made private, add a read-only token as `VERTEX1_UPDATE_TOKEN` in the env file (a fine-grained token for this one repository with Contents: read).

**One-time setup on a server that predates this:**
```
bash <(curl -fsSL https://raw.githubusercontent.com/smashwemhazi-cpu/Quant-Dashboard-Live/main/deploy/vertex1-update)
```

What changed and why is logged in [CHANGELOG-DONNA.md](CHANGELOG-DONNA.md).

## Guardrails

- DONNA never trades, moves money or messages clients. In chat she can add tasks, ideas and memories, and nothing else.
- What you type, and what she remembers about you, is sent to the Claude API to answer you. Keep client names and account numbers out of chat; use the desk tools for client work.
- Everything she keeps is in the JARVIS database on your server. `!memory` shows it, `!forget` removes it, and `!reset` clears a conversation.
