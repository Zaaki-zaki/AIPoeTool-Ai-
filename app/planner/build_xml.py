"""Minimalny build PoB od zera: klasa, ascendancy, poziom, zaalokowane nody i jedna grupa skilli (bez itemów)."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def make_xml(*, class_id: int, class_name: str, asc_id: int, asc_name: str, level: int, tree_version: str,
             nodes: list[int], gems: list[dict], items: dict[str, str] | None = None,
             masteries: dict[int, int] | None = None) -> str:
    items = items or {}
    item_xml = "".join(f'<Item id="{i}">{escape(t)}</Item>' for i, t in enumerate(items.values(), start=1))
    slot_xml = "".join(f'<Slot name={quoteattr(sl)} itemId="{i}"/>' for i, sl in enumerate(items, start=1))
    mast = ",".join(f"{{{n},{e}}}" for n, e in (masteries or {}).items())
    gem_xml = "".join(
        f'<Gem enabled="true" level="{g["level"]}" quality="{g.get("quality", 0)}" nameSpec={quoteattr(g["name"])}/>'
        for g in gems)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><PathOfBuilding>'
        f'<Build level="{level}" targetVersion="3_0" bandit="None" className={quoteattr(class_name)} '
        f'ascendClassName={quoteattr(asc_name or "None")} mainSocketGroup="1"></Build>'
        f'<Tree activeSpec="1"><Spec treeVersion="{tree_version}" classId="{class_id}" ascendClassId="{asc_id}" '
        f'nodes="{",".join(str(n) for n in nodes)}" masteryEffects="{mast}"/></Tree>'
        f'<Skills activeSkillSet="1"><SkillSet id="1"><Skill enabled="true" mainActiveSkill="1">{gem_xml}</Skill></SkillSet></Skills>'
        f'<Items activeItemSet="1">{item_xml}<ItemSet id="1">{slot_xml}</ItemSet></Items></PathOfBuilding>')
