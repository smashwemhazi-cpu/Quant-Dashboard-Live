# DONNA change log

Every change Claude makes to DONNA, newest first: what was done, why, what you'll notice, and recommendations.
SH gave Claude autonomy to make and ship changes without asking on 2026-10-23. Standing limits that autonomy
does not cover: DONNA never trades or moves money, never messages clients, Claude never handles your passwords
or keys, and nothing paid is signed up for without your OK.

## 2026-11-07 · PAD guardrails on your own trades

**What was done:**
- DONNA now checks your own cTrader trades against the PAD Policy v0.2 you approved today. She flags: trades on the Restricted List, crypto, single-name share or Watch List trades with no clearance logged first, positions closed in profit before 48 hours (14 working days for shares), leverage above the client cap for the instrument, no stop-loss or more than 2% of equity at risk, more than 10 trades in a day, and long and short in the same symbol at once.
- New commands: `!pad` (status), `!pad clear BTCUSD buy 0.05 <why>` (log your own clearance before you trade), `!pad restrict` / `!pad watch` / `!pad unlist` / `!pad list` (the two lists), `!pad class` (fix how a symbol is classed), `!pad flags`, `!pad rules`, `!pad pack 2026-11` (PDF).
- New flags show in the morning message under NEEDS YOU.
- On the 1st of each month DONNA sends you a PAD pack PDF for the previous month: every trade, every clearance, every flag, the lists, and a sign-off box for the MLRO's post-trade review. You forward it to the MLRO; DONNA does not send it anywhere else.

