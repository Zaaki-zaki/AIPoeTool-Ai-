"""Wysokopoziomowe wyceny: podobne itemy ze schowka gry i konkretne seedy Timeless Jewel."""

from __future__ import annotations

import statistics
from typing import Any, Callable

from .client import TradeClient
from .item import build_query, parse_item

# id statystyki trade dla conquerora (taka sama nazwa jak w grze; PoB trzyma te same id w LegionTradeIds.lua)
TIMELESS_TRADE_IDS = {
    1: ["explicit.pseudo_timeless_jewel_doryani", "explicit.pseudo_timeless_jewel_xibaqua", "explicit.pseudo_timeless_jewel_ahuana"],
    2: ["explicit.pseudo_timeless_jewel_kaom", "explicit.pseudo_timeless_jewel_rakiata", "explicit.pseudo_timeless_jewel_akoya"],
    3: ["explicit.pseudo_timeless_jewel_asenath", "explicit.pseudo_timeless_jewel_nasima", "explicit.pseudo_timeless_jewel_balbala"],
    4: ["explicit.pseudo_timeless_jewel_avarius", "explicit.pseudo_timeless_jewel_dominus", "explicit.pseudo_timeless_jewel_maxarius"],
    5: ["explicit.pseudo_timeless_jewel_cadiro", "explicit.pseudo_timeless_jewel_victario", "explicit.pseudo_timeless_jewel_caspiro"],
}


def timeless_query(jewel_type: int, seed: int, conqueror: int | None = None, status: str = "securable") -> dict[str, Any]:
    """Zapytanie o jewel o danym seedzie; bez conquerora szuka każdego z trzech (tak robi też PoB)."""
    ids = TIMELESS_TRADE_IDS[jewel_type]
    chosen = [ids[conqueror - 1]] if conqueror in (1, 2, 3) else ids
    return {"query": {"status": {"option": status}, "stats": [{
        "type": "count", "value": {"min": 1},
        "filters": [{"id": i, "value": {"min": seed, "max": seed}} for i in chosen]}]},
        "sort": {"price": "asc"}}


def trade_url(client: TradeClient, query: dict[str, Any]) -> str:
    """Link do tego samego wyszukiwania na stronie trade (do otwarcia w przeglądarce)."""
    import json
    from urllib.parse import quote
    q = dict(query)
    q.setdefault("sort", {"price": "asc"})
    return f"https://www.pathofexile.com/trade/search/{quote(client.league)}?q=" + quote(json.dumps(q, separators=(",", ":")))


def cheapest(listings: list[dict[str, Any]]) -> dict[str, Any] | None:
    priced = [l for l in listings if l.get("price_chaos") is not None]
    if priced:
        return min(priced, key=lambda l: l["price_chaos"])
    return listings[0] if listings else None


def price_timeless(client: TradeClient, candidates: list[dict[str, Any]], jewel_type: int, *, per_seed: int = 3,
                   status: str = "securable", progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any]]:
    """Dla kandydatów z search_timeless ({seed, conqueror_index, ...}) dopisuje najtańsze oferty.

    Zwraca kopie kandydatów z polami: listed (liczba ofert), cheapest {price_text, price_chaos, account, whisper}, offers.
    Błąd trade przerywa pętlę, a już wycenione wyniki zostają (reszta ma pole 'price_error').
    """
    from .client import TradeError

    out: list[dict[str, Any]] = []
    seen: dict[tuple[int, int | None], dict[str, Any]] = {}
    err: str | None = None
    for i, c in enumerate(candidates):
        if progress:
            progress(i, len(candidates))
        row = dict(c)
        key = (c["seed"], c.get("conqueror_index"))
        if err:
            row["price_error"] = err
            out.append(row)
            continue
        if key not in seen:
            try:
                res = client.listings(timeless_query(jewel_type, c["seed"], c.get("conqueror_index"), status), limit=per_seed)
            except TradeError as e:
                err = str(e)
                row["price_error"] = err
                out.append(row)
                continue
            seen[key] = res
        res = seen[key]
        best = cheapest(res["listings"])
        row["listed"] = res["total"]
        row["trade_url"] = trade_url(client, timeless_query(jewel_type, c["seed"], c.get("conqueror_index"), status))
        row["offers"] = res["listings"][:per_seed]
        row["cheapest"] = None if not best else {k: best.get(k) for k in ("price_text", "price_chaos", "account", "whisper", "online")}
        out.append(row)
    if progress:
        progress(len(candidates), len(candidates))
    return out


def best_value(rows: list[dict[str, Any]], *, mode: str = "value", min_gain_frac: float = 0.5) -> list[dict[str, Any]]:
    """Sortowanie wyników z cenami. mode: 'gain' (najlepszy wzrost), 'cheap' (najtańsze z sensownym zyskiem), 'value' (zysk % na chaos)."""
    listed = [r for r in rows if r.get("cheapest") and r["cheapest"].get("price_chaos")]
    unlisted = [r for r in rows if r not in listed]
    gain = lambda r: r.get("delta_pct") or 0  # noqa: E731
    top = max((gain(r) for r in listed), default=0)
    if mode == "gain":
        listed.sort(key=gain, reverse=True)
    elif mode == "cheap":
        ok = [r for r in listed if gain(r) >= top * min_gain_frac]
        rest = [r for r in listed if r not in ok]
        ok.sort(key=lambda r: r["cheapest"]["price_chaos"])
        listed = ok + sorted(rest, key=gain, reverse=True)
    else:
        listed.sort(key=lambda r: gain(r) / max(r["cheapest"]["price_chaos"], 1), reverse=True)
    return listed + sorted(unlisted, key=gain, reverse=True)


def check_item(client: TradeClient, text: str, *, status: str = "securable", min_pct: float = 0.9,
               min_match: int | None = None, overrides: dict[str, dict[str, Any]] | None = None, limit: int = 10) -> dict[str, Any]:
    """Cena podobnych itemów dla tekstu z gry. Zwraca sparsowany item, użyte filtry i oferty."""
    item = parse_item(text)
    needs_stats = item.get("rarity") in ("Rare", "Magic")
    stats = client.stats_index() if needs_stats else {}
    built = build_query(item, stats, status=status, min_pct=min_pct, min_match=min_match, overrides=overrides)
    res = client.listings({"query": built["query"], "sort": {"price": "asc"}}, limit=limit)
    prices = [l["price_chaos"] for l in res["listings"] if l.get("price_chaos") is not None]
    return {"item": item, "filters": built["filters"], "query": built["query"], "league": client.league, "trade_url": trade_url(client, {"query": built["query"]}),
            "total": res["total"], "listings": res["listings"],
            "median_chaos": round(statistics.median(prices), 2) if prices else None,
            "min_chaos": min(prices) if prices else None}
