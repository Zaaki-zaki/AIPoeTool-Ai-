"""Wysokopoziomowe narzędzie: najlepsze uniki do slotu, liczone przez podmianę itemu w PoB."""

from __future__ import annotations

import re
from typing import Any

from app.gamedata import GameDB

from .common import floor_violations, goal_setup, pct, side_effects

# slot PoB -> kategorie uniqów z bazy (kolumna `file`)
SLOT_FILES: dict[str, tuple[str, ...]] = {
    "Helmet": ("helmet",), "Body Armour": ("body",), "Gloves": ("gloves",), "Boots": ("boots",),
    "Amulet": ("amulet",), "Ring 1": ("ring",), "Ring 2": ("ring",), "Belt": ("belt",),
    "Weapon 1": ("sword", "axe", "mace", "staff", "bow", "wand", "dagger", "claw"),
    "Weapon 2": ("shield", "quiver", "sword", "axe", "mace", "wand", "dagger", "claw"),
}
NOT_CHECKED = [
    "Rolle modów: unik liczony ze środkowymi wartościami zakresów (bez craftów, enchantów, korupcji, influence).",
    "Itemy generowane (np. Replica, Foulborn, Timeless) i ich warianty alternatywne.",
    "Cena i dostępność uniqów (to dopiero warstwa poe.ninja).",
    "Zmiany w drzewku i supportach, które mogłyby wykorzystać nowy item.",
]

_KEEP_PROPS = ("Variant", "LevelReq", "Sockets", "Has Alt Variant", "Selected Variant", "Selected Alt Variant")


def unique_item_text(u: dict[str, Any]) -> str:
    """Tekst uniqa w formacie danych PoB (bez linii Rarity), aktualny wariant = ostatni."""
    lines = [u["name"], u["base"]]
    props = u["props"]
    for key in _KEEP_PROPS:
        for v in props.get(key, []):
            lines.append(f"{key}: {v}")
    lines.append(f"Implicits: {len(u['implicits'])}")
    lines += u["implicits"] + u["explicits"]
    return "\n".join(lines)


def _level_req(u: dict[str, Any]) -> int:
    """Wymagany poziom postaci: z 'LevelReq' albo z linii 'Requires' ("Level 60, 138 Dex")."""
    for key in ("LevelReq", "Requires"):
        for v in u["props"].get(key, []):
            m = re.search(r"(?:Level\s+)?(\d+)" if key == "LevelReq" else r"Level\s+(\d+)", v)
            if m:
                return int(m.group(1))
    return 0


class ItemsError(ValueError):
    """Błąd użycia narzędzia (zły slot itp.) z komunikatem czytelnym dla modelu."""


def recommend_items(engine: Any, db: GameDB, build: Any, slot: str, *, goal: str = "damage",
                    top_n: int = 5, max_sims: int = 200) -> dict[str, Any]:
    """Podmienia po kolei uniki pasujące do slotu i liczy w PoB. Oryginał slotu zawsze wraca."""
    from app.calc import PobError  # lokalnie: moduł ma działać też bez silnika w testach jednostkowych

    if slot not in SLOT_FILES:
        raise ItemsError(f"Nieznany slot '{slot}'. Dostępne: {', '.join(SLOT_FILES)}.")
    metric, floors = goal_setup(goal)

    current = next((i for i in build.equipped_items if i.slot == slot), None)
    worn = {i.name.removeprefix("Foulborn ") for i in build.equipped_items}  # uniki są zwykle limitowane

    engine.reset_item(slot)  # pewny punkt wyjścia
    base = engine.stats()
    base_val = base.get(metric, 0)

    seen: set[str] = set()
    candidates = []
    for u in db.uniques_in(SLOT_FILES[slot]):
        if u["name"] in worn or u["name"] in seen:
            continue
        if _level_req(u) > build.level:
            continue
        seen.add(u["name"])
        candidates.append(u)

    rejected: dict[str, int] = {}
    excluded: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    sims = 0
    try:
        for u in candidates:
            if sims >= max_sims:
                rejected["limit symulacji"] = rejected.get("limit symulacji", 0) + 1
                continue
            try:
                r = engine.set_item(slot, unique_item_text(u))
            except PobError as e:
                key = str(e).splitlines()[0].split(" (")[0].split(":")[0][:60]
                rejected[key] = rejected.get(key, 0) + 1
                continue
            sims += 1
            st = r["stats"]
            entry = {"name": u["name"], "base": u["base"], "delta_pct": pct(st.get(metric, 0), base_val),
                     "side_effects": side_effects(st, base, metric)}
            why = floor_violations(st, base, floors)
            for attr, key in (("Str", "str"), ("Dex", "dex"), ("Int", "int")):
                need, have = r["reqs"].get(key, 0), r["attrs"].get(attr, 0)
                if need > have:
                    why.append(f"brakuje atrybutu {attr}: wymaga {need}, build ma {round(have)}")
            if why:
                excluded.append({**entry, "reason": "; ".join(why)})
            else:
                scored.append(entry)
    finally:
        engine.reset_item(slot)

    scored.sort(key=lambda e: e["delta_pct"] if e["delta_pct"] is not None else -1e9, reverse=True)
    excluded.sort(key=lambda e: e["delta_pct"] if e["delta_pct"] is not None else -1e9, reverse=True)
    return {
        "slot": slot, "goal": goal, "metric": metric, "baseline": round(base_val, 2),
        "current_item": {"name": current.name, "base": current.base} if current else None,
        "floor": floors, "candidates_evaluated": sims, "rejected_counts": rejected,
        "ranking": scored[:top_n], "excluded_by_floor": excluded[:top_n],
        "not_checked": NOT_CHECKED,
    }
