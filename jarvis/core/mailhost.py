"""Who actually holds the mailbox for an email address.

A Google login can be made for any address (simba@company.com), so having an app-password page at
myaccount.google.com does not mean the company's mail lives in Gmail. If the domain's MX records point
elsewhere, imap.gmail.com answers AUTHENTICATIONFAILED to every password. Looking up the MX records
tells us which inbox server to use, and whether a Google app password is even the right kind of password.
"""
from __future__ import annotations

import json
import os
import urllib.request

# (MX suffix, provider, IMAP host or None, what the password must be)
PROVIDERS = [
    ("google.com", "Google Workspace / Gmail", "imap.gmail.com", "google"),
    ("googlemail.com", "Google Workspace / Gmail", "imap.gmail.com", "google"),
    ("outlook.com", "Microsoft 365 / Outlook", "outlook.office365.com", "microsoft"),
    ("zoho.com", "Zoho Mail", "imap.zoho.com", "mailbox"),
    ("zoho.eu", "Zoho Mail", "imap.zoho.eu", "mailbox"),
    ("zoho.in", "Zoho Mail", "imap.zoho.in", "mailbox"),
    ("secureserver.net", "GoDaddy Email", "imap.secureserver.net", "mailbox"),
    ("titan.email", "Titan Email", "imap.titan.email", "mailbox"),
    ("hostinger.com", "Hostinger Email", "imap.hostinger.com", "mailbox"),
    ("yahoodns.net", "Yahoo Mail", "imap.mail.yahoo.com", "mailbox"),
    ("icloud.com", "iCloud Mail", "imap.mail.me.com", "mailbox"),
]
PUBLIC = {"gmail.com": "gmail-smtp-in.l.google.com", "googlemail.com": "gmail-smtp-in.l.google.com"}


def mx_records(domain: str, timeout: float = 8.0) -> list[str]:
    """MX host names for a domain, best (lowest preference) first. Raises on network failure."""
    if domain in PUBLIC:
        return [PUBLIC[domain]]
    url = f"https://dns.google/resolve?name={domain}&type=MX"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        answers = json.load(r).get("Answer", [])
    recs = []
    for a in answers:
        if a.get("type") == 15:
            pref, _, host = str(a.get("data", "")).partition(" ")
            recs.append((int(pref) if pref.isdigit() else 99, host.rstrip(".").lower()))
    return [h for _, h in sorted(recs)]


def classify(email: str, mx: list[str]) -> dict:
    """Map an address and its MX hosts to a provider. kind: google | microsoft | mailbox | unknown | none."""
    domain = email.rsplit("@", 1)[-1].lower()
    if not mx:
        return {"domain": domain, "mx": "", "provider": "no mail server found", "imap_host": None, "kind": "none"}
    for host in mx:
        for suffix, name, imap, kind in PROVIDERS:
            if host == suffix or host.endswith("." + suffix):
                return {"domain": domain, "mx": host, "provider": name, "imap_host": imap, "kind": kind}
    return {"domain": domain, "mx": mx[0], "provider": f"the company's own mail server ({mx[0]})",
            "imap_host": None, "kind": "unknown"}


def lookup(email: str) -> dict | None:
    """classify() with a live MX lookup; None when the lookup itself fails (no network)."""
    fixed = os.getenv("VERTEX1_MX")  # comma-separated MX hosts; skips the network (tests, offline checks)
    try:
        domain = email.rsplit("@", 1)[-1].lower()
        return classify(email, [h for h in fixed.split(",") if h] if fixed is not None else mx_records(domain))
    except Exception:  # noqa: BLE001 - a diagnostic helper must never break setup
        return None
