"""Testy recommend_tree: logika na fałszywym silniku, integracja na prawdziwym PoB."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.session import Session
from app.agent.tools import ToolBox
from app.calc import PobEngine, PobError
from app.gamedata import GameDB
from app.tools.tree import recommend_tree
from tests.test_engine import _engine_available

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "game.db"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
needs_engine = pytest.mark.skipif(not _engine_available(), reason="brak silnika PoB")


class FakeTreeEngine:
    """Nody dodawane/usuwane zmieniają DPS i EHP według tabel; wynik = suma efektów (addytywnie)."""

    def __init__(self, adds: dict[int, tuple[str, int, float, float]], removes: dict[int, tuple[str, int, float, float]],
                 unreachable: set[int] = frozenset()) -> None:
        self.adds, self.removes, self.unreachable = adds, removes, unreachable
        self.asc_ids: set[int] = set()
        self.resets = 0
        self.calls = 0

    def _stats(self, dps: float = 0.0, ehp: float = 0.0) -> dict[str, float]:
        return {"CombinedDPS": 1000.0 * (1 + dps), "TotalEHP": 10000.0 * (1 + ehp),
                "FireResist": 75.0, "ColdResist": 75.0, "LightningResist": 75.0, "Life": 1000.0}

    def tree_info(self, max_dist: int = 6, max_freed: int = 4) -> dict[str, Any]:
        pool = lambda i: {"pool": "asc"} if i in self.asc_ids else {}  # noqa: E731
        add = [{"id": i, "name": n, "type": "Notable", "dist": d, "sd": [f"efekt {n}"], **pool(i)}
               for i, (n, d, _, _) in self.adds.items()]
        rem = [{"id": i, "name": n, "type": "Normal", "freed": f, "sd": [f"efekt {n}"], **pool(i)}
               for i, (n, f, _, _) in self.removes.items()]
        return {"add": add, "remove": rem, "points_used": 100, "asc_used": 8, "asc_name": "Test", "stats": self._stats()}

    def tree_try(self, alloc: list[int] | None = None, dealloc: list[int] | None = None) -> dict[str, Any]:
        self.calls += 1
        dps = ehp = 0.0
        delta = 0
        for i in dealloc or []:
            _, f, d, e = self.removes[i]
            dps, ehp, delta = dps + d, ehp + e, delta - f
        for i in alloc or []:
            if i in self.unreachable:
                raise PobError("brak ścieżki do nodu")
            _, dist, d, e = self.adds[i]
            dps, ehp, delta = dps + d, ehp + e, delta + dist
        asc_delta = 0
        for i in list(dealloc or []) + list(alloc or []):
            if i in self.asc_ids:       # węzły ascendancy liczą się do osobnej puli
                f = -self.removes[i][1] if i in (dealloc or []) else self.adds[i][1]
                delta -= f
                asc_delta += f
        return {"stats": self._stats(dps, ehp), "attrs": {}, "points_delta": delta, "asc_delta": asc_delta,
                "added": sorted(alloc or []), "removed": sorted(dealloc or [])}

    def tree_reset(self) -> dict[str, Any]:
        self.resets += 1
        return {"stats": self._stats(), "attrs": {}}

    def stats(self) -> dict[str, float]:
        return self._stats()


def _engine() -> FakeTreeEngine:
    adds = {1: ("Dmg A", 1, 0.20, 0.0), 2: ("Dmg B", 2, 0.30, -0.10), 3: ("Dmg C", 1, 0.10, 0.0),
            4: ("Nieosiągalny", 1, 0.90, 0.0), 5: ("Nic", 1, 0.0, 0.0)}
    removes = {10: ("Life small", 1, -0.01, -0.03), 11: ("Duży koszt", 2, -0.50, -0.20)}
    return FakeTreeEngine(adds, removes, unreachable={4})


def test_swaps_verified_independent_and_floors() -> None:
    eng = _engine()
    r = recommend_tree(eng)
    names = [(s["remove"]["name"], s["add"]["name"]) for s in r["swaps"]]
    assert names[0] == ("Life small", "Dmg A")           # najlepszy zysk netto przy koszcie 1 punktu
    assert len({n for pair in names for n in pair}) == 2 * len(names), "nody nie mogą się powtarzać"
    assert all(s["points_delta"] <= 0 for s in r["swaps"])
    assert all("Nieosiągalny" != s["add"]["name"] for s in r["swaps"] + r["swaps_blocked_by_floor"])
    assert r["rejected_counts"]
    blocked = {s["add"]["name"]: s["reason"] for s in r["swaps_blocked_by_floor"]}
    assert "TotalEHP" in blocked.get("Dmg B", "")         # B daje DPS, ale zjada EHP
    assert eng.resets == 1, "stan drzewka musi wrócić do wyjściowego"


def test_free_point_additions_ranked_per_point() -> None:
    r = recommend_tree(_engine())
    free = r["additions_if_free_points"]
    assert free and free[0]["name"] == "Dmg A"
    assert all(e["delta_pct"] > 0.5 for e in free) and "Nic" not in {e["name"] for e in free}


@needs_db
def test_tool_verdict_with_fake_engine() -> None:
    session = SimpleNamespace(engine=_engine(), db=None, build=None)
    res = ToolBox(session).call("recommend_tree", {})
    assert res["verdict"] == "zmień" and res["swaps"][0]["add"]["name"] == "Dmg A"
    flat = FakeTreeEngine({1: ("Nic", 1, 0.0, 0.0)}, {10: ("X", 1, -0.1, 0.0)})
    res = ToolBox(SimpleNamespace(engine=flat, db=None, build=None)).call("recommend_tree", {})
    assert res["verdict"].startswith("zostaw") and res["swaps"] == []


@needs_db
@needs_engine
def test_real_pob_restores_tree() -> None:
    path = FIXTURES[0]
    with PobEngine() as engine:
        s = Session.create(path.read_text(encoding="utf-8"), GameDB(DB_PATH), engine)
        before = engine.stats()
        res = ToolBox(s).call("recommend_tree", {})
        after = engine.stats()
        engine_layout = engine.tree_layout()
    assert "error" not in res, res
    assert res["candidates"]["additions"] > 0 and res["candidates"]["removals"] > 0
    assert abs(before["CombinedDPS"] - after["CombinedDPS"]) < 1e-6 * before["CombinedDPS"]
    assert abs(before["TotalEHP"] - after["TotalEHP"]) < 1e-6 * before["TotalEHP"]
    assert all(r["points_delta"] <= 0 and r["delta_pct"] > 0.5 for r in res["swaps"])
    layout_ids = {n["id"] for n in (engine_layout or {}).get("nodes", [])}
    assert any(len(link) == 5 for link in engine_layout["links"]), "połączenia po okręgu powinny mieć łuk"
    asc_of = {n["id"]: n.get("asc") for n in engine_layout["nodes"]}
    assert all(asc_of[a] == asc_of[b] for a, b, *_ in engine_layout["links"]), "brak linii między drzewami"
    assert any(n["t"] in "TK" and n.get("d") for n in engine_layout["nodes"]), "notable'e mają opisy"
    for o in s.last_tree["options"]:
        assert set(o["added"]) | set(o["removed"]) <= layout_ids and (o["added"] or o["removed"])


@needs_engine
def test_trial_state_does_not_leak_between_builds() -> None:
    """Snapshot drzewka i próbne itemy z jednego buildu nie mogą wpływać na kolejny wczytany build."""
    from app.parser.parse import decode_code

    def xml(p: Path) -> str:
        return decode_code(p.read_text(encoding="utf-8").strip())

    a, b = FIXTURES[0], FIXTURES[1]
    with PobEngine() as e:
        e.load_xml(xml(a))
        info_a = e.tree_info(max_dist=3)
        e.tree_try(dealloc=[info_a["remove"][0]["id"]])
        base_b = e.load_xml(xml(b))["stats"]
        info_b = e.tree_info(max_dist=3)
        assert info_b["stats"]["CombinedDPS"] == base_b["CombinedDPS"]
        assert {n["id"] for n in info_b["remove"]} != {n["id"] for n in info_a["remove"]}
        assert e.tree_reset()["stats"]["CombinedDPS"] == base_b["CombinedDPS"]


@needs_db
def test_tool_hides_node_ids_from_model_but_keeps_them_for_preview() -> None:
    eng = _engine()
    eng.removes[12] = ("Mały koszt", 2, -0.05, 0.0)   # zwalnia 2 punkty, więc pasuje do Dmg B (zjada EHP)
    session = SimpleNamespace(engine=eng, db=None, build=None, last_tree=None)
    res = ToolBox(session).call("recommend_tree", {})
    assert "diff" not in str(res) and "added" not in str(res), "model nie dostaje list id nodów"
    opts = session.last_tree["options"]
    assert opts[0]["kind"] == "swap" and opts[0]["added"] == [1] and opts[0]["removed"] == [10]
    assert any(o["kind"] == "blocked" for o in opts) and any(o["kind"] == "add" for o in opts)
    assert all({"label", "delta_pct", "points_delta", "side_effects", "added", "removed"} <= set(o) for o in opts)


class WebFake(FakeTreeEngine):
    def load_xml(self, xml: str) -> dict[str, Any]:
        return {"stats": {"MainSkill": "Cyclone", "CombinedDPS": 1000.0, "Life": 1000.0}, "groups": []}

    def tree_layout(self) -> dict[str, Any]:
        nodes = [{"id": i, "x": i * 100, "y": 0, "t": "T", "n": f"N{i}", "a": False} for i in (1, 2, 10, 11)]
        return {"nodes": nodes, "links": [[1, 2], [10, 11]], "alloc": [10, 11], "tree_version": "3_29"}


@needs_db
def test_web_tree_endpoint() -> None:
    import json

    from fastapi.testclient import TestClient

    from app.web.server import create_app
    from tests.test_agent import ScriptedLLM, call, say

    eng = WebFake({1: ("Dmg A", 1, 0.2, 0.0)}, {10: ("Life small", 1, -0.01, -0.03)})
    c = TestClient(create_app(llm=ScriptedLLM([call("recommend_tree"), say("ok")]),
                              engine_factory=lambda: eng, db_path=DB_PATH))
    assert c.get("/api/tree").status_code == 400
    code = FIXTURES[0].read_text(encoding="utf-8")
    assert c.post("/api/session", json={"code": code}).status_code == 200
    before = c.get("/api/tree").json()
    assert before["options"] == [] and len(before["nodes"]) == 4 and before["alloc"] == [10, 11]
    ev = [json.loads(x[5:]) for x in c.post("/api/chat", json={"question": "drzewko?"}).text.splitlines()
          if x.startswith("data:")]
    assert [e["type"] for e in ev] == ["tool_call", "tool_result", "answer", "done"] and ev[1]["ok"]
    after = c.get("/api/tree").json()
    assert after["options"][0]["added"] == [1] and after["options"][0]["removed"] == [10]
    assert after["nodes"] == before["nodes"]


@needs_engine
def test_real_pob_result_is_deterministic() -> None:
    """Ten sam build daje ten sam wynik (kolejność z Lua jest losowa, więc remisy muszą być rozstrzygane stałym kluczem)."""
    from app.parser.parse import decode_code

    def xml(p: Path) -> str:
        return decode_code(p.read_text(encoding="utf-8").strip())

    with PobEngine() as probe:   # build z sensownym DPS, inaczej wynik jest pusty i test niczego nie sprawdza
        path = next((p for p in FIXTURES if probe.load_xml(xml(p))["stats"].get("CombinedDPS", 0) > 1000), FIXTURES[0])
    outs = []
    for _ in range(2):
        with PobEngine() as engine:
            engine.load_xml(xml(path))
            r = recommend_tree(engine, max_adds=60, max_verify=10)
            outs.append([[(x["remove"]["name"], x["add"]["name"], x["delta_pct"]) for x in r[k]]
                         for k in ("swaps", "swaps_blocked_by_floor")]
                        + [[(x["name"], x["delta_pct"]) for x in r["additions_if_free_points"]]])
    assert outs[0] == outs[1]
