"""Klient serwera PoB (pob/server.lua). Jeden proces = jeden wczytany build; polecenia po JSON-lines."""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.parser.parse import decode_code

from .scores import add_scores

_ROOT = Path(__file__).resolve().parents[2]
_SERVER_LUA = _ROOT / "pob" / "server.lua"
# server.lua montujemy z repo, więc zmiany w nim działają bez przebudowy obrazu (obraz zawiera tylko LuaJIT i PoB)
DEFAULT_CMD = ["docker", "run", "-i", "--rm"] \
    + (["-v", f"{_SERVER_LUA}:/opt/pobserver/server.lua:ro"] if _SERVER_LUA.exists() else []) + ["aipoetool-pob"]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: bez migającej konsoli przy starcie Dockera


class PobError(RuntimeError):
    """Błąd zwrócony przez serwer PoB albo awaria procesu."""


def native_available() -> bool:
    """Silnik bez Dockera działa, gdy jest pakiet lupa (LuaJIT w Pythonie)."""
    try:
        import lupa.luajit21  # noqa: F401
    except ImportError:
        return False
    return True


def default_cmd() -> list[str]:
    """Polecenie startu serwera. POB_ENGINE_CMD pozwala podać własne; domyślnie silnik natywny (lupa), a bez niego Docker."""
    env = os.environ.get("POB_ENGINE_CMD")
    if env:
        return shlex.split(env, posix=(os.name != "nt"))
    if native_available():
        from . import setup
        try:
            src = setup.ensure()
        except RuntimeError as e:
            raise PobError(str(e)) from e
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe" and (py.with_name("python.exe")).exists():
            py = py.with_name("python.exe")  # pythonw nie ma konsoli, a potrzebujemy stdin/stdout jako rur
        logging.info("Silnik PoB: natywny (%s, PoB w %s)", py, src)
        return [str(py), "-m", "app.calc.native", str(src)]
    if os.environ.get("AIPOE_ALLOW_DOCKER") != "1":
        raise PobError("Brak pakietu lupa, więc silnik PoB nie może działać bez Dockera. Uruchom: uv sync "
                       "(Docker jest tylko rezerwą po ustawieniu AIPOE_ALLOW_DOCKER=1).")
    return list(DEFAULT_CMD)


