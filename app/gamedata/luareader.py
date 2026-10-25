"""Wczytywanie plików danych PoB (Lua) przez lupa. Nic nie jest zgadywane - czytamy to, co jest w plikach."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import lupa
from lupa import LuaRuntime

SKILL_FILES = [
    "act_str", "act_dex", "act_int",
    "sup_str", "sup_dex", "sup_int",
    "other", "glove", "minion", "spectre",
]

_STUB_SETUP = """
STUB = setmetatable({}, {
  __index = function(t, k) return t end,
  __call = function(t, ...) return t end,
})
setmetatable(_G, { __index = function(t, k) return STUB end })
"""


def to_py(v: Any) -> Any:
    """Konwersja wartości Lua -> Python. Funkcje i userdata są pomijane (None)."""
    kind = lupa.lua_type(v)
    if kind == "table":
        keys = list(v.keys())
        n = len(keys)
        if n and all(isinstance(k, int) for k in keys) and set(keys) == set(range(1, n + 1)):
            return [to_py(v[i]) for i in range(1, n + 1)]
        out: dict[str, Any] = {}
        for k, val in v.items():
            if lupa.lua_type(val) in ("function", "userdata", "thread"):
                continue
            out[str(k)] = to_py(val)
        return out
    if kind in ("function", "userdata", "thread"):
        return None
    return v


class PobData:
    """Dostęp do katalogu `src` repozytorium PathOfBuilding."""

    def __init__(self, pob_src: Path) -> None:
        self.src = Path(pob_src)
        if not (self.src / "Data" / "Gems.lua").exists():
            raise FileNotFoundError(f"Nie znaleziono Data/Gems.lua w {self.src}")
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self._loadfile = self.lua.eval(
            "function(p) local f = assert(loadfile(p)); return f() end"
        )
        self.skill_type_ids = self._read_skill_types()
        self.skill_type_names = {v: k for k, v in self.skill_type_ids.items()}
        self.lua.execute(_STUB_SETUP)
        self.lua.globals().SkillType = self.lua.table_from(self.skill_type_ids)

    def _read_skill_types(self) -> dict[str, int]:
        text = (self.src / "Data" / "Global.lua").read_text(encoding="utf8")
        m = re.search(r"^SkillType = \{(.*?)^\}", text, re.S | re.M)
        if not m:
            raise RuntimeError("Nie znaleziono tabeli SkillType w Global.lua")
        return {
            mm.group(1): int(mm.group(2))
            for mm in re.finditer(r"^\s*(\w+)\s*=\s*(\d+)", m.group(1), re.M)
        }

    def type_names(self, ids: Any) -> list[str]:
        """Lista numerów/typów Lua -> lista nazw (zachowuje kolejność, ważną dla wyrażeń AND/OR/NOT)."""
        if ids is None:
            return []
        return [self.skill_type_names.get(i, f"?{i}") for i in ids]

    def gems(self) -> dict[str, dict[str, Any]]:
        return to_py(self._loadfile(str(self.src / "Data" / "Gems.lua")))

    def effects(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Zwraca (id, plik, surowa tabela Lua) dla wszystkich efektów (skilli i supportów)."""
        out = []
        stub = self.lua.globals().STUB
        for fname in SKILL_FILES:
            fn = self._loadfile(str(self.src / "Data" / "Skills" / f"{fname}.lua"))
            skills = self.lua.table()
            fn(skills, stub, stub, stub)
            for key, val in skills.items():
                out.append((str(key), fname, val))
        return out

    def tree(self, version: str) -> dict[str, Any]:
        path = self.src / "TreeData" / version / "tree.lua"
        return self._loadfile(str(path))

    def tree_versions(self) -> list[str]:
        """Zwykłe wersje drzewka (3_29), posortowane liczbowo; pomija warianty ruthless/alternate."""
        base = self.src / "TreeData"
        found = []
        for p in base.iterdir():
            m = re.fullmatch(r"(\d+)_(\d+)", p.name)
            if m and p.is_dir() and (p / "tree.lua").exists():
                found.append(((int(m.group(1)), int(m.group(2))), p.name))
        return [name for _, name in sorted(found)]

    def unique_blocks(self) -> list[tuple[str, str]]:
        """(plik, surowy tekst) dla każdego uniqa z Data/Uniques/*.lua i Special/New.lua."""
        blocks: list[tuple[str, str]] = []
        files = sorted((self.src / "Data" / "Uniques").glob("*.lua"))
        for extra in ("New.lua", "race.lua", "BoundByDestiny.lua", "WatchersEye.lua"):
            p = self.src / "Data" / "Uniques" / "Special" / extra
            if p.exists():
                files.append(p)
        for p in files:
            try:
                tbl = self._loadfile(str(p))
            except Exception:  # plik w nieoczekiwanym formacie - pomijamy
                continue
            if lupa.lua_type(tbl) != "table":
                continue
            for _, v in tbl.items():
                if isinstance(v, str):
                    blocks.append((p.stem, v))
        return blocks
