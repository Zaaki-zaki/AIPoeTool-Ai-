"""Data models for a parsed Path of Building (PoE 1) build."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Gem(BaseModel):
    name: str
    skill_id: str = ""
    level: int = 0
    quality: int = 0
    enabled: bool = True
    is_support: bool = False


class SkillGroup(BaseModel):
    # 1-based position in the active skill set; matches Build.main_group
    index: int
    label: str = ""
    slot: str | None = None
    enabled: bool = True
    active: bool = True  # False when the group sits in the inactive weapon-swap set
    # Set when the group is granted by an item instead of socketed gems.
    source: str | None = None
    # PoB's mainActiveSkill: which active skill of this group is selected (1-based).
    main_active_index: int | None = None
    gems: list[Gem] = Field(default_factory=list)

    @property
    def from_item(self) -> bool:
        return self.source is not None

    @property
    def active_gems(self) -> list[Gem]:
        return [g for g in self.gems if not g.is_support]

    @property
    def support_gems(self) -> list[Gem]:
        return [g for g in self.gems if g.is_support]

    @property
    def main_skill_name(self) -> str | None:
        """Best effort: the Nth active gem. Vaal gems expose two skills in PoB, so this can be off by one."""
        active = self.active_gems
        idx = self.main_active_index
        if idx is not None and 1 <= idx <= len(active):
            return active[idx - 1].name
        return None


class Mod(BaseModel):
    text: str
    kind: str = "explicit"  # "implicit" | "explicit"
    tags: list[str] = Field(default_factory=list)  # e.g. crafted, fractured, mutated, enchant


class Item(BaseModel):
    id: int
    slot: str | None = None  # None = item is in the build's item list but not equipped
    active: bool = True  # False when the item sits in the inactive weapon-swap set
    rarity: str
    name: str
    base: str | None = None  # None for magic items (the base is part of the name)
    quality: int | None = None
    sockets: str | None = None
    level_req: int | None = None
    armour: int | None = None
    evasion: int | None = None
    energy_shield: int | None = None
    flags: list[str] = Field(default_factory=list)  # Corrupted, Fractured Item, Searing Exarch Item, ...
    properties: dict[str, str] = Field(default_factory=dict)  # remaining header lines, not mods
    mods: list[Mod] = Field(default_factory=list)

    @property
    def equipped(self) -> bool:
        """Assigned to a slot AND part of the active weapon set."""
        return self.slot is not None and self.active

    @property
    def foulborn(self) -> bool:
        return self.name.startswith("Foulborn ")

    @property
    def implicits(self) -> list[Mod]:
        return [m for m in self.mods if m.kind == "implicit"]

    @property
    def explicits(self) -> list[Mod]:
        return [m for m in self.mods if m.kind == "explicit"]


class Build(BaseModel):
    class_name: str
    ascendancy: str | None = None
    level: int = 0
    tree_version: str | None = None  # e.g. "3_28"; needed to resolve node IDs against the right tree data
    main_group: int | None = None  # 1-based index into skill_groups
    skill_groups: list[SkillGroup] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    # Allocated passive node IDs only. Names and keystone/notable types need tree data (phase 2).
    tree_nodes: list[int] = Field(default_factory=list)
    config: dict[str, str | bool | float] = Field(default_factory=dict)
    # Stats PoB saved into the code (PlayerStat), e.g. FullDPS, Life, EnergyShield.
    stats: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def main_skill_group(self) -> SkillGroup | None:
        if self.main_group is None:
            return None
        for group in self.skill_groups:
            if group.index == self.main_group:
                return group
        return None

    @property
    def equipped_items(self) -> list[Item]:
        return [i for i in self.items if i.equipped]
