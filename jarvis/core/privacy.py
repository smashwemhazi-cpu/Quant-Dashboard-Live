"""Mask client personal data in tool results before they reach Claude.

Names become initials, emails keep the first letter and the domain, and phones, addresses, dates of birth and
document numbers are hidden. Ids, account numbers and figures pass through, so answers can still point at the
right record. Staff fields (account_manager, owner) are kept. Switch off with DONNA_MASK_PII=0.
"""
from __future__ import annotations

import os
import re
from typing import Any

ENABLED = os.getenv("DONNA_MASK_PII", "1") != "0"
NAME_KEYS = {"name", "client_name", "full_name", "first_name", "last_name", "customer_name", "lead_name"}
HIDE_KEYS = {"phone", "mobile", "telephone", "address", "street", "dob", "date_of_birth", "birth_date", "passport",
             "id_number", "national_id", "iban", "card", "card_number", "tax_id"}
EMAIL = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def initials(v: str) -> str:
    return ".".join(p[0].upper() for p in re.split(r"[\s,]+", v.strip()) if p) + "." if v.strip() else v


def mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower()
            if isinstance(v, str) and key in NAME_KEYS:
                out[k] = initials(v)
            elif v not in (None, "") and key in HIDE_KEYS:
                out[k] = "***"
            else:
                out[k] = mask(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [mask(v) for v in obj]
    if isinstance(obj, str):
        return EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", obj)
    return obj


def for_claude(obj: Any) -> Any:
    return mask(obj) if ENABLED else obj
