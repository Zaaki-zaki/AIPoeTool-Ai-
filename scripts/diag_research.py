"""Sprawdza research z YouTube. Użycie: uv run python scripts/diag_research.py melee"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.discovery.fetch import Fetcher
from app.env import load_env
from app.gamedata import GameDB
from app.planner.research import ResearchError, research_meta

load_env()
root = Path(__file__).resolve().parents[1]
try:
    print(json.dumps(research_meta(Fetcher(root / "data" / "cache"), GameDB(root / "data" / "game.db"),
                                   " ".join(sys.argv[1:]) or "melee", []), indent=2, ensure_ascii=False))
except ResearchError as e:
    print("BŁĄD:", e)
