"""Narzędzia dla modelu. Każde zwraca zwięzły słownik (JSON) albo {"error": "..."} z czytelnym komunikatem."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.calc import PobError
from app.tools.common import BAND_PCT, GOAL_LABELS, goal_setup
from app.tools.items import SLOT_FILES, ItemsError, recommend_items
from app.tools.supports import RecommendError, recommend_supports
from app.tools.tree import recommend_tree
from app.discovery import Discovery, community_lookup
from app.discovery.fetch import Fetcher
from app.planner.leveling import NOT_CHECKED as PLAN_NOT_CHECKED, Planner
from app.planner.research import ResearchError, research_meta

from .session import Session, color_letter

TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "get_build_summary",
        "description": "Streszczenie wczytanego buildu: klasa, staty z PoB, grupy skilli, itemy, drzewko. "
                       "Dostępne od razu w kontekście, wołaj tylko jeśli potrzebujesz odświeżenia.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "recommend_supports",
        "description": "Najlepsze supporty do skilla w tym buildzie, liczone w Path of Building. "
                       "Podaj skill (nazwa gemu). Aby uzupełnić wolne sockety, podaj free_sockets (kolory R,G,B,W) "
                       "albo keep (supporty, które zostają; reszta jest zdejmowana). "
                       "Bez free_sockets i keep ocenia obecne supporty i podpowiada zamienniki.",
        "parameters": {"type": "object", "properties": {
            "skill": {"type": "string", "description": "Nazwa aktywnego gemu, np. Cyclone"},
            "free_sockets": {"type": "array", "items": {"type": "string", "enum": ["R", "G", "B", "W"]},
                             "description": "Kolory wolnych socketów w grupie"},
            "keep": {"type": "array", "items": {"type": "string"},
                     "description": "Supporty, które mają zostać w grupie"},
            "goal": {"type": "string", "enum": ["damage", "balanced", "defence"],
                     "description": "Cel rankingu: damage = więcej obrażeń (pilnując EHP), balanced = kompromis obrażenia/obrona, "
                                    "defence = więcej obrony (pilnując DPS). Jeśli użytkownik wybrał cel w interfejsie, "
                                    "ten wybór ma pierwszeństwo."},
        }, "required": ["skill"]},
    }},
    {"type": "function", "function": {
        "name": "recommend_items",
        "description": "Najlepsze uniki do slotu (podmiana itemu liczona w Path of Building, z kontrolą "
                       "przeżywalności, resistów i atrybutów). Trwa do ok. 20 s. Zwraca ranking i werdykt, "
                       "czy obecny item warto zmienić.",
        "parameters": {"type": "object", "properties": {
            "slot": {"type": "string", "enum": list(SLOT_FILES),
                     "description": "Slot: Helmet, Body Armour, Gloves, Boots, Amulet, Ring 1, Ring 2, Belt, "
                                    "Weapon 1, Weapon 2"},
            "goal": {"type": "string", "enum": ["damage", "balanced", "defence"],
                     "description": "Cel rankingu: damage = więcej obrażeń (pilnując EHP), balanced = kompromis obrażenia/obrona, "
                                    "defence = więcej obrony (pilnując DPS). Jeśli użytkownik wybrał cel w interfejsie, "
                                    "ten wybór ma pierwszeństwo."},
        }, "required": ["slot"]},
    }},
    {"type": "function", "function": {
        "name": "recommend_tree",
        "description": "Zamiany w drzewku pasywnym liczone w Path of Building: który node zwolnić i jaki notable "
                       "lub keystone wziąć za te same punkty, plus dodania na wypadek wolnych punktów. "
                       "Trwa ok. 40 s. Pilnuje przeżywalności i resistów.",
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "string", "enum": ["damage", "balanced", "defence"],
                     "description": "Cel rankingu: damage = więcej obrażeń (pilnując EHP), balanced = kompromis obrażenia/obrona, "
                                    "defence = więcej obrony (pilnując DPS). Jeśli użytkownik wybrał cel w interfejsie, "
                                    "ten wybór ma pierwszeństwo."},
        }},
    }},
    {"type": "function", "function": {
        "name": "community_info",
        "description": "Ceny (poe.ninja) i opisy ze społeczności (wiki, Reddit jeśli skonfigurowany) dla gemów i uniqów "
                       "o podanych nazwach. Dane są NIEZWERYFIKOWANE i nie zastępują liczb z PoB. Używaj do pytań o ceny, "
                       "dostępność oraz do gemów z blind_spots_pob_sees_no_gain (efekty, których PoB nie liczy). "
                       "Do 6 nazw naraz, nazwy muszą istnieć w grze.",
        "parameters": {"type": "object", "properties": {
            "names": {"type": "array", "items": {"type": "string"}, "description": "Nazwy gemów lub uniqów"}},
            "required": ["names"]},
    }},
    {"type": "function", "function": {
        "name": "research_meta",
        "description": "Research popularności buildów na YouTube dla bieżącej wersji gry i ligi (np. 3.29 + nazwa ligi z poe.ninja, "
                       "nie okno czasowe). Zapytanie przepisz z pytania użytkownika (nazwa skilla/ascendancy jest wymagana w "
                       "wynikach; 'league start' dodawaj tylko, gdy o to pyta): które skille i "
                       "ascendancy są najczęściej grane, wg wyświetleń. Podaj zapytanie (klasa, ascendancy albo styl, "
                       "np. 'melee', 'Witch', 'Juggernaut'). Dane NIEZWERYFIKOWANE i przybliżone. Dla pytań w stylu "
                       "'w co grać', gdy użytkownik nie ma pomysłu na postać.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "np. melee, caster, Marauder, Cyclone"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "make_leveling_plan",
        "description": "Generuje od zera plan rozwoju w sekcjach (Akt 1-10, mapy): drzewko z mastery i keystone'ami, punkty z questów, "
                       "ascendancy, supporty i rare'y poglądowe z modami na dany poziom, wszystko liczone w Path of Building. "
                       "Trwa kilka minut. Opcjonalny budget_chaos ogranicza koszt gemów wg cen poe.ninja. Wymaga klasy, "
                       "ascendancy i głównego skilla (nazwy z bazy gry).",
        "parameters": {"type": "object", "properties": {
            "class_name": {"type": "string", "description": "np. Marauder, Witch, Duelist"},
            "ascendancy": {"type": "string", "description": "np. Juggernaut (musi należeć do klasy)"},
            "skill": {"type": "string", "description": "Nazwa aktywnego gemu, np. Cyclone"},
            "goal": {"type": "string", "enum": ["damage", "balanced", "defence"],
                     "description": "Cel rankingu nodów; wybór użytkownika w interfejsie ma pierwszeństwo."},
            "budget_chaos": {"type": "number", "description": "Opcjonalny limit kosztu gemów w chaosach (ceny poe.ninja)."}},
            "required": ["class_name", "ascendancy", "skill"]},
    }},
    {"type": "function", "function": {
        "name": "get_gem_info",
        "description": "Dane gemu z bazy gry: typ, kolor, wymagania, opis, staty na poziomie 20.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Dokładna nazwa gemu"}}, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "lookup_knowledge",
        "description": "Szuka w bazie wiedzy o PoE 1 (mechaniki, limity, ailmenty, panteon, keystone'y, ascendancy, poprawki użytkownika). "
                       "Zawiera też lokalną kopię wiki PoE (po angielsku), więc zapytanie formułuj PO ANGIELSKU, jak nazwy w grze. "
                       "Użyj ZAWSZE zanim odpowiesz na pytanie o mechanikę gry. Zwraca teksty z bazy albo pustą listę.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Hasło lub pytanie, np. 'bleed', 'limit odporności', 'pantheon'"}},
            "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "jewel_sockets",
        "description": "Lista socketów jewel w drzewku buildu: id, okolica (najbliższy keystone), czy zaalokowany (albo ile punktów do niego brakuje) "
                       "i jaki jewel w nim jest. Wywołaj przed szukaniem Timeless Jewel, żeby wybrać socket.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "find_timeless_jewels",
        "description": "Szuka seedów Timeless Jewel dających wskazane staty w okolicy socketu i liczy w PoB zysk dla buildu; "
                       "opcjonalnie sprawdza najtańsze oferty na trade. Staty podawaj po angielsku jak w grze (np. 'critical strike chance'). "
                       "Seedów i cen NIE wolno podawać z pamięci, tylko z wyniku tego narzędzia.",
        "parameters": {"type": "object", "properties": {
            "jewel": {"type": "string", "enum": ["Glorious Vanity", "Lethal Pride", "Brutal Restraint", "Militant Faith", "Elegant Hubris"]},
            "wanted": {"type": "array", "items": {"type": "string"}, "description": "Szukane frazy statów po angielsku"},
            "socket": {"type": "integer", "description": "Id socketu z jewel_sockets; domyślnie pierwszy zaalokowany"},
            "conqueror": {"type": "integer", "description": "1-3 (kolejność jak w grze); pomiń, by sprawdzić wszystkich"},
            "goal": {"type": "string", "enum": ["damage", "balanced", "defence"]},
            "with_prices": {"type": "boolean", "description": "Czy sprawdzić ceny na trade (wolniejsze, wymaga internetu)"}},
            "required": ["jewel", "wanted"]},
    }},
    {"type": "function", "function": {
        "name": "search_uniques",
        "description": "Szuka uniqów po fragmencie moda (po angielsku, jak w grze), zwraca aktualne mody.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Fragment moda, np. 'Cyclone' albo 'Warcry Cooldown'"}},
            "required": ["text"]},
    }},
]

MAX_LIST = 10


def _short_number(v: Any) -> Any:
    return round(v, 2) if isinstance(v, float) else v


def _tree_options(swaps: list[dict[str, Any]], blocked: list[dict[str, Any]],
                  free: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Opcje do podglądu drzewka: etykieta, wpływ na staty i listy nodów (zielone = dodane, czerwone = zwolnione)."""
    opts = []
    for kind, rows in (("swap", swaps), ("blocked", blocked)):
        for r in rows:
            opts.append({
                "kind": kind, "pool": r["add"]["pool"],
                "label": ("[ascendancy] " if r["add"]["pool"] == "asc" else "") + f"{r['remove']['name']} → {r['add']['name']}",
                "remove": r["remove"], "add": r["add"], "delta_pct": r["delta_pct"],
                "points_delta": r["points_delta"], "side_effects": r["side_effects"],
                "reason": r.get("reason", ""), "added": r["diff"]["added"], "removed": r["diff"]["removed"],
            })
    for r in free:
        opts.append({
            "kind": "add", "pool": r["pool"],
            "label": ("[ascendancy] " if r["pool"] == "asc" else "") + f"+ {r['name']} (wolne punkty)",
            "add": {k: r[k] for k in ("name", "type", "effects")},
            "delta_pct": r["delta_pct"], "points_delta": r["cost_points"], "side_effects": r["side_effects"],
            "reason": "", "added": r["diff"]["added"], "removed": r["diff"]["removed"],
        })
    return opts


