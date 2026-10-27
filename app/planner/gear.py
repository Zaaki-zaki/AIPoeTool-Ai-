"""Itemy poglądowe dla planu: rare'y złożone z modów dostępnych na danym poziomie (tiery z danych PoB, nie z pamięci).

Zasady:
 - ilvl itemu = poziom postaci (ograniczone do ILVL_CAP); mod jest dozwolony, gdy jego wymagany poziom <= ilvl,
 - z każdej grupy modów bierzemy najwyższy dozwolony tier (na poziomie 30 to np. T5, nie T1),
 - waga modu na danej bazie liczona jak w grze: pierwszy tag z `weights` pasujący do tagów bazy; waga 0 = mod nie wypada,
 - wartości zakresów = środek zakresu (średni rzut),
 - wybór modów robi PoB: zachłannie (z leniwą re-ewaluacją), do 3 prefixów i 3 sufixów,
 - baza: najwyższy wymóg poziomu <= poziom postaci, archetyp (armour/evasion/ES) wybiera PoB.
To NIE jest craft: pokazuje, jakiego rzędu mody są realnie osiągalne, a nie konkretny plan craftingu.
"""

from __future__ import annotations

import heapq
import json
import re
from pathlib import Path
from typing import Any

from app.tools.common import add_scores

ILVL_CAP = 84
RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
SLOTS = ["Weapon 1", "Weapon 2", "Helmet", "Body Armour", "Gloves", "Boots", "Amulet", "Ring 1", "Ring 2", "Belt"]
SLOT_TYPE = {"Helmet": "Helmet", "Body Armour": "Body Armour", "Gloves": "Gloves", "Boots": "Boots",
             "Amulet": "Amulet", "Ring 1": "Ring", "Ring 2": "Ring", "Belt": "Belt", "Weapon 2": "Shield"}
CASTER_WEAPONS = ["Wand", "Sceptre", "Staff"]
TWO_HANDED = {"Staff", "Two Handed Axe", "Two Handed Mace", "Two Handed Sword", "Bow"}
# mody, które nie mają sensu na ekwipunku poglądowym (nie wpływają na obliczenia buildu albo wymagają ręcznych warunków)
SKIP_GROUPS = ("ItemFoundRarity", "ItemFoundQuantity", "LocalLightRadius", "LocalAccuracy", "ReducedLocalAttributeRequirements",
               "LocalStunThreshold", "StunThreshold", "Ward", "LightRadius", "ManaRegen", "LocalIncreasedAttackSpeedAndBlind")
WEAPON_FINALISTS = 2
ARMOUR_FINALISTS = 2
MAX_MODS = {"Prefix": 3, "Suffix": 3}
DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "gear.json"


class GearData:
    def __init__(self, path: Path | None = None) -> None:
        d = json.loads((path or DATA_FILE).read_text(encoding="utf-8"))
        self.bases: dict[str, dict[str, Any]] = d["bases"]
        self.mods: dict[str, dict[str, Any]] = d["mods"]

    def best_bases(self, base_type: str, level: int, per_sub: bool = True) -> list[str]:
        """Najlepsza baza (najwyższy wymóg poziomu <= level) w każdym archetypie danego typu."""
        pool: dict[str, tuple[int, str]] = {}
        for name, b in self.bases.items():
            if b["type"] != base_type or not (b.get("tags") or {}).get("default"):
                continue
            req = (b.get("req") or {}).get("level", 0)
            if req > level or "Modifier" in (b.get("implicit") or ""):  # bazy z ograniczeniami craftu (Simplex, Enthalpic...) pomijamy
                continue
            key = b.get("subType") or "-" if per_sub else "-"
            if key not in pool or req > pool[key][0]:
                pool[key] = (req, name)
        return [n for _, n in pool.values()]

    def candidates(self, base: str, ilvl: int, damage: str | None = None) -> list[dict[str, Any]]:
        """Dla każdej grupy modów najwyższy tier dozwolony na tej bazie i ilvl.

        damage: typ obrażeń buildu (physical/fire/cold/lightning/chaos). Mody obrażeń innego żywiołu odpadają,
        bo gear musi pasować do skilla i supportów (np. fizyczny Cyclone nie dostaje "Adds Fire Damage").
        None = bez ograniczeń."""
        tags = (self.bases[base].get("tags") or {})
        best: dict[str, dict[str, Any]] = {}
        for mid, m in self.mods.items():
            if m["level"] > ilvl or m["group"] in best and best[m["group"]]["level"] >= m["level"]:
                continue
            if any(g in (m["group"] or "") for g in SKIP_GROUPS):
                continue
            if damage and not _damage_ok(m["tags"], damage):
                continue
            w = 0
            for key, val in m["weights"].items():  # kolejność jak w grze: pierwszy pasujący tag decyduje
                if key == "default" or tags.get(key):
                    w = val
                    break
            if w <= 0:
                continue
            if any("Socketed Gems" in t or "Minions" in t and "Socketed" in t for t in m["text"]):
                continue
            best[m["group"]] = {**m, "id": mid, "weight": w}
        return list(best.values())