**Why:** the PAD policy (Vertex 1's "staff dealing rules", T&Cs s.16.4) needs evidence that it is followed, and the MLRO's post-trade review needs a record to work from.

**What you need to do:** nothing to switch it on. Only trades opened from 7 November 2026 are checked. If DONNA mis-classes a symbol (anything it doesn't recognise is treated as a single-name share, the strictest class), fix it with `!pad class <symbol> <forex|metal|index|commodity|crypto|share>`.

**Limits:** DONNA only reads the cTrader account she is connected to, and never places, changes or closes a trade. She cannot see other accounts, other staff or client flow, so the MLRO's review still has to look for collusion in Fynxt and cTrader.

**Recommendations:** (1) Ask the Board to note the self-approval arrangement and the 1:200 staff leverage in the minutes (PAD Policy s.8A). (2) Start the Restricted List with any listed LP, payment or tech vendor you are negotiating with.

## 2026-11-07 · Email sorting uses the cheapest Claude model

**What was done:** sorting your inbox and drafting replies now uses Claude's smallest model (Haiku) instead of the largest one. Conversation and analysis stay on the larger model.

**Why:** SH asked whether the smart features can run without credit. They can't fully, so the routine work should cost as little as possible once credit is topped up. Email sorting is the biggest user of credit.

**What you need to do:** nothing. To change it back, set `JARVIS_TRIAGE_MODEL` in the settings file.

## 2026-11-07 · Quieter console window during updates

**What was done:**
- When the console installs an update, it no longer prints the first-time setup list ("Next: 1. Fill in ... 5. Test ..."). Those steps were for the original install and need nothing from you.
- It now says "DONNA restarts for about half a minute while it installs - that is normal."
- The console file on your PC now hides the "channel 3: open failed: Connection refused" lines. They came from the console checking whether DONNA was back while she restarted after the update, and they were harmless.

**Why:** SH's console window showed both after the #45 update, which looked like errors.

**What you need to do:** nothing. The first two changes reach the server by themselves. To also hide the "channel" lines, download the new DONNA-Console.bat and replace the old one on your desktop. This is optional.

## 2026-11-07 · Desktop console shows your agents

**What was done:**
- The DONNA Console (DONNA-Console.bat on your PC) has a new **Your agents** panel under Inbox. It shows the seven duties from her mandate, each with a light: green (running), amber (waiting on setup), red (needs a fix) or grey (on demand). The first problem is named under the duty.
- Click a duty to get the `!hq` view in the conversation. Click **details** for `!agents`, which lists every agent and when it last ran.
- Ask her out loud or type "what can you do?" and she answers with her mandate. "How are the agents?" gives the `!hq` view. Both work without Claude credit.

**Why:** SH asked how the mandate and the agents show up on the desktop.

**What you need to do:** nothing. Open the console as usual once the update lands.

## 2026-11-07 · DONNA's mandate, and one place to coordinate every agent

**What was done:**
- **DONNA's mandate is written down**, in DONNA.md (the manual) and in `jarvis/agents/hq.py`: her purpose, seven duties (run your day, keep the firm compliant, watch the desk and markets, protect your own trading, grow the business, think with you, keep herself honest and running), the agents behind each duty, the commands, how you can tell each one is being done, and what she never does.
- **`!mandate`** gives that answer in Discord. Her chat persona carries the same mandate, so "what can you do for me?" gets the full answer in plain chat too.
- **`!hq`** gives one view across all agents: what needs you first, then each duty in one line ("running", "partly waiting on setup", "needs a fix").
- **`!agents`** lists every agent job under its duty, showing OK, waiting for setup, failing (since when and why) or not run yet, plus when it last ran.
- **`!run <job>`** runs any agent now, for example `!run daily_wrap`. A failure is recorded the same way the scheduler records it.
- A test fails if a new scheduled job has no duty, or if the manual stops matching the mandate.

**Why:** SH asked what DONNA's mandate should be, so the question in the manual is answered fully, and for DONNA to coordinate the agents from one place.

**What you need to do:** nothing. Try `!mandate`, `!hq` and `!agents` in #donna once the update lands.

## 2026-11-07 · Admin desk: quieter alerts, inbox without Claude credit, !workflow, !diary

**What was done:**
- **The repeating "job failed" messages.** Every 5 minutes DONNA checks cTrader. Before, the first failed check sent an alert, and each new wording of the error ("unreachable", "CANT_ROUTE_REQUEST", "rate limited") counted as a new alert. Now short blips (network, timeouts, a busy or closed broker server, rate limits) say nothing unless the job has failed for an hour (`JARVIS_FAIL_GRACE_MINUTES`) with no success in between. After that you get one alert a day at most. cTrader, execution and LP checks stay silent while the FX market is closed (Friday 22:00 to Sunday 21:00 UTC), when broker servers are often down for maintenance. Real faults, like a missing setting, a refused login or a broken report, still alert on the first failure.
- **Email keeps working without Claude credit.** If the credit runs out or the key is missing, new emails are now sorted by plain rules (regulator, KYC/compliance, payments, LP, client, newsletter, sales). Urgent mail still reaches you, and "Reply: <subject>" tasks are still made. Rule-sorted emails show "[rules]" in their summary. Before, triage quietly did nothing.
- **Inbox problems speak up.** If the mailbox refuses DONNA, or every new email fails, the triage job now raises it (once a day, or "waiting for setup") instead of staying silent.
- **`!workflow [days]`** shows where your week went: email by type and sender, replies owed, tasks in vs done, overdue, how long tasks take, and meeting hours. It ends with "Streamline" suggestions taken from those numbers: filters for bulk mail, report senders to auto-load, repeating tasks to make recurring, and focus time. It doesn't need Claude.
- **`!diary [days]`** shows the week ahead: meetings, tasks due, regulatory deadlines and free slots in work hours (`JARVIS_WORK_HOURS`, default 08:00-17:00). **`!diary add <title> | YYYY-MM-DD HH:MM | 45m`** puts an event in your own calendar, and **`!diary block 12 90m`** books focus time for task 12 in the next free slot. Events have no guests, so no invitations go out, and DONNA never changes or deletes existing events.
- **Mail you already opened still counts.** Before, DONNA only looked at *unread* inbox mail, so anything you read on your phone first was never seen. With bridge version 4 she sees all new inbox mail. Mail you have opened or already answered is counted for `!workflow` but doesn't make tasks or urgent pings, and mail you replied to isn't counted as a reply owed.
- Google bridge script is now **version 4** (calendar-write action, read mail, "already answered" flag).

**Why:** SH asked how the admin side is handled (email access, workflow analysis, diary upkeep, streamlining) and why the "screenshot" alerts keep coming back. The 07 Nov screenshot showed two weekend cTrader alerts (10:04 and 10:25 local) that resolved by themselves.

**What you need to do:** for `!diary add`, `!diary block` and read mail, paste the new `google-bridge.gs` (version 4), deploy a new version, then Run **setup** once and click Allow. Steps are in DONNA.md under "Updating the bridge script". Everything else works on its own.

**Recommendation:** top up the Claude credit. Rule sorting keeps the inbox flowing, but summaries, task wording and `!draft` replies are much better with Claude.

## 2026-11-06 · cTrader "rate limited" fix

**What was done:**
- The first read of 90 days of closed trades sent 13 requests in a row. cTrader only allows a few history requests per second, so it answered "You are being rate limited" and the whole cTrader check failed.
- DONNA now sends at most 4 history requests per check (`DONNA_CTRADER_HISTORY_REQUESTS`), one second apart (`DONNA_CTRADER_HISTORY_GAP`). The 90-day backfill finishes over about four checks (20 minutes), and each check picks up where the last one stopped.
- If cTrader still says "rate limited", DONNA keeps the open positions and equity it already read, saves how far the history got, and carries on next time. No failure alert is sent for a rate limit.

**Why:** SH got a "ctrader_positions job failed: BLOCKED_PAYLOAD_TYPE" alert right after the journal update went live.

**Recommendation:** nothing to do. `!journal` fills within about 30 minutes of this update.

## 2026-11-06 · Trading journal fills itself from cTrader

**What was done:**
- The read-only cTrader connection now also reads your closed trades, not just open positions. The first run reaches back 90 days (`DONNA_CTRADER_HISTORY_DAYS`). After that, every 5-minute check picks up anything closed since the last run.
- Each closed trade goes into the same journal that statements fill: time, symbol, buy/sell, entry, exit, size, net result after swap and commission, and balance. `!journal`, the big-loss warnings, the session and symbol breakdowns and the evening personal report all use it straight away.
- It still only sends read requests (the deal list is a read). The trading-permission refusal is unchanged.

**Why:** SH asked whether statements still need uploading now that cTrader is connected. They no longer do for this account. Statements and cBot logs can still be sent in #donna, for example for another account, and a trade already read from the feed is not counted twice.

**Recommendation:** none. Type `!journal` about 30 minutes after this goes live.

## 2026-11-06 · Daily wrap and morning markets in plain words

**What was done:**
- The daily wrap's markets block is one short row per market (Price, Today, 5 days) that fits a phone without wrapping. The morning dispatch uses the same table.
- "range 0.9x ATR" is gone. Markets that swung far more than on a normal day are named in their own line, "Unusually big swings today" (the test is still today's high-low over 1.5x the 14-day average range).
- Headings are now plain: Markets at the close, Biggest moves, News (last 24 hours), Prediction track record ("58% right out of 12 calls, a coin flip gets 50%").
- The Discord title reads "Vertex 1 daily wrap" instead of "daily_wrap".

**Why:** SH's screenshot of the Fri 06 Nov wrap still showed the old single code block. That wrap was sent at 17:01 UTC, three hours before the new layout went live (20:46 UTC), so the next wrap is the first one in the new layout. This change finishes the job by replacing the remaining jargon.

**Recommendation:** none. If you prefer the old look, set `DONNA_DISCORD_STYLE=mono` on the server.
## 2026-11-06 · Send to a lead after you confirm it

**What was done:** SH chose "draft, then ask me before sending".
- `!leads send 12` shows exactly what DONNA would send, and to whom. Nothing is sent yet.
- `!leads confirm 12`, within 10 minutes, sends it from the lead-finder Telegram account within about 5 minutes and marks the lead contacted.
- Safeguards:
  - Approved leads only.
  - One first message per person, ever.
  - At most 10 a day (`DONNA_LEAD_DAILY_CAP`), spaced 1.5 to 4 minutes apart.
  - The do-not-contact list is checked before every send.
  - Trader messages are a consent request only.
- Replies come back to your private channel. A reply marks the lead "replied". STOP marks it lost and puts it on do-not-contact for good.
- Public channels and groups can't take direct messages. For those, `!leads draft 12` gives you the text to send yourself.

**Recommendation:** answer replies yourself in Telegram, and keep `!leads stats` in the weekly review.

## 2026-11-06 · Lead Finder shows what the Telegram search is doing

**What was done:** `!leads` now has a Telegram line. It says whether a search is queued, running (results in about 10 minutes) or finished, and how many channels and groups it found. If the lead monitor doesn't pick a search up, the line gives the one command that fixes it. Countries with prospects hidden for red flags now say how many, so an empty country no longer looks broken. See them with `!leads flagged`.

**Why:** SH's `!leads` showed "Nothing found yet" everywhere with no way to tell whether the search had run.

## 2026-11-06 · Lead pipeline, conversion and quality; Telegram search on demand

**What was done:**
- Each lead now moves through steps you log from Discord: `!leads 12 approve`, then `contacted`, `replied`, `meeting`, and `partner` (IB signed) or `funded` (trader deposited). `reject <why>` and `lost <why>` close it. Every step is time-stamped, and DONNA tells you the next step after each one.
- `!leads stats` (or `!leads stats 30`) shows the funnel by source (Telegram, YouTube, web, LinkedIn, your own adds): found, approved, contacted, replied, meeting, won, reply rate, win rate and average score. **Lead quality** shows how many of the prospects you reviewed were worth approving, and the average score of the ones that converted.
- `!leads run` now starts a Telegram search straight away, with results in about 10 minutes. Before this, the first search only ran once a day. The lead monitor checks with DONNA every 5 minutes, so a search also starts within 5 minutes of clearing countries.
- `!leads sources` shows when Telegram last searched.

**Why:** SH asked how to see the leads and how to track conversion and lead quality. Telegram was connected before the countries were set, so the daily search found nothing to do.

## 2026-11-06 · Countries by name; !radar runs the Lead Finder

**What was done:**
- `!radar countries` now takes names as well as codes, for example `South Africa, Zimbabwe, Nigeria, Ghana, UAE, Kenya`. Cities like Dubai also work. Anything it doesn't recognise is listed back, and nothing is wiped by a typo.
- `!radar run` and `!radar` now run and show the Lead Finder, the same as `!leads run` and `!leads`. They no longer stop at "YouTube key missing".
- With no search source switched on, the reply lists the one command for each source, Telegram (free) first.

**Why:** SH's `!radar countries South Africa, ...` was saved as "none", and `!radar run` asked for a YouTube key.

## 2026-11-06 · Lead Finder: Telegram, web and LinkedIn, one list

**What was done:**
- `!leads` is now the **Lead Finder**: one list of possible IBs and partners per cleared country, plus traders who ask for a broker in Telegram groups you're in. Each entry shows a 0-100 score, the source (YT, TG, WEB, LI), followers, last activity and the link. The old CRM backlog moved to `!crm` (`!leads crm` also works).
- **Telegram:** once a day the lead monitor uses Telegram's own search to find public trading channels and groups in your cleared countries. It reads their public name, description and member count. It doesn't join anything or message anyone.
- **Web:** a weekly open-web search (Brave Search API) finds academies, trading communities, IB websites, public t.me channels and LinkedIn **company** pages. It drops personal LinkedIn profiles, news, reference sites and regulators.
- **LinkedIn:** LinkedIn's terms forbid automated collection, so DONNA never visits it. `!leads linkedin` gives ready-made LinkedIn search links per country. You browse yourself and log anyone useful with `!leads add <link> KE <note>`.
- **YouTube:** the Partner Radar channels feed into the same list.
- **Draft-only outreach:** nothing is sent automatically any more. `!leads draft 12` writes the first message for you to check with `!review` and send yourself. For an IB it's a partnership note with the risk warning. For a trader it's a consent request only: no pitch, and STOP is honoured. The old approve-then-auto-send route is off unless `DONNA_LEAD_AUTOSEND=1` is set.
- Red-flag marketing (promised returns, trading for clients, paid signals, prop-challenge passing) is flagged and kept off the shortlist (`!leads flagged`).
- Data protection: business information only, snippets capped at 280 characters, only countries you cleared, and the do-not-contact list is respected. Never-actioned prospects are deleted after 180 days and rejected ones after 90.

**Why:** SH said the lead generator wasn't working the right way. `!leads` only showed the CRM export, which said NO DATA, and the radar searched YouTube only.

**Needs from you:**
- Clear your marketing countries if you haven't: `!radar countries ZA,MU,KE`.
- Telegram (free): get api_id and api_hash at my.telegram.org, then run `vertex1-connect telegram` and type the login code Telegram sends you.
- Web search: `vertex1-connect leads` with a Brave Search API key. Brave asks for a card but gives $5 of free credit a month, which covers this use (about 25 searches a week per country). Optional; Claude did not sign up for it.
- YouTube (free): `vertex1-connect radar`, if not done yet.

**Recommendation:** use a dedicated work Telegram number for the lead monitor, not your personal one. Record the lead finder in your marketing / data-protection register: B2B partner prospecting under legitimate interest, with the retention above.
## 2026-11-06 · cTrader Client ID paste no longer rejected

**What was done:** `vertex1-connect` now cleans pasted IDs and tokens before checking them. It removes:
- the invisible "bracketed paste" codes some terminals add on a right-click paste
- stray spaces
- a "Client ID:" label copied along with the value

If a value is still refused, the message says how many characters it got, how the Client ID starts, and which characters were unexpected.

**Why:** SH's Client ID was refused as "not an Open API Client ID", so the old ID stayed saved and cTrader kept saying the ID and Secret don't match.

## 2026-11-06 · Cleaner reports in Discord; send DONNA files, including PDFs

**What was done**
- **Reports are laid out as sections.** Every report DONNA posts (daily wrap, morning, evening, compliance and the rest) now shows each part under its own heading:
  - Tables stay lined up.
  - News and notes wrap as normal text with bullets.
  - Green, amber and red statuses show as coloured dots.
  - "!!" items get a ⚠️.
  - All the "NO DATA" lines fold into one **Not connected yet** line at the bottom, instead of filling the report.
  - To go back to the old single block, set `DONNA_DISCORD_STYLE=mono`.
- **The daily-wrap price table is compact.** It's one short row per market (close, day, 5 days, range), so it no longer wraps onto two lines. ⚠ marks a day whose range is over 1.5x normal.
- **Send DONNA files in Discord.** Drag a file into #donna, or any channel she answers in. She reads it straight away and tells you what it was:
  - cTrader trade statements (Excel **or PDF**)
  - back-office exports (Excel/CSV)
  - cBot logs
- **PDF reading.** DONNA finds the trade table in a PDF, even across pages. A PDF without a real table (a scan or a photo) gets a plain reply asking for the Excel export.
- **New `!journal` command.** Your trading analysis from the statements: net, win rate, average win vs loss, profit factor, drawdown, big losses, best and worst session and symbol, re-entries after a loss, and overtrading days.

**Why:** SH asked for reports that read better, and for a way to upload PDFs for trade analysis. The analysis existed, but files could only arrive through the server inbox or email.

**Why the wrap still shows "Not connected yet" for CRM, book and trading activity:** no Syntellicore KPI export or cTrader end-of-day/active-accounts report has reached DONNA yet. Drop those files into #donna, or schedule them to email daily, and those sections fill in by themselves.

## 2026-10-29 · Tick tasks off in the Console

**What was done:** each task in the Console now has a ✓ button. One click marks it done, the same as typing `!done 12`, and the list refreshes.

**Why:** SH couldn't see how to complete a task from the Console.

## 2026-10-28 · Uptime alerts and off-site backups

**What was done:**
- The server checks in with the Google bridge every 10 minutes. If it goes quiet for 30 minutes, you get an email from your own Google account, and another when it is back. This works even when the server is completely down.
- The nightly database backup is now also sent, encrypted, to Google Drive / DONNA backups, keeping the newest 14. The key is made on the server with `vertex1-connect backup` and kept by you.
- Bridge script **version 3** adds both. Paste it once, then run **setup**.

**Needs from you:**
- Paste bridge version 3 and run setup.
- Run `vertex1-connect backup` and save the key.
- Optionally, turn on DigitalOcean weekly backups (about 20% of the droplet price).

## 2026-10-28 · Sanctions and PEP screening

**What was done**
- The UN Consolidated List and the OFAC SDN list (with aliases) are downloaded nightly. Any extra list can be added as a CSV.
- Every client export with names (the customer list and the KYC documents) is screened with fuzzy name matching and a date-of-birth check. Strong and possible matches, and CRM-declared PEPs, open compliance cases.
- A new red risk indicator shows while matches are open.
- `!screen` checks one name before onboarding.
- Client names are never stored.
- A failed list download keeps the previous copy. If no list is loaded, the failure is flagged rather than silently screening against nothing.

**Needs from you:**
- Schedule the CRM customer list (number, names, date of birth, PEP) to your email daily.
- Decide on a PEP data source (a paid OpenSanctions licence or a vendor).

## 2026-10-28 · Marketing attribution

**What was done:**
- `!attribution` follows each lead from its source to a funded account and its deposits and revenue, by channel or by country.
- Campaign tags and IB/partner sources are grouped automatically.
- `!spend` records marketing cost, so you see cost per funded client and ROI per channel.
- There is an IB section showing revenue against rebates.
- The leads export's trading-account column, when present, is now kept so a lead can be linked to its account.

**Needs from you:**
- Add the trading-account column to the scheduled Syntellicore leads export.
- Record spend with `!spend`.

## 2026-10-28 · Regional campaign engine

**What was done:**
- `!campaign <country> ib|clients|retention` builds a full campaign for a cleared country: messages, posts, landing copy, email and a two-week plan.
- The wording is fitted to each market. Every piece carries the risk warning and firm disclosure and passes a red-flag check.
- Each campaign has a tracking tag, and `!campaign 3` counts its leads and funded clients from the CRM leads export.
- Countries that aren't cleared are refused.

**Needs from you:**
- Set `DONNA_FIRM_DISCLOSURE` to include your licence number.
- Make sure the CRM keeps the `utm_campaign` tag in the lead source, so campaigns can be measured.

## 2026-10-28 · Partner Radar

**What was done:**
- A weekly shortlist of potential IBs (trading educators, academies and communities) from YouTube, scored on audience, views, recent activity and education focus.
- Channels with red flags (promised returns, account management, signals, prop-challenge passing) are left off the shortlist and listed separately.
- The radar searches only the countries you clear with `!radar countries`.
- You approve, reject (with a reason), or mark contacted or partner. `!radar draft 12` writes a first message that includes the risk warning. DONNA never contacts anyone.

**Needs from you:** clear your countries, and add a free YouTube Data API key (`vertex1-connect radar`).

## 2026-10-28 · IB control

**What was done:**
- The CRM's IB / partner commission report is now recognised and joined to account activity.
- `!ib` shows each IB's clients, deposits, lots, rebates paid and the broker's estimated earnings from them.
- Six abuse checks become compliance cases automatically: paid more than earned, rate mismatch, rebate farming, self-referral, in-and-out with no trading, and identical deposits.
- A new risk indicator counts IBs paid more than their clients earned.
- The daily compliance report has an IB section.

**Needs from you:**
- Schedule the IB commission report to your email.
- Set each IB's agreed rate with `!ib rate IB123 6.5` so payments can be checked against it.
- Send me the report's column names, not its contents, if DONNA doesn't recognise it.

## 2026-10-28 · STR evidence pack and a better Console

**What was done**
- **STR evidence pack:** `!str 12` assembles the case, grounds for suspicion, account activity, balances, KYC, related cases and complaints, the 15-business-day deadline, the goAML checklist and the tipping-off warning, as text and PDF. The filed or not-filed decision is recorded with the FIU reference or the reason.
- **Console improvements, from your screenshots:**
  - It fits one screen: the bottom bar is always visible, and the panels and conversation scroll inside themselves. The window opens maximised.
  - A "Needs you" strip at the top lists what is switched off and why (e.g. Claude credit, the bridge script update).
  - Overdue tasks are marked in red.
  - The inbox says "sorting paused" when Claude credit is out, instead of "nothing urgent".
  - The risk panel shows only real alerts, with one line for indicators still waiting for data.
  - **Everyday questions work without Claude credit:** "what are my tasks", "how's my day", "any complaints", "how is risk", "status".
  - **Better listening:** pick the English she listens for (UK, South Africa, Kenya, Nigeria, US). If she isn't sure she heard right, she puts the words in the box for you to check instead of acting on them.
- The "bridge script needs updating" message is now treated as "waiting for you" instead of a failure, so it no longer sends repeated job-failed alerts.

## 2026-10-28 · Complaints register

**What was done:**
- A full complaints register: number, client (account number only), category and summary for each complaint.
- An acknowledgement deadline (2 days) and a final-answer deadline (42 days), both adjustable.
- Each complaint goes through acknowledge, investigate, resolve and close. Resolving requires an outcome (upheld, partly upheld or not upheld) and a root cause, and can record any redress paid.
- The complete history of each complaint is kept for the ombud or regulator file.
- Emails that read like complaints are logged automatically for you to confirm or dismiss with a reason.
- The register feeds the morning "needs you" list, two new risk indicators, reminders, the daily compliance report and `!complaint mi`.
- It works in Discord and in the DONNA Console.

**Why:** a complaints register is a FAIS-type requirement, and it was the most important missing control in the review.

## 2026-10-28 · Reports by email, the DONNA Console, and draft replies

**What was done**
- **Reports read straight from your email.** Every 30 minutes DONNA picks up cTrader and Syntellicore report attachments, loads the ones she recognises, and runs the compliance checks straight away. Anything else is deleted at once. The back-office numbers stop going STALE once the reports are scheduled to your inbox.
- **DONNA Console.** Double-click `DONNA-Console.bat` to open her animated orb, which breathes while idle, ripples while listening, swirls while thinking and pulses while speaking.
  - You can talk to her (hold the button or the space bar), and she answers out loud.
  - Live panels show today's calendar, urgent emails, open tasks and the risk indicators.
  - It is reachable only through your private SSH link to the server, never from the internet.
- **Draft replies.** `!draft 12 <note>` writes a reply to the email behind task 12 and saves it in your Gmail Drafts. DONNA never sends anything.
- **Bridge script version 2** adds both of these. Paste it once more (DONNA.md, "Updating the bridge script"). The setup check warns you while the old copy is still in place.

**Needs from you:** paste the new bridge script, schedule the reports to your email, and add Claude API credit (for talking, triage and drafts).

## 2026-10-28 · Email and calendar with no password: the Google bridge

**What was done**
- The setup proved two things: the mail is at Google (Workspace), and the right address is used. Google still refused a correctly saved app password. On company Google accounts this happens when the admin settings don't allow app passwords.
- So DONNA no longer depends on a password. A small read-only script runs inside your own Google account (`deploy/google-bridge.gs`) and hands DONNA new unread emails and calendar events.
  - DONNA holds only the link and a random key.
  - When the bridge is set up, the inbox triage, the calendar in "My day" and `!status` all use it.
  - The setup asks for it at questions 6a and 6b.
- When Google refuses an app password, the setup now points to the bridge.

**What you'll notice:** after the 5-minute set-up in DONNA.md, the connection check shows "EMAIL OK via Google bridge" and "My day" shows your calendar.

## 2026-10-28 · Email: find out who really holds the inbox; repair after old update files

**What was done**
- **The setup now checks where your work mail really lives.** A Google login can exist for simba@vertex1brokers.com even when the company's mail is hosted somewhere else, such as Microsoft 365, the web host, Zoho or GoDaddy. In that case Google's mail server refuses every password, however correct, and that matches what kept happening. `vertex1-connect` now:
  - looks up the company's mail records and prints "Mail for vertex1brokers.com is handled by …";
  - points DONNA at the right inbox server by itself;
  - stops insisting on a 16-letter Google password when the mailbox isn't Google.
- **The check shows which address it signs in as.** It prints "signing in as <address> at <server>". If question 5 was skipped and an old or wrong address was saved, you can see it straight away.
- **The server repairs itself after an old update file.** Running an old `vertex1-update8.sh` copies 23 October code over the current version, and the auto-updater could not tell. It now fingerprints the installed code, notices the change within 30 minutes, puts the current version back and tells you in Discord.

**Why:** the right Google app password was saved ("Saved: JARVIS_IMAP_PASSWORD") and was still refused, and the installer that was run was the 23 October package (8 questions, not 12).

**Recommendation:** delete every `vertex1-update*.sh` file on your PC. Updates arrive by themselves now.

## 2026-10-28 · Why Google kept refusing the email password, and stale risk numbers

**What was done**
- **The email password fix.** When Google's 16-letter app password is copied from the browser, the gaps between the four groups are often special invisible spaces (non-breaking or thin spaces), not normal ones. The setup check treated a correctly pasted password as "not an app password", skipped it, and kept the old, wrong one. DONNA then kept reporting "Google refused the app password". Now:
  - Every kind of space and invisible character is removed, and capitals are accepted, before the check.
  - A real rejection says why, without showing the password: how many characters it had, and whether it looked like a normal Google password.
  - If Google still refuses, the setup prints the three causes in order: the app password was made under a different Google account (for example a personal Gmail instead of the work address); an old DONNA entry was reused; or the normal password was changed, which cancels every app password.
- **Stale risk numbers are labelled.** Risk indicators computed from an export older than 3 days (`JARVIS_DATA_STALE_DAYS`) now show **STALE**, with the export date, instead of RED or GREEN. The morning list now says "Back-office exports are N days old – upload fresh ones". Before this, "Oldest pending KYC = 39 days" kept growing every day from the 22 October export, even if those documents had since been processed.

**Why:** SH reported Google still refusing the right password, and the morning message showed a red KRI built on a 6-day-old export.

## 2026-10-28 · What leaves the private channel, and cleaner messages

**What was done**
- **The team channel no longer carries client-level compliance detail.** The morning dispatch and daily wrap in #track-a-brokerage now show totals and counts only:
  - Not shown there anymore: account numbers, customer numbers, AML flags ("deposited and withdrew – AML review"), turnover flags, bonus-heavy accounts and the KYC re-request lists.
  - Instead, the team sees one line: "N client-level flags are with compliance".
  - The full detail stays in your private channel and in `!compliance`, `!cases` and `!leads`.
- **Empty sections collapse.** Calendar, live positions, trading journal or compliance with nothing connected yet no longer each print a NO DATA block with technical text like "POST /api/positions". The message ends with one line, "NOT SET UP YET: …", pointing to `!status`.
- **Market moves say "today so far" when the day is still open.** A move at 20:00 your time is not a close.
- **The evening message has a compliance block:** open cases, high, overdue, new today, and any red KRIs.

**Why:** SH asked what data leaves the system and how it's presented. Account-level AML suspicions in a team channel go against need-to-know and carry a tipping-off risk. Empty, technical sections made the messages look broken when they weren't.

**What you'll notice:** shorter messages with a clear "not set up yet" line, a compliance line every evening, and no client numbers in #track-a-brokerage.

## 2026-10-27 · Head-of-quant review: three accuracy fixes

**What was done**
- **Compliance cases are no longer duplicated.** While a case is open, the same rule doesn't open a second case for the same account or customer, however many days or weeks the finding repeats. Cumulative AML findings (deposit-withdraw with no trading, turnover, large deposits, rejected deals) are logged once per data month. Before this, an unresolved stop-out or KYC finding could open a fresh case every week, which inflated the case count and the MI pack.
- **The predictor only uses finished days.** It now ignores today's still-forming price bar, both when it predicts and when it scores its track record. Since the daily wrap moved to 21:00 your time, while US markets are still open, it had been scoring some predictions against part-day prices. The track record now measures what it claims to.
- **Position risk no longer mixes currencies.** If the cTrader feed can't find a conversion rate for a position (for example a yen or pound cross), that position's risk and P&L are shown in its own currency, marked "(quote currency)", and left out of the dollar totals and the open-risk %. Before this, 3,000 yen at the stop could be counted as $3,000.

**Why:** SH asked for a head-of-quant review of everything in place, with the fixes applied before finalising.

**What you'll notice:** fewer, cleaner compliance cases; predictor scores that update a day later than before (they wait for the day to close); a clear note under position risk if a pair can't be converted.

## 2026-10-24 · Weekly management pack (PDF)

**What was done**
- New `!pack` command. DONNA replies with a PDF covering the last 7 days, or `!pack 30` for 30 days. It contains: key risk indicators, compliance cases (opened, closed, time to close), open cases, the regulatory calendar for the next 30 days, business growth numbers, and the audit-trail check.
- The same pack arrives by itself every Monday morning (`DONNA_PACK_WEEKDAY`, `JARVIS_PACK_UTC_HOUR`) in your private Discord channel.
- Case subjects and client details are left out, so the pack is safe to forward to the directors.
- It doesn't use Claude, so it works without API credit.
- New free library: fpdf2. The updater installs it automatically.

**Why:** SH asked what reports DONNA can produce. A forwardable weekly pack was the top recommendation.

**Recommendation:** add your regulatory deadlines with `!oblig add` and drop the back-office exports in. Sections without data say so plainly.

## 2026-10-24 · Plain "out of credit" reply; health check in the morning digest

**What was done**
- When Claude credit runs out, DONNA no longer pastes the raw API error into chat. She says in one line that credit is used up, where to top it up, and that every `!` command still works.
- The morning digest ends with a **SYSTEM** line. It says "all connections OK", or lists exactly what's waiting on you (Claude credit, email password, cTrader). A gap can't go unnoticed for days.
- A job that fails because credit is used up now pauses like the other "waiting for setup" jobs.

**Why:** SH asked to remove the errors and blind spots. The only reply SH had seen in #donna was a raw credit error.

**Recommendation:** top up Claude credit. Most of DONNA's thinking (chat, `!ask`, drafts, reviews) depends on it.

## 2026-10-24 · Connections waiting for setup pause quietly

**What was done**
- When a connection refuses DONNA's login (email password refused, cTrader app not approved yet, cTrader token with trading permission, expired token), or there's nothing to read yet, the job now **pauses** instead of retrying every 5 to 15 minutes. One audit line, and at most one Discord message a day. It still tries every 6 hours (`JARVIS_WAIT_RETRY_HOURS`), and straight away after any `vertex1-connect` run.
- `!audit` and `!status` now list these under "Waiting for setup (paused, not broken)" instead of counting them as errors.

**Why:** SH's `!audit` showed 39 errors in under two hours, all from cTrader waiting for the new app's approval. That looked alarming and hid any real problem.

**What you'll notice:** the error count stops growing. Today's earlier entries stay, because the audit trail can't be edited. Tomorrow's count will be clean.

## 2026-10-24 · `!status`: see the running version and what's broken

**What was done**
- New Discord command `!status`. It shows which version is running, tests the Claude key with one tiny real request (so an empty credit balance shows up), and lists every job that failed in the last 24 hours with its last error.
- Typing `#help` (or `#` with any other command) now works like `!help`, instead of being sent to Claude as chat.

**Why:** SH couldn't tell whether updates had landed. The only reply in #donna was an old "credit balance too low" error, from a `#help` sent to Claude as chat.

**What you'll notice:** type `!status` in #donna any time. It works even with no Claude credit.

**Recommendation:** top up Claude credit (console.anthropic.com > Plans & Billing). `!status` will say OK once it's done.

## 2026-10-24 · New cTrader tokens always take effect

**What was done:** when you paste new cTrader tokens into `vertex1-connect`, DONNA now uses them, even if she had refreshed an older pair and saved it in her database. Before, an old refreshed pair could quietly win.

**Why:** found while helping SH reconnect cTrader. A fresh token pair has to replace whatever was there.

## 2026-10-24 · Fewer "job failed" messages; clearer cTrader hint

**What was done**
- A failing job now alerts you at most once a day for the same error, and that holds across restarts. Before, every `vertex1-connect` run restarted DONNA, and she repeated every failure.
- Jobs for things you haven't set up yet (no executions file, calendar not connected, and similar) no longer send alerts. They're still recorded in the audit log (`!audit`).
- When cTrader says "wrong clientSecret", `vertex1-connect` now shows the first characters of the saved Client ID and the lengths of both values, so you can compare them with the Credentials page without showing the Secret.

**Why:** SH was getting a flood of red messages for problems that were already known or simply not set up.

**Recommendations**
- Re-enter the cTrader Client ID **and** Secret together (`vertex1-connect ctrader`, questions 9 and 10).

## 2026-10-24 · Security, audit trail and nightly backups (roadmap step 6, start of step 7)

**What was done**
- Client names, emails, phone numbers, addresses, dates of birth and ID or bank numbers are masked before tool results go to Claude. Client ids and figures still pass, so answers stay useful.
- The audit log is now tamper-evident. Each entry is chained to the previous one with a SHA-256 fingerprint, so an edited or deleted entry shows up.
- New `!audit` command: today's activity, the last error, and "AUDIT TRAIL: INTACT" or where it broke.
- Nightly backups of all databases at 01:30 UTC, with 14 days kept on the server.

**Why:** as a compliance officer you need to show what the assistant did, prove the record wasn't changed, and keep client data away from outside services. Before this, a lost server meant lost data.

**What you'll notice:** nothing day to day. Try `!audit`.

**Recommendations**
- Backups currently live on the same server. For real safety, turn on DigitalOcean's droplet backups (paid, about 20% of the droplet price) or tell me where an off-site copy can go. I won't sign up for anything paid without your OK.
- Make the GitHub repository private (still recommended).
- Step 5 (trading guardrails and the personal-account-dealing check) needs your firm's PAD policy: the restricted list, the holding period and the pre-clearance rule. Paste or upload the policy when you have it.

## 2026-10-24 · Daytime heads-ups (roadmap step 4)

**What was done**
- Every 15 minutes DONNA checks for things that shouldn't wait for the evening, and tells you once in your private channel:
  - a big market move (range 1.5x a normal day);
  - a regulatory obligation due within 2 days, or a compliance case due today or tomorrow;
  - new leads still unassigned after 2 hours;
  - a crashed or silent cBot;
  - a live position without a stop, or the daily-loss limit hit.
- Each one is sent once, even across restarts. One broken data source can't silence the others.

**Why:** roadmap step 4. DONNA should speak up when it matters, not only at 8am and 8pm.

**What you'll notice:** occasional "heads-up" messages during the day. If they're too frequent, raise `DONNA_MOVE_ATR` (for example to 2) in the settings file.

**Recommendations**
- Load your regulatory calendar so the deadline warnings have something to watch: `!oblig add FSCA quarterly return | 2026-10-31 | quarterly`, one line per obligation.
- Position heads-ups start working once the cTrader Secret is fixed.

## 2026-10-24 · Growth co-pilot (roadmap step 3)

**What was done**
- A weekly growth review every Monday at 05:00 UTC (09:00 your time) in your private channel, and on demand with `!growth`. It covers leads, conversion, sources and IBs, and the daily KPI trend, from the Syntellicore exports.
- Each review proposes one experiment, with a target on a number DONNA measures herself. `!exp start <n>` starts it and records the baseline. When it ends, DONNA marks it won or lost from the newest export.
- New commands: `!growth`, `!exp`, `!exp start <n>`, `!exp stop <n>`.

**Why:** roadmap step 3. Growth ideas get judged on numbers instead of memory, one at a time.

**What you'll notice:** a "GROWTH REVIEW" message on Mondays. Until a leads export is uploaded it says NO DATA, and it costs nothing because Claude isn't called.

**Recommendations**
- Upload the Syntellicore leads/customers export at least weekly (daily is better). Every number here comes from it.
- Start at most one experiment at a time, so you know what caused a change.
- Client names and ids never go to Claude from this feature, only counts and source names.

## 2026-10-23 · Hands-off updates and shorter setup

**What was done**
- The server now updates itself: every 30 minutes it installs the newest version, but only after the automatic tests have passed on GitHub. If a service doesn't come back up it returns to the previous version by itself.
- DONNA posts one line in your private channel after each update, or explains why one wasn't installed.
- Automatic tests now run on GitHub for every change (they gate the updates above).
- `vertex1-connect` can ask about one thing only: `vertex1-connect ctrader`, `email`, `calendar` or `discord`.
- `vertex1-connect` removes lines in the settings file that aren't settings (like the stray Discord ID from earlier), which stopped the file loading cleanly.

**Why:** so you never have to copy files to the server again, and fixing one setting doesn't mean pressing Enter through twelve questions.

**What you'll notice:** "Updated to ..." messages in `#track-b-personal`. Nothing else changes.

**Needs you, once:** paste one command on the server (Claude will give it to you) to switch on hands-off updates.

**Recommendations**
- Make the GitHub repository private. For a brokerage, the code (strategy logic, compliance checks) shouldn't be public. Updates keep working with a read-only token; see DONNA.md, "Hands-off updates".
- cTrader: the check said "wrong clientSecret". Run `vertex1-connect ctrader`, press Enter at every question except 10, and paste the Secret from the Vertex Acc Monitor app's Credentials page.
- Email: the saved Google app password is rejected. Make a new one at myaccount.google.com/apppasswords while signed in as your work address, check IMAP is on, then run `vertex1-connect email`.
- The Anthropic key you pasted in chat on 2026-10-23 should be deleted at console.anthropic.com → API Keys, if you haven't already.
