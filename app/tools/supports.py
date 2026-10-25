"""recommend_supports: najlepsze supporty do skilla w konkretnym buildzie.

Proces (cały w kodzie, bez udziału modelu):
  1. kandydaci z bazy gry (kompatybilność liczona jak w PoB)
  2. filtr: duplikaty i warianty tego samego supporta, level postaci, kolor wolnych socketów
  3. PoB liczy każdego kandydata osobno
  4. najlepsi kandydaci składani są w kombinacje pasujące do wolnych socketów, PoB liczy każdą kombinację
  5. wynik: ranking z liczbami z PoB + lista tego, czego narzędzie NIE sprawdziło
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol

from app.gamedata import GameDB
from app.parser.models import Build

COLOR_LETTER = {1: "R", 2: "G", 3: "B", 4: "W"}
SIDE_STATS = ("Life", "EnergyShield", "TotalEHP", "ManaUnreserved", "LifeUnreserved")

# Domyślne progi: wynik nie może pogorszyć tych statów poniżej podanej części OBECNEGO buildu
# (stan grupy sprzed jakichkolwiek zmian). Wyłączenie: floor={}.
DEFAULT_FLOOR = {"TotalEHP": 0.95}

NOT_CHECKED = [
    "Ograniczenia broni po stronie supporta/skilla (PoB policzy je jako brak efektu, czyli ~0% zysku).",
    "Wymagania atrybutów (Str/Dex/Int) gemów - liczone są tylko poziom postaci i kolor socketu.",
    "Efekty, których PoB nie modeluje; zobacz 'blind_spots' (kandydaci o ~0% zysku w PoB).",
    "Cena gemów (to dopiero warstwa poe.ninja).",
]


class EngineLike(Protocol):
    def set_main(self, group: int, skill_index: int = 1) -> dict[str, Any]: ...
    def groups(self) -> list[dict[str, Any]]: ...
    def set_group(self, group: int, gems: list[dict[str, Any]]) -> dict[str, Any]: ...


class RecommendError(ValueError):
    """Błąd danych wejściowych, który użytkownik może poprawić (komunikat po polsku)."""


def _pct(new: float, base: float) -> float | None:
    return round((new - base) / base * 100, 2) if base else None


def _assignable(colors_needed: list[str], free: list[str]) -> bool:
    """Czy każdy gem ma swój wolny socket (biały pasuje do dowolnego). Małe k, więc brute force."""
    if len(colors_needed) > len(free):
        return False
    for perm in itertools.permutations(free, len(colors_needed)):
        if all(p == "W" or p == need or need == "W" for p, need in zip(perm, colors_needed, strict=True)):
            return True
    return False


def _level_req(effect: dict[str, Any], level: int) -> int | None:
    levels = effect.get("levels") or []
    if not levels:
        return None
    entry = levels[min(level, len(levels)) - 1]
    return entry.get("levelRequirement") if isinstance(entry, dict) else None


def recommend_supports(
    engine: EngineLike,
    db: GameDB,
    build: Build,
    group: int,
    skill_index: int = 1,
    *,
    free_sockets: list[str] | None = None,
    keep: list[str] | None = None,
    metric: str = "CombinedDPS",
    top_n: int = 5,
    gem_level: int = 20,
    gem_quality: int = 20,
    pool_size: int = 12,
    max_combo_sims: int = 400,
    blind_spot_band_pct: float = 0.5,
    floor: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Zwraca ranking uzupełnień wolnych socketów (tryb 'fill') albo podmian pojedynczych supportów ('replace').

    - free_sockets=["R","W"]: grupa ma 2 wolne sockety (kolory R/G/B/W), obecne gemy zostają.
    - keep=[...]: zostają tylko wymienione supporty, reszta jest zdejmowana i ich sockety stają się wolne.
    - bez obu: tryb 'replace' - dla każdego obecnego supportu najlepszy zamiennik i jego wartość.
    - floor={"TotalEHP": 0.95}: wyniki, które obniżają dany stat poniżej tej części obecnego buildu,
      są wykluczone z rankingu (domyślnie EHP >= 95%). floor={} wyłącza progi.
    Stan silnika po wywołaniu jest przywracany do oryginalnej grupy.
    """
    if group < 1 or group > len(engine.groups()):
        raise RecommendError(f"Nie ma grupy skilli nr {group}.")
    original = engine.groups()[group - 1]
    if original.get("source"):
        raise RecommendError("Ta grupa pochodzi z itemu - nie da się jej edytować, PoB odtwarza ją sam.")
    try:
        return _run(
            engine, db, build, group, skill_index, original, free_sockets, keep, metric, top_n,
            gem_level, gem_quality, pool_size, max_combo_sims, blind_spot_band_pct,
            DEFAULT_FLOOR if floor is None else floor,
        )
    finally:
        engine.set_group(group, original["gems"])


