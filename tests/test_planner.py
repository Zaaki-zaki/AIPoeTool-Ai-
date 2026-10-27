import httpx
import pytest

from app.discovery.fetch import Fetcher
from app.gamedata import GameDB
from app.planner.research import ResearchError, research_meta
from app.planner.build_xml import make_xml
from tests.test_agent import DB_PATH, needs_db
from tests.test_engine import needs_engine

SEARCH = {"items": [
    {"id": {"videoId": "a"}, "snippet": {"title": "Cyclone Juggernaut League Starter - 3.28"}},
    {"id": {"videoId": "b"}, "snippet": {"title": "Cyclone Berserker easy start 3.28"}},
    {"id": {"videoId": "c"}, "snippet": {"title": "Wymyslony Skill Gem build 3.28"}},
    {"id": {"videoId": "d"}, "snippet": {"title": "Fireball Elementalist budget 3.27"}}]}
VIDEOS = {"items": [{"id": "a", "statistics": {"viewCount": "30000"}}, {"id": "b", "statistics": {"viewCount": "10000"}},
                    {"id": "c", "statistics": {"viewCount": "99999"}}, {"id": "d", "statistics": {"viewCount": "10000"}}]}


def fetcher(tmp_path):
    def h(req):
        return httpx.Response(200, json=SEARCH if req.url.path.endswith("/search") else VIDEOS)
    return Fetcher(tmp_path, client=httpx.Client(transport=httpx.MockTransport(h)))


@needs_db
def test_research_ranks_by_views_and_verifies_names(tmp_path):
    r = research_meta(fetcher(tmp_path), GameDB(DB_PATH), "melee", ["Juggernaut", "Berserker", "Elementalist"],
                      version="3.28", league="Mirage", key="k")
    top = r["ranking"][0]
    # film o wersji 3.27 odpada: zakres wyznacza wersja gry, nie data publikacji
    assert top["skill"] == "Cyclone" and top["ascendancy"] == "Juggernaut" and top["views"] == 30000 and top["views_share_pct"] == 75.0
    assert r["videos_in_version"] == 3 and r["version"] == "3.28"
    assert all(x["skill"] != "Fireball" for x in r["ranking"])
    assert all(x["skill"] != "Wymyslony Skill" for x in r["ranking"])
    assert "NIEZWERYFIKOWANE" in r["label"]


@needs_db
def test_research_without_key_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(ResearchError, match="YOUTUBE_API_KEY"):
        research_meta(fetcher(tmp_path), GameDB(DB_PATH), "melee", [])


def test_make_xml_is_wellformed():
    import xml.dom.minidom as md
    x = make_xml(class_id=1, class_name="Marauder", asc_id=0, asc_name="None", level=5, tree_version="3_29",
                 nodes=[1, 2], gems=[{"name": 'Cyc"lone', "level": 3}])
    md.parseString(x)


@needs_db
@needs_engine
def test_leveling_plan_is_deterministic_and_connected():
    from app.calc import PobEngine
    from app.planner.leveling import Planner
    db = GameDB(DB_PATH)
    with PobEngine() as e:
        a = Planner(e, db, "Witch", None, "Fireball", last_section=1).run()
        b = Planner(e, db, "Witch", None, "Fireball", last_section=1).run()
    assert [s["id"] for s in a["steps"]] == [s["id"] for s in b["steps"]]
    assert a["steps"][0]["point"] == 1 and len({s["id"] for s in a["steps"]}) == len(a["steps"])
    assert any(s["last"] for s in a["steps"])
    sec = a["sections"][0]
    assert sec["name"] == "Akt 1" and sec["points_total"] == 12 + 2  # poziom 13 -> 12 punktów + 2 z questów Aktu 1
    assert a["main_points"] == sec["points_total"] and sec["gear"]


@needs_db
@needs_engine
def test_gear_uses_only_mods_available_at_level_and_matching_damage():
    from app.calc import PobEngine
    from app.planner.gear import GearBuilder, GearData
    data = GearData()
    base = next(n for n, b in data.bases.items() if b["type"] == "Two Handed Axe" and (b.get("req") or {}).get("level", 0) <= 20)
    mods = data.candidates(base, 20, "physical")
    assert mods and all(m["level"] <= 20 for m in mods)
    assert not any({"fire", "cold", "lightning", "chaos"} & set(m["tags"]) and "damage" in m["tags"] for m in mods)
    assert any("Physical" in " ".join(m["text"]) for m in mods)


def test_quest_points_match_sources():
    from app.planner.leveling import SECTIONS
    assert sum(p for _, _, qs in SECTIONS for _, p in qs) == 24


@needs_db
def test_research_requires_asked_skill_in_title(tmp_path):
    r = research_meta(fetcher(tmp_path), GameDB(DB_PATH), "Cyclone", ["Juggernaut", "Berserker"], version="3.28", key="k")
    assert r["required_in_title_or_description"] == ["Cyclone"]
    assert {x["skill"] for x in r["ranking"]} == {"Cyclone"} and r["videos_in_version"] == 2


def test_guide_and_pob_code():
    import base64, zlib
    from app.planner.guide import guide_markdown, pob_code
    from app.planner.gear import GearData
    xml = '<PathOfBuilding><Build level="1"/></PathOfBuilding>'
    code = pob_code(xml)
    assert zlib.decompress(base64.b64decode(code.replace("-", "+").replace("_", "/"))).decode() == xml
    assert "starej wersji" in guide_markdown({"meta": {"skill": "X", "class_name": "A", "asc_name": "None", "tree_version": "3_29"}}, None, None)
    d = GearData()
    assert all("Modifier" not in (d.bases[b].get("implicit") or "") for b in d.best_bases("Ring", 60) + d.best_bases("Amulet", 60))
