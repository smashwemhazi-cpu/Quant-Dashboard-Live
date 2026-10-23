# D.O.N.N.A. v3 → v4 review

## How v3 works
FastAPI receives four kinds of input:

| Input | Endpoint | What happens to it |
|---|---|---|
| Trade tickets | `/api/ingest/trade` | Scored by fixed rules and written to SQLite |
| TradingView alerts | `/api/tradingview/alert` | Staged as an action that waits for approval |
| Inbound messages | `/api/comms/inbound` | Staged as an action that waits for approval |
| Strategy prompts | `/api/strategy/vibetrading` | A CrewAI chain (researcher → coder → auditor) writes a `.cs` file, which is staged for "deploy" |

A Telegram message asks an admin to approve each staged action, and `/approve` or `/reject` changes its status.

## Problems found (all were reproduced or verified before being fixed)

| Severity | Problem | Consequence |
|---|---|---|
| Critical | No authentication on any endpoint | Anyone who can reach the server can call `/api/actions/approve/{id}`, so the human approval step can be skipped entirely. They can also inject fake TradingView alerts and fake inbound messages. |
| Critical | The API key is hard-coded in the source | It leaks through git, logs and screenshots |
| High | `approve` marks an action `EXECUTED` but runs nothing | The desk believes replies were sent and strategies were deployed when they weren't |
| High | Book routing is wrong. Tested: 1 lot on a $10k balance from a manual trader → **B-BOOK**; any cBot or FIX trade → **C-BOOK**; `D-BOOK` can never be reached because the score starts at 30 | Every ordinary client is internalised on rules unrelated to how they actually trade. Toxicity is a property of an account's track record (markouts, holding time, P&L significance), not of one ticket's size or the API it came through |
| High | The "risk %" is `lot_size / balance × 100` | Lots divided by dollars means nothing: 1 lot on $10k shows 0.01%. A real risk % needs the stop distance and pip value |
| High | `crew.kickoff()` runs synchronously inside an `async` endpoint | Strategy generation blocks the event loop for minutes, so trade ingest and every other endpoint freeze |
| High | LLM output is saved straight to `.cs` and staged to deploy to the "cTrader VPS" | The file includes prose and markdown fences, so it won't compile. Nothing is compiled or backtested, and having one LLM audit another LLM's code is not validation |
| Medium | Approval is a SELECT then an UPDATE; reject has no status check | Two approvers can both "execute" the same action, and an executed action can be overwritten as REJECTED |
| Medium | Actions never expire, and IDs are only 8 hex characters | A 10-minute-old signal can be broadcast hours later; IDs are guessable |
| Medium | Telegram `parse_mode=Markdown` is used and the response status is never checked | Any `_`, `*` or backtick in a preview causes a 400 error, so the approval request is silently lost |
| Medium | Sync SQLite inside `async` handlers, no WAL mode, no busy timeout, `INSERT OR REPLACE` | The event loop blocks, concurrent writes fail with "database is locked", and retries rewrite history |
| Low | Uses the Pydantic v1 `.dict()` | Deprecated in Pydantic v2 |
| Low | Paths are hard-coded under `/root` | The service runs as root |
| Low | `system_memory`, `tool` and `List` are never used | Dead code |

## v4 (`donna/app.py`)
- **Authentication:**
  - Services call with an `X-API-Key` header.
  - Approvers use named keys, and the approver's name is recorded on the action.
  - TradingView alerts carry a passphrase in the body, which is stripped before storage.
  - The Telegram webhook is checked against a secret token and a list of allowed user IDs.
- **Action lifecycle:**
  - Statuses are `PENDING → APPROVED → EXECUTED | FAILED`, plus `REJECTED` and `EXPIRED`.
  - The approve/reject transition is atomic, each action type has its own expiry time, IDs are 128-bit, and everything goes to an audit table.
  - Action types with no executor wired up fail honestly instead of reporting success.
- **Trade screening is advisory and history-based:**
  - Accounts with too little history default to A-book.
  - Toxic accounts go to A-book with a review.
  - An account becomes a B-book *candidate* only when it is a statistically consistent loser with low toxicity, and always with `requires_review`.
  - Risk % is calculated from lots × stop × pip value.
- **Strategy generation:**
  - Runs as a background job; the endpoint returns 202 with a `job_id` straight away.
  - The prompt is wrapped as data to limit prompt injection.
  - The code is extracted from the LLM output, then statically checked for elevated access rights, a missing stop loss, martingale sizing, network/file access, and look-ahead.
  - It is then compiled against `cTrader.Automate`.
  - Only code that passes both steps is staged, and only for a **DEMO** deployment.
- **Database:** SQLite in WAL mode with a busy timeout, connections managed by a context manager, and sync handlers run in FastAPI's threadpool.

**Before production:**
- Wire the executors (Slack, Gmail, Telegram, the demo VPS copy).
- Have the dealing-desk analytics (JARVIS `dealing.traders`) feed `account_profiles` on a schedule.
- Run the service behind TLS on a non-root user.
- Move to Postgres when more than one process writes.
- Get compliance sign-off before broadcasting signals to clients, since signal broadcasts can count as regulated advice or marketing.
