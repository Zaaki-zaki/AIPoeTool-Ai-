"""Parser tekstu itemu skopiowanego w grze (Ctrl+C, także Ctrl+Alt+C) i budowanie zapytania do trade."""

from __future__ import annotations

import re
from typing import Any

SEP = "--------"
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_RANGE = re.compile(r"\((-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)\)")
_ROLLED_RANGE = re.compile(r"(?<=\d)\(-?\d+(?:\.\d+)?-\d+(?:\.\d+)?\)")  # 95(80-99): zostaje rzut, zakres tieru odpada


def _strip_ranges(t: str) -> str:
    return _RANGE.sub(lambda m: m.group(1), _ROLLED_RANGE.sub("", t))
_SUFFIX = re.compile(r"\s*\((implicit|crafted|fractured|enchant|scourge|rune|augmented|unmet)\)\s*$", re.I)
_HEADER_KIND = (("implicit", "implicit"), ("master crafted", "crafted"), ("crafted", "crafted"), ("fractured", "fractured"),
                ("enchant", "enchant"), ("scourge", "scourge"))
_FLAGS = {
    "Corrupted": "corrupted", "Mirrored": "mirrored", "Unidentified": "unidentified", "Fractured Item": "fractured",
    "Synthesised Item": "synthesised", "Split": "split",
}
_INFLUENCE = ("Shaper Item", "Elder Item", "Crusader Item", "Hunter Item", "Redeemer Item", "Warlord Item",
              "Searing Exarch Item", "Eater of Worlds Item")


class ItemParseError(ValueError):
    """Tekst nie wygląda na item z Path of Exile."""


def normalize_mod(text: str) -> str:
    """Tekst moda bez wartości: '+95 to maximum Life' -> '# to maximum life' (tak samo po stronie trade i gry)."""
    t = _strip_ranges(text)
    t = re.sub(r"\s*\((?:implicit|crafted|fractured|enchant|scourge|rune)\)\s*$", "", t, flags=re.I)
    t = _NUM.sub("#", t)
    t = t.replace("+#", "#").replace("-#", "#")
    return re.sub(r"\s+", " ", t).strip().lower()


def _values(text: str) -> list[float]:
    return [float(x) for x in _NUM.findall(_strip_ranges(text))]


