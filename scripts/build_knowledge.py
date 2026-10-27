"""Buduje data/knowledge.json: paczka wiedzy o PoE 1 dla modelu (słowniczek + dane z PoB 3.29 + baza gry).

Użycie: python scripts/build_knowledge.py <katalog src PoB>   (domyślnie ./pob_src albo PoB_SRC)
Źródła: Modules/Data.lua (stałe), Data/Pantheons.lua, Data/Bosses.lua, Modules/ConfigOptions.lua (opcje z opisami),
data/game.db (keystone'y, ascendancy, notable'y), scripts/glossary.py (ręczny słowniczek).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from glossary import G  # noqa: E402


def constants(src: Path) -> dict[str, float]:
    text = (src / "Modules" / "Data.lua").read_text(encoding="utf-8")
    out: dict[str, float] = {}
    for m in re.finditer(r"^\t(\w+) = (-?\d+(?:\.\d+)?),", text, re.M):
        out[m.group(1)] = float(m.group(2))
    misc = (src / "Data" / "Misc.lua").read_text(encoding="utf-8")
    m = re.search(r'\["maximum_physical_damage_reduction_%"\] = (\d+)', misc)  # pierwsze wystąpienie = stałe postaci
    if m:
        out["DamageReductionCap"] = float(m.group(1))
    for k in ("PoisonPercentBase", "IgnitePercentBase", "ImpaleStoredDamageBase", "LeechRateBase"):
        if k in out:
            out[k + "_pct"] = round(out[k] * 100, 1)
    return out


def nondamaging(src: Path) -> str:
    text = (src / "Modules" / "Data.lua").read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(r'\["(\w+)"\] = \{ associatedType = "(\w+)", alt = (\w+), default = ([\w.]+), min = ([\d.]+), max = ([\d.]+), precision = \d+, duration = ([\w.]+) \}', text):
        n, typ, alt, dflt, mn, mx, dur = m.groups()
        rows.append(f"{n} ({typ}): efekt od {mn} do {mx}" + (f", czas bazowo {dur} s" if dur != "nil" else ", czas zależny od siły trafienia"))
    return "Bazowe wartości efektów (dane PoB): " + "; ".join(rows) + ". Chill spowalnia, Freeze unieruchamia, Shock zwiększa obrażenia otrzymywane, Scorch obniża odporności ogień, Brittle zwiększa szansę na krytyk, Sap zmniejsza zadawane obrażenia."


def fmt(text: str, c: dict[str, float], nd: str) -> str:
    def rep(m: re.Match) -> str:
        k = m.group(1)
        if k == "NonDamagingAilments":
            return nd
        v = c[k]
        return str(int(v)) if v == int(v) else str(v)
    return re.sub(r"\{(\w+)\}", rep, text)


def pantheons(src: Path) -> list[dict]:
    text = (src / "Data" / "Pantheons.lua").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'\["(\w+)"\] = \{\s*isMajorGod = (true|false),(.*?)\n\t\},', text, re.S):
        god, major, body = m.groups()
        souls = []
        for s in re.finditer(r'name = "([^"]+)",(.*?)\n\t\t\t\},', body, re.S):
            lines = re.findall(r'line = "([^"]+)"', s.group(2))
            souls.append(f"{s.group(1)}: " + "; ".join(lines))
        title = re.sub(r"(?<!^)([A-Z])", r" \1", god)
        out.append({"id": f"pantheon_{god}", "title": f"Panteon: {title} ({'główny' if major == 'true' else 'mniejszy'})",
                    "aliases": f"pantheon panteon {title.lower()} {god.lower()}", "text": " | ".join(souls) or "brak danych", "source": "PoB Data/Pantheons"})
    return out


def config_options(src: Path) -> list[dict]:
    text = (src / "Modules" / "ConfigOptions.lua").read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'\{ var = "(\w+)", type = "\w+", label = "([^"]+)"(.*?)\},?\n', text):
        var, label, rest = m.groups()
        tip = re.search(r'tooltip = "((?:[^"\\]|\\.)*)"', rest)
        if not tip:
            continue
        out.append({"id": f"cfg_{var}", "title": f"Opcja PoB: {label.rstrip(':')}", "aliases": var.lower(),
                    "text": tip.group(1).replace("\\n", " ").replace('\\"', '"'), "source": "PoB ConfigOptions"})
    return out


def tree_entries(db: Path) -> list[dict]:
    c = sqlite3.connect(db)
    ver = sorted(r[0] for r in c.execute("select distinct version from tree_nodes"))[-1]
    out = []
    for name, kind, asc, stats in c.execute("select name, kind, ascendancy, stats from tree_nodes where version=? and kind in ('keystone','notable') and (kind='keystone' or ascendancy is not null)", (ver,)):
        st = json.loads(stats or "[]")
        if not st:
            continue
        title = f"{'Keystone' if kind == 'keystone' else 'Ascendancy: ' + asc}: {name}"
        out.append({"id": f"node_{len(out)}", "title": title, "aliases": f"{name.lower()} {(asc or '').lower()} {kind}",
                    "text": "; ".join(st), "source": f"drzewko {ver}"})
    return out


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PoB_SRC", ROOT / "pob_src"))
    c, nd = constants(src), nondamaging(src)
    entries = [{"id": i, "title": t, "aliases": a, "text": fmt(x, c, nd), "source": "słowniczek (liczby z PoB)"} for i, t, a, x in G]
    entries += pantheons(src) + tree_entries(ROOT / "data" / "game.db")
    (ROOT / "data" / "knowledge.json").write_text(json.dumps(entries, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(entries), "wpisów")


if __name__ == "__main__":
    main()
