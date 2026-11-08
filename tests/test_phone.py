"""DONNA on the phone (Telegram): owner-only, same answers as the console, ✓ and approve buttons, pairing."""
from __future__ import annotations


def _fake(monkeypatch, calls):
    from donna import phone

    def call(method, **p):
        calls.append((method, p))
        return {"message_id": 1}
    monkeypatch.setattr(phone, "call", call)
    monkeypatch.setenv("DONNA_TELEGRAM_BOT_TOKEN", "123456:abc")
    monkeypatch.setenv("DONNA_TELEGRAM_APPROVER_IDS", "42")


def test_commands_ticks_and_strangers(tmp_path, monkeypatch):
    from donna import phone
    from jarvis.agents import assistant as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    calls: list = []
    _fake(monkeypatch, calls)
    tid = a.add_task("Call FinPrime back")
    assert phone.to_command("/tasks@DonnaBot") == "!tasks" and phone.to_command("/task 4") == "!task 4"
    phone.handle_update({"message": {"text": "/tasks", "from": {"id": 42}, "chat": {"id": 42}}})
    method, p = calls[-1]
    assert method == "sendMessage" and f"Task {tid}: Call FinPrime back" in p["text"]
    assert p["reply_markup"] == {"inline_keyboard": [[{"text": f"✓ {tid}", "callback_data": f"done:{tid}"}]]}
    calls.clear()
    phone.handle_update({"message": {"text": "/tasks", "from": {"id": 7}, "chat": {"id": 7}}})
    assert calls == []                                           # a stranger gets nothing at all
    phone.handle_update({"callback_query": {"id": "c1", "data": f"done:{tid}", "from": {"id": 42},
                                            "message": {"chat": {"id": 42}, "message_id": 9, "reply_markup":
                                                        p["reply_markup"]}}})
    assert a.list_tasks() == []
    names = [m for m, _ in calls]
    assert names == ["answerCallbackQuery", "editMessageReplyMarkup", "sendMessage"]
    assert calls[1][1]["reply_markup"]["inline_keyboard"][0][0] == {"text": f"✅ {tid}", "callback_data": "noop"}
    calls.clear()
    phone.handle_update({"callback_query": {"id": "c2", "data": f"done:{tid}", "from": {"id": 7}, "message": {}}})
    assert calls == [("answerCallbackQuery", {"callback_query_id": "c2", "text": "Not allowed."})]
    assert "D.O.N.N.A on your phone" in phone.answer("/start")


def test_approve_button(monkeypatch):
    from donna import phone

    seen = []
    monkeypatch.setattr(phone, "decide", lambda aid, ok, who: seen.append((aid, ok, who)) or "Approved and done.")
    msg, markup = phone.on_callback({"data": "approve:" + "a" * 32, "from": {"id": 42}})
    assert msg == "Approved and done." and markup == {"inline_keyboard": []} and seen == [("a" * 32, True, "42")]
    assert phone.on_callback({"data": "approve:notanid", "from": {"id": 42}}) == ("", None)


def test_pair_saves_chat_and_adds_phone_to_personal_targets(tmp_path, monkeypatch):
    from donna import phone

    env = tmp_path / "v.env"
    env.write_text('JARVIS_PERSONAL_TARGETS=discord:personal\nOTHER=1\n')
    monkeypatch.setenv("DONNA_TELEGRAM_BOT_TOKEN", "123456:abc")
    sent = []

    def call(method, **p):
        if method == "getMe":
            return {"username": "DonnaBot"}
        if method == "getUpdates" and p.get("timeout"):
            return {"result": [{"update_id": 5, "message": {"text": "/start", "chat": {"id": 42, "type": "private"},
                                                            "from": {"id": 42}}}]}
        sent.append(method)
        return {"result": []}
    monkeypatch.setattr(phone, "call", call)
    assert phone.pair(5, str(env)) == "PHONE     OK - paired with Telegram user 42"
    text = env.read_text()
    assert "DONNA_TELEGRAM_CHAT_ID=42" in text and "DONNA_TELEGRAM_APPROVER_IDS=42" in text
    assert "JARVIS_PERSONAL_TARGETS=discord:personal,telegram:42" in text and "OTHER=1" in text
    assert "sendMessage" in sent
