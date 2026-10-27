"""Jewele: teksty Timeless, wyszukiwanie seedów (atrapa silnika), endpointy i integracja z prawdziwym PoB."""

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.calc import PobEngine
from app.planner import jewels as J
from app.web.server import create_app
from tests.test_agent import DB_PATH, FIXTURES, LoadingFake, ScriptedLLM, _events, call, say
from tests.test_engine import _engine_available
from tests.test_supports_tool import CYCLONE

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
needs_engine = pytest.mark.skipif(not _engine_available(), reason="brak silnika PoB")


class JewelFake(LoadingFake):
    """DPS zależy od seeda w jewelu: 1000 * (1 + seed/100000); EHP spada dla seeda 10500."""

    def __init__(self) -> None:
        super().__init__([CYCLONE], {})
        self.sock: dict[int, str | None] = {26196: None, 28475: "Hope"}
        self.committed = 0
        self.trials: list[tuple[int, str | None]] = []

    def stats(self) -> dict[str, Any]:
        return {"CombinedDPS": 1000.0, "TotalEHP": 10000.0, "Life": 1000.0}

    def jewel_reset(self) -> dict[str, Any]:
        return {"stats": self.stats(), "attrs": {}}

    def jewel_info(self) -> dict[str, Any]:
        return {"sockets": [{"id": 26196, "alloc": True, "near": "Phoenix"}, {"id": 28475, "alloc": True, "near": "Stance",
                                                                                "item": {"name": "Hope", "mods": ["+5% fire res"]}},
                            {"id": 2491, "alloc": False, "dist": 5, "near": "Arsenal"}], "spare": [], "stats": self.stats()}

    def tl_search(self, jewel_type: int, socket: int, wanted: list[dict[str, Any]], top: int = 40, **kw: Any) -> dict[str, Any]:
        assert wanted[0]["text"] == "critical strike"
        return {"results": [{"seed": s, "score": 3.0 - i, "hits": [{"node": "N", "gets": "crit"}]} for i, s in enumerate((12000, 10500, 15000))][:top],
                "scanned": 8001, "targets": 4, "matched": 3}

    def jewel_trial(self, socket: int, text: str | None, alloc: bool = False) -> dict[str, Any]:
        self.trials.append((socket, text))
        seed = int(text.split("[")[1].split("]")[0]) if text and "[" in text else 0
        st = {"CombinedDPS": 1000.0 * (1 + seed / 100000), "TotalEHP": 5000.0 if seed == 10500 else 10000.0, "Life": 1000.0}
        return {"stats": st, "attrs": {}, "item": {"name": "X"}, "points_used": 1}

    def jewel_swap(self, a: int, b: int, alloc: bool = False) -> dict[str, Any]:
        return {"stats": {"CombinedDPS": 1100.0, "TotalEHP": 10000.0}, "attrs": {}, "a": {"name": "Hope"}, "b": None}

    def jewel_commit(self) -> dict[str, Any]:
        self.committed += 1
        return {"stats": self.stats()}


def test_timeless_text_validates_seed_ranges() -> None:
    t = J.timeless_text(2, 12345, 2)
    assert "Lethal Pride [12345]" in t and "Selected Variant: 2" in t and "Commanded leadership over 12345 warriors under Kaom" in t
    assert "Has Alt Variant: true" in J.timeless_text(4, 3000, 1)
    assert J.timeless_text(5, 2040)  # krok 20
    for bad in ((5, 2030), (2, 5), (1, 9000), (9, 100)):
        with pytest.raises(J.JewelError):
            J.timeless_text(*bad)
    with pytest.raises(J.JewelError):
        J.timeless_text(2, 12345, 4)


def test_search_timeless_ranks_by_pob_and_applies_floor() -> None:
    eng = JewelFake()
    res = J.search_timeless(eng, jewel_type=2, socket=26196, wanted=["critical strike"], conqueror=1, top_n=5)
    assert [r["seed"] for r in res["ranking"]] == [15000, 12000]
    assert res["ranking"][0]["delta_pct"] == 15.0 and res["excluded_by_floor"][0]["seed"] == 10500
    assert "EHP" in res["excluded_by_floor"][0]["reason"] or "TotalEHP" in res["excluded_by_floor"][0]["reason"]
    assert all(r["conqueror_index"] == 1 for r in res["ranking"])


def test_search_timeless_requires_wanted_stats() -> None:
    with pytest.raises(J.JewelError):
        J.search_timeless(JewelFake(), jewel_type=2, socket=1, wanted=[])