def _run(
    engine: EngineLike, db: GameDB, build: Build, group: int, skill_index: int,
    original: dict[str, Any], free_sockets: list[str] | None, keep: list[str] | None,
    metric: str, top_n: int, gem_level: int, gem_quality: int, pool_size: int,
    max_combo_sims: int, band: float, floors: dict[str, float],
) -> dict[str, Any]:
    main_stats = engine.set_main(group, skill_index)
    orig_stats = dict(main_stats)  # stan sprzed zmian: punkt odniesienia dla progów

    def violation(stats: dict[str, Any]) -> str | None:
        for key, ratio in floors.items():
            ref, val = orig_stats.get(key), stats.get(key)
            if ref and val is not None and val < ref * ratio:
                return f"{key} spadłoby do {val / ref:.0%} obecnego (próg {ratio:.0%})"
        return None

    skill_name = main_stats.get("MainSkill")
    gem = db.gem(skill_name) if skill_name else None
    if gem is None or gem["is_support"]:
        raise RecommendError(f"Główny skill grupy ('{skill_name}') nie jest aktywnym gemem znanym w bazie.")

    gems = [dict(g) for g in original["gems"]]

    def is_support(g: dict[str, Any]) -> bool:
        dbg = db.gem(g["name"]) if g.get("name") else None
        return bool(dbg and dbg["is_support"])

    support_idx = [i for i, g in enumerate(gems) if is_support(g)]
    removed: list[dict[str, Any]] = []
    if keep is not None:
        keep_l = {k.lower() for k in keep}
        base = [g for g in gems if not is_support(g) or g["name"].lower() in keep_l]
        removed = [g for g in gems if is_support(g) and g["name"].lower() not in keep_l]
        gems = base
    free = [c.upper() for c in (free_sockets or [])]
    for g in removed:
        dbg = db.gem(g["name"])
        free.append(COLOR_LETTER.get(dbg["effect"]["color"], "W") if dbg and dbg["effect"] else "W")

    mode = "fill" if free else "replace"
    if mode == "replace" and not support_idx:
        raise RecommendError("Grupa nie ma supportów do podmiany. Podaj free_sockets, np. ['R','W'].")

    base_res = engine.set_group(group, gems)
    base_stats = base_res["stats"]
    base_val = base_stats.get(metric, 0)
    if not base_val:
        raise RecommendError(
            f"Metryka {metric} wynosi 0 dla skilla '{skill_name}' - PoB nie liczy dla niego tego wskaźnika. "
            "Podaj inną metrykę (np. TotalEHP) albo wybierz inny skill."
        )

    present_effects = set()
    for g in gems:
        dbg = db.gem(g["name"]) if g.get("name") else None
        if dbg:
            present_effects.add(dbg["effect_id"])
    present_plus = {
        (db.effect(e) or {}).get("flags", {}).get("plusVersionOf") for e in present_effects
    } - {None}

    candidates, rejected = [], []
    for cand in db.compatible_supports(skill_name):
        eid = cand["effect_id"]
        plus = cand["flags"].get("plusVersionOf")
        if eid in present_effects or plus in present_effects or eid in present_plus:
            rejected.append((cand["name"], "ten support (lub jego wersja Awakened) już jest w grupie"))
            continue
        eff = db.effect(eid)
        lvl = min(gem_level, (db.gem(cand["name"]) or {}).get("max_level") or gem_level)
        req = _level_req(eff, lvl) if eff else None
        if req is not None and req > build.level:
            rejected.append((cand["name"], f"wymaga poziomu postaci {req}"))
            continue
        letter = COLOR_LETTER.get(cand["color"], "W")
        if mode == "fill" and not _assignable([letter], free):
            rejected.append((cand["name"], f"brak wolnego socketu koloru {letter}"))
            continue
        candidates.append({**cand, "letter": letter, "level": lvl})

    def spec(c: dict[str, Any]) -> dict[str, Any]:
        return {"name": c["name"], "level": c["level"], "quality": gem_quality}

    def side(stats: dict[str, Any]) -> dict[str, float]:
        return {
            k: round(stats[k] - base_stats[k], 2)
            for k in SIDE_STATS if k in stats and k in base_stats and stats[k] != base_stats[k]
        }

    result: dict[str, Any] = {
        "mode": mode, "skill": skill_name, "metric": metric, "baseline": round(base_val, 2),
        "free_sockets": free, "candidates_evaluated": 0, "not_checked": NOT_CHECKED,
        "floor": floors, "excluded_by_floor": [],
        "rejected": [{"name": n, "reason": r} for n, r in rejected],
    }

    if mode == "replace":
        return _replace(engine, group, gems, original, db, candidates, spec, metric, base_val,
                        base_stats, side, result, top_n, band, violation)

    singles = []
    single_stats: dict[str, dict[str, Any]] = {}
    for c in candidates:
        s = engine.set_group(group, gems + [spec(c)])["stats"]
        singles.append((s.get(metric, 0), c))
        single_stats[c["name"]] = s
    result["candidates_evaluated"] = len(singles)
    singles.sort(key=lambda t: t[0], reverse=True)
    result["blind_spots"] = [
        c["name"] for v, c in singles if abs(_pct(v, base_val) or 0) <= band
    ]
    result["best_single"] = [
        {"support": c["name"], "value": round(v, 2), "delta_pct": _pct(v, base_val)}
        for v, c in singles[:top_n]
    ]

    k = len(free)
    pool = [c for _, c in singles[:pool_size]]
    # gemy pomagające statom z progów (np. EHP) mogą mieć słaby DPS, ale muszą trafić do puli kombinacji
    for key in floors:
        best_for_key = sorted(
            (c for _, c in singles), key=lambda c: single_stats[c["name"]].get(key, 0), reverse=True
        )[:4]
        pool += [c for c in best_for_key if c not in pool]
    combos = [
        combo for combo in itertools.combinations(pool, k)
        if _assignable([c["letter"] for c in combo], free)
    ]
    # kolejność: najpierw kombinacje z najlepszych pojedynczych, żeby limit symulacji uciął najgorsze
    combos = combos[:max_combo_sims]
    scored = []
    for combo in combos:
        s = engine.set_group(group, gems + [spec(c) for c in combo])["stats"]
        why = violation(s)
        if why:
            result["excluded_by_floor"].append(
                {"supports": [c["name"] for c in combo], "delta_pct": _pct(s.get(metric, 0), base_val),
                 "reason": why})
            continue
        scored.append((s.get(metric, 0), combo, s))
    result["combos_evaluated"] = len(scored) + len(result["excluded_by_floor"])
    result["excluded_by_floor"].sort(key=lambda r: r["delta_pct"] or 0, reverse=True)
    result["excluded_by_floor"] = result["excluded_by_floor"][:top_n]
    scored.sort(key=lambda t: t[0], reverse=True)
    result["ranking"] = [
        {
            "supports": [c["name"] for c in combo], "value": round(v, 2),
            "delta_pct": _pct(v, base_val), "side_effects": side(s),
        }
        for v, combo, s in scored[:top_n]
    ]
    return result