class PobEngine:
    def __init__(self, cmd: list[str] | None = None, timeout: float = 180.0) -> None:
        self.cmd = cmd or default_cmd()
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._err_tail: collections.deque[str] = collections.deque(maxlen=40)

    # ------------------------------------------------------------ proces
    def start(self) -> "PobEngine":
        if self._proc is not None:
            return self
        self._proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
            cwd=str(_ROOT), env={**os.environ, "PYTHONPATH": str(_ROOT), "PYTHONIOENCODING": "utf-8"},
        )
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()
        return self

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # koniec strumienia

    def _drain_err(self) -> None:
        """Stderr (logi PoB, błędy Lua/Dockera) musi być czytany, inaczej zapełni bufor; trzymamy ogon."""
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._err_tail.append(line.rstrip())

    def _failure(self, what: str) -> PobError:
        time.sleep(0.3)  # daj wątkowi stderr dopisać ostatnie linie
        tail = "\n".join(self._err_tail) or "(brak danych na stderr)"
        return PobError(f"{what}\nPolecenie: {' '.join(self.cmd)}\nOstatnie linie stderr:\n{tail}")

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and self._proc.poll() is None:
                self._proc.stdin.write('{"cmd":"quit"}\n')
                self._proc.stdin.flush()
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()
        self._proc = None

    def __enter__(self) -> "PobEngine":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._proc is None:
            self.start()
        assert self._proc and self._proc.stdin
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except OSError as e:
            raise self._failure("nie udało się wysłać polecenia do PoB (proces już nie działa)") from e
        try:
            line = self._lines.get(timeout=self.timeout)
        except queue.Empty as e:
            self._proc.kill()
            raise PobError(f"PoB nie odpowiedział w {self.timeout:.0f}s") from e
        if line is None:
            raise self._failure("proces PoB zakończył się nieoczekiwanie")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise PobError(resp.get("error", "nieznany błąd PoB"))
        if isinstance(resp.get("stats"), dict):
            add_scores(resp["stats"])
        return resp

    # ------------------------------------------------------------ API
    def load_xml(self, xml: str) -> dict[str, Any]:
        """Wczytuje build; zwraca {'stats': ..., 'groups': [...]}. Pierwsze wczytanie trwa kilka sekund."""
        return self._call({"cmd": "load", "xml": xml})

    def load_code(self, code: str) -> dict[str, Any]:
        xml = decode_code(code.strip())
        return self.load_xml(xml if isinstance(xml, str) else xml.decode("utf-8"))

    def load_file(self, path: str | Path) -> dict[str, Any]:
        return self.load_code(Path(path).read_text(encoding="utf-8"))

    def stats(self) -> dict[str, Any]:
        return self._call({"cmd": "stats"})["stats"]

    def groups(self) -> list[dict[str, Any]]:
        return self._call({"cmd": "groups"})["groups"]

    def set_main(self, group: int, skill_index: int = 1) -> dict[str, Any]:
        """Ustawia główny skill (grupa 1-based; skill_index = pozycja aktywnego skilla w grupie)."""
        return self._call({"cmd": "set_main", "group": group, "skill_index": skill_index})["stats"]

    def set_group(self, group: int, gems: list[dict[str, Any]]) -> dict[str, Any]:
        """Zastępuje całą listę gemów w grupie i przelicza. Zwraca {'stats', 'gem_errors'}.

        gems: [{"name": "Cyclone", "level": 21, "quality": 20}, ...]
        """
        resp = self._call({"cmd": "set_group", "group": group, "gems": gems})
        return {"stats": resp["stats"], "gem_errors": resp.get("gem_errors", [])}

    def set_item(self, slot: str, text: str) -> dict[str, Any]:
        """Próbna podmiana itemu w slocie (tekst w formacie PoB). Zwraca {'stats','attrs','reqs','item_name'}.

        PobError, gdy item nie pasuje do slotu albo ma nierozpoznaną bazę. Oryginał przywraca reset_item.
        """
        resp = self._call({"cmd": "set_item", "slot": slot, "text": text})
        return {k: resp[k] for k in ("stats", "attrs", "reqs", "item_name")}

    def reset_item(self, slot: str) -> dict[str, Any]:
        """Przywraca oryginalny item slotu sprzed prób set_item. Zwraca {'stats','attrs'}."""
        resp = self._call({"cmd": "reset_item", "slot": slot})
        return {"stats": resp["stats"], "attrs": resp["attrs"]}

    def attrs(self) -> dict[str, float]:
        return self._call({"cmd": "attrs"})["attrs"]

    def tree_info(self, max_dist: int = 6, max_freed: int = 4) -> dict[str, Any]:
        """Kandydaci do drzewka: 'add' (notable/keystone w zasięgu max_dist punktów), 'remove' (nody do zwolnienia)."""
        resp = self._call({"cmd": "tree_info", "max_dist": max_dist, "max_freed": max_freed})
        return {"add": resp["add"], "remove": resp["remove"], "points_used": resp["points_used"],
                "asc_used": resp.get("asc_used", 0), "asc_name": resp.get("asc_name"), "stats": resp["stats"]}

    def tree_try(self, alloc: list[int] | None = None, dealloc: list[int] | None = None,
                 mastery: dict[int, int] | None = None) -> dict[str, Any]:
        """Próbna zmiana drzewka względem stanu wyjściowego (najpierw dealloc, potem alloc ze ścieżkami).

        Zwraca {'stats','attrs','points_delta','added','removed'} (id nodów, które doszły/odeszły względem
        stanu wyjściowego); PobError, gdy node nieosiągalny albo już zaalokowany.
        """
        resp = self._call({"cmd": "tree_try", "alloc": alloc or [], "dealloc": dealloc or [], "mastery": mastery or {}})
        return {"stats": resp["stats"], "attrs": resp["attrs"], "points_delta": resp["points_delta"],
                "asc_delta": resp.get("asc_delta", 0), "added": resp["added"], "removed": resp["removed"]}

    # --- jewele ---
    def jewel_info(self) -> dict[str, Any]:
        """Sockety jewel (id, pozycja, zaalokowany, jewel w środku) i jewele poza socketami."""
        r = self._call({"cmd": "jewel_info"})
        return {"sockets": r["sockets"], "spare": r["spare"], "stats": r["stats"]}

    def jewel_trial(self, socket: int, text: str | None, alloc: bool = False) -> dict[str, Any]:
        """Próbnie wkłada jewel (tekst PoB) do socketu; text=None opróżnia socket. alloc: najpierw alokuje socket ze ścieżką."""
        r = self._call({"cmd": "jewel_trial", "socket": socket, "text": text, "alloc": alloc})
        return {k: r.get(k) for k in ("stats", "attrs", "item", "points_used")}

    def jewel_swap(self, a: int, b: int, alloc: bool = False) -> dict[str, Any]:
        """Zamienia jewele między dwoma socketami (próbnie)."""
        r = self._call({"cmd": "jewel_swap", "a": a, "b": b, "alloc": alloc})
        return {k: r.get(k) for k in ("stats", "attrs", "a", "b")}

    def jewel_reset(self) -> dict[str, Any]:
        """Cofa wszystkie próbne zmiany jewel (i próbnie zaalokowane sockety)."""
        r = self._call({"cmd": "jewel_reset"})
        return {"stats": r["stats"], "attrs": r["attrs"]}

    def jewel_commit(self) -> dict[str, Any]:
        """Obecny stan jewel i drzewka staje się stanem wyjściowym."""
        return {"stats": self._call({"cmd": "jewel_commit"})["stats"]}

    def tl_search(self, jewel_type: int, socket: int, wanted: list[dict[str, Any]], top: int = 40,
                  near: int = 0, seed_min: int | None = None, seed_max: int | None = None) -> dict[str, Any]:
        """Szuka seedów Timeless Jewel po wartościowych frazach w statach nodów w zasięgu socketu (dane LUT z PoB)."""
        r = self._call({"cmd": "tl_search", "type": jewel_type, "socket": socket, "wanted": wanted, "top": top,
                        "near": near, "seed_min": seed_min, "seed_max": seed_max})
        return {k: r.get(k) for k in ("results", "scanned", "targets", "matched", "note")}

    def tree_commit(self, alloc: list[int], mastery: dict[int, int] | None = None) -> dict[str, Any]:
        """Trwale dodaje nody (ze ścieżkami); zwraca {'stats','attrs','order'} (order = kolejność brania punktów)."""
        resp = self._call({"cmd": "tree_commit", "alloc": alloc, "mastery": mastery or {}})
        m = resp.get("masteries") or {}
        return {"stats": resp["stats"], "attrs": resp["attrs"], "order": resp["order"],
                "masteries": {int(k): int(v) for k, v in (m.items() if isinstance(m, dict) else [])}}

    def classes(self) -> dict[str, Any]:
        """Klasy i ascendancy z drzewka PoB: {'classes': [{id,name,asc:[{id,name}]}], 'tree_version'}."""
        resp = self._call({"cmd": "classes"})
        return {"classes": resp["classes"], "tree_version": resp["tree_version"]}

    def tree_reset(self) -> dict[str, Any]:
        resp = self._call({"cmd": "tree_reset"})
        return {"stats": resp["stats"], "attrs": resp["attrs"]}

    def tree_layout(self) -> dict[str, Any]:
        """Układ drzewka do rysowania: {'nodes': [{id,x,y,t,n,a}], 'links': [[a,b]], 'alloc': [id], ...}.

        t: N zwykły, T notable, K keystone, M mastery, S socket, C/A start klasy/ascendancy; a: czy ascendancy.
        """
        resp = self._call({"cmd": "tree_layout"})
        return {k: resp[k] for k in ("nodes", "links", "alloc", "tree_version")}
