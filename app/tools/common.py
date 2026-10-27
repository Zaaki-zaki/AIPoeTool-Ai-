"""Wspólne elementy narzędzi rekomendacji: zmiany procentowe, efekty uboczne i progi przeżywalności."""

from __future__ import annotations

from typing import Any

from app.calc.scores import BALANCED_WEIGHTS, add_scores  # noqa: F401  (re-eksport)

ELE_RESISTS = ("FireResist", "ColdResist", "LightningResist")
RES_CAP = 75
BAND_PCT = 0.5  # zmiana poniżej tej wartości (%) traktujemy jak brak różnicy


def pct(new: float, base: float) -> float | None:
    return round((new - base) / base * 100, 2) if base else None


def side_effects(stats: dict[str, Any], base: dict[str, Any], metric: str = "") -> dict[str, float]:
    """DPS, życie, ES i EHP w % względem obecnego buildu (zmiany <0,1% i sama metryka pomijane), resisty w punktach."""
    out: dict[str, float] = {}
    for k in ("CombinedDPS", "Life", "EnergyShield", "TotalEHP"):
        if k == metric:
            continue
        if base.get(k) and k in stats:
            d = (stats[k] - base[k]) / base[k] * 100
            if abs(d) >= 0.1:
                out[f"{k}_pct"] = round(d, 2)
    for k in ELE_RESISTS:
        if k in stats and k in base and stats[k] != base[k]:
            out[f"{k}_pts"] = round(stats[k] - base[k], 1)
    return out


GOALS: dict[str, tuple[str, dict[str, float]]] = {
    "damage": ("CombinedDPS", {"TotalEHP": 0.95}),                         # DPS, EHP nie niżej niż 95%
    "balanced": ("Balanced", {"CombinedDPS": 0.85, "TotalEHP": 0.85}),     # wynik łączony, żaden nie niżej niż 85%
    "defence": ("TotalEHP", {"CombinedDPS": 0.95}),                        # EHP, DPS nie niżej niż 95%
}
GOAL_LABELS = {
    "CombinedDPS": "więcej obrażeń (EHP nie niżej niż 95%)",
    "Balanced": "zbalansowany (DPS 60% / EHP 40%, żaden nie niżej niż 85%)",
    "TotalEHP": "więcej obrony (DPS nie niżej niż 95%)",
}


def goal_setup(goal: str) -> tuple[str, dict[str, float]]:
    """(metryka, progi) dla celu: damage, balanced albo defence. Nieznany cel traktujemy jak damage."""
    metric, floors = GOALS.get(goal, GOALS["damage"])
    return metric, dict(floors)


def floor_violations(stats: dict[str, Any], base: dict[str, Any], floors: dict[str, float]) -> list[str]:
    """Powody odrzucenia wyniku: statystyka pod progiem albo resist poniżej capa/obecnej wartości."""
    why = []
    for k, frac in floors.items():
        if base.get(k) and stats.get(k, 0) < base[k] * frac:
            why.append(f"{k} spada do {pct(stats.get(k, 0), base[k])}% obecnej wartości")
    for k in ELE_RESISTS:
        floor_v = min(base.get(k, 0), RES_CAP)
        if stats.get(k, 0) < floor_v:
            why.append(f"{k} spada z {round(base.get(k, 0))} do {round(stats.get(k, 0))}")
    return why
