"""Trade: parser itemów, zapytania, klient z limitami i walutami, ceny seedów Timeless Jewel (wszystko na atrapie HTTP)."""

from pathlib import Path

import pytest

from app.trade import TradeClient, TradeError
from app.trade.item import ItemParseError, build_query, normalize_mod, parse_item
from app.trade.pricing import best_value, check_item, price_timeless, timeless_query, trade_url

RARE = """Item Class: Rings
Rarity: Rare
Doom Loop
Amethyst Ring
--------
Requirements:
Level: 49
--------
Item Level: 83
--------
+22% to Chaos Resistance (implicit)
--------
+95 to maximum Life
+32% to Fire Resistance
(10-20)% increased Rarity of Items found
Adds 5 to 10 Fire Damage to Attacks
"""
STATS = {"result": [{"id": "explicit", "entries": [
    {"id": "explicit.stat_life", "type": "explicit", "text": "+# to maximum Life"},
    {"id": "explicit.stat_fire", "type": "explicit", "text": "+#% to Fire Resistance"}]}]}


def fake_http(log=None, status=None):
    def http(method, url, body, headers):
        if log is not None:
            log.append((method, url, body))
        if status and status.get(url.split("?")[0].rsplit("/", 1)[-1]):
            return status[url.split("?")[0].rsplit("/", 1)[-1]], {"retry-after": "1"}, {}
        if "leagues" in url:
            return 200, {}, {"result": [{"id": "Standard", "realm": "pc"}, {"id": "Mirage", "realm": "pc"},
                                         {"id": "Hardcore Mirage", "realm": "pc"}, {"id": "SSF Mirage", "realm": "pc"}]}
        if "poe.ninja" in url:
            return 200, {}, {"lines": [{"currencyTypeName": "Divine Orb", "chaosEquivalent": 150}]}
        if "data/stats" in url:
            return 200, {}, STATS
        if "/search/" in url:
            return 200, {}, {"id": "Q1", "result": ["a", "b"], "total": 2}
        if "/fetch/" in url:
            return 200, {}, {"result": [
                {"id": "a", "listing": {"price": {"amount": 2, "currency": "divine"}, "account": {"name": "x", "online": {}}, "whisper": "@x hi"},
                 "item": {"typeLine": "Amethyst Ring", "explicitMods": ["+9 to Life"]}},
                {"id": "b", "listing": {"price": {"amount": 30, "currency": "chaos"}, "account": {"name": "y"}},
                 "item": {"typeLine": "Amethyst Ring"}}]}
        return 404, {}, None
    return http


def client(tmp_path: Path, **kw) -> TradeClient:
    return TradeClient(http=fake_http(**kw), data_dir=tmp_path, min_gap=0, sleep=lambda s: None)


def test_normalize_mod_drops_values_and_tier_ranges() -> None:
    assert normalize_mod("+95(80-99) to maximum Life") == "# to maximum life"
    assert normalize_mod("(10-20)% increased Rarity of Items found") == "#% increased rarity of items found"
    assert normalize_mod("Adds 5 to 10 Fire Damage to Attacks (crafted)") == "adds # to # fire damage to attacks"


def test_parse_rare_item_sections() -> None:
    it = parse_item(RARE)
    assert (it["name"], it["base"], it["ilvl"], it["rarity"]) == ("Doom Loop", "Amethyst Ring", 83, "Rare")
    assert "gem_level" not in it  # 'Level: 49' to wymaganie, nie poziom gemu
    kinds = {m["norm"]: m["kind"] for m in it["mods"]}
    assert kinds["#% to chaos resistance"] == "implicit" and kinds["# to maximum life"] == "explicit"


def test_parse_advanced_copy_and_gem() -> None:
    it = parse_item('Item Class: Rings\nRarity: Rare\nX\nAmethyst Ring\n--------\nItem Level: 80\n--------\n'
                    '{ Prefix Modifier "Hale" (Tier: 3) — Life }\n+95(80-99) to maximum Life\n')
    assert it["mods"][0]["values"] == [95.0]
    gem = parse_item("Item Class: Skill Gems\nRarity: Gem\nCyclone\n--------\nLevel: 20\nQuality: +20% (augmented)\n")
    assert gem["gem_level"] == 20 and gem["quality"] == 20 and gem["base"] == "Cyclone"


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ItemParseError):
        parse_item("hello world")


