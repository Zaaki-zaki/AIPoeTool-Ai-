"""Baza gry w SQLite: budowanie z danych PoB i zapytania."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .luareader import PobData, to_py

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE effects (
    id TEXT PRIMARY KEY, name TEXT, is_support INTEGER, color INTEGER, file TEXT,
    description TEXT, skill_types TEXT, require_expr TEXT, exclude_expr TEXT, add_types TEXT,
    weapon_types TEXT, flags TEXT, stats TEXT, constant_stats TEXT, quality_stats TEXT,
    cast_time REAL, levels TEXT
);
CREATE TABLE gems (
    id TEXT PRIMARY KEY, name TEXT, base_name TEXT, effect_id TEXT, secondary_effect_id TEXT,
    is_support INTEGER, is_vaal INTEGER, req_str INTEGER, req_dex INTEGER, req_int INTEGER,
    max_level INTEGER, tags TEXT
);
CREATE INDEX gems_name ON gems(name COLLATE NOCASE);
CREATE INDEX effects_name ON effects(name COLLATE NOCASE);
CREATE TABLE tree_nodes (
    version TEXT, id INTEGER, name TEXT, kind TEXT, ascendancy TEXT, stats TEXT,
    PRIMARY KEY (version, id)
);
CREATE TABLE tree_edges (version TEXT, a INTEGER, b INTEGER);
CREATE INDEX tree_edges_a ON tree_edges(version, a);
CREATE TABLE uniques (
    id INTEGER PRIMARY KEY, name TEXT, base TEXT, file TEXT, props TEXT,
    variants TEXT, implicits TEXT, explicits TEXT
);
CREATE INDEX uniques_name ON uniques(name COLLATE NOCASE);
"""

CLUSTER_NODE_MIN_ID = 65536


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


_TAG_RE = re.compile(r"^(\{[^}]*\})+")
_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z ]*?:\s*(.*)$")


def _is_header(line: str) -> bool:
    return bool(
        _HEADER_LINE_RE.match(line)
        or line.startswith("Requires ")
        or line.startswith("Has no Sockets")
    )


def parse_unique(text: str) -> dict[str, Any] | None:
    """Rozbija tekst uniqa z PoB: nazwa, baza(-y), nagłówki, implicity, explicity.

    Linie mogą mieć prefiksy {variant:N}/{tags:..} - zostają w modach, bo niosą informację o wariancie.
    """
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    name = lines[0].strip()
    idx = 1
    bases: list[str] = []
    while idx < len(lines) and not _is_header(lines[idx]):
        if not bases or lines[idx].startswith("{variant:"):
            bases.append(lines[idx].strip())
            idx += 1
        else:
            break
    props: dict[str, list[str]] = {}
    variants: list[str] = []
    n_impl = 0
    while idx < len(lines):
        ln = lines[idx]
        if ln.startswith("Implicits:"):
            n_impl = int(ln.split(":", 1)[1].strip() or 0)
            idx += 1
            break
        if not _is_header(ln):
            break
        if ln.startswith("Requires "):
            props.setdefault("Requires", []).append(ln[len("Requires "):])
        elif ln.startswith("Has no Sockets"):
            props.setdefault("Sockets", []).append("none")
        else:
            key, val = ln.split(":", 1)
            props.setdefault(key.strip(), []).append(val.strip())
            if key.strip() == "Variant":
                variants.append(val.strip())
        idx += 1
    rest = lines[idx:]
    return {
        "name": name,
        "base": " / ".join(_TAG_RE.sub("", b) for b in bases),
        "bases_raw": bases,
        "props": props,
        "variants": variants,
        "implicits": rest[:n_impl],
        "explicits": rest[n_impl:],
    }


def _expr_matches(expr: list[str], types: set[str]) -> bool:
    """Port calcLib.doesTypeExpressionMatch z PoB (stos, operatory AND/OR/NOT)."""
    stack: list[bool] = []
    for t in expr:
        if t == "OR":
            other = stack.pop() if stack else False
            if stack:
                stack[-1] = stack[-1] or other
            else:
                stack.append(other)
        elif t == "AND":
            other = stack.pop() if stack else False
            if stack:
                stack[-1] = stack[-1] and other
            else:
                stack.append(other)
        elif t == "NOT":
            if stack:
                stack[-1] = not stack[-1]
        else:
            stack.append(t in types)
    return any(stack)


class GameDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row

    def close(self) -> None:
        self.con.close()

    # ---------------------------------------------------------------- build
    @classmethod
    def build(
        cls,
        pob_src: str | Path,
        out: str | Path,
        tree_versions: Iterable[str] | None = None,
        pob_commit: str | None = None,
    ) -> "GameDB":
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        data = PobData(Path(pob_src))
        con = sqlite3.connect(str(out))
        con.executescript(SCHEMA)

        # --- efekty (skille i supporty)
        rows = []
        for eid, fname, t in data.effects():
            support = bool(t["support"]) if t["support"] is not None else False
            flags = {
                k: True for k in (
                    "legacy", "hidden", "cannotBeSupported", "supportGemsOnly", "isTrigger",
                    "ignoreMinionTypes", "fromItem", "fromTree", "hideFromGemList",
                )
                if t[k]
            }
            if t["plusVersionOf"]:
                flags["plusVersionOf"] = t["plusVersionOf"]
            skill_types = [
                data.skill_type_names.get(k, f"?{k}")
                for k, v in (t["skillTypes"].items() if t["skillTypes"] else [])
                if v
            ]
            weapons = [k for k, v in (t["weaponTypes"].items() if t["weaponTypes"] else []) if v]
            levels = to_py(t["levels"]) if t["levels"] else {}
            if isinstance(levels, dict):  # pusta tabela -> {}
                levels = []
            # statInterpolation zostaje: bez niego wartości pozycyjne nie mają sensu
            rows.append((
                eid, t["name"], int(support), t["color"], fname,
                t["description"], _j(sorted(skill_types)),
                _j(data.type_names(to_py(t["requireSkillTypes"]) if t["requireSkillTypes"] else [])),
                _j(data.type_names(to_py(t["excludeSkillTypes"]) if t["excludeSkillTypes"] else [])),
                _j(data.type_names(to_py(t["addSkillTypes"]) if t["addSkillTypes"] else [])),
                _j(sorted(weapons)), _j(flags),
                _j(to_py(t["stats"]) if t["stats"] else []),
                _j(to_py(t["constantStats"]) if t["constantStats"] else []),
                _j(to_py(t["qualityStats"]) if t["qualityStats"] else []),
                t["castTime"], _j(levels),
            ))
        con.executemany(
            "INSERT OR REPLACE INTO effects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )

        # --- gemy
        grows = []
        for gid, g in data.gems().items():
            tags = g.get("tags", {})
            grows.append((
                gid, g["name"], g.get("baseTypeName"), g["grantedEffectId"],
                g.get("secondaryGrantedEffectId"), int(bool(tags.get("support"))),
                int(bool(g.get("vaalGem"))), g.get("reqStr", 0), g.get("reqDex", 0),
                g.get("reqInt", 0), g.get("naturalMaxLevel"), _j(sorted(tags)),
            ))
        con.executemany("INSERT OR REPLACE INTO gems VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", grows)

        # --- drzewko
        versions = list(tree_versions) if tree_versions else data.tree_versions()[-2:]
        for ver in versions:
            tree = to_py(data.tree(ver))
            nodes = tree["nodes"]
            nrows, erows = [], []
            for key, nd in nodes.items():
                if not isinstance(nd, dict) or "skill" not in nd:
                    continue
                nid = int(nd["skill"])
                if nd.get("isKeystone"):
                    kind = "keystone"
                elif nd.get("isMastery"):
                    kind = "mastery"
                elif nd.get("isJewelSocket"):
                    kind = "jewel_socket"
                elif nd.get("isNotable"):
                    kind = "notable"
                elif nd.get("classStartIndex") is not None:
                    kind = "class_start"
                else:
                    kind = "small"
                nrows.append((
                    ver, nid, nd.get("name"), kind, nd.get("ascendancyName"),
                    _j(nd.get("stats", [])),
                ))
                for o in nd.get("out", []):
                    erows.append((ver, nid, int(o)))
            con.executemany("INSERT OR REPLACE INTO tree_nodes VALUES (?,?,?,?,?,?)", nrows)
            con.executemany("INSERT INTO tree_edges VALUES (?,?,?)", erows)

        # --- uniki
        urows, skipped = [], 0
        for i, (fname, text) in enumerate(data.unique_blocks()):
            u = parse_unique(text)
            if not u:
                skipped += 1
                continue
            urows.append((
                i, u["name"], u["base"], fname, _j(u["props"]), _j(u["variants"]),
                _j(u["implicits"]), _j(u["explicits"]),
            ))
        con.executemany("INSERT INTO uniques VALUES (?,?,?,?,?,?,?,?)", urows)

        meta = {
            "pob_commit": pob_commit or "", "tree_versions": ",".join(versions),
            "unique_blocks_skipped": str(skipped),
            "note_generated_uniques": "Brak uniqów generowanych programowo przez PoB (Watcher's Eye, "
                                      "Forbidden Flame/Flesh, Megalomaniac, Balance of Terror, replicas). "
                                      "Ich mody są w tekście itemu z kodu PoB; pełne dane da headless PoB.",
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        con.commit()
        con.close()
        return cls(out)

    # -------------------------------------------------------------- queries
    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.con.execute("SELECT * FROM meta")}

    def counts(self) -> dict[str, int]:
        return {
            t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("effects", "gems", "tree_nodes", "tree_edges", "uniques")
        }

    def gem(self, name: str) -> dict[str, Any] | None:
        """Gem po nazwie (bez rozróżniania wielkości liter) wraz z jego efektem."""
        row = self.con.execute(
            "SELECT * FROM gems WHERE name = ? COLLATE NOCASE ORDER BY is_support LIMIT 1", (name,)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["tags"] = json.loads(out["tags"])
        out["effect"] = self.effect(out["effect_id"])
        if out["secondary_effect_id"]:
            out["secondary_effect"] = self.effect(out["secondary_effect_id"])
        return out

    def effect(self, effect_id: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        for k in ("skill_types", "require_expr", "exclude_expr", "add_types", "weapon_types",
                  "flags", "stats", "constant_stats", "quality_stats", "levels"):
            out[k] = json.loads(out[k])
        return out

    def is_known_gem(self, name: str) -> bool:
        """Gem z Gems.lua albo efekt specjalny (np. skill przyznawany przez item) - po nazwie lub id."""
        if self.con.execute(
            "SELECT 1 FROM gems WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone():
            return True
        return self.con.execute(
            "SELECT 1 FROM effects WHERE name = ? COLLATE NOCASE OR id = ?", (name, name)
        ).fetchone() is not None

    def compatible_supports(
        self,
        skill_name: str,
        weapon_type: str | None = None,
        include_legacy: bool = False,
        include_vaal_supports: bool = False,
        extra_types: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Supporty, które PoB uznałby za kompatybilne z aktywnym skillem.

        Port logiki calcLib.canGrantedEffectSupportActiveSkill. Pomija efekty zależne od innych
        supportów w linku (extra_types pozwala je dodać ręcznie) oraz stan postaci.
        weapon_type: jeśli podany, sprawdza ograniczenia broni po stronie skilla i supporta.
        """
        gem = self.gem(skill_name)
        if not gem or gem["is_support"]:
            raise ValueError(f"'{skill_name}' nie jest aktywnym gemem znanym w bazie")
        eff = gem["effect"]
        if eff is None:
            raise ValueError(f"Brak efektu dla {skill_name}")
        if eff["flags"].get("cannotBeSupported"):
            return []
        types = set(eff["skill_types"]) | set(extra_types)
        if weapon_type and eff["weapon_types"] and weapon_type not in eff["weapon_types"]:
            raise ValueError(f"{skill_name} nie działa z bronią '{weapon_type}'")
        out = []
        q = """SELECT g.name, g.id AS gem_id, g.req_str, g.req_dex, g.req_int, g.max_level, e.id AS effect_id
               FROM gems g JOIN effects e ON e.id = g.effect_id WHERE g.is_support = 1"""
        for r in self.con.execute(q):
            sup = self.effect(r["effect_id"])
            if sup is None:
                continue
            f = sup["flags"]
            if f.get("legacy") and not include_legacy:
                continue
            if f.get("supportGemsOnly") and not gem["effect_id"]:
                continue
            if sup["exclude_expr"] and _expr_matches(sup["exclude_expr"], types):
                continue
            if sup["require_expr"] and not _expr_matches(sup["require_expr"], types):
                continue
            if weapon_type and sup["weapon_types"] and weapon_type not in sup["weapon_types"]:
                continue
            out.append({
                "name": r["name"], "color": sup["color"],
                "req": {"str": r["req_str"], "dex": r["req_dex"], "int": r["req_int"]},
                "max_level": r["max_level"], "flags": f,
            })
        return sorted(out, key=lambda d: d["name"])

    def node(self, node_id: int, version: str | None = None) -> dict[str, Any] | None:
        version = version or self._default_version()
        row = self.con.execute(
            "SELECT * FROM tree_nodes WHERE version = ? AND id = ?", (version, node_id)
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        out["stats"] = json.loads(out["stats"])
        return out

    def _default_version(self) -> str:
        vs = self.meta().get("tree_versions", "").split(",")
        return vs[-1]

    def resolve_nodes(self, node_ids: Iterable[int], version: str | None = None) -> dict[str, Any]:
        """Przypisane nody -> keystone'y, notable'e, masteries, sockety i liczba małych."""
        version = version or self._default_version()
        ids = list(node_ids)
        res: dict[str, Any] = {
            "version": version, "keystones": [], "notables": [], "masteries": [],
            "jewel_sockets": 0, "small": 0, "ascendancy": [], "unknown": [],
            "cluster_nodes": 0, "other": 0,
        }
        for nid in ids:
            n = self.node(nid, version)
            if n is None:
                # id >= 65536: nody generowane przez Cluster Jewels - nie ma ich w tree.lua
                if nid >= CLUSTER_NODE_MIN_ID:
                    res["cluster_nodes"] += 1
                else:
                    res["unknown"].append(nid)
                continue
            if n["ascendancy"]:
                res["ascendancy"].append(n["name"])
            elif n["kind"] == "keystone":
                res["keystones"].append(n["name"])
            elif n["kind"] == "notable":
                res["notables"].append(n["name"])
            elif n["kind"] == "mastery":
                res["masteries"].append(n["name"])
            elif n["kind"] == "jewel_socket":
                res["jewel_sockets"] += 1
            elif n["kind"] == "small":
                res["small"] += 1
            else:
                res["other"] += 1  # np. start klasy
        return res

    def unique(self, name: str) -> list[dict[str, Any]]:
        """Unik po nazwie. Prefiks 'Foulborn ' jest ignorowany (PoB wylicza wersję Foulborn z bazowej)."""
        if name.lower().startswith("foulborn "):
            name = name[len("foulborn "):]
        out = []
        for r in self.con.execute(
            "SELECT * FROM uniques WHERE name = ? COLLATE NOCASE", (name,)
        ):
            d = dict(r)
            for k in ("props", "variants", "implicits", "explicits"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out

    @staticmethod
    def current_mods(unique: dict[str, Any]) -> dict[str, list[str]]:
        """Mody aktualnej wersji uniqa (ostatni 'Variant'), bez znaczników {variant:..}/{tags:..}."""
        n_var = len(unique["variants"])
        cur = n_var  # numeracja wariantów w PoB jest 1-based, 'Current' jest ostatni

        def keep(line: str) -> str | None:
            m = re.match(r"^\{variant:([\d,]+)\}", line)
            if m and n_var and str(cur) not in m.group(1).split(","):
                return None
            return re.sub(r"^(\{[^}]*\})+", "", line)

        return {
            "implicits": [x for x in (keep(l) for l in unique["implicits"]) if x],
            "explicits": [x for x in (keep(l) for l in unique["explicits"]) if x],
        }

    def search_uniques(self, text: str, limit: int = 25) -> list[dict[str, Any]]:
        """Uniki, w których któryś mod zawiera podany tekst."""
        like = f"%{text}%"
        out = []
        for r in self.con.execute(
            "SELECT name, base FROM uniques WHERE explicits LIKE ? OR implicits LIKE ? "
            "ORDER BY name LIMIT ?", (like, like, limit)
        ):
            out.append({"name": r["name"], "base": r["base"]})
        return out
