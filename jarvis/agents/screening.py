"""Sanctions and PEP screening of clients against the official lists.

Lists (refreshed daily, free and public):
  UN Security Council Consolidated List - the list South Africa's FIC targeted financial sanctions follow
  US OFAC SDN list (with aliases)
  Add more with JARVIS_SANCTIONS_EXTRA_CSV=/path/list.csv (columns: name, dob, list, ref) - e.g. the UK or EU
  consolidated list exported to CSV, or a licensed PEP list.

Clients: the CRM customer export (customer number, first and last name, optional date of birth and a PEP
column) and the KYC documents export are screened every time they arrive. Names are used in memory for
the comparison and never stored - DONNA keeps the customer number, the list entry matched and the score.
So screening repeats with each daily export, against that day's lists.

Matches become compliance cases (category SANCTIONS) for you to clear or escalate:
  SANCTIONS_STRONG   name match >= 0.92 and the date of birth agrees (or the list has none)
  SANCTIONS_POSSIBLE name match >= JARVIS_SCREEN_THRESHOLD (default 0.85)
  PEP_DECLARED       the CRM marks the client as a PEP - enhanced due diligence needed
Close a false positive with the reason (!close 12 false positive - different date of birth); the case
register keeps the decision. !screen <name> [YYYY-MM-DD] checks one name before onboarding.
PEP lists: there is no free official PEP list. OpenSanctions has one but its free licence is
non-commercial - a broker needs their paid licence or a vendor (feed it through the extra CSV).
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import requests

from jarvis.core import audit
from jarvis.core.config import settings

THRESHOLD = float(os.getenv("JARVIS_SCREEN_THRESHOLD", "0.85"))
STRONG = 0.92
UN_URL = os.getenv("JARVIS_UN_LIST_URL", "https://scsanctions.un.org/resources/xml/en/consolidated.xml")
OFAC_URLS = {
    "sdn": os.getenv("JARVIS_OFAC_SDN_URL", "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"),
    "alt": os.getenv("JARVIS_OFAC_ALT_URL", "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"),
}
NOISE = {"mr", "mrs", "ms", "dr", "al", "el", "bin", "ibn", "abu", "the", "of", "and"}


def _db() -> sqlite3.Connection:
    path = os.getenv("JARVIS_STATE_DB", settings.state_db)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS sanctions_entries (list TEXT, ref TEXT, name TEXT, norm TEXT, dob TEXT, kind TEXT);
        CREATE INDEX IF NOT EXISTS sanctions_norm ON sanctions_entries (norm);
        CREATE TABLE IF NOT EXISTS screening_meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    return c


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in NOISE]
    return " ".join(sorted(toks))


def similarity(a: str, b: str) -> float:
    """0..1 between two normalised names: token overlap and character similarity, whichever is fairer."""
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    # a client name fully inside a longer listed name (or the reverse) counts: lists carry full names,
    # CRMs often only first + last. Single-token names must match the whole entry.
    shorter = min(len(ta), len(tb))
    overlap = len(ta & tb) / (shorter if shorter >= 2 else max(len(ta), len(tb)))
    return max(overlap, SequenceMatcher(None, a, b).ratio())


# ------------------------------------------------------------------ lists
def parse_un(xml_bytes: bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    out = []
    for kind, tag, alias in (("person", "INDIVIDUAL", "INDIVIDUAL_ALIAS"), ("entity", "ENTITY", "ENTITY_ALIAS")):
        for el in root.iter(tag):
            ref = (el.findtext("REFERENCE_NUMBER") or "").strip()
            name = " ".join((el.findtext(f) or "").strip() for f in
                            ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")).strip()
            dob = ""
            d = el.find("INDIVIDUAL_DATE_OF_BIRTH")
            if d is not None:
                dob = (d.findtext("DATE") or d.findtext("YEAR") or "").strip()[:10]
            names = [name] + [(a.findtext("ALIAS_NAME") or "").strip() for a in el.findall(alias)]
            out += [{"list": "UN", "ref": ref, "name": n, "dob": dob, "kind": kind} for n in names if n]
    return out


def parse_ofac(sdn_csv: str, alt_csv: str = "") -> list[dict[str, str]]:
    def name(raw: str) -> str:  # "LAST, First Middle" -> "First Middle LAST"
        last, _, first = raw.partition(",")
        return f"{first.strip()} {last.strip()}".strip()

    out, kinds = [], {}
    for row in csv.reader(io.StringIO(sdn_csv)):
        if len(row) < 3 or not row[0].strip().isdigit():
            continue
        kinds[row[0]] = "person" if row[2].strip().lower() == "individual" else "entity"
        dob = re.search(r"DOB (\d{1,2} \w{3} \d{4}|\d{4})", row[11] if len(row) > 11 else "")
        out.append({"list": "OFAC", "ref": row[0].strip(), "name": name(row[1]), "dob": dob.group(1) if dob else "",
                    "kind": kinds[row[0]]})
    for row in csv.reader(io.StringIO(alt_csv)):
        if len(row) >= 4 and row[0].strip() in kinds:
            out.append({"list": "OFAC", "ref": row[0].strip(), "name": name(row[3]), "dob": "", "kind": kinds[row[0]]})
    return out


def parse_extra(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{"list": (r.get("list") or "EXTRA").strip(), "ref": (r.get("ref") or "").strip(),
                 "name": (r.get("name") or "").strip(), "dob": (r.get("dob") or "").strip(), "kind": "person"}
                for r in csv.DictReader(f) if (r.get("name") or "").strip()]


def store_entries(entries: Iterable[dict[str, str]], lists: Iterable[str]) -> int:
    rows = [(e["list"], e["ref"], e["name"][:200], norm(e["name"]), e["dob"], e["kind"]) for e in entries]
    with _db() as c:
        for name in set(lists):
            c.execute("DELETE FROM sanctions_entries WHERE list = ?", (name,))
        c.executemany("INSERT INTO sanctions_entries VALUES (?,?,?,?,?,?)", [r for r in rows if r[3]])
        c.execute("INSERT OR REPLACE INTO screening_meta VALUES ('lists_updated', ?)",
                  (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    return len(rows)


def refresh() -> dict[str, Any]:
    """Download today's lists. A list that fails to download keeps yesterday's copy."""
    got: dict[str, Any] = {}
    try:
        r = requests.get(UN_URL, timeout=120)
        r.raise_for_status()
        got["UN"] = store_entries(parse_un(r.content), ["UN"])
    except (requests.RequestException, ET.ParseError) as e:
        got["UN"] = f"failed ({type(e).__name__}) - kept the previous copy"
    try:
        sdn = requests.get(OFAC_URLS["sdn"], timeout=120)
        sdn.raise_for_status()
        alt = requests.get(OFAC_URLS["alt"], timeout=120)
        got["OFAC"] = store_entries(parse_ofac(sdn.text, alt.text if alt.ok else ""), ["OFAC"])
    except requests.RequestException as e:
        got["OFAC"] = f"failed ({type(e).__name__}) - kept the previous copy"
    extra = os.getenv("JARVIS_SANCTIONS_EXTRA_CSV", "").strip()
    if extra and os.path.exists(extra):
        rows = parse_extra(extra)
        got["EXTRA"] = store_entries(rows, {r["list"] for r in rows})
    audit.log("screening", "lists_refreshed", got)
    return got


def loaded() -> bool:
    with _db() as c:
        return bool(c.execute("SELECT COUNT(*) FROM sanctions_entries").fetchone()[0])


# ------------------------------------------------------------------ matching
def _dob_agrees(client_dob: str, list_dob: str) -> bool | None:
    """True / False when both are known, None when either is missing."""
    if not client_dob or not list_dob:
        return None
    years = re.findall(r"(?:19|20)\d{2}", list_dob)
    return bool(years) and client_dob[:4] in years


def match(name: str, dob: str = "", limit: int = 5) -> list[dict[str, Any]]:
    n = norm(name)
    if not n:
        return []
    toks = n.split()
    with _db() as c:
        # candidates share at least one name token: cheap pre-filter before the fuzzy comparison
        q = " OR ".join("(' ' || norm || ' ') LIKE ?" for _ in toks)
        cands = c.execute(f"SELECT * FROM sanctions_entries WHERE {q}", [f"% {t} %" for t in toks]).fetchall()  # noqa: S608
    out = []
    for e in cands:
        s = similarity(n, e["norm"])
        if s < THRESHOLD:
            continue
        agrees = _dob_agrees(dob, e["dob"])
        if agrees is False and s < 0.99:
            continue  # same-ish name, different birth year: not this person
        level = "strong" if s >= STRONG and agrees is not False else "possible"
        out.append({"list": e["list"], "ref": e["ref"], "entry": e["name"], "score": round(s, 3), "dob": e["dob"],
                    "dob_agrees": agrees, "level": level})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def client_columns(cols: dict[str, str]) -> dict[str, str | None] | None:
    pick = lambda *names: next((orig for low, orig in cols.items() if any(n == low or n in low for n in names)), None)  # noqa: E731
    m = {"customer": pick("customer_no", "customer id", "client id", "account"),
         "first": pick("first_name", "first name", "firstname", "given name"),
         "last": pick("last_name", "last name", "lastname", "surname", "family name"),
         "full": pick("full_name", "full name", "client name", "customer name"),
         "dob": pick("date_of_birth", "date of birth", "birth_date", "dob", "birthday"),
         "pep": pick("pep", "politically exposed")}
    return m if m["customer"] and ((m["first"] and m["last"]) or m["full"]) else None


def screen_frame(d: Any, source: str = "export") -> dict[str, Any]:
    """Screen every client in a DataFrame; open a case per match. Names are not stored."""
    from jarvis.agents import compliance as co

    m = client_columns({str(c).lower().strip(): c for c in d.columns})
    if not m:
        return {"error": "no customer number and name columns"}
    with _db() as c:
        have_lists = c.execute("SELECT COUNT(*) FROM sanctions_entries").fetchone()[0]
    screened, hits, peps, seen = 0, [], 0, set()
    for _, r in d.iterrows():
        cust = str(r[m["customer"]]).strip()
        if cust in seen:   # the KYC export has one row per document
            continue
        seen.add(cust)
        name = str(r[m["full"]]) if m["full"] else f"{r[m['first']]} {r[m['last']]}"
        if not cust or cust.lower() == "nan" or name.lower().replace("nan", "").strip() == "":
            continue
        dob = str(r[m["dob"]])[:10] if m["dob"] and str(r[m["dob"]]).lower() != "nan" else ""
        screened += 1
        if have_lists:
            for h in match(name, dob, limit=3):
                rule = "SANCTIONS_STRONG" if h["level"] == "strong" else "SANCTIONS_POSSIBLE"
                cid = co.open_case("SANCTIONS", rule, "high", cust,
                                   f"{cust} {h['level']} match ({h['score']:.0%}) to {h['list']} entry {h['ref']} "
                                   f"'{h['entry']}'" + ("" if h["dob_agrees"] is None else
                                                        f", date of birth {'agrees' if h['dob_agrees'] else 'differs'}")
                                   + " - check identity documents before any further transaction",
                                   {k: h[k] for k in ("list", "ref", "entry", "score", "dob")}, period=f"{h['list']}:{h['ref']}")
                if cid:
                    hits.append(cid)
        if m["pep"] and str(r[m["pep"]]).strip().lower() in ("yes", "y", "true", "1", "pep"):
            if co.open_case("SANCTIONS", "PEP_DECLARED", "medium", cust, f"{cust} is marked as a PEP in the CRM - "
                            "enhanced due diligence: source of wealth, senior approval, ongoing monitoring",
                            {}, period="pep"):
                peps += 1
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO screening_meta VALUES ('last_screen', ?)",
                  (f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}|{screened}|{len(hits)}|{source}",))
    audit.log("screening", "clients_screened", {"screened": screened, "new_matches": len(hits), "pep": peps,
                                                "source": source, "lists_loaded": bool(have_lists)})
    return {"screened": screened, "new_cases": hits, "pep_cases": peps, "lists_loaded": bool(have_lists)}


def status_text() -> str:
    with _db() as c:
        counts = c.execute("SELECT list, COUNT(*) FROM sanctions_entries GROUP BY list").fetchall()
        meta = {r[0]: r[1] for r in c.execute("SELECT * FROM screening_meta")}
    from jarvis.agents import compliance as co

    open_hits = [x for x in co.list_cases() if x["category"] == "SANCTIONS"]
    L = ["SANCTIONS & PEP SCREENING",
         " Lists: " + (", ".join(f"{r[0]} {r[1]:,} names" for r in counts) or "not downloaded yet")
         + (f" (updated {meta['lists_updated'][:16]} UTC)" if "lists_updated" in meta else "")]
    if "last_screen" in meta:
        ts, n, hits, src = (meta["last_screen"].split("|") + ["", "", "", ""])[:4]
        L.append(f" Last client screening: {ts[:16]} UTC, {n} clients, {hits} new match(es) ({src})")
    else:
        L.append(" No client list screened yet - schedule the CRM customer export (number, names, date of birth, PEP)")
    L.append(f" Open screening cases: {len(open_hits)}" + ("".join(f"\n  #{x['id']} {x['rule']} {x['subject']}"
                                                                  for x in open_hits[:10])))
    L.append(" Check one name: !screen <full name> [YYYY-MM-DD]")
    return "\n".join(L)


def command(cmd: str, rest: str) -> str | None:
    if cmd == "!screening":
        return status_text()
    if cmd != "!screen":
        return None
    m = re.match(r"\s*(.+?)\s*(\d{4}-\d{2}-\d{2})?\s*$", rest)
    if not m or not m.group(1).strip():
        return "Usage: !screen <full name> [date of birth YYYY-MM-DD]"
    with _db() as c:
        if not c.execute("SELECT COUNT(*) FROM sanctions_entries").fetchone()[0]:
            return "The sanctions lists haven't been downloaded yet - they load with the next daily refresh."
    hits = match(m.group(1), m.group(2) or "")
    audit.log("screening", "manual_check", {"hits": len(hits)})  # the name itself is not logged
    if not hits:
        return "No match on the loaded lists (UN, OFAC). Record the check in the client file."
    return "Possible matches - check identity documents before onboarding:\n" + "\n".join(
        f" {h['level'].upper()} {h['score']:.0%}  {h['list']} {h['ref']}: {h['entry']}"
        + (f" (born {h['dob']})" if h["dob"] else "") for h in hits)
