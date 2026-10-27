"""Research popularności buildów: YouTube Data API (poradniki), nazwy weryfikowane w bazie gry.

Zakres wyznaczają wersja gry i liga (np. 3.29 + nazwa ligi z poe.ninja), nie okno czasowe: film jest brany pod uwagę
tylko wtedy, gdy jego tytuł albo opis wspomina tę wersję lub ligę. To przybliżenie (wyświetlenia), nie statystyka graczy. Wymaga klucza
YOUTUBE_API_KEY (darmowy, YouTube Data API v3). Bez klucza zwraca czytelny błąd zamiast zgadywać.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any

from app.discovery.fetch import Fetcher

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
MAX_RESULTS = 50
# aktywne gemy, których nazwa jest zwykłym słowem i dałaby fałszywe trafienia w tytułach
STOPWORDS = {"Arc", "Blink", "Flicker Strike", "Vitality", "Grace", "Haste", "Clarity", "Determination"}


class ResearchError(RuntimeError):
    pass


def skill_names(db: Any) -> list[str]:
    rows = db.con.execute("SELECT name FROM gems WHERE is_support = 0 AND is_vaal = 0").fetchall()
    return sorted({r[0] for r in rows if len(r[0]) >= 4 and r[0] not in STOPWORDS}, key=len, reverse=True)


def _mentions(title: str, names: list[str]) -> list[str]:
    low = title.lower()
    found = []
    for n in names:
        if re.search(r"(?<![a-z])" + re.escape(n.lower()) + r"(?![a-z])", low) and not any(n in f for f in found):
            found.append(n)
    return found


NINJA_LEAGUES = "https://poe.ninja/poe1/api/economy/leagues"


def game_context(fetcher: Fetcher, db: Any) -> dict[str, Any]:
    """Aktualna wersja gry (najnowsze drzewko w bazie, np. 3_29 -> 3.29) i liga tymczasowa wg poe.ninja."""
    version = db.meta()["tree_versions"].split(",")[-1].replace("_", ".")
    league, start = os.environ.get("AIPOE_LEAGUE"), None
    data = fetcher.get_json(NINJA_LEAGUES, {}, ttl=6 * 3600) or {}
    rows = data.get("leagues") if isinstance(data, dict) else data
    for row in rows or []:
        name = row.get("id") or row.get("name") or ""
        if not name or name == "Standard" or name.startswith(("Hardcore", "SSF", "Ruthless")) and not league:
            continue
        if league and league.lower() != name.lower():
            continue
        league = name
        start = row.get("startAt") or row.get("startDate") or row.get("start")
        break
    return {"version": version, "league": league, "league_start": start}


def _relevant(text: str, version: str, league: str | None) -> bool:
    low = text.lower()
    return version in low or bool(league and league.lower() in low)


def research_meta(fetcher: Fetcher, db: Any, query: str, ascendancies: list[str], *, version: str | None = None,
                  league: str | None = None, key: str | None = None) -> dict[str, Any]:
    key = key or os.environ.get("YOUTUBE_API_KEY", "")
    if not key:
        raise ResearchError("Brak klucza YOUTUBE_API_KEY. Wpisz go w pliku .env w katalogu repo (YOUTUBE_API_KEY=...) "
                            "i uruchom aplikację ponownie.")
    ctx = game_context(fetcher, db)
    version = version or ctx["version"]
    league = league or ctx["league"]
    # Szukamy tego, o co użytkownik pyta: "league starter" dokładamy tylko, gdy o to prosi; nazwa skilla/ascendancy
    # z zapytania jest wymagana w tytule lub opisie filmu.
    starter = bool(re.search(r"league\s*start|starter|\bls\b", query, re.I))
    wanted_skills = _mentions(query, skill_names(db))
    wanted = wanted_skills + [a for a in ascendancies if a and a.lower() in query.lower()]
    base = " ".join(f'"{w}"' if w in wanted else w for w in query.split()) or query
    ver_league = f"{version} {league or ''}".strip()
    plans = [  # (zapytanie, kolejność, liczba stron): różne ujęcia, bo jedno wyszukiwanie zwraca za mało filmów z danej wersji
        (f"path of exile {ver_league} {base}", "relevance", 2),
        (f"path of exile {base} {version} build guide", "viewCount", 1),
        (f"poe {version} {base} {'league start' if starter else 'guide'}", "relevance", 1),
    ]
    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    ok = False
    for q, order, pages in plans:
        token = None
        for _ in range(pages):
            params = {"part": "snippet", "type": "video", "order": order, "maxResults": MAX_RESULTS, "key": key, "q": q}
            if token:
                params["pageToken"] = token
            if ctx.get("league_start") and ctx["league"] == league:
                params["publishedAfter"] = str(ctx["league_start"])[:10] + "T00:00:00Z"
            found = fetcher.get_json(SEARCH_URL, params, ttl=12 * 3600)
            if found is None:
                break
            ok = True
            for it in found.get("items") or []:
                vid = it.get("id", {}).get("videoId")
                if vid and vid not in seen:
                    seen.add(vid); all_items.append(it)
            token = found.get("nextPageToken")
            if not token:
                break
    if not ok:
        raise ResearchError("YouTube nie odpowiada lub klucz jest nieprawidłowy: " + "; ".join(fetcher.errors[-1:]))

    def text(it: dict[str, Any]) -> str:
        return it["snippet"]["title"] + " " + it["snippet"].get("description", "")

    items = [it for it in all_items if _relevant(text(it), version, league)
             and all(w.lower() in text(it).lower() for w in wanted)]
    ids = [it["id"]["videoId"] for it in items]
    stats_items: list[dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        part = fetcher.get_json(VIDEOS_URL, {"part": "statistics", "id": ",".join(ids[i:i + 50]), "key": key},
                                ttl=12 * 3600) or {}
        stats_items += part.get("items", [])
    stats = {"items": stats_items}
    views = {v["id"]: int(v.get("statistics", {}).get("viewCount", 0)) for v in stats.get("items", [])}
    names = skill_names(db)
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    total = 0
    for it in items:
        vid = it["id"]["videoId"]
        title = it["snippet"]["title"]
        sk = _mentions(title, names)
        asc = [a for a in ascendancies if a and a.lower() in title.lower()]
        if not sk:
            continue
        v = views.get(vid, 0)
        total += v
        for s in sk[:1]:  # tytuł opisuje jeden główny skill
            row = agg.setdefault((s, asc[0] if asc else ""), {"skill": s, "ascendancy": asc[0] if asc else None,
                                                              "videos": 0, "views": 0, "examples": []})
            row["videos"] += 1
            row["views"] += v
            if len(row["examples"]) < 2:
                row["examples"].append({"title": title, "views": v, "url": f"https://www.youtube.com/watch?v={vid}"})
    rows = sorted(agg.values(), key=lambda r: r["views"], reverse=True)[:8]
    for r in rows:
        r["views_share_pct"] = round(100 * r["views"] / total, 1) if total else None
    return {"query": query, "label": "NIEZWERYFIKOWANE: popularność wg wyświetleń poradników na YouTube, nie statystyka graczy",
            "version": version, "league": league, "videos_found": len(all_items), "videos_in_version": len(items), "required_in_title_or_description": wanted, "videos_matched": sum(r["videos"] for r in agg.values()),
            "ranking": rows,
            "note": "Skille rozpoznane w tytułach i sprawdzone w bazie gry; udział = część wyświetleń dopasowanych filmów."}
