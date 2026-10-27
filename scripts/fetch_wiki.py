"""Pobiera całą wiki Path of Exile (artykuły) do data/wiki.db i buduje indeks. Uruchom na swoim komputerze:

    uv run python scripts/fetch_wiki.py            # pobranie + indeks (można przerwać i wznowić)
    uv run python scripts/fetch_wiki.py --index    # tylko przebudowa indeksu z już pobranych stron

Źródła w kolejności: poewiki.net, potem pathofexile.fandom.com (MediaWiki API). Zapytania idą po jednym, z przerwą,
żeby nie obciążać serwisów. Treść wiki to CC BY-NC-SA; baza jest tylko do użytku lokalnego.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import wiki  # noqa: E402

SOURCES = [("poewiki", "https://www.poewiki.net/w/api.php"), ("fandom", "https://pathofexile.fandom.com/api.php")]
UA = "AIPoeTool/1.0 (lokalna baza wiedzy do uzytku osobistego)"
PARAMS = {"action": "query", "generator": "allpages", "gapnamespace": "0", "gaplimit": "50", "gapfilterredir": "nonredirects",
          "prop": "revisions", "rvprop": "content", "rvslots": "main", "format": "json", "formatversion": "2"}


def fetch(source: str, url: str, c) -> int:
    key = f"cont_{source}"
    row = c.execute("SELECT v FROM progress WHERE k=?", (key,)).fetchone()
    cont = {"gapcontinue": row[0]} if row and row[0] else {}
    total = c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as http:
        while True:
            r = http.get(url, params={**PARAMS, **cont})
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                raise RuntimeError(f"{source}: HTTP {r.status_code} (serwis odrzucił zapytanie)")
            d = r.json()
            for p in d.get("query", {}).get("pages", []):
                revs = p.get("revisions") or []
                if revs:
                    c.execute("INSERT OR REPLACE INTO pages VALUES (?,?)", (p["title"], revs[0]["slots"]["main"]["content"]))
                    total += 1
            nxt = (d.get("continue") or {}).get("gapcontinue")
            c.execute("INSERT OR REPLACE INTO progress VALUES (?,?)", (key, nxt or ""))
            c.commit()
            print(f"\r{source}: {total} stron", end="", flush=True)
            if not nxt:
                print()
                return total
            cont = {"gapcontinue": nxt, **{k: v for k, v in (d.get("continue") or {}).items() if k != "gapcontinue"}}
            time.sleep(1.0)


def main() -> None:
    c = wiki.connect()
    if "--index" not in sys.argv:
        done = False
        for name, url in SOURCES:
            try:
                fetch(name, url, c)
                done = True
                break
            except Exception as e:  # noqa: BLE001
                print(f"\n{e}")
        if not done:
            sys.exit("Nie udało się pobrać wiki z żadnego źródła. Sprawdź internet i uruchom ponownie (pobieranie się wznowi).")
    print("Buduję indeks…")
    n = wiki.build_index(c, lambda i, n: print(f"\r  strony: {i}, fragmenty: {n}", end="", flush=True))
    c.execute("VACUUM")
    print(f"\nGotowe: {n} fragmentów w data/wiki.db")


if __name__ == "__main__":
    main()
