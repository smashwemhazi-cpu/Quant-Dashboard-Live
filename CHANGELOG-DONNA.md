# DONNA change log

Every change Claude makes to DONNA, newest first: what was done, why, what you'll notice, and recommendations.
SH gave Claude autonomy to make and ship changes without asking on 2026-10-23. Standing limits that autonomy
does not cover: DONNA never trades or moves money, never messages clients, Claude never handles your passwords
or keys, and nothing paid is signed up for without your OK.

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
