"""Serwer lokalny: wklejasz kod PoB, zadajesz pytania, model woła narzędzia. Jeden użytkownik, jedna sesja."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from contextlib import asynccontextmanager
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.agent.llm import LLM, OllamaClient, game_llm
from app.agent.loop import run_agent
from app.agent.session import Session
from app.calc import PobEngine, PobError
from app.gamedata import GameDB
from app.planner.store import load_plan, save_plan, save_progress

from . import clientlog

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"


class CodeIn(BaseModel):
    code: str


class FreeCodeIn(BaseModel):
    cls: str
    asc: str = "None"
    nodes: list[int] = []
    masteries: dict[str, int] = {}


class ProgressIn(BaseModel):
    done: int


class StageEdit(BaseModel):
    stage: int
    nodes: list[int]
    masteries: dict[str, int] = {}


class EvalIn(BaseModel):
    stages: list[StageEdit]
    save: bool = False
    reset: bool = False  # przywraca etapy do wersji wygenerowanej przez planner


class PriceCheckIn(BaseModel):
    text: str
    status: str = "securable"
    min_pct: float = 0.9
    min_match: int | None = None
    overrides: dict[str, dict[str, Any]] = {}
    limit: int = 10


class LeagueIn(BaseModel):
    league: str


class JewelTryIn(BaseModel):
    socket: int
    text: str = ""
    timeless_type: int | None = None  # zamiast text: Timeless Jewel o podanym seedzie i conquerorze (1-3)
    seed: int | None = None
    conqueror: int = 1
    goal: str = "damage"
    alloc: bool = False
    apply: bool = False   # True: zostaw jewel w buildzie (zapisuje zmianę w sesji)


class JewelSwapIn(BaseModel):
    a: int
    b: int
    goal: str = "damage"
    alloc: bool = False
    apply: bool = False


class TimelessIn(BaseModel):
    type: int
    socket: int
    wanted: list[dict[str, Any]]
    goal: str = "damage"
    conqueror: int | None = None
    near: int = 0
    alloc: bool = False
    scan_top: int = 20
    top_n: int = 10
    price: bool = False
    status: str = "securable"
    sort: str = "value"   # value | gain | cheap


class QuestionIn(BaseModel):
    question: str
    goal: str | None = None   # damage | balanced | defence; None = decyduje model
    game: bool = False        # pytanie z nakładki w trakcie gry: lekki model na CPU


def create_app(
    llm: LLM | None = None,
    engine_factory: Callable[[], Any] | None = None,
    db_path: str | Path | None = None,
    data_dir: Path | None = None,
    trade_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    """Fabryka aplikacji; llm/engine_factory/db_path da się podmienić w testach."""
    from app.env import load_env
    load_env()
    custom_llm = llm is not None
    llm = llm or OllamaClient()
    game_state: dict[str, Any] = {}
    db_path = Path(db_path or os.environ.get("AIPOE_DB", ROOT / "data" / "game.db"))
    state: dict[str, Any] = {"session": None, "engine": None, "db": None, "plan": None, "layout": None, "done": 0,
                             "level_override": None}
    data_dir = data_dir or ROOT / "data"
    state["plan"], state["layout"], state["done"] = load_plan(data_dir)
    state_lock = threading.Lock()
    state["capture"] = {"id": 0, "text": "", "ts": 0.0, "status": "idle"}
    state["trade"] = None

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # noqa: ANN202
        yield
        # zamykamy proces PoB (kontener) razem z serwerem
        if state["engine"] is not None and hasattr(state["engine"], "close"):
            state["engine"].close()

    app = FastAPI(title="AIPoeTool", lifespan=lifespan)

    def get_db() -> GameDB:
        if state["db"] is None:
            if not db_path.exists():
                raise HTTPException(500, f"Brak bazy gry ({db_path}). Uruchom scripts\\update_gamedata.ps1.")
            state["db"] = GameDB(db_path)
        return state["db"]

    def get_engine() -> Any:
        if state["engine"] is None:
            state["engine"] = (engine_factory or PobEngine)()
        return state["engine"]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        sess: Session | None = state["session"]
        ollama_ok = llm.available() if hasattr(llm, "available") else True
        return {
            "model": getattr(llm, "model", "?"), "ollama_ok": ollama_ok,
            "db_ok": db_path.exists(), "build_loaded": bool(sess and sess.has_build),
            "build": sess.summary if sess and sess.has_build else None,
        }

    @app.post("/api/session")
    def load_build(body: CodeIn) -> dict[str, Any]:
        with state_lock:
            try:
                session = Session.create(body.code, get_db(), get_engine())
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            except PobError as e:
                raise HTTPException(500, f"Silnik PoB: {str(e).splitlines()[0]}") from e
            old: Session | None = state["session"]
            if old is not None and old.plan is not None:  # plan rozwoju przeżywa wczytanie buildu
                session.plan, session.plan_layout, session.plan_done = old.plan, old.plan_layout, old.plan_done
            state["session"] = session
        return session.summary

    @app.get("/api/tree")
    def tree() -> dict[str, Any]:
        """Układ drzewka + obecne nody + opcje zamian z ostatniego recommend_tree (do podglądu graficznego)."""
        session: Session | None = state["session"]
        if session is None or not session.has_build:
            raise HTTPException(400, "Najpierw wklej kod PoB i załaduj build.")
        with session.lock:  # silnik PoB obsługuje jedno polecenie naraz
            if session.layout is None:
                try:
                    session.layout = session.engine.tree_layout()
                except PobError as e:
                    raise HTTPException(500, f"Silnik PoB: {str(e).splitlines()[0]}") from e
            return {**session.layout, "options": (session.last_tree or {}).get("options", [])}

    # ------------------------------------------------------------------ drzewko bez buildu i grafiki
    @app.get("/api/tree/free")
    def tree_free(cls: str = "Marauder", asc: str = "None") -> dict[str, Any]:
        """Puste drzewko wybranej klasy (do własnego planowania bez wczytanego buildu)."""
        from app.planner.build_xml import make_xml
        with state_lock:
            if state["session"] is None:
                state["session"] = Session.empty(get_db(), get_engine())
            session = state["session"]
        with session.lock:
            engine = get_engine()
            try:
                ver = get_db().meta()["tree_versions"].split(",")[-1]
                engine.load_xml(make_xml(class_id=0, class_name="Scion", asc_id=0, asc_name="None", level=100, tree_version=ver,
                                         nodes=[], gems=[{"name": "Cyclone", "level": 1}]))
                info = engine.classes()
                c = next((x for x in info["classes"] if x["name"].lower() == cls.lower()), None)
                if c is None:
                    raise HTTPException(400, f"Nie ma klasy {cls}.")
                a = next((x for x in c["asc"] if x["name"].lower() == asc.lower()), c["asc"][0])
                engine.load_xml(make_xml(class_id=c["id"], class_name=c["name"], asc_id=a["id"], asc_name=a["name"], level=100,
                                         tree_version=ver, nodes=[], gems=[{"name": "Cyclone", "level": 1}]))
                layout = engine.tree_layout()
            except PobError as e:
                raise HTTPException(500, f"Silnik PoB: {str(e).splitlines()[0]}") from e
            finally:
                if session.xml:
                    try:
                        engine.load_xml(session.xml)
                    except PobError:
                        pass
        return {**layout, "base": list(layout["alloc"]), "cls": c["name"], "asc": a["name"],
                "classes": [{"name": x["name"], "asc": [y["name"] for y in x["asc"]]} for x in info["classes"]]}

    @app.post("/api/tree/code")
    def tree_code(body: FreeCodeIn) -> dict[str, str]:
        """Kod importu PoB dla drzewka zbudowanego bez buildu (węzły + start klasy)."""
        from app.planner.build_xml import make_xml
        from app.planner.guide import pob_code
        with state_lock:
            if state["session"] is None:
                state["session"] = Session.empty(get_db(), get_engine())
            session = state["session"]
        with session.lock:
            info = get_engine().classes()
        c = next((x for x in info["classes"] if x["name"].lower() == body.cls.lower()), None)
        if c is None:
            raise HTTPException(400, f"Nie ma klasy {body.cls}.")
        a = next((x for x in c["asc"] if x["name"].lower() == body.asc.lower()), c["asc"][0])
        xml = make_xml(class_id=c["id"], class_name=c["name"], asc_id=a["id"], asc_name=a["name"], level=100,
                       tree_version=info["tree_version"], nodes=body.nodes, gems=[{"name": "Cyclone", "level": 20}],
                       masteries={int(k): v for k, v in body.masteries.items()})
        return {"code": pob_code(xml)}

    @app.get("/api/tree/sprites")
    def tree_sprites(version: str) -> dict[str, Any]:
        from app.calc import sprites
        try:
            return sprites.load(version)
        except (OSError, ImportError) as e:
            raise HTTPException(404, "Brak grafik drzewka (silnik natywny pobiera je przy pierwszym starcie).") from e

    @app.get("/api/tree/img/{version}/{name}")
    def tree_img(version: str, name: str) -> FileResponse:
        from app.calc import sprites
        p = sprites.image_path(version, name)
        if p is None:
            raise HTTPException(404, "Brak pliku.")
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    # ------------------------------------------------------------------ plan rozwoju
    def current_plan() -> tuple[dict[str, Any] | None, dict[str, Any] | None, int]:
        sess: Session | None = state["session"]
        if sess is not None and sess.plan is not None:  # świeżo wygenerowany plan z tej sesji wygrywa z dyskiem
            return sess.plan, sess.plan_layout, sess.plan_done
        return state["plan"], state["layout"], state["done"]

    @app.get("/api/plan")
    def plan() -> dict[str, Any]:
        p, _, done = current_plan()
        lv = clientlog.last_level()
        return {"plan": p, "done": done, "game_level": lv}

    @app.get("/api/plan/next")
    def plan_next() -> dict[str, Any]:
        """Następny etap planu: węzły do wzięcia (dla nakładki Ctrl+T w grze)."""
        p, layout, done = current_plan()
        stages = (p or {}).get("stages") or []
        if not stages:
            return {"ok": False, "msg": "Nie ma planu rozwoju. Wygeneruj go w oknie aplikacji."}
        lv = clientlog.last_level()
        idx = min(done, len(stages) - 1)
        if lv:  # poziom z Client.txt wygrywa: pierwszy etap, który jeszcze nie jest za nami
            idx = next((i for i, st in enumerate(stages) if st["level"] >= lv), len(stages) - 1)
        names = {n["id"]: n for n in (layout or {}).get("nodes", [])}
        have = set(stages[idx - 1]["nodes"]) if idx > 0 else set()
        st = stages[idx]
        new = [names.get(n, {"n": str(n), "t": "?"}) for n in st["nodes"] if n not in have]
        order = {"K": 0, "A": 1, "T": 2}
        new.sort(key=lambda x: order.get(x.get("t"), 3))
        return {"ok": True, "stage": st["name"], "level": st["level"], "game_level": lv, "points": len(new),
                "nodes": [{"name": x.get("n", "?"), "type": x.get("t", "?")} for x in new],
                "dps": (st.get("stats") or {}).get("CombinedDPS"), "life": (st.get("stats") or {}).get("Life")}

    @app.get("/api/plan/layout")
    def plan_layout() -> dict[str, Any]:
        p, layout, _ = current_plan()
        if p is None or layout is None:
            raise HTTPException(404, "Nie ma jeszcze planu. Poproś o plan rozwoju w czacie.")
        return layout

    @app.get("/api/plan/code/{idx}")
    def plan_code(idx: int) -> dict[str, str]:
        from app.planner.guide import pob_code, stage_xml
        p, _, _ = current_plan()
        if p is None or not p.get("stages") or not 0 <= idx < len(p["stages"]):
            raise HTTPException(404, "Nie ma takiego etapu.")
        return {"code": pob_code(stage_xml(p, idx, get_db()))}

    @app.get("/api/plan/guide")
    def plan_guide() -> dict[str, str]:
        from app.planner.guide import guide_markdown
        p, layout, _ = current_plan()
        if p is None:
            raise HTTPException(404, "Nie ma jeszcze planu.")
        return {"markdown": guide_markdown(p, layout, get_db())}

    @app.post("/api/plan/progress")
    def plan_progress(body: ProgressIn) -> dict[str, Any]:
        p, _, _ = current_plan()
        if p is None:
            raise HTTPException(404, "Nie ma planu.")
        done = max(0, min(body.done, len(p.get("stages") or p["steps"])))
        sess: Session | None = state["session"]
        if sess is not None and sess.plan is not None:
            sess.plan_done = done
        state["done"] = done
        save_progress(p, done, data_dir)
        return {"done": done}

    def _eval_stage(engine: Any, plan: dict[str, Any], idx: int, nodes: list[int], masteries: dict[str, int]) -> dict[str, Any]:
        from app.planner.build_xml import make_xml
        from app.planner.leveling import _gem_level, STAT_KEYS
        from app.tools.common import add_scores
        st, m = plan["stages"][idx], plan["meta"]
        db = get_db()
        gems = [{"name": n, "level": _gem_level(db, n, st["level"]), "quality": 0} for n in [m["skill"], *st["supports"]]]
        xml = make_xml(class_id=m["class_id"], class_name=m["class_name"], asc_id=m["asc_id"], asc_name=m["asc_name"],
                       level=st["level"], tree_version=m["tree_version"], nodes=nodes, gems=gems, items=st["items"],
                       masteries={int(k): v for k, v in masteries.items()})
        stats = add_scores(engine.load_xml(xml)["stats"])
        info = engine.tree_info(max_dist=1)
        return {"stage": idx, "stats": {k: stats.get(k) for k in STAT_KEYS},
                "points_used": info["points_used"], "asc_used": info["asc_used"]}

    @app.post("/api/plan/eval")
    def plan_eval(body: EvalIn) -> dict[str, Any]:
        """Przelicza etapy planu w PoB po edycji drzewka; save=True zapisuje zmiany w planie, reset=True wraca do wersji planera."""
        p, layout, done = current_plan()
        if p is None or not p.get("stages"):
            raise HTTPException(404, "Nie ma planu z etapami. Wygeneruj plan rozwoju jeszcze raz.")
        with state_lock:
            if state["session"] is None:
                state["session"] = Session.empty(get_db(), get_engine())
            session = state["session"]
        out = []
        with session.lock:
            engine = get_engine()
            try:
                for e in body.stages:
                    if not 0 <= e.stage < len(p["stages"]):
                        raise HTTPException(400, f"Nie ma etapu {e.stage}.")
                    st = p["stages"][e.stage]
                    if body.reset:
                        st["nodes"], st["masteries"] = list(st.get("orig_nodes", st["nodes"])), dict(st.get("orig_masteries", st["masteries"]))
                    else:
                        st.setdefault("orig_nodes", list(st["nodes"])); st.setdefault("orig_masteries", dict(st["masteries"]))
                        nodes, mast = e.nodes, e.masteries
                        res = _eval_stage(engine, p, e.stage, nodes, mast)
                        if body.save:
                            st["nodes"], st["masteries"], st["stats"] = nodes, mast, res["stats"]
                        out.append(res)
                        continue
                    res = _eval_stage(engine, p, e.stage, st["nodes"], st["masteries"])
                    st["stats"] = res["stats"]; out.append(res)
            except PobError as ex:
                raise HTTPException(500, f"Silnik PoB: {str(ex).splitlines()[0]}") from ex
            finally:
                if session.xml:  # przywracamy build użytkownika w silniku
                    try:
                        engine.load_xml(session.xml)
                    except PobError:
                        pass
        if body.save or body.reset:
            state["plan"] = p
            save_plan(p, layout, done, data_dir)
        return {"results": out}

    @app.post("/api/reset")
    def reset() -> dict[str, bool]:
        if state["session"]:
            state["session"].history.clear()
        return {"ok": True}


    # ---------------------------------------------------------------- ceny (trade) i skrót Ctrl+D
    def get_trade() -> Any:
        if state["trade"] is None:
            from app.trade import TradeClient
            state["trade"] = (trade_factory or TradeClient)()
        return state["trade"]

    def push_capture(text: str) -> None:
        """Wywoływane przez skrót w grze: zapamiętuje item i od razu wycenia go w tle (raz, dla nakładki i okna głównego)."""
        from app.trade import TradeError
        from app.trade.item import ItemParseError
        from app.trade.pricing import check_item
        cid = state["capture"]["id"] + 1
        state["capture"] = {"id": cid, "text": text, "ts": time.time(), "status": "pending"}

        def work() -> None:
            try:
                res = check_item(get_trade(), text)
                out: dict[str, Any] = {"status": "done", "result": res}
            except (ItemParseError, TradeError) as e:
                out = {"status": "error", "error": str(e)}
            except Exception as e:  # noqa: BLE001
                out = {"status": "error", "error": f"Błąd aplikacji: {e}"}
            if state["capture"]["id"] == cid:  # nowszy skrót unieważnia starą wycenę
                state["capture"] = {**state["capture"], **out}
        threading.Thread(target=work, daemon=True).start()

    app.state.push_capture = push_capture
    app.state.overlay = False

    @app.get("/api/price/last")
    def price_last() -> dict[str, Any]:
        return {**state["capture"], "overlay": bool(app.state.overlay)}

    @app.post("/api/price/check")
    def price_check(body: PriceCheckIn) -> dict[str, Any]:
        from app.trade import TradeError
        from app.trade.item import ItemParseError
        from app.trade.pricing import check_item
        try:
            return check_item(get_trade(), body.text, status=body.status, min_pct=body.min_pct,
                              min_match=body.min_match, overrides=body.overrides, limit=max(1, min(body.limit, 20)))
        except ItemParseError as e:
            raise HTTPException(400, str(e)) from e
        except TradeError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/open_url")
    def open_url(body: dict[str, str]) -> dict[str, bool]:
        """Otwiera link trade w domyślnej przeglądarce (tylko adresy strony trade)."""
        url = body.get("url", "")
        if not url.startswith("https://www.pathofexile.com/trade/"):
            raise HTTPException(400, "Dozwolone są tylko linki do trade.")
        import webbrowser
        webbrowser.open(url)
        return {"ok": True}

    @app.get("/api/price/league")
    def price_league() -> dict[str, Any]:
        from app.trade import TradeError
        t = get_trade()
        try:
            return {"league": t.league, "leagues": [l.get("id") for l in t.leagues()]}
        except TradeError as e:
            return {"league": t.league, "leagues": [], "error": str(e)}

    @app.post("/api/price/league")
    def price_set_league(body: LeagueIn) -> dict[str, Any]:
        get_trade().league = body.league.strip()
        return {"league": get_trade().league}

    # ---------------------------------------------------------------- jewele
    def build_session() -> Session:
        sess: Session | None = state["session"]
        if sess is None or not sess.has_build:
            raise HTTPException(400, "Najpierw wczytaj build (kod z Path of Building).")
        return sess

    @app.get("/api/jewels")
    def jewels_overview() -> dict[str, Any]:
        from app.planner import jewels as J
        sess = build_session()
        with sess.lock:
            return J.overview(sess.engine)

    def _jewel_call(fn: Callable[[], dict[str, Any]], sess: Session, apply: bool) -> dict[str, Any]:
        from app.calc import PobError
        from app.planner.jewels import JewelError
        try:
            with sess.lock:
                return fn()
        except (JewelError, PobError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/jewels/try")
    def jewels_try(body: JewelTryIn) -> dict[str, Any]:
        from app.planner import jewels as J
        sess = build_session()
        try:
            text = J.timeless_text(body.timeless_type, body.seed or 0, body.conqueror) if body.timeless_type else body.text
        except J.JewelError as e:
            raise HTTPException(400, str(e)) from e
        if not text.strip():
            raise HTTPException(400, "Wklej tekst jewela albo podaj typ i seed.")

        def run() -> dict[str, Any]:
            res = J.try_jewel(sess.engine, body.socket, text, goal=body.goal, alloc=body.alloc)
            if body.apply:
                sess.engine.jewel_trial(body.socket, text, alloc=body.alloc)
                sess.engine.jewel_commit()
                res["applied"] = True
            return res
        return _jewel_call(run, sess, body.apply)

    @app.post("/api/jewels/swap")
    def jewels_swap(body: JewelSwapIn) -> dict[str, Any]:
        from app.planner import jewels as J
        sess = build_session()

        def run() -> dict[str, Any]:
            res = J.swap_sockets(sess.engine, body.a, body.b, goal=body.goal, alloc=body.alloc)
            if body.apply:
                sess.engine.jewel_swap(body.a, body.b, alloc=body.alloc)
                sess.engine.jewel_commit()
                res["applied"] = True
            return res
        return _jewel_call(run, sess, body.apply)

    @app.post("/api/jewels/timeless")
    def jewels_timeless(body: TimelessIn) -> StreamingResponse:
        from app.calc import PobError
        from app.planner import jewels as J
        from app.trade import TradeError
        from app.trade.pricing import best_value, price_timeless
        sess = build_session()
        q: queue.Queue[dict[str, Any] | None] = queue.Queue()

        def work() -> None:
            try:
                with sess.lock:
                    res = J.search_timeless(
                        sess.engine, jewel_type=body.type, socket=body.socket, wanted=body.wanted, goal=body.goal,
                        conqueror=body.conqueror, top_n=max(body.top_n, 30) if body.price else body.top_n,
                        scan_top=body.scan_top, near=body.near, alloc=body.alloc,
                        progress=lambda a, b: q.put({"type": "progress", "frac": 0.7 * a / max(b, 1)}))
                if body.price:
                    rows = res["ranking"][:max(body.top_n, 12)]
                    try:
                        priced = price_timeless(get_trade(), rows, body.type, status=body.status,
                                                progress=lambda a, b: q.put({"type": "progress", "frac": 0.7 + 0.3 * a / max(b, 1)}))
                        res["ranking"] = best_value(priced, mode=body.sort)
                        res["league"] = get_trade().league
                    except TradeError as e:
                        res["price_error"] = str(e)
                else:
                    res["ranking"] = res["ranking"][:body.top_n]
                q.put({"type": "result", "data": res})
            except (J.JewelError, PobError) as e:
                q.put({"type": "error", "text": str(e)})
            except Exception as e:  # noqa: BLE001
                q.put({"type": "error", "text": f"Błąd aplikacji: {e}"})
            finally:
                q.put(None)

        threading.Thread(target=work, daemon=True).start()

        def events() -> Iterator[str]:
            while (ev := q.get()) is not None:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield 'data: {"type": "done"}\n\n'
        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/chat")
    def chat(body: QuestionIn) -> StreamingResponse:
        session: Session | None = state["session"]
        if session is None:  # bez buildu: tryb planera
            with state_lock:
                if state["session"] is None:
                    state["session"] = Session.empty(get_db(), get_engine())
                session = state["session"]
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Puste pytanie.")

        use_llm = llm
        if body.game and not custom_llm:
            use_llm = game_state.setdefault("llm", game_llm())
            if not use_llm.available():
                msg = f"Brak modelu '{use_llm.model}' do czatu w grze. Zainstaluj: ollama pull {use_llm.model} (albo ustaw AIPOE_GAME_MODEL)."
                def no_model() -> Iterator[str]:
                    yield f"data: {json.dumps({'type': 'error', 'text': msg}, ensure_ascii=False)}\n\n"
                    yield 'data: {"type": "done"}\n\n'
                return StreamingResponse(no_model(), media_type="text/event-stream")

        def events() -> Iterator[str]:
            # jedno pytanie naraz: silnik PoB trzyma jeden build
            with session.lock:
                try:
                    for ev in run_agent(session, use_llm, question, goal=body.goal):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except Exception as e:  # noqa: BLE001 - błąd ma dotrzeć do UI, nie zerwać strumienia
                    yield f"data: {json.dumps({'type': 'error', 'text': f'Błąd aplikacji: {e}'}, ensure_ascii=False)}\n\n"
            yield 'data: {"type": "done"}\n\n'

        return StreamingResponse(events(), media_type="text/event-stream")

    return app
