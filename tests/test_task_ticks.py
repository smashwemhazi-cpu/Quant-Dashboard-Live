"""Ticking tasks done by click, undoing a tick, and changing a task in plain words."""
from __future__ import annotations

from datetime import date


def test_due_words():
    from jarvis.agents.assistant import parse_due

    sun = date(2026, 11, 8)
    assert parse_due("friday", sun) == "2026-11-13" and parse_due("fri", sun) == "2026-11-13"
    assert parse_due("sunday", sun) == "2026-11-15"                      # the next one, not today
    assert parse_due("tomorrow", sun) == "2026-11-09" and parse_due("2026-12-01") == "2026-12-01"
    assert parse_due("none") == "" and parse_due("someday") is None and parse_due("2026-02-30") is None


def test_edit_undo_and_tick(tmp_path, monkeypatch):
    from donna import discord_bot as b
    from jarvis.agents import assistant as a

    monkeypatch.setenv("JARVIS_STATE_DB", str(tmp_path / "s.db"))
    tid = a.add_task("Reply: FinPrime introduction")
    assert b.handle(f"!edit {tid} urgent") == f"Updated Task {tid}: Reply: FinPrime introduction - urgent"
    assert "due " in b.handle(f"!edit {tid} due tomorrow")
    assert b.handle(f"!edit {tid} title Call FinPrime back").startswith(f"Updated Task {tid}: Call FinPrime back")
    assert "Say when" in b.handle(f"!edit {tid} due someday") and "What should change" in b.handle(f"!edit {tid} blue")
    assert "Usage" in b.handle("!edit") and "isn't open" in b.handle("!edit 99 urgent")
    # the buttons: every task-list reply gets one per open task; a click is the same as !done
    assert b.tick_ids("!tasks") == [tid] and b.tick_ids("!today") == [] and b.tick_ids("!done 3") == [tid]
    assert b.tick(tid) == f"Ticked: Task {tid}: Call FinPrime back. Ticked by mistake? !undo {tid}"
    assert b.tick(tid) == f"Task {tid} was already done." and b.tick(99) == "Task 99 doesn't exist."
    assert b.handle("!tasks") == "No open tasks."
    assert f"Task {tid} is back on your list." in b.handle(f"!undo {tid}") and b.tick_ids("!tasks") == [tid]
    assert "was not ticked done" in b.handle(f"!undo {tid}")
    t = b.handle("!tasks")
    assert "click a task's ✓ button" in t and f"!undo {tid}" in t and f"!edit {tid} due friday" in t


def test_tick_buttons_survive_restart():
    from donna import discord_bot as b

    v = b.tick_view([3, 7], done={7})
    assert v.timeout is None
    labels = [(i.label, i.custom_id, i.disabled) for i in v.children]
    assert labels == [("✓ 3", "donna:done:3", False), ("Done ✓ 7", "donna:done:7", True)]
