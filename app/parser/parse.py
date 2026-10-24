"""Decode Path of Building export codes and parse the XML into Build models."""
from __future__ import annotations

import argparse
import base64
import binascii
import re
import sys
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Build, Gem, Item, Mod, SkillGroup

KNOWN_FLAGS = {"Corrupted", "Mirrored", "Split", "Unidentified"}
# "Fractured Item", "Searing Exarch Item", "Eater of Worlds Item", "Shaper Item", ...
FLAG_RE = re.compile(r"^[A-Za-z' ]+ Item$")
# "Quality: 20", "Armour: 878", "Cluster Jewel Skill: affliction_..." (header lines, never mods)
KEY_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]*: ")
NOISE_TAG_PREFIXES = ("tags:", "range:", "variant:")

SUMMARY_STATS = (
    "FullDPS", "CombinedDPS", "TotalDPS", "Life", "EnergyShield", "Mana", "Armour", "Evasion",
    "FireResist", "ColdResist", "LightningResist", "ChaosResist",
)


# ---------------------------------------------------------------- decoding

def decode_code(code: str) -> str:
    """PoB code (url-safe base64 + zlib) -> XML text."""
    cleaned = "".join(code.split())
    if not cleaned:
        raise ValueError("empty PoB code")
    if cleaned.startswith(("http://", "https://")):
        raise ValueError("URLs are not supported yet, paste the raw PoB code")
    cleaned = cleaned.replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    try:
        return zlib.decompress(base64.b64decode(cleaned)).decode("utf-8")
    except (binascii.Error, zlib.error, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid PoB code: {exc}") from exc


# ---------------------------------------------------------------- helpers

def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _opt_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else None


def _bool(value: str | None, default: bool = True) -> bool:
    return default if value is None else value.strip().lower() == "true"


def _pick_set(parent: ET.Element, child_tag: str, active_attr: str) -> ET.Element | None:
    """Return the active <SkillSet>/<ItemSet>/<ConfigSet> child, or None if the parent has none."""
    sets = parent.findall(child_tag)
    if not sets:
        return None
    active = parent.get(active_attr, "1")
    return next((s for s in sets if s.get("id") == active), sets[0])


# ---------------------------------------------------------------- skills

def _parse_gem(el: ET.Element) -> Gem:
    skill_id = el.get("skillId", "")
    gem_id = el.get("gemId", "")
    name = el.get("nameSpec") or skill_id or gem_id.rsplit("/", 1)[-1] or "?"
    return Gem(
        name=name,
        skill_id=skill_id,
        level=_int(el.get("level")),
        quality=_int(el.get("quality")),
        enabled=_bool(el.get("enabled")),
        is_support=skill_id.startswith("Support") or "SupportGem" in gem_id,
    )


def _use_second_weapon_set(root: ET.Element) -> bool:
    items_el = root.find("Items")
    if items_el is None:
        return False
    chosen = _pick_set(items_el, "ItemSet", "activeItemSet")
    for el in (chosen, items_el):
        if el is not None and el.get("useSecondWeaponSet") is not None:
            return _bool(el.get("useSecondWeaponSet"), False)
    return False


def _slot_active(slot: str | None, use_second: bool) -> bool:
    if slot is None:
        return True
    if slot.endswith(" Swap"):
        return use_second
    if slot in ("Weapon 1", "Weapon 2"):
        return not use_second
    return True


def _parse_skill_groups(root: ET.Element, use_second: bool = False) -> list[SkillGroup]:
    skills = root.find("Skills")
    if skills is None:
        return []
    chosen = _pick_set(skills, "SkillSet", "activeSkillSet")
    elements = chosen.findall("Skill") if chosen is not None else skills.findall("Skill")
    groups = []
    for index, el in enumerate(elements, start=1):
        slot = el.get("slot") or None
        groups.append(
            SkillGroup(
                index=index,
                label=el.get("label", ""),
                slot=slot,
                active=_slot_active(slot, use_second),
                enabled=_bool(el.get("enabled")),
                source=el.get("source") or None,
                main_active_index=_opt_int(el.get("mainActiveSkill")),
                gems=[_parse_gem(g) for g in el.findall("Gem")],
            )
        )
    return groups


# ---------------------------------------------------------------- items

def _split_tags(line: str) -> tuple[list[str], str]:
    """'{crafted}{tags:life}+10 to Life' -> (['crafted', 'tags:life'], '+10 to Life')."""
    tags: list[str] = []
    while line.startswith("{"):
        end = line.find("}")
        if end == -1:
            break
        tags.append(line[1:end])
        line = line[end + 1:]
    return tags, line.strip()


def _is_flag(text: str) -> bool:
    return text in KNOWN_FLAGS or text.startswith("Foil Unique") or bool(FLAG_RE.match(text))


def _is_header_line(line: str) -> bool:
    return line.startswith("Requires ") or _is_flag(line) or bool(KEY_VALUE_RE.match(line))


def _build_mods(
    lines: list[str], kind: str, selected_variant: str | None, flags: list[str]
) -> list[Mod]:
    mods: list[Mod] = []
    for line in lines:
        tags, text = _split_tags(line)
        variants: list[str] | None = None
        clean: list[str] = []
        for tag in tags:
            if tag.startswith("variant:"):
                variants = tag[len("variant:"):].split(",")
            elif tag.startswith(NOISE_TAG_PREFIXES):
                continue
            else:
                clean.append(tag)
        if variants is not None and selected_variant is not None and selected_variant not in variants:
            continue  # line belongs to a variant of the unique that is not selected
        if _is_flag(text):
            flags.append(text)
            continue
        if text:
            mods.append(Mod(text=text, kind=kind, tags=clean))
    return mods


def parse_item_text(item_id: int, text: str, slot: str | None = None) -> Item:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("Rarity:"):
        raise ValueError(f"item {item_id}: no Rarity line")
    rarity = lines[0].split(":", 1)[1].strip().upper()
    name = lines[1] if len(lines) > 1 else ""
    start = 2
    base: str | None = None
    if rarity in ("RARE", "UNIQUE", "RELIC") and len(lines) > 2:
        base = lines[2]
        start = 3
    elif rarity == "NORMAL":
        base = name
    body = lines[start:]

    imp_idx = next((i for i, ln in enumerate(body) if ln.startswith("Implicits:")), None)
    if imp_idx is not None:
        header = body[:imp_idx]
        count = _int(body[imp_idx].split(":", 1)[1])
        rest = body[imp_idx + 1:]
        implicit_lines, explicit_lines = rest[:count], rest[count:]
    else:  # no Implicits marker: header = leading header-looking lines, everything else is a mod
        n = 0
        while n < len(body) and _is_header_line(body[n]):
            n += 1
        header, implicit_lines, explicit_lines = body[:n], [], body[n:]

    props: dict[str, str] = {}
    flags: list[str] = []
    for line in header:
        if _is_flag(line):
            flags.append(line)
        elif line.startswith("Requires "):
            props.setdefault("Requires", line[len("Requires "):])
        elif ": " in line:
            key, value = line.split(": ", 1)
            props.setdefault(key.strip(), value.strip())
        else:
            flags.append(line)

    selected = props.get("Selected Variant")
    mods = _build_mods(implicit_lines, "implicit", selected, flags)
    mods += _build_mods(explicit_lines, "explicit", selected, flags)

    return Item(
        id=item_id,
        slot=slot,
        rarity=rarity,
        name=name,
        base=base,
        quality=_opt_int(props.get("Quality")),
        sockets=props.get("Sockets"),
        level_req=_opt_int(props.get("LevelReq")),
        armour=_opt_int(props.get("Armour")),
        evasion=_opt_int(props.get("Evasion")),
        energy_shield=_opt_int(props.get("Energy Shield")),
        flags=list(dict.fromkeys(flags)),
        properties=props,
        mods=mods,
    )


def _parse_items(
    root: ET.Element,
    warnings: list[str],
    extra_slots: dict[int, str] | None = None,
    use_second: bool = False,
) -> list[Item]:
    items_el = root.find("Items")
    if items_el is None:
        return []
    chosen = _pick_set(items_el, "ItemSet", "activeItemSet")
    slot_elements = chosen.findall("Slot") if chosen is not None else items_el.findall("Slot")
    slots: dict[int, str] = dict(extra_slots or {})
    for el in slot_elements:
        item_id, name = _int(el.get("itemId")), el.get("name")
        if item_id > 0 and name:
            slots[item_id] = name

    items: list[Item] = []
    for el in items_el.findall("Item"):
        item_id = _int(el.get("id"), -1)
        try:
            item = parse_item_text(item_id, "".join(el.itertext()), slots.get(item_id))
            item.active = _slot_active(item.slot, use_second)
            items.append(item)
        except ValueError as exc:
            warnings.append(str(exc))
    return items


# ---------------------------------------------------------------- tree / config / stats

def _active_spec(root: ET.Element) -> ET.Element | None:
    tree = root.find("Tree")
    if tree is None:
        return None
    specs = tree.findall("Spec")
    if not specs:
        return None
    active = _int(tree.get("activeSpec"), 1) - 1
    return specs[active] if 0 <= active < len(specs) else specs[0]


def _parse_tree_nodes(spec: ET.Element | None) -> list[int]:
    if spec is None:
        return []
    return [int(x) for x in spec.get("nodes", "").split(",") if x.strip().isdigit()]


def _jewel_slots(spec: ET.Element | None, allocated: set[int]) -> dict[int, str]:
    """itemId -> 'Jewel <nodeId>' for jewels socketed in allocated tree sockets."""
    slots: dict[int, str] = {}
    sockets = spec.find("Sockets") if spec is not None else None
    if sockets is None:
        return slots
    for el in sockets.findall("Socket"):
        node_id, item_id = _int(el.get("nodeId")), _int(el.get("itemId"))
        if item_id > 0 and node_id in allocated:
            slots[item_id] = f"Jewel {node_id}"
    return slots


def _parse_config(root: ET.Element) -> dict[str, str | bool | float]:
    config = root.find("Config")
    if config is None:
        return {}
    chosen = _pick_set(config, "ConfigSet", "activeConfigSet")
    inputs = chosen.findall("Input") if chosen is not None else config.findall("Input")
    out: dict[str, str | bool | float] = {}
    for el in inputs:
        name = el.get("name")
        if not name:
            continue
        if el.get("boolean") is not None:
            out[name] = _bool(el.get("boolean"))
        elif el.get("number") is not None:
            out[name] = _num(el.get("number"))
        elif el.get("string") is not None:
            out[name] = el.get("string", "")
    return out


def _num(value: str | None) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _parse_stats(build_el: ET.Element) -> dict[str, float]:
    stats: dict[str, float] = {}
    for el in build_el.findall("PlayerStat"):
        name = el.get("stat")
        if name:
            stats[name] = _num(el.get("value"))
    return stats


# ---------------------------------------------------------------- public API

def parse_xml(xml_text: str) -> Build:
    root = ET.fromstring(xml_text.encode("utf-8"))
    build_el = root.find("Build")
    if build_el is None:
        raise ValueError("no <Build> element in PoB XML")
    ascendancy = build_el.get("ascendClassName")
    if ascendancy in (None, "", "None"):
        ascendancy = None
    warnings: list[str] = []
    spec = _active_spec(root)
    tree_nodes = _parse_tree_nodes(spec)
    use_second = _use_second_weapon_set(root)
    return Build(
        class_name=build_el.get("className", "?"),
        ascendancy=ascendancy,
        level=_int(build_el.get("level")),
        tree_version=spec.get("treeVersion") if spec is not None else None,
        main_group=_opt_int(build_el.get("mainSocketGroup")),
        skill_groups=_parse_skill_groups(root, use_second),
        items=_parse_items(root, warnings, _jewel_slots(spec, set(tree_nodes)), use_second),
        tree_nodes=tree_nodes,
        config=_parse_config(root),
        stats=_parse_stats(build_el),
        warnings=warnings,
    )


def parse_code(code: str) -> Build:
    return parse_xml(decode_code(code))


def parse_file(path: str | Path) -> Build:
    return parse_code(Path(path).read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- CLI

def _gem_str(gem: Gem) -> str:
    return f"{gem.name} {gem.level}/{gem.quality}" + ("" if gem.enabled else " (off)")


def format_summary(build: Build) -> str:
    out: list[str] = []
    asc = f" / {build.ascendancy}" if build.ascendancy else ""
    out.append(f"{build.class_name}{asc}, level {build.level}")

    main = build.main_skill_group
    if main is not None:
        out.append(
            f"Main group: #{main.index} (mainActiveSkill={main.main_active_index}, "
            f"best guess: {main.main_skill_name or '?'})"
        )
    else:
        out.append("Main group: unknown")

    shown = [f"{k}={build.stats[k]:g}" for k in SUMMARY_STATS if k in build.stats]
    out.append("Stats saved in code: " + (", ".join(shown) if shown else f"none ({len(build.stats)} total)"))
    out.append(
        f"Tree {build.tree_version or '?'}: {len(build.tree_nodes)} allocated nodes (IDs only), "
        f"config inputs: {len(build.config)}"
    )
    out.append("")

    out.append(f"Skill groups ({len(build.skill_groups)}):")
    for group in build.skill_groups:
        tags = []
        if group.index == build.main_group:
            tags.append("MAIN")
        if group.from_item:
            tags.append("item-granted")
        if not group.active:
            tags.append("inactive weapon set")
        if not group.enabled:
            tags.append("disabled")
        head = f"  #{group.index}"
        if group.slot:
            head += f" [{group.slot}]"
        if tags:
            head += f" ({', '.join(tags)})"
        out.append(head)
        if group.active_gems:
            out.append("      active:  " + ", ".join(_gem_str(g) for g in group.active_gems))
        if group.support_gems:
            out.append("      support: " + ", ".join(_gem_str(g) for g in group.support_gems))
    out.append("")

    equipped = build.equipped_items
    out.append(f"Items ({len(equipped)} equipped, {len(build.items) - len(equipped)} inactive or unassigned):")
    for item in build.items:
        where = (item.slot or "not equipped") + ("" if item.active else ", inactive weapon set")
        label = f"  [{where}] {item.rarity} {item.name}"
        if item.base and item.base != item.name:
            label += f" ({item.base})"
        extras = []
        if item.quality is not None:
            extras.append(f"q{item.quality}")
        if item.sockets:
            extras.append(f"sockets {item.sockets}")
        extras += item.flags
        if extras:
            label += "  | " + ", ".join(extras)
        out.append(label)
        for mod in item.mods:
            prefix = "(implicit) " if mod.kind == "implicit" else ""
            suffix = "".join(f" [{t}]" for t in mod.tags)
            out.append(f"      - {prefix}{mod.text}{suffix}")

    if build.warnings:
        out.append("")
        out.append("Warnings:")
        out.extend(f"  {w}" for w in build.warnings)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m app.parser", description="Summarize a Path of Building code file.")
    parser.add_argument("file", type=Path, help="text file containing one PoB export code")
    parser.add_argument("--json", action="store_true", help="dump the full Build model as JSON")
    args = parser.parse_args(argv)
    try:
        build = parse_file(args.file)
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(build.model_dump_json(indent=2) if args.json else format_summary(build))
    return 0
