"""Testy agenta, narzędzi i serwera. LLM jest skryptowany (bez Ollamy); silnik: atrapa lub prawdziwy PoB."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMError
from app.agent.loop import SYSTEM_PROMPT, extract_tool_calls, run_agent, system_prompt
from app.agent.session import Session
from app.agent.tools import TOOLS, ToolBox
from app.calc import PobEngine
from app.gamedata import GameDB
from app.parser.models import Build
from app.parser.parse import decode_code, parse_file
from app.web.server import create_app
from tests.test_engine import _engine_available
from tests.test_supports_tool import CYCLONE, FakeEngine, _by_color

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "game.db"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))
needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
needs_engine = pytest.mark.skipif(not _engine_available(), reason="brak silnika PoB")


class ScriptedLLM:
    """Zwraca kolejne przygotowane wiadomości; zapamiętuje, co dostał."""

    model = "scripted"

    def __init__(self, replies: list[dict[str, Any] | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        self.calls.append(([dict(m) for m in messages], tools))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def call(tool: str, /, **args: Any) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"function": {"name": tool, "arguments": args}}]}


def say(text: str) -> dict[str, Any]:
    return {"content": text}


class LoadingFake(FakeEngine):
    def load_xml(self, xml: str) -> dict[str, Any]:
        return {"stats": {"MainSkill": "Cyclone", "CombinedDPS": 1000.0, "Life": 1000.0}, "groups": self.groups()}


def fake_session(db: GameDB, mult: dict[str, float] | None = None) -> Session:
    eng = LoadingFake([CYCLONE], mult or {})
    summary = {
        "class": "Duelist", "level": 100, "stats": {"CombinedDPS": 1000.0},
        "skill_groups": [{"group": 1, "slot": "Weapon 1", "from_item": False, "enabled": True,
                          "active_skills": ["Cyclone 20/0"], "supports": []}],
    }
    return Session(db=db, engine=eng, build=Build(class_name="Duelist", level=100), summary=summary)


@pytest.fixture(scope="module")
def db() -> GameDB:
    return GameDB(DB_PATH)


# ------------------------------------------------------------------ pętla agenta
@needs_db
def test_tool_call_then_answer(db: GameDB) -> None:
    s = fake_session(db)
    llm = ScriptedLLM([call("get_gem_info", name="Cyclone"), say("Cyclone to skill ataku.")])
    events = list(run_agent(s, llm, "co to Cyclone?"))
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "answer"]
    assert events[1]["ok"] is True
    # drugi request do modelu zawiera wynik narzędzia jako wiadomość roli tool
    tool_msgs = [m for m in llm.calls[1][0] if m["role"] == "tool"]
    assert tool_msgs and json.loads(tool_msgs[0]["content"])["name"] == "Cyclone"
    # do historii trafiają tylko pytanie i odpowiedź
    assert [m["role"] for m in s.history] == ["user", "assistant"]


@needs_db
def test_system_prompt_contains_rules_and_summary(db: GameDB) -> None:
    s = fake_session(db)
    p = system_prompt(s)
    assert "Żadnych liczb ani faktów o grze z pamięci" in p and '"Duelist"' in p
    assert SYSTEM_PROMPT in p


def test_text_tool_call_fallback() -> None:
    msg = {"content": '<tool_call>{"name": "get_gem_info", "arguments": {"name": "Cyclone"}}</tool_call>'}
    assert extract_tool_calls(msg) == [{"name": "get_gem_info", "arguments": {"name": "Cyclone"}}]
    bare = {"content": '{"name": "search_uniques", "arguments": {"text": "Cyclone"}}'}
    assert extract_tool_calls(bare)[0]["name"] == "search_uniques"
    assert extract_tool_calls({"content": "zwykła odpowiedź"}) == []
    assert extract_tool_calls({"content": '{"name": "nieznane", "arguments": {}}'}) == []


def test_string_arguments_are_parsed() -> None:
    msg = {"tool_calls": [{"function": {"name": "get_gem_info", "arguments": '{"name": "Cyclone"}'}}]}
    assert extract_tool_calls(msg)[0]["arguments"] == {"name": "Cyclone"}


@needs_db
def test_unknown_tool_is_reported_and_model_recovers(db: GameDB) -> None:
    llm = ScriptedLLM([call("wymyslone_narzedzie"), say("Przepraszam, nie mam takiego narzędzia.")])
    events = list(run_agent(fake_session(db), llm, "?"))
    assert events[1]["ok"] is False and events[-1]["type"] == "answer"


@needs_db
def test_tool_call_limit_forces_an_answer(db: GameDB) -> None:
    replies = [call("get_gem_info", name="Cyclone")] * 3 + [say("Wystarczy.")]
    llm = ScriptedLLM(replies)
    events = list(run_agent(fake_session(db), llm, "?", max_calls=3))
    assert sum(e["type"] == "tool_call" for e in events) == 3
    assert events[-1] == {"type": "answer", "text": "Wystarczy."}
    assert llm.calls[-1][1] is None  # ostatnie wywołanie bez narzędzi


@needs_db
def test_llm_error_becomes_event(db: GameDB) -> None:
    llm = ScriptedLLM([LLMError("Nie mogę połączyć się z Ollamą")])
    events = list(run_agent(fake_session(db), llm, "?"))
    assert events == [{"type": "error", "text": "Nie mogę połączyć się z Ollamą"}]


@needs_db
def test_think_tags_are_stripped(db: GameDB) -> None:
    llm = ScriptedLLM([say("<think>rozważam</think>Odpowiedź.")])
    assert list(run_agent(fake_session(db), llm, "?"))[-1]["text"] == "Odpowiedź."


# ------------------------------------------------------------------ narzędzia
@needs_db
def test_gem_info_known_and_unknown(db: GameDB) -> None:
    tb = ToolBox(fake_session(db))
    ok = tb.call("get_gem_info", {"name": "Cyclone"})
    assert ok["type"] == "active" and ok["color"] in "RGBW" and ok["stat_values_at_20"]
    assert "error" in tb.call("get_gem_info", {"name": "Wymyslony Gem"})
    assert "error" in tb.call("get_gem_info", {})  # brak wymaganego argumentu


@needs_db
def test_search_uniques_returns_current_mods_containing_text(db: GameDB) -> None:
    res = ToolBox(fake_session(db)).call("search_uniques", {"text": "Warcry Cooldown"})
    assert res["found"] > 0
    for u in res["uniques"]:
        assert any("warcry cooldown" in m.lower() for m in u["mods"])


@needs_db
def test_recommend_supports_tool_with_fake_engine(db: GameDB) -> None:
    best = _by_color(db)["R"][0]
    tb = ToolBox(fake_session(db, {best: 1.5}))
    res = tb.call("recommend_supports", {"skill": "Cyclone", "free_sockets": ["R"]})
    assert res["mode"] == "fill" and res["ranking"][0]["supports"] == [best]
    assert res["group"] == 1 and "not_checked" in res and "rejected_counts" in res


@needs_db
def test_recommend_supports_tool_errors_are_readable(db: GameDB) -> None:
    tb = ToolBox(fake_session(db))
    assert "Nie zgaduj" in tb.call("recommend_supports", {"skill": "Wymyslony"})["error"]
    assert "nie ma edytowalnej grupy" in tb.call("recommend_supports", {"skill": "Fireball"})["error"]


def test_tool_schemas_are_valid() -> None:
    names = [t["function"]["name"] for t in TOOLS]
    assert len(names) == len(set(names)) == 12
    for t in TOOLS:
        assert t["function"]["parameters"]["type"] == "object" and t["function"]["description"]


# ------------------------------------------------------------------ serwer
def _client(llm: ScriptedLLM, db_path: Path = DB_PATH) -> TestClient:
    return TestClient(create_app(llm=llm, engine_factory=lambda: LoadingFake([CYCLONE], {}), db_path=db_path))


def _events(resp: Any) -> list[dict[str, Any]]:
    return [json.loads(ln[5:]) for ln in resp.text.splitlines() if ln.startswith("data:")]


@needs_db
def test_web_status_and_index() -> None:
    c = _client(ScriptedLLM([]))
    st = c.get("/api/status").json()
    assert st["db_ok"] is True and st["build_loaded"] is False and st["model"] == "scripted"
    assert "AIPoeTool" in c.get("/").text


@needs_db
def test_web_load_build_and_chat() -> None:
    c = _client(ScriptedLLM([call("get_gem_info", name="Cyclone"), say("Gotowe.")]))
    code = FIXTURES[0].read_text(encoding="utf-8")
    r = c.post("/api/session", json={"code": code})
    assert r.status_code == 200 and r.json()["class"]
    ev = _events(c.post("/api/chat", json={"question": "co to Cyclone?"}))
    assert [e["type"] for e in ev] == ["tool_call", "tool_result", "answer", "done"]
    assert c.get("/api/status").json()["build_loaded"] is True


@needs_db
def test_web_accepts_raw_xml_and_rejects_garbage() -> None:
    c = _client(ScriptedLLM([]))
    xml = decode_code(FIXTURES[0].read_text(encoding="utf-8").strip())
    assert c.post("/api/session", json={"code": xml}).status_code == 200
    bad = c.post("/api/session", json={"code": "to nie jest kod"})
    assert bad.status_code == 400 and "kod Path of Building" in bad.json()["detail"]


@needs_db
def test_web_chat_requires_build_and_question() -> None:
    c = _client(ScriptedLLM([]))
    assert c.post("/api/chat", json={"question": "   "}).status_code == 400
    c.post("/api/session", json={"code": FIXTURES[0].read_text(encoding="utf-8")})
    assert c.post("/api/chat", json={"question": "   "}).status_code == 400


def test_web_reports_missing_db(tmp_path: Path) -> None:
    c = _client(ScriptedLLM([]), db_path=tmp_path / "nie_ma.db")
    r = c.post("/api/session", json={"code": FIXTURES[0].read_text(encoding="utf-8")})
    assert r.status_code == 500 and "update_gamedata" in r.json()["detail"]


# ------------------------------------------------------------------ pełny tor z prawdziwym PoB
@needs_db
@needs_engine
def test_end_to_end_with_real_pob_and_scripted_llm() -> None:
    path = next(
        p for p in FIXTURES
        if any(g.active_gems and any(x.name == "Cyclone" for x in g.gems) and not g.from_item
               for g in parse_file(p).skill_groups)
    )
    build = parse_file(path)
    grp = next(g for g in build.skill_groups if not g.from_item and any(x.name == "Cyclone" for x in g.gems))
    keep = next(g.name for g in grp.gems if g.is_support)
    llm = ScriptedLLM([
        call("recommend_supports", skill="Cyclone", keep=[keep]),
        say("Najlepsze uzupełnienie podano w wyniku narzędzia."),
    ])
    with PobEngine() as engine:
        c = TestClient(create_app(llm=llm, engine_factory=lambda: engine, db_path=DB_PATH))
        assert c.post("/api/session", json={"code": path.read_text(encoding="utf-8")}).status_code == 200
        ev = _events(c.post("/api/chat", json={"question": "jakie supporty do Cyclone?"}))
    results = [e for e in ev if e["type"] == "tool_result"]
    assert results and results[0]["ok"], results
    tool_msg = next(m for m in llm.calls[1][0] if m["role"] == "tool")
    data = json.loads(tool_msg["content"])
    assert data["mode"] == "fill" and data["ranking"] and data["floor"] == {"TotalEHP": 0.95}
    assert data["ranking"][0]["delta_pct"] is not None


# ------------------------------------------------------------------ tryb planera (bez buildu) i plan rozwoju
@needs_db
def test_planner_mode_without_build_uses_planner_tools_and_prompt() -> None:
    from app.agent.tools import available_tools
    llm = ScriptedLLM([say("Zacznijmy od klasy.")])
    c = _client(llm)
    r = c.post("/api/chat", json={"question": "nie mam pomysłu na postać"})
    assert r.status_code == 200 and "Zacznijmy" in r.text
    msgs, tools = llm.calls[0]
    assert "NIE wczytał buildu" in msgs[0]["content"]
    names = {t["function"]["name"] for t in tools}
    assert "make_leveling_plan" in names and "research_meta" in names and "recommend_supports" not in names
    assert c.get("/api/status").json()["build_loaded"] is False
    assert c.get("/api/tree").status_code == 400
    assert len(available_tools(Session.empty(GameDB(DB_PATH), None))) == 6


@needs_db
def test_plan_endpoints_progress_and_persistence(tmp_path: Path) -> None:
    from app.planner.store import save_plan
    plan = {"class": "Witch", "ascendancy": "None", "skill": "Fireball", "steps": [{"id": 1}, {"id": 2}, {"id": 3}]}
    save_plan(plan, {"nodes": [], "links": [], "alloc": []}, 1, tmp_path)
    app = create_app(llm=ScriptedLLM([]), engine_factory=lambda: LoadingFake([CYCLONE], {}), db_path=DB_PATH, data_dir=tmp_path)
    c = TestClient(app)
    assert c.get("/api/plan").json()["done"] == 1
    assert c.get("/api/plan/layout").status_code == 200
    assert c.post("/api/plan/progress", json={"done": 99}).json()["done"] == 3
    assert create_app(llm=ScriptedLLM([]), db_path=DB_PATH, data_dir=tmp_path) and TestClient(
        create_app(llm=ScriptedLLM([]), db_path=DB_PATH, data_dir=tmp_path)).get("/api/plan").json()["done"] == 3


def test_clientlog_parses_last_level(tmp_path: Path) -> None:
    from app.web.clientlog import last_level
    f = tmp_path / "Client.txt"
    f.write_text("2026/10/25 12:00:01 123 abc [INFO Client 1] : Zak (Marauder) is now level 5\n"
                 "2026/10/25 12:10:01 123 abc [INFO Client 1] : Zak (Marauder) is now level 6\n", encoding="utf-8")
    assert last_level(f) == {"character": "Zak", "class": "Marauder", "level": 6}
    assert last_level(tmp_path / "brak.txt") is None


def test_knowledge_search_and_teach(tmp_path):
    from app.knowledge import Knowledge, parse_teach
    kb = Knowledge(learned=tmp_path / "l.json")
    assert any("Odporności" in h["title"] for h in kb.search("ile wynosi max resist"))
    assert kb.search("zzzz qqqq") == []
    assert parse_teach("Zapamiętaj: Cyclone nie ma cooldownu") == "Cyclone nie ma cooldownu"
    kb.teach("Cyclone nie ma cooldownu")
    assert kb.search("cyclone cooldown")[0]["source"] == "użytkownik"


def test_wiki_index_and_search(tmp_path):
    from app import wiki
    db = tmp_path / "w.db"
    c = wiki.connect(db)
    c.execute("insert into pages values(?,?)", ("Bleeding", "'''Bleeding''' is a [[damage over time|debuff]].{{Infobox|duration=5}}\n== Mechanics ==\nBleeding deals [[physical damage]] over time. " * 2))
    c.commit()
    assert wiki.build_index(c) >= 2
    c.close()
    assert wiki.search("how does bleeding work", 3, db)[0]["title"].startswith("Bleeding")
    assert wiki.search("zzzzz", 3, db) == [] and wiki.search("bleeding", 3, tmp_path / "none.db") == []
    from app.knowledge import Knowledge
    kb = Knowledge(learned=tmp_path / "l.json", wiki_path=db)
    assert "Bleeding" in kb.context("jak działa bleeding")


def test_guide_page_text():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    import fetch_guides
    title, text = fetch_guides.page_text('<title>T</title><nav>menu</nav><p>Take Bladestorm early.</p><script>var x=1</script>'
                                         '<script id="__NEXT_DATA__" type="application/json">{"a":"Use Ancestral Warchief totem for clearing packs."}</script><p>end</p>')
    assert title == "T" and "Bladestorm" in text and "Warchief" in text and "menu" not in text and "var x" not in text and text.endswith("end")
