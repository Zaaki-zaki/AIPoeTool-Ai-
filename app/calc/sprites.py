"""Atlas ikon drzewka pasywnego z danych PoB (TreeData/<wersja>/sprites.lua) w formie JSON dla przeglądarki."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import setup

NEEDED = ("normalActive", "normalInactive", "notableActive", "notableInactive", "keystoneActive", "keystoneInactive",
          "mastery", "masteryActiveSelected", "masteryInactive", "masteryConnected", "frame", "jewel")


def _conv(x: Any) -> Any:
    from lupa.luajit21 import lua_type
    if lua_type(x) != "table":
        return x
    items = list(x.items())
    if items and all(isinstance(k, (int, float)) for k, _ in items):
        return [_conv(v) for _, v in sorted(items, key=lambda kv: kv[0])]
    return {str(k): _conv(v) for k, v in items}


def load(version: str) -> dict[str, Any]:
    """Zwraca {'sheets': {nazwa: {file, w, h, coords}}, 'scale': float}. file = nazwa pliku w TreeData/<wersja>/."""
    from lupa.luajit21 import LuaRuntime
    d = setup.src_dir() / "TreeData" / version
    lua = LuaRuntime(unpack_returned_tuples=True)
    data = _conv(lua.execute((d / "sprites.lua").read_text(encoding="utf-8")))
    sheets = {}
    for name in NEEDED:
        s = data["sprites"].get(name)
        if s:
            sheets[name] = {"file": s["filename"].split("/")[-1].split("?")[0], "w": s["w"], "h": s["h"], "coords": s["coords"]}
    return {"sheets": sheets, "scale": 1 / 0.3835}


def image_path(version: str, name: str) -> Path | None:
    p = (setup.src_dir() / "TreeData" / version / Path(name).name).resolve()
    root = (setup.src_dir() / "TreeData").resolve()
    return p if root in p.parents and p.is_file() else None
