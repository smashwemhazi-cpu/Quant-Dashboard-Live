# DONNA change log

Every change Claude makes to DONNA, newest first: what was done, why, what you'll notice, and recommendations.
SH gave Claude autonomy to make and ship changes without asking on 2026-10-23. Standing limits that autonomy
does not cover: DONNA never trades or moves money, never messages clients, Claude never handles your passwords
or keys, and nothing paid is signed up for without your OK.

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
