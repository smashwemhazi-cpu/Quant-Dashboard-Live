"""Ask your own policies: D.O.N.N.A finds the passage that answers a question, with the document and page.

!policy what does our AML policy say about PEPs?    the best 3 passages, each with document and page
!policy list                                       the policies she has read
!policy remove <name>                              forget one
Adding one: in Discord, attach the PDF / Word / text file to a message that says  !policy add

Plain search on the server (SQLite full-text search), no Claude credit needed and nothing leaves the server.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jarvis.core.config import settings

TYPES = (".pdf", ".docx", ".txt", ".md")
MAX_MB = 25
_STOP = {"what", "does", "our", "the", "say", "says", "about", "for", "and", "are", "is", "do", "we", "how", "when",
         "who", "which", "should", "can", "with", "policy", "policies", "a", "an", "of", "to", "in", "on", "i", "my",
         "must", "be", "it", "there", "any", "this", "that", "under", "from", "by", "or", "if", "need", "have"}


def folder() -> Path:
    return Path(os.getenv("JARVIS_POLICY_DIR", str(Path(os.getenv("JARVIS_STATE_DB", settings.state_db)).parent
                                                    / "policies")))


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(os.getenv("JARVIS_STATE_DB", settings.state_db))
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS policy_docs (name TEXT PRIMARY KEY, pages INTEGER, added TEXT)")
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS policy_fts USING fts5(name UNINDEXED, page UNINDEXED, text, "
                  "tokenize='porter unicode61')")
    except sqlite3.OperationalError:        # a SQLite without full-text search: plain table, simpler scoring
        c.execute("CREATE TABLE IF NOT EXISTS policy_fts (name TEXT, page INTEGER, text TEXT)")
    return c


def _fts(c: sqlite3.Connection) -> bool:
    row = c.execute("SELECT sql FROM sqlite_master WHERE name = 'policy_fts'").fetchone()
    return bool(row and "VIRTUAL" in (row[0] or "").upper())


# ------------------------------------------------------------------ reading files
def pages_of(name: str, data: bytes) -> list[str]:
    """Text of each page (PDF) or of the whole document split into ~3000-character 'pages' (Word, text)."""
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]
    if ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        text = re.sub(r"<w:p[ >].*?</w:p>", lambda m: re.sub(r"<[^>]+>", "", m.group(0)) + "\n", xml, flags=re.S)
        text = re.sub(r"<[^>]+>", "", text)
    else:
        text = data.decode("utf-8", "ignore")
    paras, pages, cur = text.split("\n"), [], ""
    for p in paras:
        if len(cur) + len(p) > 3000 and cur:
            pages.append(cur)
            cur = ""
        cur += p + "\n"
    return pages + ([cur] if cur.strip() else [])


def add(name: str, data: bytes) -> str:
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name)[-120:]
    if Path(safe).suffix.lower() not in TYPES:
        return f"{name}: I read policies as PDF, Word (.docx) or text files."
    if len(data) > MAX_MB * 2**20:
        return f"{name}: too big (over {MAX_MB} MB)."
    try:
        pages = pages_of(safe, data)
    except Exception as e:  # noqa: BLE001 - a broken file gets a plain answer
        return f"{name}: I couldn't read it ({type(e).__name__}). If it's a scanned PDF, it has no text to search."
    if not any(p.strip() for p in pages):
        return f"{name}: no text inside - it looks like a scan. Export it as a text PDF or Word file."
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / safe).write_bytes(data)
    with _db() as c:
        c.execute("DELETE FROM policy_fts WHERE name = ?", (safe,))
        c.executemany("INSERT INTO policy_fts (name, page, text) VALUES (?,?,?)",
                      [(safe, i, t) for i, t in enumerate(pages, 1) if t.strip()])
        c.execute("INSERT OR REPLACE INTO policy_docs VALUES (?,?,?)",
                  (safe, len(pages), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return f"Read {safe}: {len(pages)} page{'s' if len(pages) != 1 else ''}. Ask me: !policy <your question>"


def remove(name: str) -> bool:
    with _db() as c:
        row = c.execute("SELECT name FROM policy_docs WHERE lower(name) LIKE ?", (f"%{name.lower().strip()}%",)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM policy_fts WHERE name = ?", (row[0],))
        c.execute("DELETE FROM policy_docs WHERE name = ?", (row[0],))
    (folder() / row[0]).unlink(missing_ok=True)
    return True


def docs() -> list[dict[str, Any]]:
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM policy_docs ORDER BY name")]


# ------------------------------------------------------------------ asking
def terms(question: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{2,}", question.lower()) if w not in _STOP]


def _snippet(text: str, words: list[str], size: int = 450) -> str:
    """The sentence window that holds most of the question's words."""
    sentences = re.split(r"(?<=[.!?;:])\s+|\n{2,}", re.sub(r"[ \t]+", " ", text))
    best, best_score = 0, -1
    for i, _ in enumerate(sentences):
        window = " ".join(sentences[i:i + 3]).lower()
        score = sum(window.count(w[:6]) for w in words)
        if score > best_score:
            best, best_score = i, score
    out = re.sub(r"\s+", " ", " ".join(sentences[best:best + 3])).strip()
    return out[:size] + ("..." if len(out) > size else "")


