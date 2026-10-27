"""Ścieżka rozwoju od zera, podzielona na sekcje (akty i mapy) jak w poradnikach: drzewko, mastery, keystone'y,
supporty i ekwipunek poglądowy liczone w PoB.

Źródła założeń (nie są danymi z gry, wypisywane w wyniku):
 - punkty z questów: 24 w kampanii, rozpisane na akty (mobalytics.gg "side quest passive points", poedb.tw/Passive_skill),
   zakładamy zabicie wszystkich bandytów (Deal with the Bandits),
 - ascendancy: 2 punkty za każdy z 4 labiryntów, poziomy 33/55/68/75 (maxroll.gg Labyrinth Guide),
 - tempo poziomów (poziom postaci na końcu aktu) jest PRZYBLIŻONE - to typowe tempo, nie wartość z gry,
 - linki: LINKS (poziom -> liczba socketów w linku), też założenie,
 - ekwipunek: rare'y poglądowe z modami dozwolonymi na ilvl = poziom sekcji (app/planner/gear.py).
"""

from __future__ import annotations

import heapq
from typing import Any

from app.parser.models import Build
from app.tools.common import add_scores, goal_setup
from app.tools.supports import RecommendError, _level_req

from .build_xml import make_xml
from .gear import GearBuilder, GearData

# (nazwa sekcji, typowy poziom na końcu sekcji, questy z punktami pasywnymi w tym akcie)
SECTIONS: list[tuple[str, int, list[tuple[str, int]]]] = [
    ("Akt 1", 13, [("The Dweller of the Deep", 1), ("The Marooned Mariner", 1)]),
    ("Akt 2", 23, [("Through Sacred Ground", 1), ("The Way Forward", 1), ("Deal With the Bandits (zabij wszystkich)", 1)]),
    ("Akt 3", 33, [("Victario's Secrets", 1), ("Piety's Pets", 1)]),
    ("Akt 4", 38, [("An Indomitable Spirit", 1)]),
    ("Akt 5", 44, [("In Service to Science", 1), ("Kitava's Torments", 1)]),
    ("Akt 6", 52, [("The Father of War", 1), ("The Cloven One", 1), ("The Puppet Mistress", 1)]),
    ("Akt 7", 57, [("The Master of a Million Faces", 1), ("Queen of Despair", 1), ("Kishara's Star", 1)]),
    ("Akt 8", 62, [("Love Is Dead", 1), ("The Gemling Legion", 1), ("Reflection of Terror", 1)]),
    ("Akt 9", 65, [("Queen of the Sands", 1), ("The Ruler of Highgate", 1)]),
    ("Akt 10", 68, [("Vilenta's Vengeance", 1), ("An End to Hunger", 2)]),
    ("Mapy I (do lvl 75)", 75, []),
    ("Mapy II (do lvl 85)", 85, []),
    ("Mapy III (do lvl 92)", 92, []),
]
STAT_KEYS = ("CombinedDPS", "Life", "EnergyShield", "TotalEHP", "FireResist", "ColdResist", "LightningResist")
ASC_UNLOCK = [33, 55, 68, 75]  # po 2 punkty
LINKS = [(1, 1), (8, 2), (15, 3), (25, 4), (50, 5), (70, 6)]
RELOAD_EVERY = 8
MIN_SUPPORT_GAIN = 0.03   # support wchodzi do linku tylko, gdy PoB pokazuje >= 3% poprawy wyniku (z ekwipunkiem)
KEYSTONE_MIN_GAIN = 0.06  # keystone ma wady, więc musi dawać wyraźny zysk
KEYSTONE_BLOCK = {"Chaos Inoculation", "Eternal Youth", "The Agnostic", "Solipsism", "Zealot's Oath", "Wind Dancer",
                  "Blood Magic", "Necromantic Aegis", "Hollow Palm Technique", "Ghost Dance", "Lethe Shade"}
