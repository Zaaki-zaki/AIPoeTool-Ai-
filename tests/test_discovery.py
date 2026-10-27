"""Warstwa discovery na nagranych odpowiedziach (chmura nie sięga do tych serwisów); bez sieci."""

import json
from pathlib import Path

import httpx
import pytest

from app.agent.loop import run_agent, scope_footer
from app.agent.session import Session
from app.discovery import Discovery, community_lookup
from app.discovery.fetch import Fetcher
from app.discovery.sources import NinjaPrices, RedditNotes, WikiNotes
from app.gamedata import GameDB
from tests.test_agent import ScriptedLLM, call, fake_session, needs_db, say, DB_PATH

NINJA = {"lines": [
    {"name": "Empower Support", "chaosValue": 900, "gemLevel": 4, "gemQuality": 0, "listingCount": 30},
    {"name": "Cyclone", "chaosValue": 1.5, "gemLevel": 20, "gemQuality": 20, "listingCount": 10},
    {"name": "Cyclone", "chaosValue": 0.5, "gemLevel": 20, "gemQuality": 0, "listingCount": 99},
]}
WIKI = {"query": {"pages": [{"title": "Cyclone", "extract": "Cyclone is an attack skill."}]}}
REDDIT = {"data": {"children": [{"data": {"title": "Cyclone tips", "selftext": "x " * 50 + "Cyclone ignores X", "score": 5,
                                          "permalink": "/r/pathofexile/abc"}}]}}


def mock_fetcher(tmp_path, fail=()):
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host in fail:
            return httpx.Response(503)
        if host == "poe.ninja" and req.url.path.endswith("/leagues"):
            return httpx.Response(200, json=[{"id": "TestLeague", "name": "Test League"}])
        if host == "poe.ninja":
            return httpx.Response(200, json=NINJA if req.url.params["type"] == "SkillGem" else {"lines": []})
        if host == "www.poewiki.net":
            return httpx.Response(200, json=WIKI)
        if host == "www.reddit.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if host == "oauth.reddit.com":
            return httpx.Response(200, json=REDDIT)
        return httpx.Response(404)
    f = Fetcher(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    return f


def test_ninja_prefers_plain_20_0_gem(tmp_path):
    out = NinjaPrices(mock_fetcher(tmp_path), "Test").lookup(["cyclone", "Empower Support"])
    assert out["cyclone"]["chaos"] == 0.5 and out["Empower Support"]["chaos"] == 900


def test_league_autodetected(tmp_path):
    n = NinjaPrices(mock_fetcher(tmp_path))
    assert n.league == "TestLeague" or __import__("os").environ.get("AIPOE_LEAGUE")


def test_cache_used_without_network(tmp_path):
    f = mock_fetcher(tmp_path)
    WikiNotes(f).lookup(["Cyclone"])
    f.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert WikiNotes(f).lookup(["Cyclone"])["Cyclone"]["text"].startswith("Cyclone is")
    assert not f.errors


def test_reddit_needs_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    assert not RedditNotes(mock_fetcher(tmp_path)).available()
    monkeypatch.setenv("REDDIT_CLIENT_ID", "a")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "b")
    r = RedditNotes(mock_fetcher(tmp_path))
    assert r.available() and r.lookup(["Cyclone"])["Cyclone"]["posts"][0]["url"].endswith("/r/pathofexile/abc")


@needs_db
def test_lookup_verifies_names_and_labels(tmp_path):
    f = mock_fetcher(tmp_path)
    d = Discovery([NinjaPrices(f), WikiNotes(f)], f)
    res = community_lookup(d, GameDB(DB_PATH), ["Cyclone", "Wymyślony Gem"])
    assert res["unknown_names"] == ["Wymyślony Gem"]
    assert [i["name"] for i in res["items"]] == ["Cyclone"]
    assert "NIEZWERYFIKOWANE" in res["label"]
    assert res["items"][0]["price"]["chaos"] == 0.5 and "wiki" in res["items"][0]


@needs_db
def test_sources_down_degrade_gracefully(tmp_path):
    f = mock_fetcher(tmp_path, fail=("poe.ninja", "www.poewiki.net"))
    d = Discovery([NinjaPrices(f), WikiNotes(f), RedditNotes(f)], f)
    res = community_lookup(d, GameDB(DB_PATH), ["Cyclone"])
    assert res["sources_used"] == [] and len(res["sources_skipped"]) == 3
    assert res["items"] == [{"name": "Cyclone"}] and res["network_errors"]


def test_footer_marks_community_data():
    f = scope_footer([], [], False, None, ["poe.ninja (ceny)"], ["reddit (brak konfiguracji)"])
    assert "NIEZWERYFIKOWANE" in f and "Niedostępne źródła" in f and "ceny" not in f.split("Nie sprawdzono:")[1].split(".")[0]


@needs_db
def test_agent_tool_end_to_end(tmp_path):
    db = GameDB(DB_PATH)
    s = fake_session(db)
    f = mock_fetcher(tmp_path)
    s.discovery = Discovery([NinjaPrices(f), WikiNotes(f)], f)
    llm = ScriptedLLM([call("community_info", names=["Cyclone"]), say("Cyclone kosztuje ok. 0.5c (niezweryfikowane).")])
    events = list(run_agent(s, llm, "ile kosztuje Cyclone?"))
    ans = events[-1]["text"]
    assert events[-1]["type"] == "answer" and "NIEZWERYFIKOWANE" in ans
    tool_msg = json.loads(llm.calls[1][0][-1]["content"])
    assert tool_msg["items"][0]["price"]["chaos"] == 0.5
