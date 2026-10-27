"""Poradnik z planu: kod importu PoB dla każdego etapu i tekst w układzie znanym z poradników (przegląd, etapy, drzewko, linki, ekwipunek)."""

from __future__ import annotations

import base64
import zlib
from typing import Any

from app.planner.build_xml import make_xml
from app.planner.leveling import ASC_UNLOCK, _gem_level

SLOT_PL = {"Weapon 1": "Broń", "Weapon 2": "Druga ręka", "Helmet": "Hełm", "Body Armour": "Zbroja", "Gloves": "Rękawice",
           "Boots": "Buty", "Amulet": "Amulet", "Ring 1": "Pierścień 1", "Ring 2": "Pierścień 2", "Belt": "Pas"}
STAT_PL = [("CombinedDPS", "DPS"), ("Life", "Życie"), ("EnergyShield", "Tarcza energii"), ("TotalEHP", "EHP"),
           ("FireResist", "Odp. ogień"), ("ColdResist", "Odp. zimno"), ("LightningResist", "Odp. błyskawice")]


def stage_xml(plan: dict[str, Any], idx: int, db: Any) -> str:
    st, m = plan["stages"][idx], plan["meta"]
    gems = [{"name": n, "level": _gem_level(db, n, st["level"]), "quality": 0} for n in [m["skill"], *st["supports"]]]
    return make_xml(class_id=m["class_id"], class_name=m["class_name"], asc_id=m["asc_id"], asc_name=m["asc_name"],
                    level=st["level"], tree_version=m["tree_version"], nodes=st["nodes"], gems=gems, items=st.get("items") or {},
                    masteries={int(k): v for k, v in (st.get("masteries") or {}).items()})


def pob_code(xml: str) -> str:
    """Format importu PoB: zlib + base64 w wariancie URL-safe."""
    return base64.b64encode(zlib.compress(xml.encode("utf-8"), 9)).decode().replace("+", "-").replace("/", "_")


def _fmt(v: Any) -> str:
    return f"{v:,.0f}".replace(",", " ") if isinstance(v, (int, float)) else "-"


def guide_markdown(plan: dict[str, Any], layout: dict[str, Any] | None, db: Any, with_codes: bool = True) -> str:
    m, stages = plan["meta"], plan.get("stages") or []
    names = {n["id"]: n for n in (layout or {}).get("nodes", [])}
    cls = m["asc_name"] if m.get("asc_name") and m["asc_name"] != "None" else m["class_name"]
    L: list[str] = [f"# {m['skill']} {cls}: poradnik rozwoju", ""]
    if not stages:
        return "\n".join(L + ["Ten plan jest ze starej wersji, wygeneruj go jeszcze raz."])
    last = stages[-1]
    L += ["## Przegląd",
          f"Build na {m['skill']} ({plan.get('damage_type') or 'fizyczne'} obrażenia), klasa {m['class_name']}"
          + (f", ascendancy {m['asc_name']}" if m.get("asc_name") and m["asc_name"] != "None" else "") + f". Drzewko {m['tree_version']}.",
          "Wszystkie liczby pochodzą z obliczeń Path of Building na ekwipunku poglądowym (rare'y złożone z modów dostępnych na danym poziomie), nie z pamięci modelu.",
          "", "### Cel końcowy (" + last["name"] + ")"]
    L += [f"- {lab}: {_fmt(last['stats'].get(k))}" for k, lab in STAT_PL if last["stats"].get(k) is not None]
    asc = [n for n in ASC_UNLOCK]
    L += ["", f"Punkty ascendancy: po 2 na poziomach {', '.join(str(a) for a in asc)} (laby)."]
    prev: set[int] = set()
    prev_stats: dict[str, Any] = {}
    for i, st in enumerate(stages):
        nodes = set(st["nodes"])
        new = [names.get(n, {"n": str(n), "t": "?"}) for n in st["nodes"] if n not in prev]
        gone = [names.get(n, {"n": str(n)}) for n in prev if n not in nodes]
        L += ["", f"## {st['name']} (poziom {st['level']})",
              f"Punkty: {len(nodes) - st.get('asc_total', 0)}/{st['points_total']}"
              + (f", w tym {st['quest_points']} z questów w tej sekcji" if st.get("quest_points") else "")
              + (f", ascendancy {st['asc_total']}" if st.get("asc_total") else ""), ""]
        L.append("**Statystyki**")
        for k, lab in STAT_PL:
            v = st["stats"].get(k)
            if v is None:
                continue
            pv = prev_stats.get(k)
            d = f" ({'+' if v >= pv else ''}{_fmt(v - pv)})" if isinstance(pv, (int, float)) and k in ("CombinedDPS", "Life", "TotalEHP") else ""
            L.append(f"- {lab}: {_fmt(v)}{d}")
        key = [x for x in new if x.get("t") in ("K", "A")]
        notable = [x for x in new if x.get("t") == "T"]
        mast = [x for x in new if x.get("t") == "M"]
        L += ["", f"**Drzewko**: +{len(new)} nodów" + (f", -{len(gone)}" if gone else "")]
        for x in key:
            L.append(f"- {'keystone' if x.get('t') == 'K' else 'ascendancy'}: **{x.get('n')}** — " + "; ".join(x.get("d") or []))
        for x in notable:
            L.append(f"- **{x.get('n')}** — " + "; ".join((x.get("d") or [])[:3]))
        for x in mast:
            L.append(f"- mastery {x.get('n')}")
        if not (key or notable or mast):
            L.append("- same węzły małe i droga do następnych")
        if gone:
            L.append("- zwolnione: " + ", ".join(str(x.get("n")) for x in gone[:10]))
        L += ["", f"**Linki ({1 + len(st['supports'])})**: {m['skill']} + " + (", ".join(st["supports"]) or "brak supportów")]
        if st.get("items"):
            L += ["", "**Ekwipunek poglądowy**"]
            for sl, txt in st["items"].items():
                lines = txt.splitlines()
                base = lines[2] if len(lines) > 2 else "?"
                mods = lines[5 + int(lines[4].split(":")[1]) if len(lines) > 4 and lines[4].startswith("Implicits") else 4:]
                L.append(f"- {SLOT_PL.get(sl, sl)}: {base} — " + "; ".join(mods))
        if with_codes:
            L += ["", "**Kod PoB**", "```", pob_code(stage_xml(plan, i, db)), "```"]
        prev, prev_stats = nodes, st["stats"]
    L += ["", "## Uwagi", "- Ekwipunek jest poglądowy: pokazuje rząd wielkości modów osiągalnych na poziomie, a nie plan craftingu.",
          "- Poziomy kończące akty i questowe punkty to założenia, sprawdź je w grze.",
          "- Cena klejnotów pochodzi z poe.ninja; rare'y nie są wyceniane."]
    return "\n".join(L)
