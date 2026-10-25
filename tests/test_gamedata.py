"""Testy bazy gry. Testy na danych wymagają zbudowanej bazy: scripts\\update_gamedata.ps1."""

from pathlib import Path

import pytest

from app.gamedata import GameDB
from app.gamedata.db import _expr_matches, parse_unique
from app.parser.parse import parse_file

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "game.db"
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))

needs_db = pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db - uruchom scripts/update_gamedata.ps1")


@pytest.fixture(scope="module")
def db() -> GameDB:
    return GameDB(DB_PATH)


# ------------------------------------------------------------------ czyste funkcje
def test_expr_single_and_or() -> None:
    assert _expr_matches(["Attack"], {"Attack"})
    assert not _expr_matches(["Spell"], {"Attack"})
    # lista bez operatorów = OR (any)
    assert _expr_matches(["Damage", "Attack"], {"Attack"})


def test_expr_operators() -> None:
    assert _expr_matches(["Attack", "Melee", "AND"], {"Attack", "Melee"})
    assert not _expr_matches(["Attack", "Melee", "AND"], {"Attack"})
    assert _expr_matches(["Attack", "Spell", "OR"], {"Spell"})
    assert _expr_matches(["Attack", "NOT"], {"Spell"})
    assert not _expr_matches(["Attack", "NOT"], {"Attack"})


UNIQUE_VARIANT_BASE = """Uul-Netol's Kiss
{variant:1}Labrys
{variant:2}Vaal Axe
Variant: Pre 3.21.0
Variant: Current
League: Breach
Implicits: 1
{variant:2}25% chance to Maim on Hit
{variant:1}(140-170)% increased Physical Damage
{variant:2}(230-270)% increased Physical Damage
15% reduced Attack Speed
"""

UNIQUE_NO_IMPLICITS_LINE = """Abyssus
Ezomyte Burgonet
Variant: Pre 2.2.0
Variant: Current
Requires Level 60, 138 Str
+(20-25) to all Attributes
{variant:1}+(100-150)% to Melee Critical Strike Multiplier
{variant:2}+(150-225)% to Melee Critical Strike Multiplier
"""


def test_parse_unique_variant_bases() -> None:
    u = parse_unique(UNIQUE_VARIANT_BASE)
    assert u is not None
    assert u["name"] == "Uul-Netol's Kiss"
    assert u["base"] == "Labrys / Vaal Axe"
    assert u["variants"] == ["Pre 3.21.0", "Current"]
    assert u["implicits"] == ["{variant:2}25% chance to Maim on Hit"]
    assert len(u["explicits"]) == 3


def test_parse_unique_without_implicits_line() -> None:
    u = parse_unique(UNIQUE_NO_IMPLICITS_LINE)
    assert u is not None
    assert u["base"] == "Ezomyte Burgonet"
    assert u["props"]["Requires"] == ["Level 60, 138 Str"]
    assert u["implicits"] == []
    assert u["explicits"][0] == "+(20-25) to all Attributes"


def test_current_mods_filters_old_variants() -> None:
    u = parse_unique(UNIQUE_VARIANT_BASE)
    assert u is not None
    mods = GameDB.current_mods(u)
    assert mods["implicits"] == ["25% chance to Maim on Hit"]
    assert "(230-270)% increased Physical Damage" in mods["explicits"]
    assert "(140-170)% increased Physical Damage" not in mods["explicits"]
    assert "15% reduced Attack Speed" in mods["explicits"]


# ------------------------------------------------------------------ dane
@needs_db
def test_counts_are_plausible(db: GameDB) -> None:
    c = db.counts()
    assert c["gems"] > 700
    assert c["effects"] > 1400
    assert c["uniques"] > 1000
    assert c["tree_nodes"] > 3000


@needs_db
def test_cyclone_support_compatibility(db: GameDB) -> None:
    names = {s["name"] for s in db.compatible_supports("Cyclone")}
    assert {"Brutality", "Fortify", "Lifetap", "Cast while Channelling"} <= names
    # Arrogance wymaga skilla z rezerwacją, Cyclone go nie ma
    assert "Arrogance" not in names
    assert len(names) > 50


@needs_db
def test_supports_are_not_active_gems(db: GameDB) -> None:
    with pytest.raises(ValueError):
        db.compatible_supports("Brutality")


@needs_db
def test_gem_lookup_case_insensitive(db: GameDB) -> None:
    g = db.gem("cyclone")
    assert g is not None and g["name"] == "Cyclone"
    assert g["effect"]["levels"], "efekt powinien mieć dane poziomów"


@needs_db
def test_foulborn_prefix_is_ignored(db: GameDB) -> None:
    assert db.unique("Foulborn Uul-Netol's Kiss")
    assert db.unique("Foulborn Uul-Netol's Kiss")[0]["name"] == "Uul-Netol's Kiss"


@needs_db
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_nodes_all_resolve(db: GameDB, path: Path) -> None:
    b = parse_file(path)
    r = db.resolve_nodes(b.tree_nodes, b.tree_version)
    assert r["unknown"] == [], f"nody spoza drzewka i spoza zakresu klastrów: {r['unknown']}"
    total = (
        len(r["keystones"]) + len(r["notables"]) + len(r["masteries"]) + r["jewel_sockets"]
        + r["small"] + len(r["ascendancy"]) + r["cluster_nodes"] + r["other"]
    )
    assert total == len(set(b.tree_nodes))


@needs_db
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_gems_all_known(db: GameDB, path: Path) -> None:
    b = parse_file(path)
    missing = {g.name for sg in b.skill_groups for g in sg.gems if not db.is_known_gem(g.name)}
    assert not missing, f"gemy spoza bazy: {missing}"
