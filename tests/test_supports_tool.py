"""Testy recommend_supports. Logika: fałszywy silnik (wymaga tylko data/game.db). Integracja: prawdziwy PoB."""

import math
from pathlib import Path
from typing import Any

import pytest

from app.calc import PobEngine
from app.gamedata import GameDB
from app.parser.models import Build
from app.parser.parse import parse_file
from app.tools.supports import COLOR_LETTER, RecommendError, recommend_supports
from tests.test_engine import _engine_available

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "game.db"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
needs_engine = pytest.mark.skipif(not _engine_available(), reason="brak silnika PoB")


@pytest.fixture(scope="module")
def db() -> GameDB:
    return GameDB(DB_PATH)


class FakeEngine:
    """DPS = 1000 * iloczyn mnożników supportów; wszystko inne stałe. Zapamiętuje stan grupy."""

    def __init__(self, gems: list[dict[str, Any]], mult: dict[str, float], source: str | None = None,
                 skill: str = "Cyclone", ehp: dict[str, float] | None = None) -> None:
        self.current = [dict(g) for g in gems]
        self.mult = mult
        self.ehp = ehp or {}
        self.source = source
        self.skill = skill
        self.calls = 0

    def groups(self) -> list[dict[str, Any]]:
        return [{"index": 1, "slot": "Weapon 1", "source": self.source, "gems": [dict(g) for g in self.current]}]

    def set_main(self, group: int, skill_index: int = 1) -> dict[str, Any]:
        return {"MainSkill": self.skill, "CombinedDPS": self._dps(self.current), "Life": 1000.0,
                "TotalEHP": self._ehp(self.current)}

    def _ehp(self, gems: list[dict[str, Any]]) -> float:
        v = 10000.0
        for g in gems:
            v *= self.ehp.get(g["name"], 1.0)
        return v

    def _dps(self, gems: list[dict[str, Any]]) -> float:
        v = 1000.0
        for g in gems:
            v *= self.mult.get(g["name"], 1.0)
        return v

    def set_group(self, group: int, gems: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        self.current = [dict(g) for g in gems]
        return {"stats": {"CombinedDPS": self._dps(gems), "Life": 1000.0, "TotalEHP": self._ehp(gems)},
                "gem_errors": []}


def _by_color(db: GameDB, skill: str = "Cyclone") -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"R": [], "G": [], "B": [], "W": []}
    for s in db.compatible_supports(skill):
        out[COLOR_LETTER[s["color"]]].append(s["name"])
    return out


CYCLONE = {"name": "Cyclone", "level": 20, "quality": 0}
BUILD = Build(class_name="Duelist", level=100)


@needs_db
def test_fill_picks_best_combination(db: GameDB) -> None:
    c = _by_color(db)
    best_r, other_r, best_g = c["R"][0], c["R"][1], c["G"][0]
    eng = FakeEngine([CYCLONE], {best_r: 1.5, other_r: 1.4, best_g: 1.3, c["G"][1]: 1.1})
    res = recommend_supports(eng, db, BUILD, 1, free_sockets=["R", "G"], pool_size=20)
    assert res["mode"] == "fill"
    top = res["ranking"][0]
    assert set(top["supports"]) == {best_r, best_g}
    assert math.isclose(top["delta_pct"], (1.5 * 1.3 - 1) * 100, abs_tol=0.01)
    values = [r["value"] for r in res["ranking"]]
    assert values == sorted(values, reverse=True)


@needs_db
def test_engine_state_is_restored(db: GameDB) -> None:
    c = _by_color(db)
    eng = FakeEngine([CYCLONE], {c["R"][0]: 1.5})
    before = eng.groups()[0]["gems"]
    recommend_supports(eng, db, BUILD, 1, free_sockets=["R"])
    assert eng.groups()[0]["gems"] == before


@needs_db
def test_color_filter_respects_free_socket_colors(db: GameDB) -> None:
    c = _by_color(db)
    eng = FakeEngine([CYCLONE], {c["B"][0]: 5.0})  # niebieski byłby najlepszy, ale socket jest czerwony
    res = recommend_supports(eng, db, BUILD, 1, free_sockets=["R"], pool_size=50)
    names = {n for r in res["ranking"] for n in r["supports"]}
    assert names <= set(c["R"])
    assert any(x["reason"].startswith("brak wolnego socketu koloru B") for x in res["rejected"])


@needs_db
def test_white_socket_accepts_any_color(db: GameDB) -> None:
    c = _by_color(db)
    eng = FakeEngine([CYCLONE], {c["B"][0]: 5.0})
    res = recommend_supports(eng, db, BUILD, 1, free_sockets=["W"], pool_size=50)
    assert res["ranking"][0]["supports"] == [c["B"][0]]


@needs_db
def test_already_present_support_is_rejected(db: GameDB) -> None:
    present = _by_color(db)["R"][0]
    eng = FakeEngine([CYCLONE, {"name": present, "level": 20, "quality": 0}], {present: 1.5})
    res = recommend_supports(eng, db, BUILD, 1, free_sockets=["W"], pool_size=50)
    assert present not in {n for r in res["ranking"] for n in r["supports"]}
    assert any(x["name"] == present and "już jest" in x["reason"] for x in res["rejected"])


@needs_db
def test_character_level_filter(db: GameDB) -> None:
    eng = FakeEngine([CYCLONE], {})
    res = recommend_supports(eng, db, Build(class_name="Duelist", level=10), 1, free_sockets=["W"],
                             gem_level=20)
    assert any(x["reason"].startswith("wymaga poziomu postaci") for x in res["rejected"])