def test_build_query_rare_uses_count_and_overrides() -> None:
    it = parse_item(RARE)
    idx = {("explicit", "# to maximum life"): "explicit.stat_life", ("explicit", "#% to fire resistance"): "explicit.stat_fire"}
    q = build_query(it, idx, min_pct=0.9)
    grp = q["query"]["stats"][0]
    assert grp["type"] == "count" and grp["value"]["min"] == 2
    assert {f["id"]: f["value"]["min"] for f in grp["filters"]} == {"explicit.stat_life": 86, "explicit.stat_fire": 29}
    q2 = build_query(it, idx, overrides={"explicit.stat_fire": {"enabled": False}})
    assert [f["id"] for f in q2["query"]["stats"][0]["filters"]] == ["explicit.stat_life"]


def test_build_query_unique_by_name() -> None:
    it = parse_item("Item Class: Belts\nRarity: Unique\nHeadhunter\nLeather Belt\n--------\nItem Level: 84\n")
    q = build_query(it, {})["query"]
    assert q["name"] == "Headhunter" and q["type"] == "Leather Belt"


def test_client_picks_challenge_league_and_converts_currency(tmp_path: Path) -> None:
    c = client(tmp_path)
    assert c.league == "Mirage"
    assert c.to_chaos(2, "divine") == 300 and c.to_chaos(5, "chaos") == 5 and c.to_chaos(1, "mirror") is None


def test_check_item_returns_prices_and_filters(tmp_path: Path) -> None:
    log: list = []
    r = check_item(client(tmp_path, log=log), RARE)
    assert r["total"] == 2 and r["min_chaos"] == 30 and r["median_chaos"] == 165
    assert [f["id"] for f in r["filters"] if f["enabled"]] == ["explicit.stat_life", "explicit.stat_fire"]
    assert any("/search/Mirage" in u for _, u, _ in log) and r["trade_url"].startswith("https://www.pathofexile.com/trade/search/Mirage?q=")
    assert (tmp_path / "trade_stats.json").exists()


def test_rate_limit_and_block_are_reported(tmp_path: Path) -> None:
    with pytest.raises(TradeError, match="403"):
        client(tmp_path, status={"Mirage": 403}).search({"query": {}})
    with pytest.raises(TradeError, match="ogranicza"):
        client(tmp_path, status={"Mirage": 429}).search({"query": {}})


def test_timeless_query_and_pricing(tmp_path: Path) -> None:
    q = timeless_query(2, 12345, 2)
    f = q["query"]["stats"][0]["filters"]
    assert f == [{"id": "explicit.pseudo_timeless_jewel_rakiata", "value": {"min": 12345, "max": 12345}}]
    assert len(timeless_query(1, 7000)["query"]["stats"][0]["filters"]) == 3
    rows = price_timeless(client(tmp_path), [{"seed": 7000, "conqueror_index": 2, "delta_pct": 5.0},
                                              {"seed": 7001, "conqueror_index": None, "delta_pct": 9.0}], 1)
    assert rows[0]["cheapest"]["price_chaos"] == 30 and rows[0]["trade_url"].startswith("https://www.pathofexile.com/trade/search/")
    assert [r["seed"] for r in best_value(rows, mode="gain")] == [7001, 7000]


def test_pricing_survives_trade_failure(tmp_path: Path) -> None:
    rows = price_timeless(client(tmp_path, status={"Mirage": 403}), [{"seed": 1, "delta_pct": 1}, {"seed": 2, "delta_pct": 2}], 1)
    assert all("price_error" in r for r in rows)
    assert [r["seed"] for r in best_value(rows)] == [2, 1]


def test_trade_url_is_encoded(tmp_path: Path) -> None:
    u = trade_url(client(tmp_path), {"query": {"type": "Amethyst Ring"}})
    assert " " not in u and "%22Amethyst%20Ring%22" in u


def test_user_agent_is_ascii():
    from app.trade.client import USER_AGENT
    USER_AGENT.encode("ascii")  # httpx koduje nagłówki jako ASCII