ELEMENTS = {"fire", "cold", "lightning", "chaos"}


def _damage_ok(tags: list[str], damage: str) -> bool:
    t = set(tags)
    if "damage" not in t or "resistance" in t:
        return True
    elems = t & ELEMENTS
    if "elemental" in t:
        elems |= {"elemental"}
    allowed = {damage, "elemental"} if damage in ("fire", "cold", "lightning") else ({damage} if damage == "chaos" else set())
    return elems <= allowed


def _mid(match: re.Match) -> str:
    lo, hi = float(match.group(1)), float(match.group(2))
    v = (lo + hi) / 2
    return str(int(round(v))) if abs(lo) >= 5 or abs(hi) >= 5 or lo == int(lo) and hi == int(hi) and (hi - lo) >= 2 else f"{v:.1f}".rstrip("0").rstrip(".")


def mod_lines(mod: dict[str, Any]) -> list[str]:
    return [RANGE.sub(_mid, t) for t in mod["text"]]


def item_text(data: GearData, base: str, ilvl: int, mods: list[dict[str, Any]], name: str = "Poglądowy") -> str:
    b = data.bases[base]
    imp = b.get("implicit")
    lines = ["Rarity: RARE", name, base, f"Item Level: {ilvl}"]
    imps = [imp] if imp else []
    lines.append(f"Implicits: {len(imps)}")
    lines += imps
    for m in mods:
        lines += mod_lines(m)
    return "\n".join(lines)


def _is_two_handed(base_type: str) -> bool:
    return base_type in TWO_HANDED


