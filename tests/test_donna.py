import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DONNA_DB_PATH", str(tmp_path / "d.db"))
    monkeypatch.setenv("DONNA_FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("DONNA_API_KEY", "svc")
    monkeypatch.setenv("DONNA_APPROVER_KEYS", "alice:k1")
    monkeypatch.setenv("DONNA_TV_PASSPHRASE", "tv")
    monkeypatch.setenv("DONNA_TELEGRAM_WEBHOOK_SECRET", "tg")
    monkeypatch.setenv("DONNA_TELEGRAM_APPROVER_IDS", "42")
    import donna.app as m
    m = importlib.reload(m)
    with TestClient(m.app) as c:
        c.mod = m
        yield c


SVC, APPR = {"X-API-Key": "svc"}, {"X-Approver-Key": "k1"}
TRADE = dict(trade_id="t1", account_id="A1", symbol="EURUSD", side="buy", lots=1, balance=10000)


def test_everything_requires_auth(client):
    assert client.post("/api/ingest/trade", json=TRADE).status_code == 401
    assert client.post("/api/actions/x/approve").status_code == 401
    assert client.post("/api/actions/x/approve", headers=SVC).status_code == 401  # service key can't approve
    bad = dict(passphrase="nope", ticker="X", price=1, event="e")
    assert client.post("/api/tradingview/alert", json=bad).status_code == 401


def test_unknown_account_defaults_to_a_book_and_real_risk_pct(client):
    r = client.post("/api/ingest/trade", headers=SVC,
                    json={**TRADE, "stop_loss_pips": 20, "pip_value_per_lot": 10}).json()
    assert r["recommendation"] == "A-BOOK" and r["risk_pct"] == 2.0
    assert client.post("/api/ingest/trade", headers=SVC, json=TRADE).json()["status"] == "DUPLICATE"


def test_b_book_needs_history_and_review(client):
    with client.mod.db() as c:
        c.execute("INSERT INTO account_profiles VALUES ('L', 200, 10, 'consistent loser', 'x')")
    r = client.post("/api/ingest/trade", headers=SVC, json={**TRADE, "trade_id": "t2", "account_id": "L"}).json()
    assert r["recommendation"] == "B-BOOK-CANDIDATE" and r["requires_review"]


def _stage(client):
    r = client.post("/api/tradingview/alert", json=dict(passphrase="tv", ticker="XAGUSD", price=30, event="cross"))
    return r.json()["action_id"]


def test_approval_is_atomic_and_honest(client):
    aid = _stage(client)
    assert len(aid) == 32
    r = client.post(f"/api/actions/{aid}/approve", headers=APPR).json()
    assert r["status"] == "FAILED" and "not configured" in r["result"]  # never a fake EXECUTED
    assert client.post(f"/api/actions/{aid}/approve", headers=APPR).status_code == 409
    assert client.post(f"/api/actions/{aid}/reject", headers=APPR).status_code == 409  # can't un-execute
    with client.mod.db() as c:
        row = c.execute("SELECT decided_by, payload FROM actions WHERE action_id=?", (aid,)).fetchone()
    assert row["decided_by"] == "alice" and "tv" not in row["payload"]


def test_expired_actions_cannot_be_approved(client):
    aid = _stage(client)
    with client.mod.db() as c:
        c.execute("UPDATE actions SET expires_at = '2000-01-01' WHERE action_id = ?", (aid,))
    r = client.post(f"/api/actions/{aid}/approve", headers=APPR)
    assert r.status_code == 409 and "EXPIRED" in r.text


def test_telegram_webhook_secret_and_allowlist(client):
    aid = _stage(client)
    body = lambda uid: {"message": {"from": {"id": uid}, "text": f"/reject {aid}"}}  # noqa: E731
    assert client.post("/api/telegram/webhook", json=body(42)).status_code == 401
    h = {"X-Telegram-Bot-Api-Secret-Token": "tg"}
    assert client.post("/api/telegram/webhook", json=body(7), headers=h).json()["status"] == "ignored"
    assert client.post("/api/telegram/webhook", json=body(42), headers=h).json()["status"] == "ok"


def test_static_checks_catch_unsafe_cbot(client):
    m = client.mod
    bad = "```csharp\n[Robot(AccessRights = AccessRights.FullAccess)] class X { void F(){ volume * 2; } }\n```"
    issues = m.static_check(m.extract_csharp("Here you go:\n" + bad + "\nThanks"))
    assert "elevated AccessRights requested" in issues
    assert "possible martingale / volume doubling" in issues
    assert "no stop loss handling found" in issues


def test_strategy_job_runs_in_background_and_gates_deploy(client, monkeypatch):
    m = client.mod
    monkeypatch.setattr(m, "run_strategy_crew",
                        lambda p: "```csharp\n[Robot(AccessRights = AccessRights.None)] // stopLoss\n```")
    job = client.post("/api/strategy/generate", headers=SVC, json={"prompt": "silver pullback scalper"}).json()
    res = client.get(f"/api/jobs/{job['job_id']}", headers=SVC).json()
    assert res["status"] == "DONE" and res["result"]["compiled"] is False
    assert "action_id" not in res["result"]  # not compiled -> never staged for deployment


# ---------------------------------------------------------------- channels, leads, TradingView
def test_reply_executor_routes_slack_thread(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(client.mod.channels, "send_slack",
                        lambda ch, text, ts=None: sent.update(ch=ch, ts=ts) or "slack ok")
    r = client.post("/api/comms/inbound", headers=SVC, json={
        "source": "slack", "sender": "U1", "recipient": "C42", "content": "hi", "thread_id": "123.45"}).json()
    out = client.post(f"/api/actions/{r['action_id']}/approve", headers=APPR).json()
    assert out["status"] == "EXECUTED" and sent == {"ch": "C42", "ts": "123.45"}


def test_trader_lead_is_draft_only_by_default(client, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import leadfinder

    lead = {"handle": "Big_Trader", "source_chat": "FX Pros", "snippet": "looking for a broker with FIX API",
            "matched_terms": ["fix api"], "intent_score": 5}
    r = client.post("/api/leads", headers=SVC, json=lead).json()
    assert r["status"] == "LISTED" and r["staged"] is False
    assert client.get("/api/actions/pending", headers=APPR).json() == []       # nothing queued to send
    t = leadfinder.shortlist("trader")
    assert t[0]["url"] == "https://t.me/Big_Trader" and "FIX API" in t[0]["about"]
    d = leadfinder.draft(t[0]["id"])
    assert "not sent" in d and "STOP" in d and "spread" not in d.lower()        # consent request, no pitch


def test_telegram_search_results_reach_the_lead_finder(client, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import leadfinder, radar

    assert client.get("/api/prospects/queries", headers=SVC).json() == {}       # no country cleared: no search
    radar.set_permitted(["KE"])
    assert "forex Kenya" in client.get("/api/prospects/queries", headers=SVC).json()["KE"]
    items = [{"username": "nairobi_fx_academy", "title": "Nairobi FX Academy", "about": "Forex education, "
              "price action mentorship and a trading community", "members": 12000, "country": "KE"},
             {"username": "vipsignalske", "title": "VIP Signals KE", "about": "Guaranteed 100% win signals",
              "members": 40000, "country": "KE"},
             {"username": "london_fx", "title": "London FX", "about": "forex", "members": 9, "country": "ZA"}]
    assert client.post("/api/prospects", json=items).status_code == 401
    assert client.post("/api/prospects", headers=SVC, json=items).json() == {"stored": 2}  # ZA isn't cleared
    sl = leadfinder.shortlist("ib")
    assert [r["name"] for r in sl] == ["Nairobi FX Academy"] and sl[0]["source"] == "telegram"
    assert "promises returns" in leadfinder.command("!leads", "flagged")


def test_lead_flow_consent_once_and_stop_is_permanent(client, monkeypatch):
    monkeypatch.setenv("DONNA_LEAD_AUTOSEND", "1")   # the old approve-then-send route, now opt-in
    lead = {"handle": "Big_Trader", "source_chat": "FX Pros", "snippet": "need FIX API",
            "matched_terms": ["fix api"], "intent_score": 5}
    r = client.post("/api/leads", headers=SVC, json=lead).json()
    assert r["staged"]
    text = client.get("/api/actions/pending", headers=APPR).json()[0]["preview_text"]
    assert "STOP" in text and "raw spread" not in text.lower()     # consent request, not a pitch
    assert client.post("/api/leads", headers=SVC, json=lead).json()["staged"] is False  # once only
    aid = r["action_id"]
    assert client.post(f"/api/actions/{aid}/approve", headers=APPR).json()["result"] == "queued for worker"
    assert client.get("/api/outreach/approved", headers=SVC).json()[0]["action_id"] == aid
    client.post(f"/api/outreach/{aid}/result", headers=SVC, json={"ok": True})
    assert client.post("/api/leads/reply", headers=SVC,
                       json={"handle": "big_trader", "text": "Please STOP messaging me"}).json()["status"] == "OPTED_OUT"
    assert client.post("/api/leads", headers=SVC, json=lead).json()["status"] == "DO_NOT_CONTACT"
    assert client.post("/api/leads/reply", headers=SVC,
                       json={"handle": "stranger", "text": "yes"}).json()["status"] == "IGNORED"


def test_tradingview_snapshot_accepts_text_plain(client):
    import json as _j
    body = _j.dumps({"passphrase": "tv", "kind": "daily_snapshot", "bars": [
        {"symbol": "OANDA:XAUUSD", "t": 1761004800000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "e200": None}]})
    r = client.post("/api/tradingview/snapshot", content=body, headers={"Content-Type": "text/plain"})
    assert r.json() == {"status": "STORED", "bars": 1}
    bad = body.replace('"tv"', '"nope"')
    assert client.post("/api/tradingview/snapshot", content=bad, headers={"Content-Type": "text/plain"}).status_code == 401


def test_lead_scorer_ignores_substrings_and_promoters():
    from donna.lead_monitor import score_message
    assert score_message("is it possible the bible is visible").value == 0
    assert score_message("Join my VIP signals t.me/abc for FIX API").value == 0
    assert score_message("Looking for a broker with FIX API for 50 lots/month?").value >= 5


def test_discord_embeds_named_webhook_and_rate_limit(monkeypatch):
    from donna import channels

    monkeypatch.setenv("DONNA_DISCORD_WEBHOOK_REPORTS", "https://discord.com/api/webhooks/1/test")
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, str(body)

        def json(self):
            return self._b

    def fake_post(url, params=None, json=None, timeout=None):
        calls.append((url, json))
        return R(429, {"retry_after": 0}) if len(calls) == 1 else R(200, {"id": str(len(calls))})

    monkeypatch.setattr(channels.requests, "post", fake_post)
    monkeypatch.setattr(channels.time, "sleep", lambda s: None)
    monkeypatch.setenv("DONNA_DISCORD_STYLE", "mono")
    text = "\n".join(f"Gold (XAU): $4,2{i:02d}.00 | day +0.40%" for i in range(300)) + "\n```evil```"
    out = channels.broadcast(text, ["discord:reports"], title="Vertex 1 daily_wrap")
    assert out["discord:reports"].startswith("discord:reports msg")
    sent = [j for _, j in calls[1:]]
    assert len(sent) >= 2 and sent[0]["embeds"][0]["title"] == "Vertex 1 daily_wrap"
    assert all(len(j["embeds"][0]["description"]) <= 4096 for j in sent)
    assert all(j["allowed_mentions"] == {"parse": []} for j in sent)
    assert "```evil```" not in "".join(j["embeds"][0]["description"] for j in sent)
    assert channels.broadcast("x", ["discord:missing"])["discord:missing"].startswith("ERROR")


def test_broadcast_survives_network_failure(monkeypatch):
    from donna import channels

    monkeypatch.setenv("DONNA_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/test")

    def boom(*a, **k):
        raise channels.requests.ConnectionError("proxy said no")

    monkeypatch.setattr(channels.requests, "post", boom)
    out = channels.broadcast("hi", ["discord", "slack:#desk"])
    assert out["discord"] == "ERROR discord unreachable: ConnectionError"
    assert out["slack:#desk"].startswith("ERROR")  # other targets still attempted


def test_discord_url_debris_and_bad_url(monkeypatch):
    from donna import channels

    monkeypatch.setenv("DONNA_DISCORD_WEBHOOK_REPORTS", '  "https://discord.com/api/webhooks/1/abc"  ')
    assert channels._env("DONNA_DISCORD_WEBHOOK_REPORTS") == "https://discord.com/api/webhooks/1/abc"
    monkeypatch.setenv("DONNA_DISCORD_WEBHOOK_REPORTS", "paste-here")
    out = channels.broadcast("x", ["discord:reports"])["discord:reports"]
    assert "not a Discord webhook URL" in out


def test_discord_task_commands(tmp_path, monkeypatch):
    from donna import discord_bot as b

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("DONNA_DISCORD_COMMAND_CHANNELS", "#track-b-personal")
    assert b.allowed(1, "track-b-personal") and not b.allowed(1, "general")
    monkeypatch.setenv("DONNA_DISCORD_ALLOWED_USERS", "42")
    assert b.allowed(42, "Track-B-Personal") and not b.allowed(7, "track-b-personal")
    monkeypatch.delenv("DONNA_DISCORD_COMMAND_CHANNELS")
    assert not b.allowed(42, "track-b-personal")  # no channel configured -> answer nowhere

    assert b.handle("hello there") is None and b.handle("!unknown") is None
    assert b.handle("!tasks") == "No open tasks."
    assert b.handle("!add Call LP about XAU spreads | 2026-10-24 | high") == \
        "Added #1: Call LP about XAU spreads (due 2026-10-24) [high]"
    b.handle("!add Archive test KYC docs")
    assert "#1" in b.handle("!tasks") and "2026-10-24" in b.handle("!tasks")
    out = b.handle("!done 1 99")
    assert "#1 done" in out and "#99 not found" in out and "#1 " not in out.split("\n\n")[1]
    parts = b.chunks("x" * 5000)
    assert all(len(p) <= 2000 for p in parts) and len(parts) == 3


def test_discord_ask_uses_read_only_brain_with_memory(tmp_path, monkeypatch):
    from donna import discord_bot as b
    from jarvis.core import brain

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    seen = []

    class FakeBrain:
        def ask(self, messages, user, allow_writes, system=None):
            seen.append(dict(msgs=messages, user=user, writes=allow_writes, system=system))
            return "3 clients at risk"

    monkeypatch.setattr(brain, "Brain", FakeBrain)
    assert b.handle("!ask who is at risk?", "desk") == "3 clients at risk"
    assert seen[0]["msgs"] == [{"role": "user", "content": "who is at risk?"}]
    assert not seen[0]["writes"] and "D.O.N.N.A" in seen[0]["system"]
    b.handle("!ask and the biggest one?", "desk")
    assert [m["content"] for m in seen[1]["msgs"]] == ["who is at risk?", "3 clients at risk", "and the biggest one?"]
    assert b.handle("!ask   ").startswith("Usage")


def test_status_reports_version_credit_and_failures(tmp_path, monkeypatch):
    from donna import discord_bot
    from jarvis.core import audit

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    audit.log("scheduler", "job_error", {"job": "inbox_triage", "error": "AUTHENTICATIONFAILED"})

    class Broke:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("Your credit balance is too low to access the Anthropic API.")

    out = discord_bot.status_text(Broke())
    assert "DONNA version" in out and "NO CREDIT" in out and "inbox_triage: AUTHENTICATIONFAILED" in out


def test_hash_slip_runs_the_command(monkeypatch):
    from donna import discord_bot

    assert discord_bot.handle("#help") == discord_bot.HELP
    assert discord_bot.handle("#nonsense") is None


def test_no_credit_is_a_plain_message():
    from jarvis.agents import mind
    from jarvis.core import brain

    assert brain.NO_CREDIT.startswith(mind.FAILED_REPLIES) and "!help" in brain.NO_CREDIT


def test_console_only_answers_through_the_tunnel(client, monkeypatch):
    from donna import console

    monkeypatch.setattr(console, "LOCAL_CLIENTS", {"testclient"})   # TestClient's address stands in for 127.0.0.1
    monkeypatch.setattr(console, "state", lambda: {"now": "x", "tasks": []})
    monkeypatch.setattr(console, "reply", lambda text: f"echo {text}")
    local, api = {"host": "localhost:8765"}, {"host": "localhost:8765", "X-Donna": "console"}
    page = client.get("/console", headers=local)
    assert page.status_code == 200 and "D.O.N.N.A" in page.text and page.headers["x-frame-options"] == "DENY"
    assert client.get("/api/console/state", headers=api).json() == {"now": "x", "tasks": []}
    assert client.post("/api/console/say", headers=api, json={"text": "hi"}).json() == {"reply": "echo hi"}
    # through nginx, a DNS-rebinding host, or a cross-site page without the header: as if it didn't exist
    assert client.get("/console", headers={**local, "X-Forwarded-For": "1.2.3.4"}).status_code == 404
    assert client.get("/console", headers={"host": "evil.example:8765"}).status_code == 404
    assert client.post("/api/console/say", headers=local, json={"text": "!done 1"}).status_code == 404
    monkeypatch.setattr(console, "LOCAL_CLIENTS", {"127.0.0.1"})
    assert client.get("/console", headers=local).status_code == 404     # not from the server itself


def test_console_reply_routes_commands_and_chat(monkeypatch):
    from donna import console, discord_bot
    from jarvis.agents import mind

    monkeypatch.setattr(discord_bot, "handle", lambda text, channel="": f"cmd {text} in {channel}")
    monkeypatch.setattr(mind, "chat", lambda text, channel, allow_writes: f"chat {text} {channel} {allow_writes}")
    assert console.reply("!tasks") == "cmd !tasks in console"
    assert console.reply("how is my day?") == "cmd !today in console"          # answered without Claude
    assert console.reply("what tasks do I have") == "cmd !tasks in console"
    assert console.reply("any complaints open?") == "cmd !complaints in console"
    q = "should we widen gold spreads before the FOMC given our B-book exposure?"
    assert console.reply(q) == f"chat {q} console False"


WRAP = """VERTEX 1 | DAILY WRAP | Fri 06 Nov 2026 (data to last close)
----------------------------------
CLOSES
              Close     Day  5 days  Range
XAUUSD     4,073.70  -0.97%  +4.48%   0.9x
EURUSD      1.11570  -0.69%  -0.23%   1.6x \u26a0

TOP MOVERS: GER40 (DAX) -1.92%, NAS100 (Nasdaq) +1.90%

24H NEWS RECAP (ranked by market relevance)
Macro:
 - U.S. economy added 165,000 jobs in October
 - Nasdaq drops into *correction* territory

BUSINESS (back office)
CRM KPIs: NO DATA - no Syntellicore KPI export ingested

BOOK: NO DATA - no cTrader end-of-day report ingested

COMPLIANCE
 [GREEN ] Open high-severity cases: 0
 [RED   ] Overdue cases: 2
----------------------------------
Internal use only."""


def test_rich_layout_sections_tables_and_missing_data():
    from donna import channels

    es = channels.rich_embeds(WRAP, "Vertex 1 daily_wrap", 1)
    assert len(es) == 1 and es[0]["title"] == "Vertex 1 daily_wrap" and "DAILY WRAP" in es[0]["description"]
    f = {x["name"]: x["value"] for x in es[0]["fields"]}
    assert f["Closes"].startswith("```") and "XAUUSD" in f["Closes"]              # tables stay monospace
    assert f["Top Movers"].startswith("GER40")                                     # one-liners keep their heading
    news = f["24H News Recap (ranked by market relevance)"]
    assert "\u2022 U.S. economy" in news and "**Macro**" in news and "\\*correction\\*" in news
    assert "CRM KPIs" in f["\u26AA Not connected yet"] and "Book" in f["\u26AA Not connected yet"]
    assert "\U0001F7E2" in f["Compliance"] and "\U0001F534" in f["Compliance"]
    assert "Internal use only." in es[0]["footer"]["text"]
    assert all(len(x["value"]) <= 1024 for x in es[0]["fields"])


def test_rich_layout_splits_long_reports():
    from donna import channels

    text = "NEWS\n" + "\n".join(f" - headline number {i} " + "x" * 80 for i in range(200))
    es = channels.rich_embeds(text, "t", 1)
    assert len(es) > 1 and all(len(e["fields"]) <= 25 for e in es)
    assert all(sum(len(x["name"]) + len(x["value"]) for x in e["fields"]) <= 6000 for e in es)


def test_lead_message_is_sent_only_after_confirmation_once(client, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import leadfinder as lf

    lead = {"handle": "Big_Trader", "source_chat": "FX Pros", "snippet": "looking for a broker with FIX API",
            "matched_terms": ["fix api"], "intent_score": 5}
    pid = client.post("/api/leads", headers=SVC, json=lead).json()["prospect"]
    assert "Approve #" in lf.command("!leads", f"send {pid}")                    # approved leads only
    lf.command("!leads", f"{pid} approve")
    assert "Nothing waiting" in lf.command("!leads", f"confirm {pid}")           # confirm needs a send first
    ask = lf.command("!leads", f"send {pid}")
    assert "READY TO SEND to @Big_Trader" in ask and "STOP" in ask and f"!leads confirm {pid}" in ask
    assert client.get("/api/leads/outbox", headers=SVC).json() == []             # still nothing to send
    assert "Confirmed" in lf.command("!leads", f"confirm {pid}")
    q = client.get("/api/leads/outbox", headers=SVC).json()
    assert [i["handle"] for i in q] == ["Big_Trader"]
    client.post(f"/api/leads/outbox/{pid}/result", headers=SVC, json={"ok": True})
    assert lf.shortlist("trader")[0]["status"] == "contacted"
    assert "already had its one first message" in lf.command("!leads", f"send {pid}")
    r = client.post("/api/leads/reply", headers=SVC, json={"handle": "Big_Trader", "text": "yes please"}).json()
    assert r["status"] == "REPLIED" and lf.shortlist("trader")[0]["status"] == "replied"
    r = client.post("/api/leads/reply", headers=SVC, json={"handle": "big_trader", "text": "STOP"}).json()
    assert r["status"] == "OPTED_OUT"
    assert client.post("/api/leads", headers=SVC, json=lead).json()["status"] == "DO_NOT_CONTACT"
    # an expired confirmation and the daily cap
    t = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
    ids = [lf.add({"source": "manual", "ext_id": f"https://t.me/person{i}", "kind": "trader", "country": "",
                   "name": f"p{i}", "url": f"https://t.me/person{i}"}) for i in range(12)]
    for i in ids:
        lf.update(i, "approved")
    lf.ask_send(ids[0], t)
    assert "expired" in lf.confirm_send(ids[0], t + timedelta(minutes=11))
    monkeypatch.setattr(lf, "SEND_CAP", 3)   # the first lead above counts too
    for i in ids[1:3]:
        lf.ask_send(i, t)
        assert "Confirmed" in lf.confirm_send(i, t)
    assert "limit of 3" in lf.ask_send(ids[3], t)
    ch = lf.add({"source": "telegram", "ext_id": "kefx", "kind": "ib", "country": "", "name": "KE FX",
                 "url": "https://t.me/kefx"})
    assert "can't take a direct message" in lf.ask_send(ch)                     # channels: you send by hand


def test_send_preview_warns_or_blocks_on_red_protect_kpis(client, tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    from jarvis.agents import kpi, leadfinder as lf

    pid = lf.add({"source": "manual", "ext_id": "https://t.me/someone1", "kind": "trader", "country": "",
                  "name": "someone", "url": "https://t.me/someone1"})
    lf.update(pid, "approved")
    monkeypatch.setattr(kpi, "brake", lambda *a, **k: ["Open high-severity cases"])
    assert "Protect KPIs in the red: Open high-severity cases" in lf.ask_send(pid)
    monkeypatch.setenv("DONNA_KPI_BRAKE", "block")
    assert lf.ask_send(pid).startswith("Not sending")
    assert "Nothing waiting" in lf.confirm_send(pid)
