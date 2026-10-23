# DONNA change log

Every change Claude makes to DONNA, newest first: what was done, why, what you'll notice, and recommendations.
SH gave Claude autonomy to make and ship changes without asking on 2026-10-23. Standing limits that autonomy
does not cover: DONNA never trades or moves money, never messages clients, Claude never handles your passwords
or keys, and nothing paid is signed up for without your OK.

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
