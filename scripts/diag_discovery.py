"""Sprawdza na żywo (na Twoim komputerze), które źródła discovery działają. Użycie: uv run python scripts/diag_discovery.py [Gem ...]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.discovery import Discovery, community_lookup
from app.gamedata import GameDB

root = Path(__file__).resolve().parents[1]
names = sys.argv[1:] or ["Cyclone", "Empower Support"]
d = Discovery.default(root / "data" / "cache")
res = community_lookup(d, GameDB(root / "data" / "game.db"), names)
print(json.dumps(res, indent=2, ensure_ascii=False))
