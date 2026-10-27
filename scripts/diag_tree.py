"""Diagnostyka recommend_tree na buildzie testowym: pokazuje, na którym etapie znikają zamiany.

Uruchom:  uv run python scripts/diag_tree.py [numer_fixture]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.calc import PobEngine  # noqa: E402
from app.parser.parse import decode_code  # noqa: E402
from app.tools.common import BAND_PCT, floor_violations, goal_setup, pct  # noqa: E402

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
path = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))[idx]
metric, floors = goal_setup("damage")
with PobEngine() as e:
    base = e.load_xml(decode_code(path.read_text(encoding="utf-8").strip()))["stats"]
    print(f"build: {path.name}")
    print({k: round(base.get(k, 0), 2) for k in ("CombinedDPS", "TotalEHP", "Life", "FireResist", "ColdResist", "LightningResist")})
    info = e.tree_info(max_dist=5)
    print("kandydaci: add", len(info["add"]), "remove", len(info["remove"]), "points", info["points_used"],
          "asc", info.get("asc_name"), info.get("asc_used"))
    adds = sorted(info["add"], key=lambda n: (n["dist"], n["name"], n["id"]))[:60]
    stats = {"gain>band": 0, "gain<=band": 0, "trial_err": 0}
    good = []
    for n in adds:
        try:
            r = e.tree_try(alloc=[n["id"]])
        except Exception as ex:  # noqa: BLE001
            stats["trial_err"] += 1
            continue
        d = pct(r["stats"].get(metric, 0), base.get(metric, 0)) or 0
        stats["gain>band" if d > BAND_PCT else "gain<=band"] += 1
        if d > BAND_PCT:
            good.append((d, n, r))
    print("pojedyncze dodania:", stats)
    for d, n, r in sorted(good, key=lambda t: -t[0])[:5]:
        print(f"  +{d}%  {n['name']} dist={n['dist']} cost={r['points_delta']} floor={floor_violations(r['stats'], base, floors)}")
    rem = []
    for n in info["remove"]:
        r = e.tree_try(dealloc=[n["id"]])
        rem.append((pct(r["stats"].get(metric, 0), base.get(metric, 0)) or 0, n))
    print("usuwanie (najmniej bolesne):")
    for d, n in sorted(rem, key=lambda t: -t[0])[:5]:
        print(f"  {d}%  {n['name']} freed={n['freed']}")
    if good and rem:
        d, n, r = sorted(good, key=lambda t: -t[0])[0]
        for dr, rn in sorted(rem, key=lambda t: -t[0])[:3]:
            if rn["freed"] >= r["points_delta"]:
                x = e.tree_try(dealloc=[rn["id"]], alloc=[n["id"]])
                print(f"para {rn['name']} -> {n['name']}: delta={pct(x['stats'].get(metric, 0), base.get(metric, 0))}% "
                      f"points={x['points_delta']} floor={floor_violations(x['stats'], base, floors)}")
    e.tree_reset()
