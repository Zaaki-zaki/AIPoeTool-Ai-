"""Warstwa odkrywania: nazwy weryfikowane w bazie gry, dane ze społeczności zawsze jako niezweryfikowane."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.gamedata import GameDB

from .fetch import Fetcher
from .sources import NinjaPrices, RedditNotes, Source, WikiNotes

LABEL = "NIEZWERYFIKOWANE: dane ze społeczności, PoB ich nie liczy ani nie potwierdza"
MAX_NAMES = 6


class Discovery:
    def __init__(self, sources: list[Source], fetcher: Fetcher | None = None) -> None:
        self.sources = sources
        self.fetcher = fetcher

    @classmethod
    def default(cls, cache_dir: Path | str | None = None) -> "Discovery":
        f = Fetcher(cache_dir)
        return cls([NinjaPrices(f), WikiNotes(f), RedditNotes(f)], f)


def _canonical(db: GameDB, name: str) -> str | None:
    name = name.strip()
    variants = [name]
    if name.lower().endswith(" support"):
        variants.append(name[:-8].strip())
    else:
        variants.append(name + " Support")
    for v in variants:
        g = db.gem(v)
        if g:
            return g["name"]
        u = db.unique(v)
        if u:
            return u[0]["name"]
    return None


def community_lookup(disc: Discovery, db: GameDB, names: list[str]) -> dict[str, Any]:
    """Dla nazw z bazy (gemy, uniki) zbiera ceny i opisy. Nieznane nazwy odrzuca (nie zgadujemy)."""
    verified: list[str] = []
    unknown: list[str] = []
    for n in names[:MAX_NAMES * 2]:
        c = _canonical(db, str(n))
        if c and c not in verified:
            verified.append(c)
        elif not c:
            unknown.append(str(n))
    verified = verified[:MAX_NAMES]
    items: dict[str, dict[str, Any]] = {n: {} for n in verified}
    used: list[str] = []
    skipped: list[str] = []
    if disc.fetcher:
        disc.fetcher.errors.clear()
    for src in disc.sources:
        if not src.available():
            skipped.append(f"{src.name} (brak konfiguracji)")
            continue
        try:
            found = src.lookup(verified)
        except Exception as e:  # noqa: BLE001 - jedno zepsute źródło nie może wywrócić odpowiedzi
            skipped.append(f"{src.name} (błąd: {type(e).__name__})")
            continue
        if not found:
            skipped.append(f"{src.name} (brak danych lub źródło niedostępne)")
            continue
        used.append(src.name)
        key = "price" if "ceny" in src.name else "wiki" if "wiki" in src.name else "reddit"
        for n, data in found.items():
            items[n][key] = data
    return {
        "label": LABEL, "items": [{"name": n, **d} for n, d in items.items()],
        "unknown_names": unknown, "sources_used": used, "sources_skipped": skipped,
        "network_errors": (disc.fetcher.errors[:3] if disc.fetcher else []),
        "rule": "Nie podawaj tych danych jako faktów o grze. Ceny są orientacyjne (liga w polu league). "
                "Liczby obrażeń nadal pochodzą wyłącznie z PoB.",
    }
