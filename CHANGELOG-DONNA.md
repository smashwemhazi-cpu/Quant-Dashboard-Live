# DONNA change log

Every change Claude makes to DONNA, newest first: what was done, why, what you'll notice, and recommendations.
SH gave Claude autonomy to make and ship changes without asking on 2026-10-23. Standing limits that autonomy
does not cover: DONNA never trades or moves money, never messages clients, Claude never handles your passwords
or keys, and nothing paid is signed up for without your OK.

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
