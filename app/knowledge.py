"""Paczka wiedzy o PoE 1 (data/knowledge.json) + fakty, których użytkownik nauczył aplikację (data/learned.json).

Wyszukiwanie jest proste i deterministyczne (słowa kluczowe, dopasowanie po rdzeniu słowa), bez modelu embeddingów.
Wynik trafia do modelu jako KONTEKST przed odpowiedzią, więc model opiera się na tekście z bazy, a nie na pamięci.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STOP = {"jak", "jest", "czy", "co", "to", "w", "na", "z", "i", "a", "do", "dla", "sie", "się", "mi", "jaki", "jaka", "jakie", "po", "o",
        "the", "of", "and", "is", "what", "how", "mam", "chce", "chcę", "mozna", "można", "ma", "te", "ten", "ta"}


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(c))


def _stems(t: str) -> list[str]:
    return [w[:5] for w in re.findall(r"[a-z0-9']+", _norm(t)) if len(w) > 2 and w not in STOP]


class Knowledge:
    def __init__(self, path: Path | None = None, learned: Path | None = None, wiki_path: Path | None = None) -> None:
        self.wiki_path = wiki_path
        self.path = path or ROOT / "data" / "knowledge.json"
        self.learned_path = learned or ROOT / "data" / "learned.json"
        try:
            self.entries: list[dict[str, Any]] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.entries = []
        self._index = [(set(_stems(e["title"] + " " + e.get("aliases", ""))), set(_stems(e["text"]))) for e in self.entries]

    # --- nauczone fakty
    def learned(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.learned_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def teach(self, text: str) -> int:
        items = self.learned()
        items.append({"text": text.strip(), "ts": int(time.time())})
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        self.learned_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return len(items)

    # --- wyszukiwanie
    def search(self, query: str, k: int = 4, min_score: float = 3.0) -> list[dict[str, Any]]:
        q = set(_stems(query))
        if not q:
            return []
        scored = []
        for e, (head, body) in zip(self.entries, self._index):
            sc = 3.0 * len(q & head) + 0.5 * len(q & body)
            if sc >= min_score:
                scored.append((sc, e))
        for i, f in enumerate(self.learned()):
            hit = len(q & set(_stems(f["text"])))
            if hit >= 1:  # nauczone fakty mają pierwszeństwo
                scored.append((10.0 + hit, {"id": f"learned_{i}", "title": "Poprawka użytkownika", "text": f["text"], "source": "użytkownik"}))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:k]]

    def wiki_hits(self, question: str, hits: list[dict[str, Any]], k: int = 4) -> list[dict[str, Any]]:
        """Fragmenty lokalnej wiki (angielskiej). Pytanie po polsku wzbogacamy angielskimi aliasami trafionych wpisów."""
        from app import wiki
        extra = " ".join(h.get("aliases", "") for h in hits[:2])
        return wiki.search(f"{question} {extra}", k, path=self.wiki_path)

    def context(self, question: str, k: int = 4) -> str:
        hits = self.search(question, k)
        hits = hits + self.wiki_hits(question, hits)
        if not hits:
            return ""
        lines = [f"- [{h['title']}] {h['text']}" for h in hits]
        return ("WIEDZA Z BAZY (pewna; opieraj odpowiedź na niej, nie na pamięci. Jeśli nie wystarcza, powiedz, że nie masz tej informacji):\n"
                + "\n".join(lines))


_TEACH = re.compile(r"^\s*(zapamiętaj|zapamietaj|popraw|nauczę cię|naucze cie|pamiętaj|pamietaj)\s*[:\-]?\s*(.+)$", re.I | re.S)


def parse_teach(question: str) -> str | None:
    m = _TEACH.match(question)
    return m.group(2).strip() if m else None
