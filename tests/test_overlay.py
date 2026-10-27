"""Nakładka: funkcje pomocnicze (bez okna) i test dymny Tk, jeśli jest ekran i Tkinter."""

import os
import time
from datetime import datetime, timezone

import pytest

from app import overlay as O


def test_ago_formats_relative_time() -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    assert O.ago("2026-10-25T11:59:40Z", now) == "przed chwilą"
    assert O.ago("2026-10-25T11:30:00Z", now) == "30 min temu"
    assert O.ago("2026-10-25T09:00:00+00:00", now) == "3 h temu"
    assert O.ago("2026-10-22T12:00:00Z", now) == "3 d temu"
    assert O.ago(None) == "?" and O.ago("śmieci") == "?"


def test_chaos_and_first_line_and_estimate() -> None:
    assert O.chaos_txt(None) == "?" and O.chaos_txt(30.0) == "30c" and O.chaos_txt(2.5) == "2.5c"
    assert O.chaos_txt(250.4) == "250c" and O.chaos_txt(0.5) == "0.5c"
    assert O.first_line("\n\n## **Wynik** `x`\nDruga") == "Wynik x"
    assert O.first_line("a" * 300, 20).endswith("…") and len(O.first_line("a" * 300, 20)) == 20
    assert O.estimate(0, 20) == 0 and O.estimate(10, 20) < O.estimate(60, 20) < 98


def test_popup_rows_cheapest_first_with_chaos_hint() -> None:
    res = {"listings": [
        {"price_text": "2 divine", "currency": "divine", "price_chaos": 300, "account": "bob", "online": False, "indexed": "2026-10-22T10:00:00Z"},
        {"price_text": "30 chaos", "currency": "chaos", "price_chaos": 30, "account": "amy", "online": True},
        {"price_text": "?", "currency": "mirror", "price_chaos": None, "account": "zed"}]}
    rows = O.popup_rows(res, 2)
    assert [r["price"] for r in rows] == ["30 chaos", "2 divine"]
    assert rows[0]["chaos"] == "" and rows[1]["chaos"] == "300c" and rows[0]["online"] == "1"


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="brak ekranu")
def test_overlay_starts_and_stops_with_tk() -> None:
    pytest.importorskip("tkinter")
    ov = O.Overlay("http://127.0.0.1:9", "left")
    assert ov.start(), ov.error
    ov.show_price()
    time.sleep(0.3)
    ov.stop()


def test_tree_text():
    from app.overlay import tree_text
    assert "Brak" in tree_text({"ok": False, "msg": "Brak planu."})
    t = tree_text({"ok": True, "stage": "Akt 3", "level": 30, "game_level": 28, "points": 2,
                   "nodes": [{"name": "Heart of Thunder", "type": "K"}, {"name": "Life", "type": "S"}]})
    assert "Akt 3" in t and "★ keystone  Heart of Thunder" in t and "·  Life" in t
