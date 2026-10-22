# DONNA + JARVIS: where we are and how to switch it on

## What exists now
| Piece | File | Status |
|---|---|---|
| Ops console: retention, pipeline, execution, markets, trade planner, chat | `jarvis/`, `pages/1_JARVIS_Console.py` | Built and tested on demo data |
| AI dealing desk: LP slippage root cause, toxicity, A/B book, desk report | `jarvis/agents/dealing.py` | Built and tested; **needs a bridge/fills adapter** for real data |
| Bot Lab + Silver Scalper v2 | `jarvis/agents/botlab.py`, `bots/ctrader/` | Built; the cBot compiles but **needs a backtest** |
| DONNA core: auth, human approvals, audit | `donna/app.py` | Built and tested |
| **Channels:** Telegram, Slack, Discord, Teams, email | `donna/channels.py` | **Wired.** Each one is live once its env var is set |
| **Lead monitor:** Telegram, consent-first | `donna/lead_monitor.py` | Built; needs your Telegram API ID and the groups to watch |
| **Morning dispatch + daily wrap** | `jarvis/agents/briefing.py` | Built and rendered on test data |
| **TradingView feed + predictor** | `donna/tradingview/*.pine`, `jarvis/adapters/market.py`, `jarvis/agents/predictor.py` | Built. The predictor is validated on synthetic data; **the Pine script has not been compiled yet** |

## 1. Environment (`.env` on the VPS; never commit it)
```bash
# DONNA core
DONNA_DB_PATH=/srv/donna/donna_central.db
DONNA_API_KEY=<long random>
DONNA_APPROVER_KEYS=simba:<long random>,ops:<long random>
DONNA_TV_PASSPHRASE=<long random>
# Telegram bot (approvals + internal reports). Create it with @BotFather
DONNA_TELEGRAM_BOT_TOKEN=...
DONNA_TELEGRAM_CHAT_ID=<your desk group id, e.g. -100...>
DONNA_TELEGRAM_WEBHOOK_SECRET=<long random>
DONNA_TELEGRAM_APPROVER_IDS=<your Telegram user id>
# Slack (bot token with chat:write, invited to the channel)
DONNA_SLACK_BOT_TOKEN=xoxb-...
# Discord / Teams (channel webhook URLs; Teams: Workflows "post to channel when a webhook request is received")
DONNA_DISCORD_WEBHOOK_URL=...
DONNA_TEAMS_WEBHOOK_URL=...
# Email (Gmail: 2FA + app password, or your mail server)
DONNA_SMTP_HOST=smtp.gmail.com
DONNA_SMTP_PORT=465
DONNA_SMTP_USER=desk@vertex1...
DONNA_SMTP_PASSWORD=<app password>
# Where things go
DONNA_SIGNAL_TARGETS=discord,telegram:-100<public channel>     # approved TradingView signals
JARVIS_REPORT_TARGETS=telegram:-100<desk group>,email:exec@vertex1...,slack:#desk
DONNA_DEMO_DEPLOY_DIR=/srv/ctrader-demo/cbots                   # approved cBots land here (DEMO only)
DONNA_REP_NAME="Simba from the Vertex 1 desk"
DONNA_FIRM_DISCLOSURE="Vertex 1 Brokers (FSP <number>)"
DONNA_URL=http://127.0.0.1:8080
```
Register the Telegram approval webhook once:
`https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<host>/api/telegram/webhook&secret_token=<DONNA_TELEGRAM_WEBHOOK_SECRET>`

## 2. Processes (systemd, run as a non-root user)
```bash
uvicorn donna.app:app --host 127.0.0.1 --port 8080   # behind nginx/Caddy with TLS
python -m jarvis.scheduler                           # 06:00 UTC dispatch, 22:00 UTC wrap, alerts
python -m donna.lead_monitor                         # Telegram user session (asks for the login code once)
streamlit run dashboard.py                           # console
```

## 3. Daily reports
- **Morning dispatch, 06:00 UTC weekdays (forward-looking):**
  - System health, plus a check of whether every market feed is current
  - Each instrument vs its previous close, with the date and source
  - Predictor outlook
  - Overnight headlines ranked by relevance to our instruments
  - CRM: leads by status and the pipeline
  - Desk priorities
- **Daily wrap, 22:00 UTC weekdays (backward-looking):**
  - Closes, daily and 5-day change, and the day's range vs ATR (flagged when above 1.5×)
  - Top movers
  - 24-hour news recap
  - The predictor scorecard: yesterday's live predictions graded against what actually happened
- **When data is missing, the report says so.** The old dispatch printed "Zero toxic flow" when no statements had been ingested. The new one says the check was **NOT performed**.
- **Formatting:**
  - FX prints as 1.13452, not "$1.13".
  - DAX prints in EUR points.
  - Gold, silver and oil fall back to *futures* prices from Yahoo, labelled as such, until TradingView data arrives.
- **Test runs:** `agents()["briefing"].morning_dispatch(send=False)` prints the report without sending it.

## 4. TradingView → predictor
1. **History:** for each symbol in `WATCHLIST`, open a daily chart, choose Export chart data, and save the file as `jarvis/data/tradingview/<KEY>.csv` (for example `XAUUSD.csv`). Aim for 4+ years of bars.
2. **Daily updates:**
   - Add `donna/tradingview/donna_daily_snapshot.pine` to a 1D chart and set the passphrase.
   - Create an alert with "Any alert() function call" and the webhook `https://<host>/api/tradingview/snapshot`.
   - This needs a TradingView plan that includes webhook alerts. **Compile the script in the Pine Editor first; I could not compile it here.**
3. **What the predictor does:**
   - Retrains on past data only, every 20 days, and predicts the next day.
   - Compares itself against simply guessing the base rate.
   - Says **EDGE** only when it beats that baseline with p < 0.05.
   - Logs every live prediction and grades it the next day in the wrap.
   - Expect "no proven edge" for most daily-direction calls; that is the honest result.

## 5. Lead monitor: what it does and the rules it enforces
- **Watching:** it listens in Telegram groups you are a member of, and scores messages using whole words and weights. Promoters, bots and links are skipped.
- **Leads:** each handle becomes one lead in DONNA. Handles on the do-not-contact list are never stored again.
- **First contact:**
  - It is a single **consent request**, not a sales pitch.
  - It identifies the firm and includes a STOP opt-out.
  - You approve it in Telegram before anything is sent.
- **Why consent-first:** POPIA s69 allows one unsolicited electronic approach, and only to request consent. GDPR is similar for EU contacts. Have compliance confirm the wording.
- **Sending:** approved messages go out slowly, 90–240 s apart. A PeerFlood response pauses outreach for 24 h, because Telegram restricts accounts that message many strangers.
- **Replies:**
  - STOP or "not interested" → permanent do-not-contact.
  - YES → handed to a human.
  - Anything else → flagged for a human.