NOT_CHECKED = [
    "Ekwipunek to rare'y poglądowe: mody z tierami dozwolonymi na danym poziomie, średnie rzuty, wybór przez PoB. "
    "To nie jest plan craftingu ani lista konkretnych przedmiotów; unikatów planner nie dobiera.",
    "Tempo poziomów w aktach jest przybliżone, a punkty z questów przypisane do końca aktu (nie do chwili questa).",
    "Dostępność gemów u vendorów/w nagrodach questowych i kolory socketów nie są sprawdzane (zakładamy białe sockety).",
    "Kolejność jest zachłanna (najlepszy zysk na punkt), nie globalnie optymalna; wcześniejsze nody nie są cofane (bez respecu).",
    "Keystone'y są wybierane tylko przy wyraźnym zysku w PoB; część wad (np. zależnych od stylu gry) PoB nie modeluje.",
]


ELEMENT_TYPES = ["Physical", "Fire", "Cold", "Lightning", "Chaos"]


def damage_type_of(gem: dict[str, Any]) -> str | None:
    """Typ obrażeń skilla z jego tagów w danych gry; atak bez tagu żywiołu = fizyczny, czar bez tagu = brak ograniczeń."""
    eff = gem["effect"]
    types = eff.get("skill_types") or []
    for t in ELEMENT_TYPES:
        if t in types:
            return t.lower()
    return "physical" if "Attack" in types else None


def _links_at(level: int) -> int:
    n = 1
    for lv, k in LINKS:
        if level >= lv:
            n = k
    return n


def _gem_level(db: Any, name: str, char_level: int) -> int:
    g = db.gem(name)
    levels = ((g or {}).get("effect") or {}).get("levels") or []
    best = 1
    for i, entry in enumerate(levels[:20], start=1):
        req = entry.get("levelRequirement") if isinstance(entry, dict) else None
        if req is None or req <= char_level:
            best = i
    return best


def _metric(stats: dict[str, Any], metric: str) -> float:
    return float(stats.get(metric) or 0)


