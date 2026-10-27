"""Pobranie PoB (kod Lua z GitHuba) do katalogu engine/pob, żeby silnik działał bez Dockera.

Uruchamia się samo przy pierwszym starcie aplikacji albo ręcznie: uv run python -m app.calc.setup
Pobiera archiwum zip z przypiętym commitem (ta sama wersja co w pob/Dockerfile) i rozpakowuje tylko kod i dane (bez obrazków).
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

TREE_IMG_VERSION = "3_29"  # grafiki drzewka (ikony nodów) tylko dla bieżącej wersji
POB_COMMIT = "16de4b82d57f1c0de6eb40f37143c32d4da36a02"
URL = f"https://codeload.github.com/PathOfBuildingCommunity/PathOfBuilding/zip/{POB_COMMIT}"
ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = Path(os.environ.get("AIPOE_POB_DIR") or ROOT / "engine" / "pob")
SKIP_EXT = {".png", ".jpg", ".jpeg", ".webp", ".dds", ".tga", ".ogg", ".mp3", ".gif", ".ico", ".psd"}
_lock = threading.Lock()


def src_dir() -> Path:
    return ENGINE_DIR / "src"


def ready() -> bool:
    return ((src_dir() / "HeadlessWrapper.lua").exists() and (ENGINE_DIR / "runtime" / "lua" / "dkjson.lua").exists()
            and (src_dir() / "TreeData" / TREE_IMG_VERSION / "skills-3.jpg").exists())


def extract(data: bytes, dest: Path = ENGINE_DIR) -> int:
    """Rozpakowuje zip z GitHuba (jeden katalog główny) do dest. Zwraca liczbę plików."""
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    n = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else info.filename
            if not rel or not (rel.startswith("src/") or rel.startswith("runtime/") or rel == "LICENSE.md"):
                continue
            if Path(rel).suffix.lower() in SKIP_EXT and not rel.startswith(f"src/TreeData/{TREE_IMG_VERSION}/"):
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            n += 1
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return n


def ensure(progress=None) -> Path:
    """Zapewnia, że PoB jest na dysku (pobiera przy pierwszym użyciu). Zwraca katalog src."""
    with _lock:
        if ready():
            return src_dir()
        import httpx
        if progress:
            progress("Pobieram Path of Building (jednorazowo, kilkadziesiąt MB)…")
        try:
            r = httpx.get(URL, timeout=300, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Nie udało się pobrać PoB z GitHuba ({e}). Sprawdź internet albo użyj Dockera (POB_ENGINE_CMD).") from e
        n = extract(r.content)
        if not ready():
            raise RuntimeError(f"Archiwum PoB jest niekompletne ({n} plików).")
        return src_dir()


if __name__ == "__main__":
    print("PoB:", ensure(print))
    sys.exit(0)