GOAL_TOOLS = ("recommend_supports", "recommend_items", "recommend_tree", "make_leveling_plan", "find_timeless_jewels")
PLANNER_TOOLS = ("research_meta", "make_leveling_plan", "get_gem_info", "search_uniques", "community_info", "lookup_knowledge")


def available_tools(session: Session) -> list[dict[str, Any]]:
    """Bez wczytanego buildu model dostaje tylko narzędzia planera i bazy gry."""
    if getattr(session, "has_build", True):
        return TOOLS
    return [t for t in TOOLS if t["function"]["name"] in PLANNER_TOOLS]


def _section_brief(sec: dict[str, Any]) -> dict[str, Any]:
    return {"name": sec["name"], "level": sec["level"], "points_total": sec["points_total"],
            "quest_points": sec["quest_points"], "links": sec["links"], "weapon": sec["weapon_type"],
            "supports": [f"{x['name']} (+{x['contribution_pct']}%)" for x in sec["supports"]],
            "gear_mods": {g["slot"]: [m["mod"] for m in g["mods"]] for g in sec["gear"][:3]},
            "stats": {k: (round(v) if isinstance(v, (int, float)) else v) for k, v in sec["stats"].items()}}


class ToolBox:
    def __init__(self, session: Session, goal: str | None = None) -> None:
        self.s = session
        self.goal = goal  # cel wybrany przez użytkownika w interfejsie; wygrywa z tym, co poda model
        self.progress = None  # callable(str, frac=None): komunikaty postępu dla UI (długie narzędzia)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            return {"error": f"Nieznane narzędzie '{name}'. Dostępne: {', '.join(t['function']['name'] for t in TOOLS)}"}
        if not isinstance(args, dict):
            return {"error": "Argumenty narzędzia muszą być obiektem JSON."}
        if self.goal and name in GOAL_TOOLS:
            args = {**args, "goal": self.goal}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"Złe argumenty narzędzia {name}: {e}"}
        except (RecommendError, ItemsError) as e:
            return {"error": str(e)}
        except PobError as e:
            return {"error": f"Silnik PoB: {str(e).splitlines()[0]}"}

    # ------------------------------------------------------------------ narzędzia
    def _t_lookup_knowledge(self, query: str) -> dict[str, Any]:
        from app.knowledge import Knowledge
        kb = Knowledge()
        hits = kb.search(query, 4)
        hits += kb.wiki_hits(query, hits, 4)
        return {"results": [{"title": h["title"], "text": h["text"], "source": h.get("source")} for h in hits]
                or "brak wpisu w bazie wiedzy: nie zgaduj, powiedz użytkownikowi że tego nie wiesz"}

    def _t_jewel_sockets(self) -> dict[str, Any]:
        from app.planner import jewels as J
        info = J.overview(self.s.engine)
        return {"sockets": [{"id": x["id"], "near": x["near"], "allocated": x["alloc"], "points_to_reach": x.get("dist"),
                             "jewel": (x.get("item") or {}).get("name"), "mods": ((x.get("item") or {}).get("mods") or [])[:4]}
                            for x in info["sockets"]], "spare_jewels": [i.get("name") for i in info["spare"]]}

    def _t_find_timeless_jewels(self, jewel: str, wanted: list[str], socket: int | None = None, conqueror: int | None = None,
                                goal: str = "damage", with_prices: bool = False) -> dict[str, Any]:
        from app.planner import jewels as J
        types = {t["name"]: k for k, t in J.TIMELESS.items()}
        if jewel not in types:
            return {"error": f"Nieznany jewel '{jewel}'. Dostępne: {', '.join(types)}"}
        if socket is None:
            info = J.overview(self.s.engine)
            alloc = [x for x in info["sockets"] if x["alloc"]]
            if not alloc:
                return {"error": "Żaden socket jewel nie jest zaalokowany; podaj socket (jewel_sockets pokazuje koszt dojścia)."}
            socket = alloc[0]["id"]
        try:
            res = J.search_timeless(self.s.engine, jewel_type=types[jewel], socket=int(socket), wanted=list(wanted), goal=goal,
                                    conqueror=conqueror, top_n=8, scan_top=16, alloc=True,
                                    progress=lambda a, b: self.progress and self.progress("liczenie seedów", 0.7 * a / max(b, 1)))
        except J.JewelError as e:
            return {"error": str(e)}
        if with_prices:
            from app.trade import TradeClient, TradeError
            from app.trade.pricing import best_value, price_timeless
            try:
                tc = TradeClient()
                rows = price_timeless(tc, res["ranking"][:8], types[jewel])
                res["ranking"] = best_value(rows, mode="value")
                res["league"] = tc.league
            except TradeError as e:
                res["price_error"] = str(e)
        for r in res["ranking"]:
            r.pop("offers", None)
            r["hits"] = r.get("hits", [])[:3]
        res["ranking"] = res["ranking"][:8]
        return res

    def _t_get_build_summary(self) -> dict[str, Any]:
        return self.s.summary

    def _t_community_info(self, names: list[str]) -> dict[str, Any]:
        if not isinstance(names, list) or not names:
            return {"error": "Podaj listę nazw (names)."}
        disc = getattr(self.s, "discovery", None)
        if disc is None:
            disc = self.s.discovery = Discovery.default(Path(__file__).resolve().parents[2] / "data" / "cache")
        return json.loads(json.dumps(community_lookup(disc, self.s.db, names), default=_short_number))

    def _t_research_meta(self, query: str) -> dict[str, Any]:
        f = getattr(self.s, "fetcher", None)
        if f is None:
            f = self.s.fetcher = Fetcher(Path(__file__).resolve().parents[2] / "data" / "cache")
        try:
            names = self._ascendancy_names()
            return json.loads(json.dumps(research_meta(f, self.s.db, query, names), default=_short_number))
        except ResearchError as e:
            return {"error": str(e)}

    def _ascendancy_names(self) -> list[str]:
        try:
            info = self.s.engine.classes()
        except Exception:  # noqa: BLE001 - silnik bez załadowanego buildu: ładujemy minimalny
            from app.planner.build_xml import make_xml
            ver = self.s.db.meta()["tree_versions"].split(",")[-1]
            self.s.engine.load_xml(make_xml(class_id=0, class_name="Scion", asc_id=0, asc_name="None", level=1,
                                            tree_version=ver, nodes=[], gems=[]))
            info = self.s.engine.classes()
        return sorted({a["name"] for c in info["classes"] for a in c["asc"] if a["id"]})

    def _t_make_leveling_plan(self, class_name: str, ascendancy: str, skill: str, goal: str = "balanced",
                              budget_chaos: float | None = None) -> dict[str, Any]:
        import time
        from app.discovery.sources import NinjaPrices
        from app.planner.store import save_plan
        price_fn = None
        if budget_chaos is not None:
            f = getattr(self.s, "fetcher", None)
            if f is None:
                f = self.s.fetcher = Fetcher(Path(__file__).resolve().parents[2] / "data" / "cache")
            price_fn = NinjaPrices(f).lookup
        planner = Planner(self.s.engine, self.s.db, class_name, ascendancy, skill, goal=goal,
                          budget_chaos=budget_chaos, price_fn=price_fn)
        res = planner.run(progress=lambda i, n, m: self.progress and self.progress(f"sekcja {i + 1}/{n} – {m}", (i + 1) / n))
        layout = self.s.engine.tree_layout()
        final_nodes = set(res["stages"][-1]["nodes"]) if res["stages"] else set()
        layout["base"] = [i for i in layout["alloc"] if i not in final_nodes]  # start klasy i ascendancy (zawsze zaalokowane)
        if self.s.xml:  # przywracamy build użytkownika w silniku
            self.s.engine.load_xml(self.s.xml)
            self.s.layout = None
        plan = {"class": planner.cls["name"], "ascendancy": planner.asc["name"], "skill": planner.skill, "goal": goal,
                "tree_version": planner.tree_version, "created": int(time.time()), "steps": res["steps"],
                "supports": res["supports"], "sections": res["sections"], "stages": res["stages"], "meta": res["meta"],
                "damage_type": res["damage_type"],
                "budget_chaos": res["budget_chaos"], "spent_gems_chaos": res["spent_gems_chaos"],
                "final_stats": {k: round(v, 1) for k, v in res["final_stats"].items()
                                if k in ("Life", "CombinedDPS", "TotalEHP")},
                "assumptions": PLAN_NOT_CHECKED}
        for stg in plan["stages"]:
            stg["orig_nodes"], stg["orig_masteries"] = list(stg["nodes"]), dict(stg["masteries"])
        self.s.plan, self.s.plan_layout, self.s.plan_done = plan, layout, 0
        save_plan(plan, layout, 0)
        notables = [{"point": st["point"], "level": st["level"], "name": st["name"], "pool": st["pool"],
                     "effects": (st["effects"] or [])[:3], "gain_pct": st["gain_pct"]}
                    for st in plan["steps"] if st["type"] in ("T", "K") and st["last"]]
        return json.loads(json.dumps({
            "class": plan["class"], "ascendancy": plan["ascendancy"], "skill": plan["skill"],
            "goal_label": GOAL_LABELS.get(goal_setup(goal)[0], goal), "main_points": res["main_points"],
            "ascendancy_points": res["asc_points"], "steps_total": len(plan["steps"]),
            "key_nodes_in_order": notables, "sections": [_section_brief(x) for x in plan["sections"]],
            "result_stats": plan["final_stats"], "damage_type": plan["damage_type"],
            "budget_chaos": plan["budget_chaos"], "spent_gems_chaos": plan["spent_gems_chaos"],
            "legend": "Plan jest podzielony na sekcje (akty, mapy); level = poziom na końcu sekcji (przybliżone tempo), "
                      "punkty = wszystkie dostępne do końca sekcji razem z questami; contribution_pct supportu = spadek wyniku "
                      "po jego zdjęciu (PoB, z ekwipunkiem poglądowym). Podgląd: przycisk 'Plan rozwoju' w aplikacji.",
            "not_checked": plan["assumptions"]}, default=_short_number))

    def _t_get_gem_info(self, name: str) -> dict[str, Any]:
        g = self.s.db.gem(name)
        if g is None:
            return {"error": f"Nie ma gemu '{name}' w bazie. Nie zgaduj nazw; sprawdź pisownię."}
        eff = g["effect"] or {}
        levels = eff.get("levels") or []
        lvl20 = levels[min(20, len(levels)) - 1] if levels else {}
        stats = eff.get("stats") or []
        values = {}
        if isinstance(lvl20, dict):
            for i, stat_id in enumerate(stats, start=1):
                if str(i) in lvl20:
                    values[stat_id] = lvl20[str(i)]
        return {
            "name": g["name"], "type": "support" if g["is_support"] else "active",
            "color": color_letter(eff.get("color")), "tags": g["tags"],
            "max_level": g["max_level"], "description": eff.get("description"),
            "level_requirement_at_20": lvl20.get("levelRequirement") if isinstance(lvl20, dict) else None,
            "mana_multiplier_at_20": lvl20.get("manaMultiplier") if isinstance(lvl20, dict) else None,
            "stat_values_at_20": values,
            "note": "Staty podane jako identyfikatory gry; wartości z PoB.",
        }

    def _t_search_uniques(self, text: str) -> dict[str, Any]:
        out = []
        for hit in self.s.db.search_uniques(text, limit=80):
            for u in self.s.db.unique(hit["name"]):
                mods = self.s.db.current_mods(u)
                lines = mods["implicits"] + mods["explicits"]
                if any(text.lower() in line.lower() for line in lines):
                    out.append({"name": u["name"], "base": u["base"], "mods": lines[:8]})
                    break
            if len(out) >= MAX_LIST:
                break
        return {"query": text, "found": len(out), "uniques": out}

    def _t_recommend_supports(self, skill: str, free_sockets: list[str] | None = None,
                              keep: list[str] | None = None, goal: str = "damage") -> dict[str, Any]:
        group, skill_index = self._find_group(skill)
        metric, floor = goal_setup(goal)
        res = recommend_supports(
            self.s.engine, self.s.db, self.s.build, group, skill_index,
            free_sockets=free_sockets, keep=keep, metric=metric, floor=floor, top_n=5,
        )
        return self._compact(res, group)

    def _t_recommend_items(self, slot: str, goal: str = "damage") -> dict[str, Any]:
        res = recommend_items(self.s.engine, self.s.db, self.s.build, slot, goal=goal)
        better = [r for r in res["ranking"] if (r["delta_pct"] or 0) > BAND_PCT]
        blocked = [r for r in res["excluded_by_floor"] if (r["delta_pct"] or 0) > BAND_PCT][:3]
        out = {
            "slot": res["slot"], "goal": res["goal"], "metric": res["metric"], "baseline": res["baseline"],
            "current_item": res["current_item"], "candidates_evaluated": res["candidates_evaluated"],
            "rejected_counts": res["rejected_counts"], "floor": res["floor"],
            "verdict": "wymień" if better else (
                "zostaw (są uniki lepsze w metryce celu, ale kosztem progów przeżywalności, resistów lub atrybutów: "
                "patrz better_but_excluded)" if blocked
                else "zostaw (żaden unik z bazy nie jest lepszy)"),
            "better_items": better,
            "better_but_excluded": blocked,
            "goal_label": GOAL_LABELS.get(res["metric"], res["metric"]),
            "legend": ("delta_pct = zmiana metryki celu (patrz goal_label) całego buildu po wymianie itemu; "
                       "side_effects: *_pct zmiana % względem obecnego buildu (w tym CombinedDPS_pct i TotalEHP_pct, "
                       "o ile nie są metryką celu), *_pts zmiana punktów resista; "
                       "better_but_excluded = lepsze w metryce celu, ale odrzucone przez próg (powód w reason)."),
            "not_checked": res["not_checked"],
        }
        return json.loads(json.dumps(out, default=_short_number))

    def _t_recommend_tree(self, goal: str = "damage") -> dict[str, Any]:
        res = recommend_tree(self.s.engine, goal)
        swaps = [r for r in res["swaps"] if (r["delta_pct"] or 0) > BAND_PCT][:3]
        blocked = [r for r in res["swaps_blocked_by_floor"] if (r["delta_pct"] or 0) > BAND_PCT][:2]
        free = res["additions_if_free_points"][:3]
        self.s.last_tree = {"goal": res["goal"], "metric": res["metric"],
                            "options": _tree_options(swaps, blocked, free)}

        def bare(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:  # model nie dostaje list id nodów
            return [{k: v for k, v in r.items() if k != "diff"} for r in rows]
        out = {
            "goal": res["goal"], "metric": res["metric"], "baseline": res["baseline"], "floor": res["floor"],
            "goal_label": GOAL_LABELS.get(res["metric"], res["metric"]),
            "points_used": res["points_used"], "ascendancy": res["ascendancy"],
            "ascendancy_points_used": res["ascendancy_points_used"], "candidates": res["candidates"],
            "verdict": "zmień" if swaps else (
                "zostaw (są zamiany lepsze w metryce celu, ale kosztem progów przeżywalności lub resistów: "
                "patrz swaps_blocked_by_floor)" if blocked else "zostaw (żadna sprawdzona zamiana nie jest lepsza)"),
            "swaps": bare(swaps), "swaps_blocked_by_floor": bare(blocked),
            "additions_if_free_points": bare(free),
            "preview": "Zmiany można obejrzeć na wykresie drzewka w tej aplikacji: przycisk 'Pokaż zmiany na drzewku' "
                       "pod odpowiedzią albo 'Drzewko' w nagłówku (to nie jest okno Path of Building).",
            "legend": ("swaps: zwolnij 'remove' i weź 'add'; delta_pct = zmiana metryki celu (patrz goal_label) po zamianie, "
                       "side_effects: pozostałe staty w %, resisty w punktach; "
                       "points_delta <= 0 = nie kosztuje dodatkowych punktów (pool 'asc' = punkty ascendancy, osobna pula); "
                       "effects = opis nodów z gry; "
                       "additions_if_free_points = tylko jeśli masz nieużyte punkty (cost_points)."),
            "not_checked": res["not_checked"],
        }
        return json.loads(json.dumps(out, default=_short_number))

    # ------------------------------------------------------------------ pomocnicze
    def _find_group(self, skill: str) -> tuple[int, int]:
        """Grupa (1-based) i indeks aktywnego skilla w grupie dla gemu o podanej nazwie."""
        want = skill.strip().lower()
        if not self.s.db.is_known_gem(skill):
            raise RecommendError(f"Nie ma gemu '{skill}' w bazie. Nie zgaduj nazw.")
        candidates = [
            g["group"] for g in self.s.summary["skill_groups"]
            if not g["from_item"] and g["enabled"]
            and any(a.lower().rsplit(" ", 1)[0] == want for a in g["active_skills"])
        ]
        if not candidates:
            raise RecommendError(
                f"W buildzie nie ma edytowalnej grupy ze skillem '{skill}' "
                "(grupy z itemów PoB odtwarza sam)."
            )
        group = candidates[0]
        for idx in range(1, 9):
            main = self.s.engine.set_main(group, idx).get("MainSkill")
            if main and main.lower() == want:
                return group, idx
        raise RecommendError(f"PoB nie pozwolił ustawić '{skill}' jako głównego skilla grupy {group}.")

    @staticmethod
    def _compact(res: dict[str, Any], group: int) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for r in res.get("rejected", []):
            key = r["reason"].split(" koloru")[0].split(" postaci")[0]
            reasons[key] = reasons.get(key, 0) + 1
        out: dict[str, Any] = {
            "skill": res["skill"], "group": group, "mode": res["mode"], "metric": res["metric"],
            "baseline": res["baseline"], "free_sockets": res["free_sockets"],
            "candidates_evaluated": res["candidates_evaluated"],
            "rejected_counts": reasons, "floor": res.get("floor"),
            "goal_label": GOAL_LABELS.get(res["metric"], res["metric"]),
            "not_checked": res["not_checked"],
        }
        if res["mode"] == "fill":
            out["ranking"] = res["ranking"]
            out["best_single"] = res["best_single"][:3]
            out["excluded_by_floor"] = res["excluded_by_floor"][:3]
            out["blind_spots_pob_sees_no_gain"] = res["blind_spots"][:10]
        else:
            rows = []
            for row in res["replace_report"]:
                better = [r for r in row["best_replacements"] if (r["delta_pct"] or 0) > BAND_PCT][:3]
                rows.append({
                    "current": row["current"],
                    "gives_metric_pct": row["value_of_current_pct"],
                    "verdict": "wymień" if better else "zostaw (żaden zamiennik nie jest lepszy)",
                    "better_replacements": better,
                })
            out["replace_report"] = rows
            out["goal_label"] = GOAL_LABELS.get(res["metric"], res["metric"])
            out["legend"] = ("delta_pct = zmiana metryki celu (patrz goal_label) całego buildu po wymianie "
                             "(tylko dodatnie, czyli zyski); side_effects w % względem obecnego buildu "
                             "(CombinedDPS_pct, TotalEHP_pct itd.); ujemne = gorzej.")
        return json.loads(json.dumps(out, default=_short_number))