def test_swap_and_try() -> None:
    eng = JewelFake()
    r = J.swap_sockets(eng, 26196, 28475)
    assert r["delta_pct"] == 10.0 and r["now_in_a"] == "Hope"
    t = J.try_jewel(eng, 26196, J.timeless_text(2, 12000))
    assert t["delta_pct"] == 12.0


@needs_db
def test_jewel_endpoints_and_agent_tools() -> None:
    eng = JewelFake()
    llm = ScriptedLLM([call("jewel_sockets"), call("find_timeless_jewels", jewel="Lethal Pride", wanted=["critical strike"], conqueror=1), say("ok")])
    c = TestClient(create_app(llm=llm, engine_factory=lambda: eng, db_path=DB_PATH))
    assert c.get("/api/jewels").status_code == 400  # bez buildu
    assert c.post("/api/session", json={"code": FIXTURES[0].read_text(encoding="utf-8")}).status_code == 200
    ov = c.get("/api/jewels").json()
    assert [s["id"] for s in ov["sockets"]] == [26196, 28475, 2491]
    r = c.post("/api/jewels/try", json={"socket": 26196, "timeless_type": 2, "seed": 12000, "conqueror": 1, "apply": True}).json()
    assert r["delta_pct"] == 12.0 and r["applied"] and eng.committed == 1
    assert c.post("/api/jewels/try", json={"socket": 26196}).status_code == 400
    assert c.post("/api/jewels/try", json={"socket": 26196, "timeless_type": 2, "seed": 5}).status_code == 400
    assert c.post("/api/jewels/swap", json={"a": 26196, "b": 28475}).json()["delta_pct"] == 10.0
    ev = _events(c.post("/api/jewels/timeless", json={"type": 2, "socket": 26196, "wanted": [{"text": "critical strike", "weight": 1}], "conqueror": 1}))
    assert ev[-1]["type"] == "done" and [e for e in ev if e["type"] == "result"][0]["data"]["ranking"][0]["seed"] == 15000
    assert any(e["type"] == "progress" for e in ev)
    ev2 = _events(c.post("/api/chat", json={"question": "jewele?"}))
    kinds = [e["type"] for e in ev2]
    assert kinds.count("tool_result") == 2 and kinds[-2:] == ["answer", "done"]


@needs_db
def test_price_endpoints_with_fake_trade(tmp_path: Path) -> None:
    from tests.test_trade import RARE, client as trade_client
    c = TestClient(create_app(llm=ScriptedLLM([]), engine_factory=lambda: JewelFake(), db_path=DB_PATH,
                              trade_factory=lambda: trade_client(tmp_path)))
    r = c.post("/api/price/check", json={"text": RARE}).json()
    assert r["min_chaos"] == 30 and r["league"] == "Mirage"
    assert c.post("/api/price/check", json={"text": "śmieci"}).status_code == 400
    assert c.get("/api/price/last").json()["id"] == 0
    c.app.state.push_capture(RARE)
    last = c.get("/api/price/last").json()
    assert last["id"] == 1 and last["overlay"] is False
    for _ in range(50):  # wycena idzie w tle
        last = c.get("/api/price/last").json()
        if last["status"] != "pending":
            break
        time.sleep(0.05)
    assert last["status"] == "done" and last["result"]["min_chaos"] == 30
    c.app.state.push_capture("śmieci")
    for _ in range(50):
        last = c.get("/api/price/last").json()
        if last["status"] != "pending":
            break
        time.sleep(0.05)
    assert last["id"] == 2 and last["status"] == "error"
    assert c.post("/api/open_url", json={"url": "https://evil.example/"}).status_code == 400


def test_hotkey_module_is_safe_off_windows() -> None:
    from app import hotkey
    l = hotkey.HotkeyListener(lambda t: None, key="x1")
    assert l.key == "D"
    if not hotkey.IS_WINDOWS:
        assert l.start() is False and "Windows" in (l.error or "")


@needs_engine
def test_real_pob_timeless_search_and_swap() -> None:
    with PobEngine() as e:
        e.load_file(FIXTURES[0])
        info = e.jewel_info()
        alloc = [s["id"] for s in info["sockets"] if s["alloc"]]
        assert alloc
        base = e.stats()["CombinedDPS"]
        res = J.search_timeless(e, jewel_type=2, socket=alloc[0], wanted=["critical strike", "life"], top_n=3, scan_top=6)
        assert res["seeds_scanned"] == 8001 and res["nodes_in_radius_counted"] >= 1 and res["ranking"]
        assert e.stats()["CombinedDPS"] == base  # próby nie zostają w buildzie
        e.jewel_trial(alloc[0], J.timeless_text(1, 7000, 2))
        assert e.jewel_reset()["stats"]["CombinedDPS"] == base
