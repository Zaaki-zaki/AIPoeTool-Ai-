"""Zapis planu na dysku (data/plan.json + data/plan_layout.json), żeby przetrwał restart aplikacji."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[2] / "data"


def save_plan(plan: dict[str, Any], layout: dict[str, Any], done: int, data: Path = DATA) -> None:
    data.mkdir(exist_ok=True)
    (data / "plan.json").write_text(json.dumps({"plan": plan, "done": done}, ensure_ascii=False), encoding="utf-8")
    (data / "plan_layout.json").write_text(json.dumps(layout), encoding="utf-8")


def save_progress(plan: dict[str, Any], done: int, data: Path = DATA) -> None:
    (data / "plan.json").write_text(json.dumps({"plan": plan, "done": done}, ensure_ascii=False), encoding="utf-8")


def load_plan(data: Path = DATA) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int]:
    try:
        d = json.loads((data / "plan.json").read_text(encoding="utf-8"))
        layout = json.loads((data / "plan_layout.json").read_text(encoding="utf-8"))
        return d["plan"], layout, int(d.get("done", 0))
    except (OSError, ValueError, KeyError):
        return None, None, 0
