"""Read-only Google mail and calendar through SH's own Apps Script web app (deploy/google-bridge.gs).

Used when IMAP with an app password is blocked (Google Workspace admins can switch app passwords off).
The script runs inside SH's account, so DONNA holds only the web-app link and a random key, never a password.
JARVIS_GOOGLE_BRIDGE_URL=https://script.google.com/macros/s/.../exec   JARVIS_GOOGLE_BRIDGE_KEY=<key from setup()>
"""
from __future__ import annotations

import os
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
        data = r.json()
    except requests.RequestException as e:
        return {"error": f"Google bridge unreachable ({type(e).__name__})"}
    except ValueError:  # Google answered with a sign-in page, not JSON
        return {"error": "Google bridge answered with a web page - the deployment's access must be 'Anyone'"}
    if not isinstance(data, dict):
        return {"error": "Google bridge sent an unexpected answer"}
    if data.get("error") == "bad key":
        return {"error": "Google bridge refused the key - paste the key from setup() again (vertex1-connect email)"}
    return data
