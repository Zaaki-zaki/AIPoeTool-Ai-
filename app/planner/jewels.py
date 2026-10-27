"""Jewele: teksty Timeless Jewel w formacie PoB, wyszukiwanie seedów po wpisanych modach i dokładna ocena w PoB.

Seedy przeszukuje silnik na danych PoB (tablice LUT), a najlepsze kandydaty liczy pełnym kalkulatorem na buildzie.
"""

from __future__ import annotations

from typing import Any

from app.tools.common import pct, side_effects

# typ -> (nazwa, seedy min/max/krok, conquerorzy, wzorce tekstu jewela, opis frakcji)
TIMELESS: dict[int, dict[str, Any]] = {
    1: {"name": "Glorious Vanity", "range": (100, 8000, 1), "faction": "the Vaal",
        "conq": ["Doryani (Corrupted Soul)", "Xibaqua (Divine Flesh)", "Ahuana (Immortal Ambition)"],
        "line": ["Bathed in the blood of {seed} sacrificed in the name of Doryani",
                 "Bathed in the blood of {seed} sacrificed in the name of Xibaqua",
                 "Bathed in the blood of {seed} sacrificed in the name of Ahuana"]},
    2: {"name": "Lethal Pride", "range": (10000, 18000, 1), "faction": "the Karui",
        "conq": ["Kaom (Strength of Blood)", "Rakiata (Tempered by War)", "Akoya (Chainbreaker)"],
        "line": ["Commanded leadership over {seed} warriors under Kaom",
                 "Commanded leadership over {seed} warriors under Rakiata",
                 "Commanded leadership over {seed} warriors under Akoya"]},
    3: {"name": "Brutal Restraint", "range": (500, 8000, 1), "faction": "the Maraketh",
        "conq": ["Asenath (Dance with Death)", "Nasima (Second Sight)", "Balbala (The Traitor)"],
        "line": ["Denoted service of {seed} dekhara in the akhara of Asenath",
                 "Denoted service of {seed} dekhara in the akhara of Nasima",
                 "Denoted service of {seed} dekhara in the akhara of Balbala"]},
    4: {"name": "Militant Faith", "range": (2000, 10000, 1), "faction": "the Templars",
        "conq": ["Avarius (Power of Purpose)", "Dominus (Inner Conviction)", "Maxarius (Transcendence)"],
        "line": ["Carved to glorify {seed} new faithful converted by High Templar Avarius",
                 "Carved to glorify {seed} new faithful converted by High Templar Dominus",
                 "Carved to glorify {seed} new faithful converted by High Templar Maxarius"]},
    5: {"name": "Elegant Hubris", "range": (2000, 160000, 20), "faction": "the Eternal Empire",
        "conq": ["Cadiro (Supreme Decadence)", "Victario (Supreme Grandstanding)", "Caspiro (Supreme Ostentation)"],
        "line": ["Commissioned {seed} coins to commemorate Cadiro",
                 "Commissioned {seed} coins to commemorate Victario",
                 "Commissioned {seed} coins to commemorate Caspiro"]},
}
_DEVOTION = [
    "4% increased Totem Damage per 10 Devotion", "4% increased Brand Damage per 10 Devotion",
    "Channelling Skills deal 4% increased Damage per 10 Devotion", "4% increased Area Damage per 10 Devotion",
    "4% increased Elemental Damage per 10 Devotion", "+2% to all Elemental Resistances per 10 Devotion",
    "3% increased Effect of non-Damaging Ailments on Enemies per 10 Devotion",
    "4% reduced Elemental Ailment Duration on you per 10 Devotion",
    "4% reduced Duration of Curses on you per 10 Devotion", "1% increased Minion Attack and Cast Speed per 10 Devotion",
    "Minions have +60 to Accuracy Rating per 10 Devotion", "Regenerate 0.6 Mana per Second per 10 Devotion",
    "1% reduced Mana Cost of Skills per 10 Devotion", "1% increased effect of Non-Curse Auras per 10 Devotion",
    "3% increased Defences from Equipped Shield per 10 Devotion", "3% increased Mana Cost Efficiency per 10 Devotion",
]
_DEVOTION_NAMES = [
    "Totem Damage", "Brand Damage", "Channelling Damage", "Area Damage", "Elemental Damage", "Elemental Resistances",
    "Effect of non-Damaging Ailments", "Elemental Ailment Duration", "Duration of Curses",
    "Minion Attack and Cast Speed", "Minions Accuracy Rating", "Mana Regen", "Mana Cost (legacy)",
    "Non-Curse Aura Effect", "Defences from Shield", "Mana Cost Efficiency",
]


