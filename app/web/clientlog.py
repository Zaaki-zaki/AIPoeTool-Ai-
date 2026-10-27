"""Poziom postaci z logu gry (Client.txt): linia "... : Nazwa (Klasa) is now level N". Tylko odczyt pliku logu."""

from __future__ import annotations

import os
import re
from pathlib import Path

CANDIDATES = [
    r"C:\Program Files (x86)\Grinding Gear Games\Path of Exile\logs\Client.txt",
    r"C:\Program Files (x86)\Steam\steamapps\common\Path of Exile\logs\Client.txt",
    r"C:\Program Files\Steam\steamapps\common\Path of Exile\logs\Client.txt",
    r"D:\Steam\steamapps\common\Path of Exile\logs\Client.txt",
    r"D:\SteamLibrary\steamapps\common\Path of Exile\logs\Client.txt",
    r"D:\Games\Path of Exile\logs\Client.txt",
]
LEVEL = re.compile(r"\] : (.+?) \((\w+)\) is now level (\d+)")
TAIL_BYTES = 400_000


def find_log() -> Path | None:
    env = os.environ.get("POE_CLIENT_TXT")
    for c in ([env] if env else []) + CANDIDATES:
        p = Path(c)
        if p.is_file():
            return p
    return None


def last_level(path: Path | None = None) -> dict | None:
    """Ostatni awans w logu: {'character','class','level'} albo None (brak pliku/awansu)."""
    path = path or find_log()
    if not path:
        return None
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            text = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    hits = LEVEL.findall(text)
    if not hits:
        return None
    name, cls, lvl = hits[-1]
    return {"character": name, "class": cls, "level": int(lvl)}
