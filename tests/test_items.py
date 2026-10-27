"""Testy recommend_items: logika na fałszywym silniku (wymaga data/game.db), integracja na prawdziwym PoB."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.session import Session
from app.agent.tools import ToolBox
from app.calc import PobEngine, PobError
from app.calc.scores import add_scores
from app.gamedata import GameDB
from app.parser.models import Item
from app.parser.parse import parse_file
from app.tools.items import SLOT_FILES, ItemsError, _level_req, recommend_items, unique_item_text
from tests.test_engine import _engine_available

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "game.db"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
needs_engine = pytest.mark.skipif(not _engine_available(), reason="brak silnika PoB")


@pytest.fixture(scope="module")
def db() -> GameDB:
    return GameDB(DB_PATH)


class FakeItemEngine:
    """Każdy unik ma zadany DPS/EHP/resist; slot pamięta próbny item. reset_item przywraca stan wyjściowy."""

    def __init__(self, table: dict[str, dict[str, float]], reject: set[str] = frozenset(),
                 reqs: dict[str, dict[str, int]] | None = None) -> None:
        self.table, self.reject, self.reqs = table, reject, reqs or {}
        self.trial: str | None = None
        self.resets = 0

    def _stats(self) -> dict[str, float]:
        row = self.table.get(self.trial or "", {})
        return add_scores({"CombinedDPS": row.get("dps", 1000.0), "TotalEHP": row.get("ehp", 10000.0),
                           "Life": 1000.0, "FireResist": row.get("fire", 75.0), "ColdResist": 75.0,
                           "LightningResist": 75.0})

    def reset_item(self, slot: str) -> dict[str, Any]:
        self.trial = None
        self.resets += 1
        return {"stats": self._stats(), "attrs": {}}

    def stats(self) -> dict[str, float]:
        return self._stats()

    def set_item(self, slot: str, text: str) -> dict[str, Any]:
        name = text.splitlines()[0]
        if name in self.reject:
            raise PobError("item nie pasuje do slotu")
        self.trial = name
        return {"stats": self._stats(), "attrs": {"Str": 100, "Dex": 100, "Int": 100},
                "reqs": {"str": 0, "dex": 0, "int": 0, **self.reqs.get(name, {})}, "item_name": name}


def _build(level: int = 100, equipped: str | None = None) -> Any:
    items = [Item(id=1, slot="Helmet", rarity="UNIQUE", name=equipped, base="X")] if equipped else []
    return SimpleNamespace(level=level, equipped_items=items)


@needs_db
def test_unique_text_has_name_base_implicits(db: GameDB) -> None:
    u = db.unique("Atziri's Disfavour")[0]
    lines = unique_item_text(u).splitlines()
    assert lines[0] == "Atziri's Disfavour" and lines[1] == "Vaal Axe"
    assert f"Implicits: {len(u['implicits'])}" in lines
    assert any(ln.startswith("Variant:") for ln in lines)


@needs_db
def test_uniques_in_categories(db: GameDB) -> None:
    for slot, files in SLOT_FILES.items():
        assert db.uniques_in(files), slot


@needs_db
def test_ranking_sorted_floor_and_restore(db: GameDB) -> None:
    helmets = db.uniques_in(("helmet",))
    names = [u["name"] for u in helmets if _level_req(u) <= 100][:6]
    a, b, c, d, e, f = names
    eng = FakeItemEngine({
        a: {"dps": 1100}, b: {"dps": 1300}, c: {"dps": 1500, "ehp": 6000},   # c: za duży spadek EHP
        d: {"dps": 1400, "fire": 40},                                         # d: resist poniżej capa
        e: {"dps": 900}, f: {"dps": 1000},
    }, reject={f}, reqs={b: {"str": 150}})                                    # b: brak atrybutu, f: nie pasuje
    r = recommend_items(eng, db, _build(), "Helmet", top_n=5)
    assert r["ranking"][0]["name"] == a
    deltas = [x["delta_pct"] for x in r["ranking"]]
    assert deltas == sorted(deltas, reverse=True)
    excluded = {x["name"]: x["reason"] for x in r["excluded_by_floor"]}
    assert "TotalEHP" in excluded[c] and "FireResist" in excluded[d] and "Str" in excluded[b]
    assert f not in {x["name"] for x in r["ranking"]} and r["rejected_counts"]
    assert eng.trial is None, "oryginał musi wrócić"


@needs_db
def test_worn_unique_is_not_a_candidate(db: GameDB) -> None:
    helmets = db.uniques_in(("helmet",))
    worn = helmets[0]["name"]
    eng = FakeItemEngine({})
    r = recommend_items(eng, db, _build(equipped=worn), "Helmet", top_n=500)
    assert worn not in {x["name"] for x in r["ranking"]}
    assert r["current_item"]["name"] == worn


@needs_db
def test_level_filter_and_bad_slot(db: GameDB) -> None:
    eng = FakeItemEngine({})
    low = recommend_items(eng, db, _build(level=1), "Helmet", top_n=500)
    high = recommend_items(eng, db, _build(level=100), "Helmet", top_n=500)
    assert low["candidates_evaluated"] < high["candidates_evaluated"]
    with pytest.raises(ItemsError):
        recommend_items(eng, db, _build(), "Flask 1")


@needs_db
@needs_engine
def test_real_pob_restores_slot_and_tool_verdict(db: GameDB) -> None:
    path = FIXTURES[0]
    build = parse_file(path)
    slot = next(i.slot for i in build.equipped_items if i.slot in ("Helmet", "Boots", "Gloves", "Belt"))
    with PobEngine() as engine:
        s = Session.create(path.read_text(encoding="utf-8"), db, engine)
        before = engine.stats()
        res = ToolBox(s).call("recommend_items", {"slot": slot})
        after = engine.stats()
        bad = ToolBox(s).call("recommend_items", {"slot": "Flask 1"})
    assert "error" not in res, res
    assert res["verdict"] in ("wymień",) or res["verdict"].startswith("zostaw")
    assert res["candidates_evaluated"] > 0
    assert abs(before["CombinedDPS"] - after["CombinedDPS"]) < 1e-6 * before["CombinedDPS"]
    assert all(r["delta_pct"] > 0.5 for r in res["better_items"])
    assert "error" in bad


def test_scope_footer_lists_only_what_was_checked() -> None:
    from app.agent.loop import scope_footer
    assert scope_footer([], []) == ""
    text = scope_footer(["Cyclone"], ["Helmet", "Boots"])
    assert "supporty: Cyclone" in text and "Helmet, Boots" in text
    assert "drzewko pasywne" in text and "Body Armour" in text.split("Nie sprawdzono:")[1]
    assert "Helmet" not in text.split("Nie sprawdzono:")[1]


@needs_db
def test_verdict_mentions_blocked_upgrades(db: GameDB) -> None:
    helmets = [u["name"] for u in db.uniques_in(("helmet",)) if _level_req(u) <= 100]
    eng = FakeItemEngine({helmets[0]: {"dps": 1500, "ehp": 6000}})   # lepszy DPS, ale zbyt duży spadek EHP
    session = SimpleNamespace(engine=eng, db=db, build=_build())
    res = ToolBox(session).call("recommend_items", {"slot": "Helmet"})
    assert res["verdict"].startswith("zostaw") and "better_but_excluded" in res["verdict"]
    assert res["better_but_excluded"][0]["name"] == helmets[0] and res["better_items"] == []
    clean = ToolBox(SimpleNamespace(engine=FakeItemEngine({}), db=db, build=_build())).call(
        "recommend_items", {"slot": "Helmet"})
    assert clean["verdict"] == "zostaw (żaden unik z bazy nie jest lepszy)"


def test_scope_footer_tree() -> None:
    from app.agent.loop import scope_footer
    text = scope_footer([], [], True)
    assert "zamiany nodów w drzewku" in text and "drzewko pasywne" not in text.split("Nie sprawdzono:")[1]
    assert "drzewko pasywne" in scope_footer(["Cyclone"], [], False).split("Nie sprawdzono:")[1]


@needs_db
def test_goal_changes_the_winner(db: GameDB) -> None:
    helmets = [u["name"] for u in db.uniques_in(("helmet",)) if _level_req(u) <= 100][:3]
    a, c, _ = helmets
    table = {a: {"dps": 1300, "ehp": 9200},     # dużo DPS, EHP -8%
             c: {"dps": 1100, "ehp": 10000}}    # mało DPS, EHP bez zmian
    win = {}
    for goal in ("damage", "balanced"):
        r = recommend_items(FakeItemEngine(table), db, _build(), "Helmet", goal=goal)
        win[goal] = r["ranking"][0]["name"]
    assert win == {"damage": c, "balanced": a}, "damage pilnuje EHP >= 95%, balanced dopuszcza -15%"
    d = recommend_items(FakeItemEngine(table), db, _build(), "Helmet", goal="balanced")["ranking"][0]
    assert d["side_effects"]["CombinedDPS_pct"] == 30.0 and d["side_effects"]["TotalEHP_pct"] == -8.0
    assert "CombinedDPS_pct" not in recommend_items(FakeItemEngine(table), db, _build(), "Helmet")["ranking"][0]["side_effects"]


def test_add_scores_and_goal_setup() -> None:
    from app.tools.common import GOAL_LABELS, goal_setup, side_effects
    s = add_scores({"CombinedDPS": 1000.0, "TotalEHP": 10000.0})
    assert abs(s["Balanced"] - 1000 ** 0.6 * 10000 ** 0.4) < 1e-6
    assert "Balanced" not in add_scores({"CombinedDPS": 0.0, "TotalEHP": 10.0})
    assert goal_setup("balanced")[0] == "Balanced" and goal_setup("nieznany") == goal_setup("damage")
    assert all(goal_setup(g)[0] in GOAL_LABELS for g in ("damage", "balanced", "defence"))
    # 10% więcej DPS i 15% mniej EHP: wynik łączony ~ 0,6*10 - 0,4*15 = 0
    up = add_scores({"CombinedDPS": 1100.0, "TotalEHP": 8500.0})
    base = add_scores({"CombinedDPS": 1000.0, "TotalEHP": 10000.0})
    assert abs(up["Balanced"] / base["Balanced"] - 1) < 0.01
    assert side_effects({"CombinedDPS": 1100.0, "TotalEHP": 9000.0}, {"CombinedDPS": 1000.0, "TotalEHP": 10000.0},
                        "TotalEHP") == {"CombinedDPS_pct": 10.0}


@needs_db
def test_user_goal_overrides_model_goal_and_reaches_prompt(db: GameDB) -> None:
    from app.agent.loop import scope_footer, system_prompt
    helmets = [u["name"] for u in db.uniques_in(("helmet",)) if _level_req(u) <= 100][:2]
    table = {helmets[0]: {"dps": 1300, "ehp": 9200}}
    session = SimpleNamespace(engine=FakeItemEngine(table), db=db, build=_build(), summary={"class": "X"})
    res = ToolBox(session, goal="balanced").call("recommend_items", {"slot": "Helmet", "goal": "damage"})
    assert res["goal"] == "balanced" and "zbalansowany" in res["goal_label"]
    assert ToolBox(session).call("recommend_items", {"slot": "Helmet", "goal": "defence"})["goal"] == "defence"
    assert "wybrał w interfejsie cel rankingu: \"balanced\"" in system_prompt(session, "balanced")
    assert system_prompt(session, "balanced").index("wybrał w interfejsie") < system_prompt(session, "balanced").index("STRESZCZENIE")
    assert "wybrał w interfejsie" not in system_prompt(session)
    assert scope_footer(["Cyclone"], [], False, "zbalansowany").endswith("Cel rankingu: zbalansowany.")


@needs_engine
def test_real_pob_stats_carry_balanced_score() -> None:
    with PobEngine() as e:
        from app.parser.parse import decode_code
        stats = e.load_xml(decode_code(next(p for p in FIXTURES).read_text(encoding="utf-8").strip()))["stats"]
        if stats.get("CombinedDPS", 0) > 0:
            assert abs(stats["Balanced"] - stats["CombinedDPS"] ** 0.6 * stats["TotalEHP"] ** 0.4) < 1e-6 * stats["Balanced"]
        assert "Balanced" in e.stats() or stats.get("CombinedDPS", 0) == 0
