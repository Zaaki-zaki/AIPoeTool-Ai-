"""Lokalna baza wiki PoE: pobranie (MediaWiki API), czyszczenie wikitekstu, indeks pełnotekstowy SQLite FTS5 i wyszukiwanie.

Baza leży w data/wiki.db (jest w .gitignore). Powstaje skryptem scripts/fetch_wiki.py uruchamianym na komputerze użytkownika.
Wyszukiwanie używa rankingu BM25 (tytuł > sekcja > treść), więc zwraca fragmenty stron, a nie całe strony.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
DB_FILE = ROOT / "data" / "wiki.db"
TOKEN = re.compile(r"[A-Za-z0-9']{3,}")
STOP = {"the", "and", "for", "with", "how", "what", "does", "work", "works", "are", "this", "that", "from", "you", "can"}

_TPL = re.compile(r"\{\{([^{}]*)\}\}")


def _template(m: re.Match) -> str:
    parts = [p.strip() for p in m.group(1).split("|")]
    name, args = parts[0], parts[1:]
    if name.lower() in ("main", "see also", "further", "clear", "reflist", "stub", "disambig", "dpl", "dplvar"):
        return ""
    vals = []
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            if v.strip() and not k.strip().lower().startswith(("image", "icon", "file", "style", "class")):
                vals.append(f"{k.strip().replace('_', ' ')}: {v.strip()}")
        elif a:
            vals.append(a)
    return f"{name}: " + "; ".join(vals) if vals else name


def clean(wikitext: str) -> str:
    t = re.sub(r"<!--.*?-->", "", wikitext, flags=re.S)
    t = re.sub(r"<ref[^>]*?/>|<ref[^>]*?>.*?</ref>", "", t, flags=re.S)
    for _ in range(6):  # szablony zagnieżdżone: od najgłębszych
        n = _TPL.sub(_template, t)
        if n == t:
            break
        t = n
    t = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", "", t, flags=re.I)
    t = re.sub(r"\[\[([^\]|]*)\|([^\]]*)\]\]", r"\2", t)
    t = re.sub(r"\[\[([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", t)
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"^[*#:;]+\s*", "- ", t, flags=re.M)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def chunks(title: str, wikitext: str, size: int = 1400) -> Iterator[tuple[str, str]]:
    """Dzieli oczyszczony tekst strony na fragmenty według nagłówków (i długości). Zwraca (sekcja, tekst)."""
    text = clean(wikitext)
    sec, buf = "", []
    parts = re.split(r"^=+\s*(.*?)\s*=+\s*$", text, flags=re.M)
    pieces = [("", parts[0])] + [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    for sec, body in pieces:
        body = body.strip()
        while body:
            cut = body[:size]
            if len(body) > size:
                p = max(cut.rfind("\n"), cut.rfind(". "))
                cut = cut[:p + 1] if p > size // 2 else cut
            yield sec, cut.strip()
            body = body[len(cut):].strip()


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS pages(title TEXT PRIMARY KEY, wikitext TEXT);
        CREATE TABLE IF NOT EXISTS progress(k TEXT PRIMARY KEY, v TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(title, section, body, tokenize='porter unicode61');
    """)
    return c


def build_index(c: sqlite3.Connection, progress: Any = None, prefix: str | None = None) -> int:
    """Buduje indeks fragmentów. prefix: tylko strony o takim początku tytułu (np. '[YouTube]'), reszta indeksu zostaje."""
    if prefix:
        c.execute("DELETE FROM chunks WHERE title LIKE ?", (prefix.replace("%", "") + "%",))
        rows = c.execute("SELECT title, wikitext FROM pages WHERE title LIKE ?", (prefix.replace("%", "") + "%",)).fetchall()
    else:
        c.execute("DELETE FROM chunks")
        rows = c.execute("SELECT title, wikitext FROM pages").fetchall()
    n = 0
    for i, (title, wt) in enumerate(rows):
        if wt.lstrip().lower().startswith("#redirect"):
            continue
        for sec, body in chunks(title, wt):
            if len(body) > 40:
                c.execute("INSERT INTO chunks VALUES (?,?,?)", (title, sec, body)); n += 1
        if i % 2000 == 0:
            c.commit()
            if progress:
                progress(i, n)
    c.commit()
    return n


def search(query: str, k: int = 5, path: Path | None = None, max_chars: int = 900) -> list[dict[str, str]]:
    p = path or DB_FILE
    if not p.exists():
        return []
    words = [w for w in dict.fromkeys(x.lower() for x in TOKEN.findall(query)) if w not in STOP]
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in words)
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        rows = c.execute("SELECT title, section, body FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks, 10.0, 3.0, 1.0) LIMIT ?", (q, k)).fetchall()
        c.close()
    except sqlite3.Error:
        return []
    return [{"title": t + (f" / {s}" if s else ""), "text": b[:max_chars], "source": "wiki"} for t, s, b in rows]