class JewelError(ValueError):
    """Błąd użycia narzędzia jewel (zły typ, brak socketu itp.) z komunikatem po polsku."""


def timeless_text(jewel_type: int, seed: int, conqueror: int = 1, devotion: tuple[int, int] = (0, 1)) -> str:
    """Tekst Timeless Jewel w formacie PoB. conqueror: 1-3 (kolejność jak w grze); devotion: dwa warianty Militant Faith (0-15)."""
    t = TIMELESS.get(jewel_type)
    if not t:
        raise JewelError(f"Nieznany typ Timeless Jewel: {jewel_type} (1-5).")
    if conqueror not in (1, 2, 3):
        raise JewelError("conqueror musi być 1, 2 albo 3.")
    lo, hi, step = t["range"]
    if not (lo <= seed <= hi) or (seed - lo) % step:
        raise JewelError(f"Seed {seed} jest poza zakresem {t['name']} ({lo}-{hi}, krok {step}).")
    variants = "\n".join(f"Variant: {c}" for c in t["conq"])
    extra_sel, extra_lines = "", ""
    lines = "\n".join(f"{{variant:{i + 1}}}{l.format(seed=seed)}" for i, l in enumerate(t["line"]))
    if jewel_type == 4:
        variants += "\n" + "\n".join(f"Variant: {n}" for n in _DEVOTION_NAMES)
        lines += "\n" + "\n".join(f"{{variant:{i + 4}}}{l}" for i, l in enumerate(_DEVOTION))
        extra_sel = "\nHas Alt Variant: true\nHas Alt Variant Two: true"
        extra_lines = f"\nSelected Alt Variant: {devotion[0] + 4}\nSelected Alt Variant Two: {devotion[1] + 4}"
    return (f"{t['name']} [{seed}]\nTimeless Jewel\nLeague: Legion\nLimited to: 1 Historic{extra_sel}\n{variants}\n"
            f"Selected Variant: {conqueror}{extra_lines}\nRadius: Large\nImplicits: 0\n{lines}\n"
            f"Passives in radius are Conquered by {t['faction']}\nHistoric\n")


def _norm_wanted(wanted: list[Any]) -> list[dict[str, Any]]:
    out = []
    for w in wanted or []:
        if isinstance(w, str):
            text, weight = w, 1.0
        else:
            text, weight = str(w.get("text", "")), float(w.get("weight", 1.0))
        text = text.strip().lower()
        if text:
            out.append({"text": text, "weight": weight})
    if not out:
        raise JewelError("Podaj co najmniej jedną szukaną statystykę (po angielsku, np. 'critical strike chance').")
    return out


def overview(engine: Any) -> dict[str, Any]:
    """Sockety jewel z zawartością i punktami dojścia do niezaalokowanych, plus jewele poza socketami."""
    engine.jewel_reset()
    info = engine.jewel_info()
    return {"sockets": info["sockets"], "spare": info["spare"]}