def search(question: str, limit: int = 3) -> list[dict[str, Any]]:
    words = terms(question)
    if not words:
        return []
    with _db() as c:
        if _fts(c):
            q = " OR ".join(f'"{w}"*' if len(w) > 3 else f'"{w}"' for w in words)
            rows = [dict(r) for r in c.execute(
                "SELECT name, page, text, bm25(policy_fts) AS rank FROM policy_fts WHERE policy_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (q, limit * 4))]
        else:
            rows = [dict(r) for r in c.execute("SELECT name, page, text FROM policy_fts")]
            for r in rows:
                r["rank"] = -sum(r["text"].lower().count(w) for w in words)
            rows = sorted([r for r in rows if r["rank"] < 0], key=lambda r: r["rank"])[:limit * 4]
    # prefer pages that hold more of the different words, then the search rank
    for r in rows:
        r["hits"] = sum(1 for w in words if w[:6] in r["text"].lower())
    rows.sort(key=lambda r: (-r["hits"], r["rank"]))
    return [{"doc": r["name"], "page": r["page"], "text": _snippet(r["text"], words)} for r in rows[:limit]]


def answer_text(question: str) -> str:
    if not docs():
        return ("I haven't read any policies yet. In Discord, attach your policy PDFs or Word files to a message "
                "that says: !policy add")
    hits = search(question)
    if not hits:
        return (f"Nothing in your {len(docs())} policies matches \"{question}\". Try other words, "
                "or !policy list to see what I've read.")
    L = [f"FROM YOUR POLICIES: \"{question}\"", ""]
    for n, h in enumerate(hits, 1):
        L += [f"{n}. {h['doc']}, page {h['page']}", f"   \"{h['text']}\"", ""]
    L.append("These are the passages themselves, not my summary - check the page before you rely on it.")
    return "\n".join(L)


def command(cmd: str, rest: str) -> str | None:
    if cmd != "!policy":
        return None
    sub, _, arg = rest.strip().partition(" ")
    if not rest.strip():
        return "Ask a question: !policy what does our AML policy say about PEPs?   (or: !policy list)"
    if sub.lower() == "list" and not arg:
        ds = docs()
        return ("Policies I've read:\n" + "\n".join(f"- {d['name']} ({d['pages']} pages, added {d['added'][:10]})"
                                                    for d in ds)) if ds else "No policies yet. Attach files to: !policy add"
    if sub.lower() == "remove" and arg:
        return f"Forgot {arg}." if remove(arg) else f"No policy called {arg}. See !policy list"
    if sub.lower() == "add":
        return "Attach the policy file(s) to the same message: !policy add  (PDF, Word or text)"
    return answer_text(rest.strip())
