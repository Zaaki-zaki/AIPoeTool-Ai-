import base64
import zlib
from pathlib import Path

import pytest

from app.parser import parse_code, parse_file, parse_item_text

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.txt"))

# Lines that are item metadata and must never show up as mods.
HEADER_PREFIXES = (
    "Requires", "LevelReq", "Quality:", "Sockets:", "Implicits:", "Unique ID", "Armour:", "Evasion:",
    "Energy Shield:", "Catalyst", "Cluster Jewel Skill", "Cluster Jewel Node Count", "Radius:",
    "Rarity:", "Item Level", "Selected Variant",
)

SYNTHETIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PathOfBuilding>
  <Build level="90" className="Witch" ascendClassName="Necromancer" mainSocketGroup="2">
    <PlayerStat stat="Life" value="4500"/>
    <PlayerStat stat="FullDPS" value="123456.5"/>
  </Build>
  <Skills activeSkillSet="1">
    <SkillSet id="1">
      <Skill mainActiveSkill="1" label="" enabled="true" slot="Body Armour">
        <Gem nameSpec="Hatred" skillId="Hatred" gemId="Metadata/Items/Gems/SkillGemHatred" level="20" quality="0" enabled="true"/>
      </Skill>
      <Skill mainActiveSkill="1" label="Main" enabled="true" slot="Weapon 1">
        <Gem nameSpec="Fireball" skillId="Fireball" gemId="Metadata/Items/Gems/SkillGemFireball" level="21" quality="20" enabled="true"/>
        <Gem nameSpec="Spell Echo" skillId="SupportSpellEcho" gemId="Metadata/Items/Gems/SupportGemSpellEcho" level="20" quality="0" enabled="true"/>
        <Gem nameSpec="Controlled Destruction" skillId="SupportControlledDestruction" gemId="Metadata/Items/Gems/SupportGemControlledDestruction" level="1" quality="0" enabled="false"/>
      </Skill>
      <Skill mainActiveSkill="1" enabled="true" slot="Weapon 1" source="Item:3:Some Sword">
        <Gem nameSpec="Vulnerability" skillId="Vulnerability" level="1" quality="0" enabled="true"/>
      </Skill>
      <Skill mainActiveSkill="1" enabled="true" slot="Weapon 1 Swap">
        <Gem nameSpec="Phase Run" skillId="NewPhaseRun" level="20" quality="0" enabled="true"/>
      </Skill>
    </SkillSet>
  </Skills>
  <Tree activeSpec="2">
    <Spec title="old" nodes="1,2"/>
    <Spec title="current" treeVersion="3_28" nodes="10,20,30">
      <Sockets>
        <Socket nodeId="20" itemId="5"/>
        <Socket nodeId="99" itemId="6"/>
      </Sockets>
    </Spec>
  </Tree>
  <Items activeItemSet="1">
    <Item id="1">
      Rarity: UNIQUE
      Abyssus
      Ironclad Vestments
      Unique ID: abc
      Quality: 17
      Sockets: R-R-W-R
      LevelReq: 60
      Armour: 878
      Implicits: 0
      +25 to all Attributes
      50% increased Physical Damage taken
    </Item>
    <Item id="2">
      Rarity: RARE
      Vengeance Talons
      Slink Gloves
      Searing Exarch Item
      Intangibility: 8%
      Quality: 20
      Implicits: 1
      {crafted}+15% to Fire and Chaos Resistances
      {fractured}+105 to maximum Life
      {range:0.5}{tags:life}+30 to maximum Life
      Fractured Item
    </Item>
    <Item id="3">
      Rarity: UNIQUE
      Foulborn Uul-Netol's Kiss
      Fish Hook
      Selected Variant: 2
      Variant: Old
      Variant: New
      Implicits: 0
      {variant:1}Old line
      {variant:2}New line
      {variant:1,2}Always line
      Corrupted
    </Item>
    <Item id="4">
      Rarity: MAGIC
      Flagellant's Jade Flask of the Armadillo
      Quality: 20
      Implicits: 0
      59% increased Armour during Effect
    </Item>
    <Item id="5">
      Rarity: UNIQUE
      Watcher's Eye
      Prismatic Jewel
      Implicits: 0
      5% increased maximum Life
    </Item>
    <Item id="6">
      Rarity: RARE
      Spare Jewel
      Cobalt Jewel
      Implicits: 0
      +5 to Strength
    </Item>
    <Item id="7">
      Rarity: RARE
      Swap Bow
      Maraketh Bow
      Implicits: 0
      +1 to Level of Socketed Gems
    </Item>
    <Item id="8">
      Rarity: UNIQUE
      Replica Thing
      Eternal Sword
      Implicits: 0
      Foil Unique (Celestial Emerald)
      +10 to Strength
    </Item>
    <ItemSet id="1" useSecondWeaponSet="false">
      <Slot name="Weapon 1 Swap" itemId="7"/>
      <Slot name="Helmet" itemId="8"/>
      <Slot name="Body Armour" itemId="1"/>
      <Slot name="Gloves" itemId="2"/>
      <Slot name="Weapon 1" itemId="3"/>
    </ItemSet>
  </Items>
  <Config activeConfigSet="1">
    <ConfigSet id="1">
      <Input name="enemyIsBoss" string="Pinnacle"/>
      <Input name="usePowerCharges" boolean="true"/>
      <Input name="multiplierPoison" number="12"/>
    </ConfigSet>
  </Config>
