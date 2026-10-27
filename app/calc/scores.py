"""Wyniki pochodne dopisywane do statów z PoB (bez zależności od reszty aplikacji)."""

from __future__ import annotations

from typing import Any

# Wynik zbalansowany: średnia geometryczna ważona. Zmiana % tego wyniku = 0,6 * zmiana % DPS + 0,4 * zmiana % EHP
# (dla małych zmian), więc 10% więcej DPS wyrównuje się z 15% mniej EHP.
BALANCED_WEIGHTS = (0.6, 0.4)


def add_scores(stats: dict[str, Any]) -> dict[str, Any]:
    """Dopisuje do statów wynik łączony 'Balanced' (jeśli są DPS i EHP). Wywoływane przez silnik dla każdej odpowiedzi."""
    dps, ehp = stats.get("CombinedDPS"), stats.get("TotalEHP")
    if isinstance(dps, (int, float)) and isinstance(ehp, (int, float)) and dps > 0 and ehp > 0:
        stats["Balanced"] = dps ** BALANCED_WEIGHTS[0] * ehp ** BALANCED_WEIGHTS[1]
    return stats
