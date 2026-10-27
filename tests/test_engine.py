"""Testy silnika PoB. Wymagają obrazu Docker `aipoetool-pob` (docker build -t aipoetool-pob pob)
albo zmiennej POB_ENGINE_CMD wskazującej lokalny serwer. Bez tego są pomijane."""

import math
import os
import subprocess
from pathlib import Path

import pytest

from app.calc import PobEngine
from app.gamedata import GameDB
from app.parser.parse import parse_file

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("*.txt"))
DB_PATH = ROOT / "data" / "game.db"
COMPARED = [
    "Life", "EnergyShield", "Mana", "Armour", "Evasion", "FireResist", "ColdResist",
    "LightningResist", "ChaosResist", "TotalDPS", "CombinedDPS",
]


def _engine_available() -> bool:
    if os.environ.get("POB_ENGINE_CMD"):
        return True
    try:
        from app.calc import setup
        from app.calc.engine import native_available
        if native_available() and setup.ready():  # silnik natywny (lupa) z pobranym PoB
            return True
    except ImportError:
        pass
    try:
        r = subprocess.run(["docker", "image", "inspect", "aipoetool-pob"], capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


needs_engine = pytest.mark.skipif(
    not _engine_available(), reason="brak silnika PoB (obraz aipoetool-pob lub POB_ENGINE_CMD)"
)


@pytest.fixture(scope="module")
def engine():
    with PobEngine() as e:
        yield e


@needs_engine
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_headless_reproduces_saved_stats(engine: PobEngine, path: Path) -> None:
    """Najważniejszy test: silnik liczy build tak samo jak PoB, w którym zapisano kod."""
    saved = parse_file(path).stats
    got = engine.load_file(path)["stats"]
    checked = 0
    for key in COMPARED:
        if key not in saved:
            continue
        assert key in got, f"silnik nie zwrócił {key}"
        # Staty obronne muszą zgadzać się co do cyfry. DPS: tolerancja 0.1% - w jednym z buildów
        # (Ethereal Knives) wersja PoB użyta do zapisu kodu daje różnicę ~0.014%.
        tol = 1e-3 if "DPS" in key else 1e-6
        assert math.isclose(got[key], float(saved[key]), rel_tol=tol, abs_tol=1e-6), (
            f"{key}: zapisane={saved[key]} silnik={got[key]}"
        )
        checked += 1
    assert checked >= 8


def _editable_group(loaded: dict) -> dict:
    """Pierwsza grupa z gemami, która nie pochodzi z itemu (te PoB odtwarza sam)."""
    return next(g for g in loaded["groups"] if g["gems"] and not g.get("source"))


@needs_engine
def test_item_granted_group_cannot_be_edited(engine: PobEngine) -> None:
    from app.calc import PobError

    for path in FIXTURES:
        loaded = engine.load_file(path)
        grp = next((g for g in loaded["groups"] if g.get("source")), None)
        if grp:
            with pytest.raises(PobError):
                engine.set_group(grp["index"], grp["gems"])
            return
    pytest.skip("brak grup z itemów w fixtures")


@needs_engine
@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_rewriting_same_gems_is_a_noop(engine: PobEngine, path: Path) -> None:
    loaded = engine.load_file(path)
    base = loaded["stats"]
    group = _editable_group(loaded)
    res = engine.set_group(group["index"], group["gems"])
    assert res["gem_errors"] == []
    for key in ("Life", "TotalDPS", "CombinedDPS"):
        if key in base:
            assert math.isclose(res["stats"][key], base[key], rel_tol=1e-9, abs_tol=1e-9), key


@needs_engine
def test_unknown_gem_is_reported(engine: PobEngine) -> None:
    loaded = engine.load_file(FIXTURES[0])
    group = _editable_group(loaded)
    res = engine.set_group(group["index"], group["gems"] + [{"name": "Nieistniejacy Gem XYZ"}])
    assert any("Nieistniejacy" in e for e in res["gem_errors"])


@needs_engine
@pytest.mark.skipif(not DB_PATH.exists(), reason="brak data/game.db")
def test_every_db_compatible_support_loads_in_pob(engine: PobEngine) -> None:
    """Kompatybilność z bazy gry (port logiki PoB) musi dać gemy, które PoB zna i przyjmuje."""
    for path in FIXTURES:
        loaded = engine.load_file(path)
        group = next(
            (g for g in loaded["groups"] if any(x["name"] == "Cyclone" for x in g["gems"])), None
        )
        if group:
            break
    else:
        pytest.skip("żaden fixture nie ma Cyclone")
    db = GameDB(DB_PATH)
    keep = [
        {"name": x["name"], "level": x["level"], "quality": x["quality"]}
        for x in group["gems"] if x["name"] in ("Cyclone",)
    ]
    for sup in db.compatible_supports("Cyclone")[:25]:
        res = engine.set_group(group["index"], keep + [{"name": sup["name"], "level": 20}])
        assert res["gem_errors"] == [], f"{sup['name']}: {res['gem_errors']}"


def test_native_setup_extract(tmp_path):
    import io
    import zipfile
    from app.calc import setup
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("PoB-abc/src/HeadlessWrapper.lua", "x")
        z.writestr("PoB-abc/runtime/lua/dkjson.lua", "x")
        z.writestr("PoB-abc/src/Assets/a.png", "x")
        z.writestr("PoB-abc/src/TreeData/3_29/skills-3.jpg", "x")
        z.writestr("PoB-abc/docs/readme.md", "x")
    dest = tmp_path / "pob"
    assert setup.extract(b.getvalue(), dest) == 3 and (dest / "src" / "TreeData" / "3_29" / "skills-3.jpg").exists()
    assert (dest / "src" / "HeadlessWrapper.lua").exists() and not (dest / "src" / "Assets").exists()
