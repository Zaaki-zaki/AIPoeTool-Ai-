"""Sesja: wczytany build + silnik PoB + baza gry + historia rozmowy."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.gamedata import GameDB
from app.parser.models import Build
from app.parser.parse import decode_code, parse_xml
from app.tools.supports import COLOR_LETTER

SUMMARY_STATS = (
    "Life", "EnergyShield", "Mana", "Armour", "Evasion", "FireResist", "ColdResist",
    "LightningResist", "ChaosResist", "TotalEHP", "TotalDPS", "CombinedDPS", "FullDPS", "Speed",
    "CritChance", "ManaUnreserved",
)
MAX_HISTORY_TURNS = 6


def _fmt_gem(g: dict[str, Any]) -> str:
    return f"{g['name']} {g.get('level', '?')}/{g.get('quality', 0)}"


def build_summary(build: Build, db: GameDB, loaded: dict[str, Any]) -> dict[str, Any]:
    """Zwięzłe streszczenie buildu dla modelu (kilka tysięcy znaków, nie cały XML)."""
    versions = db.meta().get("tree_versions", "").split(",")
    tree_version = build.tree_version if build.tree_version in versions else None
    nodes = db.resolve_nodes(build.tree_nodes, tree_version)
    warnings = list(build.warnings)
    if build.tree_version and tree_version is None:
        warnings.append(
            f"Drzewko {build.tree_version} nie ma w bazie (są: {', '.join(versions)}); "
            "nazwy nodów mogą być niepełne."
        )

    groups = []
    for g in loaded.get("groups", []):
        actives, supports = [], []
        for gem in g["gems"]:
            info = db.gem(gem["name"]) if gem.get("name") else None
            (supports if info and info["is_support"] else actives).append(_fmt_gem(gem))
        groups.append({
            "group": g["index"], "slot": g.get("slot"), "from_item": bool(g.get("source")),
            "enabled": g.get("enabled", True), "active_skills": actives, "supports": supports,
        })

    items = [
        {"slot": i.slot, "name": i.name, "base": i.base, "rarity": i.rarity, "sockets": i.sockets}
        for i in build.equipped_items
    ]
    stats = loaded.get("stats", {})
    return {
        "class": build.class_name, "ascendancy": build.ascendancy, "level": build.level,
        "main_skill_in_pob": stats.get("MainSkill"),
        "stats": {k: round(stats[k], 2) for k in SUMMARY_STATS if k in stats},
        "skill_groups": groups,
        "items": items,
        "tree": {
            "keystones": nodes["keystones"], "notables": nodes["notables"],
            "masteries_count": len(nodes["masteries"]), "jewel_sockets": nodes["jewel_sockets"],
            "cluster_nodes_count": nodes["cluster_nodes"],
        },
        "warnings": warnings,
    }


@dataclass
class Session:
    db: GameDB
    engine: Any  # PobEngine albo atrapa z tym samym interfejsem
    build: Build
    summary: dict[str, Any]
    history: list[dict[str, Any]] = field(default_factory=list)
    # ostatnie opcje z recommend_tree (z listami nodów do podglądu) i układ drzewka; nie trafiają do modelu
    last_tree: dict[str, Any] | None = None
    layout: dict[str, Any] | None = None
    xml: str | None = None          # XML wczytanego buildu (do przywrócenia silnika po planowaniu)
    plan: dict[str, Any] | None = None  # ostatnia ścieżka rozwoju (kroki, supporty); układ drzewka osobno
    plan_layout: dict[str, Any] | None = None
    plan_done: int = 0              # ile punktów planu gracz oznaczył jako wzięte
    discovery: Any = None  # app.discovery.Discovery; tworzone przy pierwszym użyciu (testy podstawiają atrapę)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def create(cls, code: str, db: GameDB, engine: Any) -> "Session":
        """code: kod PoB (base64) albo surowy XML. Zwraca gotową sesję lub rzuca ValueError/PobError."""
        text = code.strip()
        if text.startswith("<"):
            xml = text
        else:
            try:
                xml = decode_code(text)
            except Exception as e:  # noqa: BLE001
                raise ValueError("To nie wygląda na kod Path of Building (base64) ani na XML.") from e
        build = parse_xml(xml)
        loaded = engine.load_xml(xml)
        return cls(db=db, engine=engine, build=build, summary=build_summary(build, db, loaded), xml=xml)

    @classmethod
    def empty(cls, db: GameDB, engine: Any) -> "Session":
        """Sesja bez buildu: tryb planera (research i ścieżka rozwoju od zera)."""
        return cls(db=db, engine=engine, build=None, summary={"planner_mode": True})  # type: ignore[arg-type]

    @property
    def has_build(self) -> bool:
        return self.build is not None

    def remember(self, question: str, answer: str) -> None:
        """Do historii trafiają tylko pytanie i końcowa odpowiedź (wyniki narzędzi zjadałyby kontekst)."""
        self.history += [{"role": "user", "content": question},
                         {"role": "assistant", "content": answer}]
        self.history = self.history[-2 * MAX_HISTORY_TURNS:]


def color_letter(effect_color: int | None) -> str:
    return COLOR_LETTER.get(effect_color or 4, "W")
