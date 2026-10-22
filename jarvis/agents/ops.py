"""Admin, project management and inbox triage."""
from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header
from typing import Any

import pandas as pd

from jarvis.adapters.sources import default_source
from jarvis.core import audit
from jarvis.core.config import settings
from jarvis.core.registry import Agent, register, tool

# Cheap keyword pre-triage. The Brain does the nuanced reading; this keeps the
# daily scan free and deterministic.
URGENT = ("margin call", "complaint", "withdrawal", "chargeback", "regulator", "fsca", "fca",
          "cysec", "audit", "kyc", "aml", "stop out", "outage", "urgent")


@register
class OpsAgent(Agent):
    name = "ops"
    description = "Tasks/projects, inbox triage and admin follow-ups."

    @tool()
    def tasks(self, owner: str = "", include_done: bool = False) -> dict[str, Any]:
        """Project tasks grouped as overdue / due this week / later, optionally for one owner."""
        d = default_source().load("tasks")
        if owner:
            d = d[d["owner"].str.lower() == owner.lower()]
        if not include_done:
            d = d[d["status"].str.lower() != "done"]
        d["due"] = pd.to_datetime(d["due"], errors="coerce")
        today = pd.Timestamp.now().normalize()
        cols = ["task_id", "title", "owner", "due", "priority", "project", "status"]
        fmt = lambda x: x[cols].assign(due=x["due"].dt.date.astype(str)).to_dict("records")  # noqa: E731
        return {
            "overdue": fmt(d[d["due"] < today]),
            "this_week": fmt(d[(d["due"] >= today) & (d["due"] <= today + pd.Timedelta(days=7))]),
            "later": len(d[d["due"] > today + pd.Timedelta(days=7)]),
        }

    @tool()
    def inbox(self, max_messages: int = 30) -> list[dict[str, Any]]:
        """Read-only scan of the latest unread emails (IMAP). Returns sender, subject,
        a snippet and an 'urgent' flag for compliance/client-money keywords.
        Never marks as read, never sends."""
        if not settings.imap_host:
            return [{"error": "IMAP not configured (set JARVIS_IMAP_HOST/USER/PASSWORD)"}]
        out: list[dict[str, Any]] = []
        with imaplib.IMAP4_SSL(settings.imap_host) as m:
            m.login(settings.imap_user, settings.imap_password)
            m.select("INBOX", readonly=True)
            _, data = m.search(None, "UNSEEN")
            for num in data[0].split()[-max_messages:][::-1]:
                _, msg_data = m.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(msg_data[0][1])
                subj = str(make_header(decode_header(msg.get("Subject", ""))))
                body = ""
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = (part.get_payload(decode=True) or b"").decode(errors="ignore")
                        break
                text = f"{subj} {body}".lower()
                out.append({"from": msg.get("From"), "subject": subj, "date": msg.get("Date"),
                            "snippet": body[:400], "urgent": any(k in text for k in URGENT)})
        return out

    @tool(writes=True)
    def draft_note(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Save a draft email/follow-up for human review. Does NOT send anything."""
        audit.log("jarvis", "draft_created", {"to": to, "subject": subject, "body": body})
        return {"saved": True, "note": "Draft stored in audit log for review; nothing was sent."}