@needs_db
def test_zero_metric_is_explained(db: GameDB) -> None:
    class ZeroEngine(FakeEngine):
        def _dps(self, gems: list[dict[str, Any]]) -> float:
            return 0.0

    with pytest.raises(RecommendError, match="wynosi 0"):
        recommend_supports(ZeroEngine([CYCLONE], {}), db, BUILD, 1, free_sockets=["R"])


@needs_db
def test_item_granted_group_is_refused(db: GameDB) -> None:
    eng = FakeEngine([CYCLONE], {}, source="Item:1:Foo")
    with pytest.raises(RecommendError, match="pochodzi z itemu"):
        recommend_supports(eng, db, BUILD, 1, free_sockets=["R"])


@needs_db
def test_replace_mode_reports_value_of_each_support(db: GameDB) -> None:
    c = _by_color(db)
    cur, better = c["R"][0], c["R"][1]
    eng = FakeEngine([CYCLONE, {"name": cur, "level": 20, "quality": 0}], {cur: 1.2, better: 1.6})
    res = recommend_supports(eng, db, BUILD, 1)
    assert res["mode"] == "replace"
    row = res["replace_report"][0]
    assert row["current"] == cur
    assert math.isclose(row["value_of_current_pct"], 20.0, abs_tol=0.01)
    assert row["best_replacements"][0]["support"] == better


@needs_db
def test_ehp_floor_keeps_survivability_gem_in_the_ranking(db: GameDB) -> None:
    """Para z wyższym DPS, ale bez gemu dającego EHP, nie może wygrać z parą z tym gemem."""
    c = _by_color(db)
    dps_gem, other, ehp_gem = c["B"][0], c["B"][1], c["B"][2]
    eng = FakeEngine(
        [CYCLONE, {"name": ehp_gem, "level": 20, "quality": 0}],
        {dps_gem: 1.6, other: 1.5, ehp_gem: 1.3},
        ehp={ehp_gem: 1.5},
    )
    # keep=[] zdejmuje ehp_gem i zwalnia jego socket; próg liczony względem pełnego, oryginalnego buildu
    res = recommend_supports(eng, db, BUILD, 1, keep=[], pool_size=3, top_n=3)
    assert res["ranking"][0]["supports"] == [ehp_gem]
    assert any(e["supports"] != [ehp_gem] for e in res["excluded_by_floor"])
    assert "TotalEHP" in res["excluded_by_floor"][0]["reason"]


@needs_db
def test_floor_can_be_disabled(db: GameDB) -> None:
    c = _by_color(db)
    dps_gem, ehp_gem = c["B"][0], c["B"][2]
    eng = FakeEngine([CYCLONE, {"name": ehp_gem, "level": 20, "quality": 0}],
                     {dps_gem: 1.6, ehp_gem: 1.3}, ehp={ehp_gem: 1.5})
    res = recommend_supports(eng, db, BUILD, 1, keep=[], pool_size=3, floor={})
    assert res["ranking"][0]["supports"] == [dps_gem]
    assert res["excluded_by_floor"] == []


@needs_db
def test_blind_spots_are_reported(db: GameDB) -> None:
    """Kandydaci, którym PoB (tu: model) nie daje żadnego zysku, trafiają do listy blind_spots."""
    c = _by_color(db)
    eng = FakeEngine([CYCLONE], {c["R"][0]: 1.5})
    res = recommend_supports(eng, db, BUILD, 1, free_sockets=["R", "G", "B"], pool_size=5)
    assert c["R"][0] not in res["blind_spots"]
    assert len(res["blind_spots"]) > 10


# ------------------------------------------------------------------ prawdziwy PoB
@needs_db
@needs_engine
def test_real_pob_ranking_is_consistent() -> None:
    db = GameDB(DB_PATH)
    for path in FIXTURES:
        build = parse_file(path)
        grp = next((g for g in build.skill_groups
                    if not g.from_item and any(x.name == "Cyclone" for x in g.gems)), None)
        if grp:
            break
    else:
        pytest.skip("żaden fixture nie ma Cyclone")
    first_support = next(g.name for g in grp.gems if g.is_support)
    with PobEngine() as eng:
        eng.load_file(path)
        base = eng.set_main(grp.index, 1)["CombinedDPS"]
        res = recommend_supports(eng, db, build, grp.index, 1, keep=[first_support],
                                 top_n=3, pool_size=6)
        # stan silnika wrócił do oryginału
        assert math.isclose(eng.stats()["CombinedDPS"], base, rel_tol=1e-9)
        vals = [r["value"] for r in res["ranking"]]
        assert vals == sorted(vals, reverse=True) and len(vals) == 3
        # najlepszą kombinację można niezależnie odtworzyć w silniku i dostać ten sam wynik
        orig = eng.groups()[grp.index - 1]["gems"]
        keep_gems = [g for g in orig if g["name"] == "Cyclone" or g["name"] == first_support
                     or g["name"] == "Desecrate"]
        top = res["ranking"][0]
        check = eng.set_group(grp.index, keep_gems + [
            {"name": n, "level": 20, "quality": 20} for n in top["supports"]])
        assert math.isclose(check["stats"]["CombinedDPS"], top["value"], rel_tol=1e-6)
        eng.set_group(grp.index, orig)