def parse_item(text: str) -> dict[str, Any]:
    """Wyciąga z tekstu klasę, rzadkość, nazwę, bazę, poziom itemu, gniazda, jakość i listę modów (z rodzajem)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "Rarity:" not in text:
        raise ItemParseError("To nie wygląda na item z gry (brak linii 'Rarity:'). Skopiuj item w grze przez Ctrl+C.")
    sections = [[l.rstrip() for l in s.strip("\n").split("\n") if l.strip()] for s in re.split(rf"\n{SEP}\n", text)]
    sections = [s for s in sections if s]
    head = sections[0]
    item: dict[str, Any] = {"class": None, "rarity": None, "name": "", "base": "", "mods": [], "flags": [], "influences": []}
    rest: list[str] = []
    for l in head:
        if l.startswith("Item Class:"):
            item["class"] = l.split(":", 1)[1].strip()
        elif l.startswith("Rarity:"):
            item["rarity"] = l.split(":", 1)[1].strip()
        else:
            rest.append(l)
    if item["rarity"] in ("Normal", "Magic", "Gem", "Currency", "Divination Card") and len(rest) >= 1:
        item["name"], item["base"] = "", rest[-1] if item["rarity"] != "Magic" else rest[-1]
        if item["rarity"] in ("Gem", "Currency", "Divination Card"):
            item["name"] = item["base"] = rest[0]
    elif len(rest) >= 2:
        item["name"], item["base"] = rest[0], rest[1]
    elif rest:
        item["name"] = item["base"] = rest[0]
    mod_kind_next: str | None = None
    for sec in sections[1:]:
        for l in sec:
            m = re.match(r"^(Item Level|Level|Quality|Stack Size|Map Tier|Sockets|Gem Level):\s*(.*)$", l)
            if m:
                key, val = m.group(1), m.group(2)
                if key == "Item Level":
                    item["ilvl"] = int(_NUM.search(val).group())  # type: ignore[union-attr]
                elif key in ("Level", "Gem Level"):
                    n = _NUM.search(val)
                    if n and (item["rarity"] == "Gem" or key == "Gem Level"):
                        item["gem_level"] = int(float(n.group()))
                elif key == "Quality":
                    n = _NUM.search(val)
                    item["quality"] = int(float(n.group())) if n else 0
                elif key == "Stack Size":
                    item["stack"] = val
                elif key == "Map Tier":
                    item["map_tier"] = int(float(_NUM.search(val).group()))  # type: ignore[union-attr]
                elif key == "Sockets":
                    item["sockets"] = val
                    groups = val.split()
                    item["links"] = max((len(re.findall(r"[RGBWAD]", g)) for g in groups), default=0)
                continue
            if l in _FLAGS:
                item["flags"].append(_FLAGS[l])
                continue
            if l in _INFLUENCE:
                item["influences"].append(l.replace(" Item", ""))
                continue
            if l.startswith("{") and l.rstrip().endswith("}"):  # nagłówek Ctrl+Alt+C: { Implicit Modifier — Life }
                low = l.lower()
                mod_kind_next = next((k for pat, k in _HEADER_KIND if pat in low), "explicit")
                continue
            if re.match(r"^(Requirements:|Level:|Str:|Dex:|Int:|Armour:|Evasion Rating:|Energy Shield:|Ward:|Physical Damage:|"
                        r"Elemental Damage:|Chaos Damage:|Critical Strike Chance:|Attacks per Second:|Weapon Range:|"
                        r"Block Chance:|Radius:|Limited to:|Note:|Place into|Right click|Shift click|Travel to|Can be|"
                        r"Cannot|Reward:|Item Quantity|Item Rarity|Monster|Area is|Players|Pack Size|More |Corrupted|"
                        r"Unidentified|Cost|Experience:|Cooldown|Cast Time|Mana Cost|Reserves|Cost per)", l):
                mod_kind_next = None
                continue
            if not (_NUM.search(l) or any(w in l.lower() for w in ("immune", "cannot", "grants", "you have", "nearby"))):
                mod_kind_next = None
                continue
            suffix = _SUFFIX.search(l)
            kind = suffix.group(1).lower() if suffix else (mod_kind_next or "explicit")
            if kind in ("rune", "augmented", "unmet"):
                kind = "explicit"
            item["mods"].append({"text": _SUFFIX.sub("", l), "norm": normalize_mod(l), "kind": kind, "values": _values(l)})
    # linie typu 'Quality: +20% (augmented)' i właściwości zbroi nie są modami: odfiltrowane powyżej; usuwamy duplikaty
    seen, mods = set(), []
    for m in item["mods"]:
        k = (m["norm"], m["kind"], tuple(m["values"]))
        if k not in seen:
            seen.add(k)
            mods.append(m)
    item["mods"] = mods
    item["corrupted"] = "corrupted" in item["flags"]
    return item


def build_query(item: dict[str, Any], stats: dict[tuple[str, str], str], *, status: str = "securable",
                min_pct: float = 0.9, min_match: int | None = None, only_kinds: tuple[str, ...] = ("explicit", "implicit", "fractured", "crafted", "enchant"),
                overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Zapytanie do trade dla podobnych itemów. Zwraca {'query': ..., 'filters': [opis filtrów do UI]}.

    Unique/gem/waluta: wyszukiwanie po nazwie. Magiczne i rzadkie: baza + filtry modów (min = min_pct * wartość).
    overrides: {"<id statystyki>": {"min":..,"max":..,"enabled":bool}} z edycji w UI.
    """
    overrides = overrides or {}
    q: dict[str, Any] = {"status": {"option": status}, "stats": []}
    rarity = item.get("rarity")
    filters: list[dict[str, Any]] = []
    if rarity == "Unique":
        if item.get("name"):
            q["name"] = item["name"]
        q["type"] = item["base"]
    elif rarity in ("Gem", "Currency", "Divination Card", "Normal"):
        q["type"] = item["base"] or item["name"]
    else:
        base = item["base"]
        q["type"] = base
    misc: dict[str, Any] = {}
    if item.get("gem_level"):
        misc["gem_level"] = {"min": item["gem_level"]}
    if item.get("quality") and rarity == "Gem":
        misc["quality"] = {"min": item["quality"]}
    if item.get("corrupted") is not None and rarity in ("Gem", "Unique"):
        misc["corrupted"] = {"option": "true" if item["corrupted"] else "false"}
    if misc:
        q["filters"] = {"misc_filters": {"filters": misc}}
    if rarity in ("Rare", "Magic"):
        if item.get("ilvl"):
            q.setdefault("filters", {}).setdefault("misc_filters", {"filters": {}})["filters"]["ilvl"] = {"min": max(1, item["ilvl"] - 4)}
        for m in item["mods"]:
            if m["kind"] not in only_kinds:
                continue
            sid = stats.get((m["kind"], m["norm"])) or stats.get(("explicit", m["norm"])) or stats.get(("implicit", m["norm"]))
            f = {"id": sid, "text": m["text"], "kind": m["kind"], "value": m["values"][0] if m["values"] else None,
                 "mapped": sid is not None, "enabled": m["kind"] != "implicit" and sid is not None}
            if sid and f["value"] is not None:
                lo = f["value"] * min_pct
                f["min"] = round(lo) if abs(lo) >= 10 else round(lo, 1)
                f["max"] = None
            ov = overrides.get(sid or "")
            if ov:
                for k in ("min", "max", "enabled"):
                    if k in ov:
                        f[k] = ov[k]
            filters.append(f)
        on = [f for f in filters if f["enabled"] and f["id"]]
        if on:
            need = min_match if min_match is not None else (len(on) if len(on) <= 3 else len(on) - 1)
            q["stats"].append({"type": "count", "value": {"min": max(1, min(need, len(on)))}, "filters": [
                {"id": f["id"], "value": {k: v for k, v in (("min", f.get("min")), ("max", f.get("max"))) if v is not None}}
                for f in on]})
    if not q["stats"]:
        q["stats"].append({"type": "and", "filters": []})
    return {"query": q, "filters": filters}
