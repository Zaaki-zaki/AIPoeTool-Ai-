"""Eksportuje bazy itemów i mody rare z danych PoB do data/gear.json.

    python scripts/build_gear_data.py --pob-src <ścieżka>/src --out data/gear.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lupa  # noqa: E402
from lupa import LuaRuntime  # noqa: E402

from app.gamedata.luareader import to_py  # noqa: E402

BASE_FILES = ["amulet", "axe", "belt", "body", "boots", "bow", "claw", "dagger", "gloves", "helmet", "mace",
              "quiver", "ring", "shield", "staff", "sword", "wand"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pob-src", required=True)
    ap.add_argument("--out", default="data/gear.json")
    a = ap.parse_args()
    src = Path(a.pob_src) / "Data"
    lua = LuaRuntime(unpack_returned_tuples=True)
    run = lua.eval("function(p) local f = assert(loadfile(p)); return f() end")
    bases = {}
    for f in BASE_FILES:
        tbl = lua.table()
        fn = run(str(src / "Bases" / f"{f}.lua"))
        fn(tbl)
        for name, b in to_py(tbl).items():
            if b.get("type") and "Talisman" not in name:
                bases[name] = {k: b.get(k) for k in ("type", "subType", "socketLimit", "tags", "implicit", "weapon", "armour", "req")}
    mods = {}
    for k, m in to_py(run(str(src / "ModExplicit.lua"))).items():
        if m.get("type") not in ("Prefix", "Suffix"):
            continue
        w = dict(zip(m.get("weightKey") or [], m.get("weightVal") or []))
        if not any(w.values()):
            continue
        lines = [x for x in m if isinstance(x, str) and x.isdigit()]  # nie używane
        text = [v for kk, v in m.items() if kk.isdigit() or kk == "1" or kk == "2"]
        mods[k] = {"type": m["type"], "level": m.get("level", 1), "group": m.get("group"), "text": [t for t in text if isinstance(t, str)],
                   "weights": w, "mult": dict(zip(m.get("weightMultiplierKey") or [], m.get("weightMultiplierVal") or [])),
                   "tags": m.get("modTags") or []}
    Path(a.out).write_text(json.dumps({"bases": bases, "mods": mods}, separators=(",", ":")), encoding="utf-8")
    print(len(bases), "baz,", len(mods), "modów")


main()
