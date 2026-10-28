"""Read-only Google mail and calendar through SH's own Apps Script web app (deploy/google-bridge.gs).

Used when IMAP with an app password is blocked (Google Workspace admins can switch app passwords off).
The script runs inside SH's account, so DONNA holds only the web-app link and a random key, never a password.
JARVIS_GOOGLE_BRIDGE_URL=https://script.google.com/macros/s/.../exec   JARVIS_GOOGLE_BRIDGE_KEY=<key from setup()>
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests


def configured() -> bool:
    return bool(os.getenv("JARVIS_GOOGLE_BRIDGE_URL", "").strip() and os.getenv("JARVIS_GOOGLE_BRIDGE_KEY", "").strip())


def call(action: str = "ping", **params: Any) -> dict[str, Any]:
    """One request to the bridge. Returns its JSON, or {"error": ...} - never raises."""
    if not configured():
        return {"error": "NO DATA - Google bridge not set up (JARVIS_GOOGLE_BRIDGE_URL / _KEY)"}
    try:
        r = requests.get(os.getenv("JARVIS_GOOGLE_BRIDGE_URL", "").strip().strip("\"'"), timeout=60,
                         params={"action": action, "key": os.getenv("JARVIS_GOOGLE_BRIDGE_KEY", "").strip(), **params})
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"Google bridge unreachable ({type(e).__name__})"}
    try:
        data = r.json()
    except ValueError:  # requests' JSONDecodeError is also a RequestException, so it is caught here first
        return {"error": web_page_reason(str(getattr(r, "url", "")), str(getattr(r, "text", "")))}
    if not isinstance(data, dict):
        return {"error": "Google bridge sent an unexpected answer"}
    if data.get("error") == "bad key":
        return {"error": "Google bridge refused the key - paste the key from setup() again (vertex1-connect email)"}
    return data


def web_page_reason(url: str, page: str) -> str:
    """Google answered with a web page instead of the bridge's JSON: say which setting causes it."""
    if "accounts.google.com" in url or "ServiceLogin" in page or "accounts.google.com" in page[:5000]:
        return ("Google asked for a sign-in: in Deploy > Manage deployments > edit, 'Who has access' must be "
                "'Anyone' (not 'Anyone with Google account' or 'Anyone within Vertex 1 Brokers'), then New version > Deploy")
    if "Script function not found" in page:
        return ("the deployment has no code in it: save the script, then Deploy > Manage deployments > edit > "
                "Version: New version > Deploy")
    if "unable to open the file" in page or "not found" in page.lower()[:3000]:
        return "the bridge link is wrong or the deployment was archived - copy the Web app URL again (Deploy > Manage deployments)"
    m = re.search(r"<title>(.*?)</title>", page, re.IGNORECASE | re.DOTALL)
    return f"Google bridge answered with a web page ({(m.group(1).strip() if m else 'no title')[:80]}) instead of data"