def search_timeless(engine: Any, *, jewel_type: int, socket: int, wanted: list[Any], goal: str = "damage",
                    conqueror: int | None = None, top_n: int = 10, scan_top: int = 24, near: int = 0,
                    alloc: bool = False, devotion: tuple[int, int] = (0, 1), progress: Any = None) -> dict[str, Any]:
    """Seedy pasujące do wpisanych statów (LUT z PoB), potem pełne przeliczenie najlepszych na tym buildzie."""
    from app.calc import PobError
    from app.tools.common import floor_violations, goal_setup

    t = TIMELESS.get(jewel_type)
    if not t:
        raise JewelError("Typ Timeless Jewel: 1 Glorious Vanity, 2 Lethal Pride, 3 Brutal Restraint, 4 Militant Faith, 5 Elegant Hubris.")
    wanted = _norm_wanted(wanted)
    engine.jewel_reset()
    base = engine.stats()
    metric, floors = goal_setup(goal)
    base_val = base.get(metric, 0)
    try:
        scan = engine.tl_search(jewel_type, socket, wanted, top=scan_top, near=near)
    except PobError as e:
        raise JewelError(str(e)) from e
    cands = scan["results"] or []
    conqs = [conqueror] if conqueror in (1, 2, 3) else [1, 2, 3]
    total, done = max(1, len(cands) * len(conqs)), 0
    ranking: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    try:
        for c in cands:
            for cq in conqs:
                done += 1
                if progress:
                    progress(done, total)
                try:
                    r = engine.jewel_trial(socket, timeless_text(jewel_type, c["seed"], cq, devotion), alloc=alloc)
                except PobError as e:
                    raise JewelError(str(e)) from e
                st = r["stats"]
                entry = {"seed": c["seed"], "conqueror": t["conq"][cq - 1], "conqueror_index": cq,
                         "match_score": round(c["score"], 2), "hits": c["hits"][:6],
                         "delta_pct": pct(st.get(metric, 0), base_val), "side_effects": side_effects(st, base, metric)}
                why = floor_violations(st, base, floors)
                if why:
                    excluded.append({**entry, "reason": "; ".join(why)})
                else:
                    ranking.append(entry)
    finally:
        engine.jewel_reset()
    key = lambda e: e["delta_pct"] if e["delta_pct"] is not None else -1e9  # noqa: E731
    ranking.sort(key=key, reverse=True)
    excluded.sort(key=key, reverse=True)
    return {
        "jewel": t["name"], "socket": socket, "goal": goal, "metric": metric, "baseline": round(base_val, 2),
        "wanted": wanted, "seeds_scanned": scan["scanned"], "seeds_matching": scan["matched"],
        "nodes_in_radius_counted": scan["targets"], "note": scan.get("note"),
        "ranking": ranking[:top_n], "excluded_by_floor": excluded[:top_n],
        "not_checked": [
            "Wstępny wybór seedów idzie po frazach w statach nodów (bez wartości rolli); dokładnie policzone są tylko najlepsze kandydaty.",
            "Ceny i dostępność konkretnych seedów (to osobny krok: wyszukiwanie na trade).",
        ],
    }


def swap_sockets(engine: Any, a: int, b: int, *, goal: str = "damage", alloc: bool = False) -> dict[str, Any]:
    """Co się stanie, gdy jewele z socketów a i b zamienią się miejscami."""
    from app.calc import PobError
    from app.tools.common import floor_violations, goal_setup

    engine.jewel_reset()
    base = engine.stats()
    metric, floors = goal_setup(goal)
    try:
        r = engine.jewel_swap(a, b, alloc=alloc)
    except PobError as e:
        raise JewelError(str(e)) from e
    finally:
        pass
    st = r["stats"]
    engine.jewel_reset()
    return {"a": a, "b": b, "goal": goal, "metric": metric, "baseline": round(base.get(metric, 0), 2),
            "delta_pct": pct(st.get(metric, 0), base.get(metric, 0)), "side_effects": side_effects(st, base, metric),
            "floor_violations": floor_violations(st, base, floors),
            "now_in_a": (r.get("a") or {}).get("name"), "now_in_b": (r.get("b") or {}).get("name")}


def try_jewel(engine: Any, socket: int, text: str, *, goal: str = "damage", alloc: bool = False) -> dict[str, Any]:
    """Wynik włożenia dowolnego jewela (tekst z gry / PoB) do socketu, bez zapisywania zmiany."""
    from app.calc import PobError
    from app.tools.common import floor_violations, goal_setup

    engine.jewel_reset()
    base = engine.stats()
    metric, floors = goal_setup(goal)
    try:
        r = engine.jewel_trial(socket, text, alloc=alloc)
    except PobError as e:
        raise JewelError(str(e)) from e
    finally:
        pass
    st = r["stats"]
    engine.jewel_reset()
    return {"socket": socket, "goal": goal, "metric": metric, "baseline": round(base.get(metric, 0), 2),
            "delta_pct": pct(st.get(metric, 0), base.get(metric, 0)), "side_effects": side_effects(st, base, metric),
            "floor_violations": floor_violations(st, base, floors), "item": (r.get("item") or {}).get("name")}
