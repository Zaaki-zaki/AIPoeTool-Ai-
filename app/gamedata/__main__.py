"""CLI: budowanie i podgląd bazy gry.

    python -m app.gamedata build --pob-src C:\\sciezka\\PathOfBuilding\\src --out data\\game.db
    python -m app.gamedata supports "Cyclone" --db data\\game.db
    python -m app.gamedata gem "Cyclone" --db data\\game.db
    python -m app.gamedata node 12345 --db data\\game.db
"""

from __future__ import annotations

import argparse
import json
import sys

from .db import GameDB


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.gamedata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--pob-src", required=True)
    b.add_argument("--out", default="data/game.db")
    b.add_argument("--tree-versions", default="", help="np. 3_28,3_29 (domyślnie dwie najnowsze)")
    b.add_argument("--commit", default="")
    for name in ("supports", "gem", "node", "unique", "info"):
        p = sub.add_parser(name)
        p.add_argument("--db", default="data/game.db")
        if name != "info":
            p.add_argument("arg")
        if name == "supports":
            p.add_argument("--weapon", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "build":
        vers = [v for v in a.tree_versions.split(",") if v] or None
        db = GameDB.build(a.pob_src, a.out, vers, a.commit or None)
        print(json.dumps(db.counts(), indent=2), db.meta())
        return 0

    db = GameDB(a.db)
    if a.cmd == "info":
        print(json.dumps({"counts": db.counts(), "meta": db.meta()}, indent=2))
    elif a.cmd == "supports":
        res = db.compatible_supports(a.arg, weapon_type=a.weapon)
        print(f"{len(res)} kompatybilnych supportów dla {a.arg}")
        for s in res:
            print(" -", s["name"])
    elif a.cmd == "gem":
        g = db.gem(a.arg)
        if g is None:
            print("brak"); return 1
        g["effect"].pop("levels", None)
        print(json.dumps(g, indent=2, ensure_ascii=False))
    elif a.cmd == "node":
        print(json.dumps(db.node(int(a.arg)), indent=2, ensure_ascii=False))
    elif a.cmd == "unique":
        print(json.dumps(db.unique(a.arg), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