def _replace(engine, group, gems, original, db, candidates, spec, metric, base_val, base_stats, side,
             result, top_n, band, violation):  # noqa: ANN001
    """Dla każdego obecnego supportu: ile jest wart (strata po zdjęciu) i co byłoby lepszym zamiennikiem."""
    report = []
    sims = 0
    for i, g in enumerate(gems):
        dbg = db.gem(g["name"]) if g.get("name") else None
        if not (dbg and dbg["is_support"]):
            continue
        without = gems[:i] + gems[i + 1:]
        s0 = engine.set_group(group, without)["stats"]
        sims += 1
        cur_color = COLOR_LETTER.get(dbg["effect"]["color"], "W")
        best = []
        for c in candidates:
            if c["letter"] != cur_color and cur_color != "W":
                continue  # zamiennik musi pasować do koloru zwalnianego socketu
            s = engine.set_group(group, without + [spec(c)])["stats"]
            sims += 1
            if violation(s):
                continue
            best.append((s.get(metric, 0), c, s))
        best.sort(key=lambda t: t[0], reverse=True)
        report.append({
            "current": g["name"],
            "value_of_current_pct": _pct(base_val, s0.get(metric, 0)),  # ile % DPS daje ten support
            "best_replacements": [
                {"support": c["name"], "value": round(v, 2), "delta_pct": _pct(v, base_val),
                 "side_effects": side(s)}
                for v, c, s in best[:top_n]
            ],
        })
    result["candidates_evaluated"] = sims
    result["replace_report"] = report
    return result
