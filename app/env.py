"""Wczytuje plik .env z katalogu repo (KLUCZ=wartość), nie nadpisując zmiennych już ustawionych w systemie."""

import os
from pathlib import Path


def load_env() -> None:
    p = Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = p.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):  # pusta zmienna systemowa nie blokuje wartości z .env
            os.environ[k] = v