class GearBuilder:
    """Dobiera ekwipunek poglądowy na dany poziom, oceniając warianty w PoB (set_item na załadowanym buildzie)."""

    def __init__(self, engine: Any, data: GearData, metric: str, weapon_types: list[str], caster: bool,
                 top_per_round: int = 6, damage: str | None = None) -> None:
        self.e, self.data, self.metric = engine, data, metric
        self.damage = damage
        wt = [t for t in weapon_types if t not in ("None",)]
        self.weapon_types = wt or (CASTER_WEAPONS if caster else ["One Handed Sword", "Two Handed Sword"])
        self.top = top_per_round

    def _score(self, stats: dict[str, Any]) -> float:
        return float(add_scores(stats).get(self.metric) or 0)

    def _try(self, slot: str, text: str) -> float | None:
        try:
            return self._score(self.e.set_item(slot, text)["stats"])
        except Exception:  # noqa: BLE001 - item nie pasuje do slotu / bazy
            return None

    def _fill(self, slot: str, base: str, ilvl: int, cur_score: float) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
        """Zachłannie dokłada mody. Zwraca (mody, wynik końcowy, log kroków)."""
        cands = self.data.candidates(base, ilvl, self.damage)
        chosen: list[dict[str, Any]] = []
        log: list[dict[str, Any]] = []
        score = cur_score
        used: set[str] = set()

        def trial(m: dict[str, Any]) -> float | None:
            return self._try(slot, item_text(self.data, base, ilvl, chosen + [m]))

        heap: list[tuple[float, str]] = []
        by_id = {c["id"]: c for c in cands}
        for c in cands:
            sc = trial(c)
            if sc is not None and sc > score:
                heapq.heappush(heap, (-sc, c["id"]))
        counts = {"Prefix": 0, "Suffix": 0}
        while heap and sum(counts.values()) < 6:
            re_eval: list[tuple[float, str]] = []
            # leniwa ewaluacja: odświeżamy kilku najlepszych, bierzemy najlepszego po odświeżeniu
            for _ in range(min(self.top, len(heap))):
                _, mid = heapq.heappop(heap)
                m = by_id[mid]
                if m["group"] in used or counts[m["type"]] >= MAX_MODS[m["type"]]:
                    continue
                sc = trial(m)
                if sc is not None and sc > score:
                    re_eval.append((-sc, mid))
            if not re_eval:
                break
            re_eval.sort()
            sc, mid = -re_eval[0][0], re_eval[0][1]
            m = by_id[mid]
            chosen.append(m); used.add(m["group"]); counts[m["type"]] += 1
            log.append({"mod": " / ".join(mod_lines(m)), "tier_level": m["level"], "gain_pct": round((sc / score - 1) * 100, 1) if score else None})
            score = sc
            for item in re_eval[1:]:
                heapq.heappush(heap, item)
        self._try(slot, item_text(self.data, base, ilvl, chosen))
        return chosen, score, log

    def build(self, level: int, base_stats: dict[str, Any], keep_two_hander: bool | None = None) -> dict[str, Any]:
        """Wymaga załadowanego buildu (bez itemów albo ze starymi). Zwraca {'items': {slot: tekst}, 'detail': [...], 'stats': {...}}."""
        ilvl = min(ILVL_CAP, max(1, level))
        score = self._score(base_stats)
        items: dict[str, str] = {}
        detail: list[dict[str, Any]] = []
        stats = base_stats

        # 1) broń: dla każdego dozwolonego typu najlepsza baza, wybiera PoB po wyniku z modami
        best: tuple[float, str, str, list, list] | None = None
        ranked = []
        for wtype in self.weapon_types:
            for base in self.data.best_bases(wtype, level, per_sub=False):
                b = self._try("Weapon 1", item_text(self.data, base, ilvl, []))
                if b is not None:
                    ranked.append((b, base, wtype))
        ranked.sort(reverse=True)
        for _, base, wtype in ranked[:WEAPON_FINALISTS]:
            chosen, sc, log = self._fill("Weapon 1", base, ilvl, score)
            if best is None or sc > best[0]:
                best = (sc, base, wtype, chosen, log)
        slots = [s for s in SLOTS if s not in ("Weapon 1", "Weapon 2")]
        if best:
            sc, base, wtype, chosen, log = best
            text = item_text(self.data, base, ilvl, chosen, "Broń poglądowa")
            self._try("Weapon 1", text)
            items["Weapon 1"] = text
            detail.append({"slot": "Weapon 1", "base": base, "type": wtype, "ilvl": ilvl, "mods": log})
            score = sc
            if not _is_two_handed(wtype) and wtype != "Bow":
                slots.insert(0, "Weapon 2")
        for slot in slots:
            btype = SLOT_TYPE[slot]
            bases = self.data.best_bases(btype, level)
            if not bases:
                continue
            pick: tuple[float, str, list, list] | None = None
            if len(bases) > ARMOUR_FINALISTS:
                bare = sorted(((self._try(slot, item_text(self.data, b, ilvl, [])) or 0, b) for b in bases), reverse=True)
                bases = [b for _, b in bare[:ARMOUR_FINALISTS]]
            for base in bases:
                chosen, sc, log = self._fill(slot, base, ilvl, score)
                if pick is None or sc > pick[0]:
                    pick = (sc, base, chosen, log)
            if pick is None or pick[0] <= score * 0.999 and not pick[2]:
                continue
            sc, base, chosen, log = pick
            text = item_text(self.data, base, ilvl, chosen, f"{slot} poglądowy")
            self._try(slot, text)
            items[slot] = text
            detail.append({"slot": slot, "base": base, "ilvl": ilvl, "mods": log})
            score = sc
        return {"items": items, "detail": detail, "score": score}