class Planner:
    def __init__(self, engine: Any, db: Any, class_name: str, ascendancy: str | None, skill: str,
                 goal: str = "balanced", max_dist: int = 5, budget_chaos: float | None = None,
                 price_fn: Any = None, last_section: int | None = None, damage: str | None = None) -> None:
        self.e, self.db = engine, db
        self.goal, self.max_dist = goal, max_dist
        self.metric, _ = goal_setup(goal)
        self.budget, self.price_fn = budget_chaos, price_fn
        self.sections = SECTIONS[: (last_section or len(SECTIONS))]
        g = db.gem(skill)
        if g is None or g["is_support"]:
            raise RecommendError(f"'{skill}' nie jest aktywnym gemem w bazie.")
        self.skill = g["name"]
        self.skill_gem = g
        ver = db.meta()["tree_versions"].split(",")[-1]
        boot = make_xml(class_id=0, class_name="Scion", asc_id=0, asc_name="None", level=1, tree_version=ver,
                        nodes=[], gems=[{"name": self.skill, "level": 1}])
        self.e.load_xml(boot)
        info = self.e.classes()
        self.tree_version = info["tree_version"]
        cls = next((c for c in info["classes"] if c["name"].lower() == class_name.lower()), None)
        if cls is None:
            raise RecommendError(f"Nieznana klasa '{class_name}'. Dostępne: {', '.join(c['name'] for c in info['classes'])}.")
        self.cls = cls
        asc = None
        if ascendancy:
            asc = next((a for a in cls["asc"] if a["name"].lower() == ascendancy.lower()), None)
            if asc is None:
                raise RecommendError(f"Klasa {cls['name']} nie ma ascendancy '{ascendancy}'. Dostępne: "
                                     f"{', '.join(a['name'] for a in cls['asc'] if a['id'])}.")
        self.asc = asc or {"id": 0, "name": "None"}
        caster = "Spell" in (self.skill_gem["effect"].get("skill_types") or []) or not self.skill_gem["effect"].get("weapon_types")
        self.damage = (damage or damage_type_of(self.skill_gem) or "").lower() or None
        self.gear = GearBuilder(engine, GearData(), self.metric, self.skill_gem["effect"].get("weapon_types") or [], caster,
                                damage=self.damage)
        self.nodes: list[int] = []
        self.masteries: dict[int, int] = {}
        self.items: dict[str, str] = {}
        self.supports: list[str] = []
        self.weapon_type: str | None = None
        self._prices: dict[str, Any] = {}
        self._spent = 0.0

    # -- ładowanie stanu
    def _gems(self, level: int, extra: list[str] | None = None, without: str | None = None) -> list[dict[str, Any]]:
        names = [self.skill] + [s for s in self.supports if s != without] + (extra or [])
        return [{"name": n, "level": _gem_level(self.db, n, level), "quality": 0} for n in names]

    def _load(self, level: int, without: str | None = None) -> dict[str, Any]:
        xml = make_xml(class_id=self.cls["id"], class_name=self.cls["name"], asc_id=self.asc["id"],
                       asc_name=self.asc["name"], level=level, tree_version=self.tree_version,
                       nodes=self.nodes, gems=self._gems(level, without=without), items=self.items,
                       masteries=self.masteries)
        return add_scores(self.e.load_xml(xml)["stats"])

    # -- supporty: wchodzą tylko przy wyraźnym zysku w PoB, z ekwipunkiem
    def _price(self, name: str) -> float | None:
        if name not in self._prices and self.price_fn:
            self._prices.update(self.price_fn([name]))
        row = self._prices.get(name)
        return None if not row else row.get("chaos")

    def _pick_supports(self, level: int, log: list[dict[str, Any]]) -> None:
        want = _links_at(level) - 1
        pool: list[str] | None = None
        while len(self.supports) < want:
            base = _metric(self._load(level), self.metric)
            names = pool
            if names is None:
                try:
                    comp = self.db.compatible_supports(self.skill, weapon_type=self.weapon_type)
                except ValueError:
                    comp = self.db.compatible_supports(self.skill)
                present = {s.lower() for s in self.supports}
                names = []
                for c in comp:
                    if c["name"].lower() in present or c["flags"].get("legacy") or c["flags"].get("hidden"):
                        continue
                    eff = self.db.effect(c["effect_id"])
                    req = _level_req(eff, _gem_level(self.db, c["name"], level)) if eff else None
                    if req is not None and req > level:
                        continue
                    names.append(c["name"])
            scored: list[tuple[float, str]] = []
            for n in names:
                if n in self.supports:
                    continue
                price = self._price(n) if self.budget is not None else None
                if self.budget is not None and price is not None and self._spent + price > self.budget:
                    continue
                try:
                    st = self.e.set_group(1, self._gems(level, extra=[n]))["stats"]
                except Exception:  # noqa: BLE001
                    continue
                scored.append((_metric(add_scores(st), self.metric), n))
            scored.sort(reverse=True)
            if not scored or not base or scored[0][0] / base - 1 < MIN_SUPPORT_GAIN:
                log.append({"level": level, "note": "wolny link: żaden support nie daje >= "
                            f"{MIN_SUPPORT_GAIN:.0%} poprawy w PoB, więc go nie dobieram"})
                return
            best_val, best = scored[0]
            pool = [n for _, n in scored[:15]]
            price = self._price(best) if self.budget is not None else None
            self._spent += price or 0
            self.supports.append(best)
            log.append({"level": level, "support": best, "gain_pct": round((best_val / base - 1) * 100, 1),
                        "price_chaos": price})

    # -- wybór następnego nodu
    def _evaluate(self, cand: dict[str, Any], base: float) -> tuple[float, dict[str, Any]] | None:
        opts = cand.get("opts") or [None]
        best: tuple[float, dict[str, Any]] | None = None
        for o in opts:
            try:
                r = self.e.tree_try(alloc=[cand["id"]], mastery={cand["id"]: o["effect"]} if o else None)
            except Exception:  # noqa: BLE001 - node nieosiągalny w tym stanie
                continue
            st = add_scores(r["stats"])
            cost = max(r["points_delta"], 1)
            if cand["pool"] == "asc":
                cost = max(r.get("asc_delta", 0), 1)
            gain = (_metric(st, self.metric) / base - 1) if base else 0.0
            if cand["type"] == "Keystone" and gain < KEYSTONE_MIN_GAIN:
                continue
            ev = (gain / cost, {"gain": gain, "cost": cost, "added": r["added"],
                                "mastery": o["effect"] if o else None, "mastery_text": o["stats"] if o else None})
            if best is None or ev[0] > best[0]:
                best = ev
        return best

    def _best(self, pool: str, budget: int, base: float, cache: dict[int, tuple[float, dict]]) -> dict[str, Any] | None:
        info = self.e.tree_info(max_dist=self.max_dist)
        cands = [c for c in info["add"] if c["pool"] == pool and c["dist"] <= budget
                 and (c["type"] != "Keystone" or c["name"] not in KEYSTONE_BLOCK)]
        by_id = {c["id"]: c for c in cands}
        heap: list[tuple[float, int]] = []
        for c in sorted(cands, key=lambda c: c["id"]):
            if c["id"] not in cache:
                ev = self._evaluate(c, base)
                if ev:
                    cache[c["id"]] = ev
            if c["id"] in cache:
                heapq.heappush(heap, (-cache[c["id"]][0], c["id"]))
        while heap:
            _, cid = heapq.heappop(heap)
            c = by_id[cid]
            ev = self._evaluate(c, base)  # świeża ocena (cache bywa nieaktualny po zmianie drzewka)
            if not ev:
                cache.pop(cid, None)
                continue
            cache[cid] = ev
            if ev[0] <= 0:
                continue
            if not heap or ev[0] >= -heap[0][0] - 1e-12:
                return {**c, "ratio": ev[0], **ev[1]}
            heapq.heappush(heap, (-ev[0], cid))
        return None

    def _marginal(self, level: int) -> list[dict[str, Any]]:
        """Wkład każdego supporta: spadek wyniku po jego zdjęciu (pełne przeliczenie w PoB)."""
        full = _metric(self._load(level), self.metric)
        out = []
        for s in self.supports:
            without = _metric(self._load(level, without=s), self.metric)
            out.append({"name": s, "level": _gem_level(self.db, s, level),
                        "contribution_pct": round((full / without - 1) * 100, 1) if without else None})
        self._load(level)
        return out

    def run(self, progress: Any = None) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        notables: dict[int, dict[str, Any]] = {}
        sections: list[dict[str, Any]] = []
        stages: list[dict[str, Any]] = []
        support_log: list[dict[str, Any]] = []
        asc_unlocks = list(ASC_UNLOCK) if self.asc["id"] else []
        asc_avail = asc_pts = main_pts = quest_cum = 0
        stats: dict[str, Any] = {}
        total_sections = len(self.sections)
        for si, (name, level, quests) in enumerate(self.sections):
            q_pts = sum(p for _, p in quests)
            quest_cum += q_pts
            target = (level - 1) + quest_cum  # punkty z poziomów + z questów do końca sekcji
            while asc_unlocks and level >= asc_unlocks[0]:
                asc_unlocks.pop(0); asc_avail += 2
            asc_start = asc_avail
            # 1) ekwipunek i supporty na poziom sekcji (stan drzewka z poprzednich sekcji)
            if progress:
                progress(si, total_sections, f"{name}: dobieram ekwipunek poglądowy i supporty")
            self.items = {}  # ekwipunek liczymy od zera na poziom sekcji (stary nie może zaniżać progu zysku)
            stats = self._load(level)
            gear = self.gear.build(level, stats)
            self.items = gear["items"]
            wd = next((d for d in gear["detail"] if d["slot"] == "Weapon 1"), None)
            self.weapon_type = wd["type"] if wd else self.weapon_type
            if wd and si == 0:
                self.gear.weapon_types = [wd["type"]]  # typ broni wybrany w pierwszej sekcji jest utrzymany (jak w poradnikach)
            slog: list[dict[str, Any]] = []
            self._pick_supports(level, slog)
            support_log += slog
            stats = self._load(level)
            # 2) punkty drzewka
            cache_main: dict[int, tuple[float, dict]] = {}
            cache_asc: dict[int, tuple[float, dict]] = {}
            sec_steps_start = len(steps)
            stalled = 0
            since_reload = 0
            while main_pts < target or asc_avail:
                base = _metric(stats, self.metric)
                pick, pool = None, "main"
                if asc_avail:
                    pick, pool = self._best("asc", asc_avail, base, cache_asc), "asc"
                if pick is None and main_pts < target:
                    pool = "main"
                    pick = self._best("main", min(self.max_dist, target - main_pts), base, cache_main)
                if pick is None:
                    stalled += 1
                    if stalled >= 3:
                        break
                    self.max_dist += 1
                    continue
                stalled = 0
                res = self.e.tree_commit([pick["id"]], mastery={pick["id"]: pick["mastery"]} if pick.get("mastery") else None)
                stats = add_scores(res["stats"])
                order = res["order"]
                self.nodes += order
                if res.get("masteries"):
                    self.masteries.update(res["masteries"])
                if pool == "asc":
                    asc_avail -= pick["cost"]; asc_pts += pick["cost"]
                else:
                    main_pts += pick["cost"]
                for k, nid in enumerate(order):
                    last = k == len(order) - 1
                    steps.append({"id": nid, "pool": pool, "section": si, "act": name,
                                  "point": (main_pts - len(order) + k + 1) if pool == "main" else None,
                                  "level": level, "target": pick["id"], "target_name": pick["name"], "last": last,
                                  "gain_pct": round(pick["gain"] * 100, 1) if last else None,
                                  "mastery_text": pick.get("mastery_text") if last else None})
                notables[pick["id"]] = {"name": pick["name"], "type": pick["type"],
                                        "effects": pick.get("mastery_text") or pick.get("sd") or []}
                since_reload += len(order)
                if since_reload >= RELOAD_EVERY:
                    since_reload = 0
                    stats = self._load(level)
                    cache_main.clear(); cache_asc.clear()
                if progress:
                    progress(si, total_sections, f"{name}: {pick['name']}")
            stats = self._load(level)
            stages.append({"name": name, "level": level, "points_total": target, "quest_points": q_pts,
                           "asc_total": sum(2 for u in ASC_UNLOCK if level >= u) if self.asc["id"] else 0,
                           "nodes": list(self.nodes), "masteries": {str(k): v for k, v in self.masteries.items()},
                           "items": dict(self.items), "supports": list(self.supports),
                           "stats": {k: stats.get(k) for k in STAT_KEYS}})
            sections.append({
                "name": name, "level": level, "points_total": target, "quest_points": q_pts,
                "quests": [{"name": n, "points": p} for n, p in quests], "ascendancy_points_new": asc_pts_new(asc_start, asc_avail),
                "links": _links_at(level), "supports": self._marginal(level) if self.supports else [],
                "support_log": slog, "gear": gear["detail"], "weapon_type": self.weapon_type,
                "steps": [i for i in range(sec_steps_start, len(steps))],
                "stats": {k: stats.get(k) for k in ("CombinedDPS", "Life", "EnergyShield", "TotalEHP", "FireResist",
                                                     "ColdResist", "LightningResist")},
            })
        info = {n["id"]: n for n in self.e.tree_layout()["nodes"]}
        for st in steps:
            n = info.get(st["id"], {})
            st.update(name=n.get("n"), type=n.get("t"), effects=n.get("d") or [])
        meta = {"class_id": self.cls["id"], "class_name": self.cls["name"], "asc_id": self.asc["id"],
                "asc_name": self.asc["name"], "tree_version": self.tree_version, "skill": self.skill}
        return {"steps": steps, "notables": notables, "sections": sections, "stages": stages, "meta": meta, "supports": support_log,
                "damage_type": self.damage, "final_stats": stats, "main_points": main_pts, "asc_points": asc_pts, "items": self.items,
                "not_checked": NOT_CHECKED, "budget_chaos": self.budget, "spent_gems_chaos": round(self._spent, 1)}


def asc_pts_new(start: int, left: int) -> int:
    return start - left
