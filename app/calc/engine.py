"""Klient serwera PoB (pob/server.lua). Jeden proces = jeden wczytany build; polecenia po JSON-lines."""

from __future__ import annotations

import collections
import json
import os
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.parser.parse import decode_code

DEFAULT_CMD = ["docker", "run", "-i", "--rm", "aipoetool-pob"]


class PobError(RuntimeError):
    """Błąd zwrócony przez serwer PoB albo awaria procesu."""


def default_cmd() -> list[str]:
    """Polecenie startu serwera. POB_ENGINE_CMD pozwala podać własne (np. lokalny LuaJIT)."""
    env = os.environ.get("POB_ENGINE_CMD")
    return shlex.split(env, posix=(os.name != "nt")) if env else list(DEFAULT_CMD)


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
            text=True, encoding="utf-8", errors="replace", bufsize=1,
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
