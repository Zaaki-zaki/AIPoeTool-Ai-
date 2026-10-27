"""Źródła społeczności. Wszystkie zwracają dane opisane jako NIEZWERYFIKOWANE; liczby PoB zostają nietknięte.

Ścieżki i pola API zapisano według dokumentacji/pamięci i nie były testowane na żywo z chmury (allowlista):
parsery są pokryte testami na przykładowych odpowiedziach, a działanie na żywo sprawdza scripts/diag_discovery.py.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Protocol

from .fetch import Fetcher

NINJA_URL = "https://poe.ninja/poe1/api/economy/stash/current/item/overview"
NINJA_LEAGUES = "https://poe.ninja/poe1/api/economy/leagues"
WIKI_URL = "https://www.poewiki.net/w/api.php"
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_SEARCH_URL = "https://oauth.reddit.com/r/pathofexile/search"


class Source(Protocol):
    name: str

    def available(self) -> bool: ...
    def lookup(self, names: list[str]) -> dict[str, dict[str, Any]]: ...


class NinjaPrices:
    """Ceny z udokumentowanego API ekonomii poe.ninja (nie statystyki buildów: te nie są do użytku zewnętrznego)."""
    name = "poe.ninja (ceny)"
    TYPES = ("SkillGem", "UniqueArmour", "UniqueWeapon", "UniqueAccessory", "UniqueJewel", "UniqueFlask")

    def __init__(self, fetcher: Fetcher, league: str | None = None) -> None:
        self.f = fetcher
        self._league = league or os.environ.get("AIPOE_LEAGUE")

    @property
    def league(self) -> str:
        """Z AIPOE_LEAGUE albo pierwsza liga z /economy/leagues (wg dokumentacji: bieżąca liga tymczasowa)."""
        if not self._league:
            data = self.f.get_json(NINJA_LEAGUES, ttl=24 * 3600)
            first = data[0] if isinstance(data, list) and data else {}
            self._league = first.get("id") or first.get("name") or "Standard"
        return self._league

    def available(self) -> bool:
        return True

    def lookup(self, names: list[str]) -> dict[str, dict[str, Any]]:
        want = {n.lower(): n for n in names}
        want.update({n.lower() + " support": n for n in names})  # w bazie supporty bez sufiksu, na poe.ninja z nim
        out: dict[str, dict[str, Any]] = {}
        for typ in self.TYPES:
            if len(out) == len(names):
                break
            data = self.f.get_json(NINJA_URL, {"league": self.league, "type": typ})
            for line in (data or {}).get("lines", []):
                key = str(line.get("name", "")).lower()
                if key not in want:
                    continue
                # gemy mają warianty poziom/jakość/korupcja: bierzemy najtańszy 20/0 lub pierwszy wiersz
                row = {"chaos": line.get("chaosValue"), "divine": line.get("divineValue"),
                       "listings": line.get("listingCount"), "level": line.get("gemLevel"),
                       "quality": line.get("gemQuality"), "links": line.get("links"), "league": self.league}
                prev = out.get(want[key])
                if prev is None or _gem_rank(row) < _gem_rank(prev):
                    out[want[key]] = row
        return out


def _gem_rank(row: dict[str, Any]) -> tuple[int, float]:
    lvl = row.get("level") or 0
    return (0 if lvl in (0, 20) and not (row.get("quality") or 0) else 1, row.get("chaos") or 0)


class WikiNotes:
    """Wstęp z poewiki.net (MediaWiki API, wtyczka TextExtracts). Opis pisany przez społeczność."""
    name = "poewiki.net"

    def __init__(self, fetcher: Fetcher) -> None:
        self.f = fetcher

    def available(self) -> bool:
        return True

    def lookup(self, names: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for n in names:
            data = self.f.get_json(WIKI_URL, {
                "action": "query", "prop": "extracts", "explaintext": 1, "exintro": 1, "exlimit": 1, "exchars": 900,
                "titles": n, "redirects": 1, "format": "json", "formatversion": 2,
            })
            for page in ((data or {}).get("query") or {}).get("pages", []):
                text = (page.get("extract") or "").strip()
                if text and not page.get("missing"):
                    out[n] = {"text": text, "url": "https://www.poewiki.net/wiki/" + str(page.get("title", n)).replace(" ", "_")}
        return out


class RedditNotes:
    """Posty z r/pathofexile przez oficjalne API Reddita. Wymaga własnych kluczy (REDDIT_CLIENT_ID/SECRET)."""
    name = "reddit r/pathofexile"

    def __init__(self, fetcher: Fetcher) -> None:
        self.f = fetcher
        self.cid = os.environ.get("REDDIT_CLIENT_ID", "")
        self.secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self._token: tuple[str, float] | None = None

    def available(self) -> bool:
        return bool(self.cid and self.secret)

    def _auth(self) -> str | None:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        try:
            r = self.f.client.post(REDDIT_TOKEN_URL, auth=(self.cid, self.secret),
                                   data={"grant_type": "client_credentials"})
            r.raise_for_status()
            j = r.json()
            self._token = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
            return self._token[0]
        except Exception as e:  # noqa: BLE001 - źródło opcjonalne
            self.f.errors.append(f"reddit auth: {type(e).__name__}: {str(e)[:100]}")
            return None

    def lookup(self, names: list[str]) -> dict[str, dict[str, Any]]:
        token = self._auth()
        if not token:
            return {}
        out: dict[str, dict[str, Any]] = {}
        for n in names:
            data = self.f.get_json(REDDIT_SEARCH_URL, {
                "q": f'"{n}"', "restrict_sr": 1, "sort": "relevance", "t": "year", "limit": 5, "raw_json": 1,
            }, headers={"Authorization": f"bearer {token}"})
            posts = []
            for ch in ((data or {}).get("data") or {}).get("children", []):
                d = ch.get("data") or {}
                if n.lower() not in (d.get("title", "") + " " + d.get("selftext", "")).lower():
                    continue
                posts.append({"title": d.get("title"), "score": d.get("score"),
                              "excerpt": _excerpt(d.get("selftext", ""), n),
                              "url": "https://www.reddit.com" + str(d.get("permalink", ""))})
            if posts:
                out[n] = {"posts": posts[:3]}
        return out


def _excerpt(text: str, name: str, width: int = 240) -> str:
    text = re.sub(r"\s+", " ", text)
    i = text.lower().find(name.lower())
    if i < 0:
        return text[:width]
    return text[max(0, i - width // 3): i + width].strip()