</PathOfBuilding>
"""


def encode(xml: str) -> str:
    return base64.b64encode(zlib.compress(xml.encode())).decode().replace("+", "-").replace("/", "_")


def test_synthetic_roundtrip():
    build = parse_code(encode(SYNTHETIC_XML))
    assert (build.class_name, build.ascendancy, build.level) == ("Witch", "Necromancer", 90)
    assert build.stats == {"Life": 4500.0, "FullDPS": 123456.5}
    assert build.tree_nodes == [10, 20, 30]  # activeSpec=2
    assert build.config == {"enemyIsBoss": "Pinnacle", "usePowerCharges": True, "multiplierPoison": 12.0}

    main = build.main_skill_group
    assert main is not None and main.index == 2 and main.main_skill_name == "Fireball"
    assert [g.name for g in main.support_gems] == ["Spell Echo", "Controlled Destruction"]
    assert main.gems[2].enabled is False
    assert build.skill_groups[2].from_item and not main.from_item
    assert build.tree_version == "3_28"
    swap = build.skill_groups[3]
    assert swap.slot == "Weapon 1 Swap" and swap.active is False
    assert main.active and build.skill_groups[0].active


def test_synthetic_items():
    build = parse_code(encode(SYNTHETIC_XML))
    by_id = {i.id: i for i in build.items}
    abyssus = by_id[1]
    assert abyssus.slot == "Body Armour" and abyssus.base == "Ironclad Vestments"
    assert (abyssus.quality, abyssus.sockets, abyssus.level_req, abyssus.armour) == (17, "R-R-W-R", 60, 878)
    assert [m.text for m in abyssus.mods] == ["+25 to all Attributes", "50% increased Physical Damage taken"]

    gloves = by_id[2]
    assert gloves.flags == ["Searing Exarch Item", "Fractured Item"]
    assert gloves.properties["Intangibility"] == "8%"
    assert gloves.implicits[0].text == "+15% to Fire and Chaos Resistances" and gloves.implicits[0].tags == ["crafted"]
    assert [m.text for m in gloves.explicits] == ["+105 to maximum Life", "+30 to maximum Life"]
    assert gloves.explicits[0].tags == ["fractured"] and gloves.explicits[1].tags == []

    kiss = by_id[3]
    assert kiss.foulborn and "Corrupted" in kiss.flags
    assert [m.text for m in kiss.mods] == ["New line", "Always line"]  # Selected Variant: 2

    assert by_id[5].slot == "Jewel 20" and by_id[5].equipped  # socketed in an allocated node
    assert by_id[6].slot is None  # socket node 99 is not allocated

    assert by_id[7].slot == "Weapon 1 Swap" and not by_id[7].active and not by_id[7].equipped
    assert by_id[8].flags == ["Foil Unique (Celestial Emerald)"] and [m.text for m in by_id[8].mods] == ["+10 to Strength"]

    flask = by_id[4]
    assert flask.slot is None and flask.base is None and not flask.equipped


def test_item_without_implicits_marker():
    item = parse_item_text(9, "Rarity: RARE\nDoom Bite\nAxe\nQuality: 5\nRequires Class Duelist\n+10 to Strength\n")
    assert item.base == "Axe" and item.quality == 5
    assert [m.text for m in item.mods] == ["+10 to Strength"]
    assert item.properties["Requires"] == "Class Duelist"


def test_bad_code():
    with pytest.raises(ValueError):
        parse_code("not a pob code!!!")


def test_fixtures_present():
    assert FIXTURES, "put PoB codes (one per .txt file) into tests/fixtures"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture(path):
    build = parse_file(path)
    assert build.class_name and build.class_name != "?" and build.level > 0
    assert build.skill_groups
    assert any(g.active_gems for g in build.skill_groups)
    assert build.main_group is not None and build.main_skill_group is not None
    assert build.main_skill_group.active
    assert build.tree_version
    assert build.tree_nodes
    assert build.equipped_items
    assert not build.warnings
    for item in build.items:
        assert item.name
        if item.rarity in ("RARE", "UNIQUE"):
            assert item.base
        for mod in item.mods:
            assert not mod.text.startswith(HEADER_PREFIXES), f"{item.name}: header line in mods: {mod.text}"
            assert not mod.text.startswith("{"), f"{item.name}: unparsed tag: {mod.text}"
