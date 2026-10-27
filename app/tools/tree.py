"""Narzędzie: zamiany w drzewku pasywnym liczone w PoB (dodanie notable'a / zwolnienie punktów)."""

from __future__ import annotations

from typing import Any

from .common import BAND_PCT, floor_violations, goal_setup, pct, side_effects

NOT_CHECKED = [
    "Tylko notable'y i keystone'y w zasięgu kilku punktów od obecnego drzewka; małe nody liczone są tylko jako ścieżka.",
    "Ascendancy: tylko własna klasa ascendancy, bez wyborów typu 'jedno z kilku'. Punkty ascendancy liczone osobno.",
    "Nie ruszamy masteries, socketów jewel ani cluster jewel.",
    "Zwolnione punkty to nody, które nic nie niosą dalej (liście), a nie przebudowa całych gałęzi.",
    "Wymagania atrybutów itemów i gemów po zmianie drzewka.",
    "Efekty, których PoB nie modeluje, oraz zależności między keystone'ami a itemami.",
]


def _node(n: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"name": n["name"], "type": n["type"], "pool": n.get("pool", "main"),
            "effects": (n.get("sd") or [])[:4], **extra}


def _cost(n: dict[str, Any], r: dict[str, Any]) -> int:
    """Koszt dodania noda w jego puli punktów (ascendancy liczone osobno od drzewka głównego)."""
    return r.get("asc_delta", 0) if n.get("pool") == "asc" else r["points_delta"]


def _diff(r: dict[str, Any]) -> dict[str, list[int]]:
    """Id nodów, które doszły i odeszły względem obecnego drzewka (do podglądu graficznego)."""
    return {"added": list(r.get("added", [])), "removed": list(r.get("removed", []))}


def _independent(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Od najlepszej: każdy node bierzemy najwyżej raz, żeby proponowane zamiany były niezależne."""
    used: set[str] = set()
    out = []
    for r in sorted(rows, key=lambda e: e["delta_pct"] or 0, reverse=True):
        a, b = r["add"]["name"], r["remove"]["name"]
        if a in used or b in used:
            continue
        used.update((a, b))
        out.append(r)
    return out


def recommend_tree(engine: Any, goal: str = "damage", *, top_n: int = 5, max_dist: int = 5,
                   max_adds: int = 150, max_verify: int = 25, max_verify_asc: int = 8) -> dict[str, Any]:
    """Zwraca zamiany (zwolnij X, weź Y) zweryfikowane symulacją oraz dodania, gdyby były wolne punkty."""
    from app.calc import PobError

    metric, floors = goal_setup(goal)
    info = engine.tree_info(max_dist=max_dist)
    base = info["stats"]
    for n in info["add"] + info["remove"]:
        n.setdefault("pool", "main")
    base_val = base.get(metric, 0)
    adds = sorted(info["add"], key=lambda n: (n["dist"], n["name"], n["id"]))[:max_adds]
    rejected: dict[str, int] = {}

    def trial(**kw: Any) -> dict[str, Any] | None:
        try:
            return engine.tree_try(**kw)
        except PobError as e:
            key = str(e).splitlines()[0].split(":")[0][:60]
            rejected[key] = rejected.get(key, 0) + 1
            return None

    try:
        gains: list[tuple[dict[str, Any], float, dict[str, Any]]] = []  # (node, delta_pct, stats)
        for n in adds:
            r = trial(alloc=[n["id"]])
            if r:
                gains.append((n, pct(r["stats"].get(metric, 0), base_val) or 0.0, r))
        losses: list[tuple[dict[str, Any], float]] = []
        # stała kolejność (kolejność z Lua jest losowa), żeby remisy dawały zawsze ten sam wynik
        for n in sorted(info["remove"], key=lambda n: (n["freed"], n["name"], n["id"])):
            r = trial(dealloc=[n["id"]])
            if r:
                losses.append((n, pct(r["stats"].get(metric, 0), base_val) or 0.0))

        # dodania, gdyby były wolne punkty (koszt = liczba punktów ścieżki)
        free = []
        for n, d, r in gains:
            if d <= BAND_PCT or floor_violations(r["stats"], base, floors):
                continue
            free.append(_node(n, cost_points=_cost(n, r), delta_pct=d,
                              side_effects=side_effects(r["stats"], base, metric), diff=_diff(r)))
        free.sort(key=lambda e: e["delta_pct"] / max(e["cost_points"], 1), reverse=True)

        # zamiany: estymata addytywna, potem prawdziwa symulacja najlepszych par
        pairs = []
        for n, d, r in gains:
            if d <= BAND_PCT:
                continue
            cost = _cost(n, r)
            for rem, loss in losses:
                if rem["pool"] == n["pool"] and rem["freed"] >= cost:
                    pairs.append((d + loss, n, rem))
        pairs.sort(key=lambda t: (-t[0], t[2]["freed"], t[1]["name"], t[2]["name"], t[1]["id"], t[2]["id"]))
        swaps, blocked = [], []
        # ascendancy ma osobną pulę punktów i własną kolejkę weryfikacji, inaczej gubiłaby się wśród nodów z drzewka
        main_pairs = [p for p in pairs if p[1]["pool"] == "main"][:max_verify]
        asc_pairs = [p for p in pairs if p[1]["pool"] == "asc"][:max_verify_asc]
        to_verify = main_pairs + asc_pairs
        for est, add, rem in to_verify:
            r = trial(dealloc=[rem["id"]], alloc=[add["id"]])
            if not r or r["points_delta"] > 0 or r.get("asc_delta", 0) > 0:
                continue
            row = {"remove": _node(rem), "add": _node(add), "pool": add["pool"],
                   "points_delta": r["asc_delta"] if add["pool"] == "asc" else r["points_delta"],
                   "delta_pct": pct(r["stats"].get(metric, 0), base_val),
                   "side_effects": side_effects(r["stats"], base, metric), "diff": _diff(r)}
            why = floor_violations(r["stats"], base, floors)
            (blocked if why else swaps).append({**row, "reason": "; ".join(why)} if why else row)
    finally:
        engine.tree_reset()

    swaps = _independent(swaps)
    blocked = _independent(blocked)
    return {
        "goal": goal, "metric": metric, "baseline": round(base_val, 2), "floor": floors,
        "points_used": info["points_used"], "ascendancy": info.get("asc_name"),
        "ascendancy_points_used": info.get("asc_used", 0),
        "candidates": {"additions": len(adds), "removals": len(info["remove"]), "pairs_verified": len(to_verify)},
        "swaps": swaps[:top_n], "swaps_blocked_by_floor": blocked[:3],
        "additions_if_free_points": free[:top_n], "rejected_counts": rejected,
        "not_checked": NOT_CHECKED,
    }
