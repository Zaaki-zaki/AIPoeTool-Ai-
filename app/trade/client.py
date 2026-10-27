"""Klient oficjalnego API trade (pathofexile.com/api/trade) z limitem zapytań i przeliczaniem walut na chaosy.

Działa tylko z komputera użytkownika (z serwerów chmurowych trade jest zablokowany). Wszystkie zapytania idą jawnie:
GGG wymaga opisowego User-Agenta i respektowania nagłówków limitów, więc klient odstępuje zapytania i słucha 429.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

BASE = "https://www.pathofexile.com"
NINJA = "https://poe.ninja/api/data/currencyoverview"
USER_AGENT = "AIPoeTool/1.0 (personal local tool)"
DATA = Path(__file__).resolve().parents[2] / "data"

# id waluty z trade -> nazwa na poe.ninja
CURRENCY_NAMES = {
    "divine": "Divine Orb", "exalted": "Exalted Orb", "mirror": "Mirror of Kalandra", "annul": "Orb of Annulment",
    "alch": "Orb of Alchemy", "vaal": "Vaal Orb", "regal": "Regal Orb", "fusing": "Orb of Fusing",
    "chrome": "Chromatic Orb", "jewellers": "Jeweller's Orb", "scour": "Orb of Scouring", "blessed": "Blessed Orb",
    "chance": "Orb of Chance", "aug": "Orb of Augmentation", "transmute": "Orb of Transmutation",
    "alt": "Orb of Alteration", "chisel": "Cartographer's Chisel", "gcp": "Gemcutter's Prism",
}
_SKIP_LEAGUE = ("hardcore", "hc ", "ssf", "ruthless", "solo", "standard", "private")

Http = Callable[[str, str, dict[str, Any] | None, dict[str, str]], tuple[int, dict[str, str], Any]]


class TradeError(RuntimeError):
    """Problem z trade (limit zapytań, blokada, zła odpowiedź) - komunikat po polsku."""


def _httpx_call(method: str, url: str, body: dict[str, Any] | None, headers: dict[str, str]) -> tuple[int, dict[str, str], Any]:
    try:
        r = httpx.request(method, url, json=body, headers=headers, timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        raise TradeError(f"Brak połączenia z trade ({e}).") from e
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, {k.lower(): v for k, v in r.headers.items()}, data


class TradeClient:
    def __init__(self, league: str | None = None, http: Http | None = None, data_dir: Path | None = None,
                 min_gap: float = 1.3, sleep: Callable[[float], None] = time.sleep) -> None:
        self.http = http or _httpx_call
        self.data_dir = Path(data_dir) if data_dir else DATA
        self.min_gap = min_gap
        self._sleep = sleep
        self._last = 0.0
        self._lock = threading.Lock()
        self._league = league or os.environ.get("AIPOE_LEAGUE") or None
        self._rates: dict[str, float] | None = None
        self._stats: dict[tuple[str, str], str] | None = None

    # ---- niski poziom ----
    def _call(self, method: str, path: str, body: dict[str, Any] | None = None, base: str = BASE) -> Any:
        url = path if path.startswith("http") else base + path
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            with self._lock:
                wait = self.min_gap - (time.time() - self._last)
                if wait > 0:
                    self._sleep(wait)
                status, hdr, data = self.http(method, url, body, headers)
                self._last = time.time()
            if status == 429:
                retry = int(float(hdr.get("retry-after", "5")))
                if attempt == 2 or retry > 90:
                    raise TradeError(f"Trade ogranicza liczbę zapytań. Spróbuj za {retry} s.")
                self._sleep(retry + 1)
                continue
            if status in (401, 403):
                raise TradeError("Trade odrzuciło zapytanie (403). Za dużo zapytań albo blokada sieci; spróbuj za kilka minut.")
            if status >= 400:
                msg = ""
                if isinstance(data, dict):
                    msg = (data.get("error") or {}).get("message", "") if isinstance(data.get("error"), dict) else ""
                raise TradeError(f"Trade zwróciło błąd {status}. {msg}".strip())
            self._respect_limits(hdr)
            return data
        raise TradeError("Trade nie odpowiada.")

    def _respect_limits(self, hdr: dict[str, str]) -> None:
        """Gdy któreś okno limitu jest prawie pełne, czeka do jego końca (nagłówki X-Rate-Limit-Ip i -State)."""
        rules, state = hdr.get("x-rate-limit-ip"), hdr.get("x-rate-limit-ip-state")
        if not rules or not state:
            return
        try:
            for rule, st in zip(rules.split(","), state.split(",")):
                limit, period, _ = (int(x) for x in rule.split(":"))
                used, _, restricted = (int(x) for x in st.split(":"))
                if restricted:
                    self._sleep(min(restricted, 60))
                elif used >= limit - 1:
                    self._sleep(min(period, 30))
        except ValueError:
            return

    # ---- liga i waluty ----
    def leagues(self) -> list[dict[str, Any]]:
        data = self._call("GET", "/api/trade/data/leagues")
        return [l for l in (data or {}).get("result", []) if l.get("realm", "pc") == "pc"]

    @property
    def league(self) -> str:
        if not self._league:
            self._league = self._pick_league()
        return self._league

    @league.setter
    def league(self, value: str) -> None:
        self._league = value or None
        self._rates = None

    def _pick_league(self) -> str:
        try:
            for l in self.leagues():
                lid = str(l.get("id", ""))
                if lid and not any(s in lid.lower() for s in _SKIP_LEAGUE):
                    return lid
        except TradeError:
            pass
        return "Standard"

    def rates(self) -> dict[str, float]:
        """Kursy walut w chaosach z poe.ninja (chaos = 1). Bez nich waluty inne niż chaos nie mają wartości w chaosach."""
        if self._rates is None:
            rates = {"chaos": 1.0}
            try:
                data = self._call("GET", f"{NINJA}?league={self.league}&type=Currency")
                by_name = {l["currencyTypeName"]: l.get("chaosEquivalent") for l in (data or {}).get("lines", [])}
                for tid, name in CURRENCY_NAMES.items():
                    if by_name.get(name):
                        rates[tid] = float(by_name[name])
            except (TradeError, KeyError, TypeError, ValueError):
                pass
            self._rates = rates
        return self._rates

    def to_chaos(self, amount: float, currency: str) -> float | None:
        rate = self.rates().get(currency)
        return round(amount * rate, 2) if rate else None

    # ---- statystyki (mapowanie tekst moda -> id filtra) ----
    def stats_index(self) -> dict[tuple[str, str], str]:
        if self._stats is None:
            cache = self.data_dir / "trade_stats.json"
            raw = None
            if cache.exists():
                try:
                    raw = json.loads(cache.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raw = None
            if raw is None:
                raw = self._call("GET", "/api/trade/data/stats")
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
            from .item import normalize_mod
            idx: dict[tuple[str, str], str] = {}
            for grp in (raw or {}).get("result", []):
                for e in grp.get("entries", []):
                    key = (str(e.get("type", grp.get("id", ""))).lower(), normalize_mod(str(e.get("text", ""))))
                    idx.setdefault(key, e["id"])
            self._stats = idx
        return self._stats

    # ---- wyszukiwanie ----
    def search(self, query: dict[str, Any]) -> dict[str, Any]:
        data = self._call("POST", f"/api/trade/search/{self.league}", query)
        if not isinstance(data, dict) or "id" not in data:
            raise TradeError("Trade zwróciło nieoczekiwaną odpowiedź na wyszukiwanie.")
        return data

    def fetch(self, ids: list[str], query_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(ids), 10):
            chunk = ",".join(ids[i:i + 10])
            data = self._call("GET", f"/api/trade/fetch/{chunk}?query={query_id}")
            out += [r for r in (data or {}).get("result", []) if r]
        return out

    def listings(self, query: dict[str, Any], limit: int = 10) -> dict[str, Any]:
        """Szuka i pobiera najtańsze oferty. Zwraca {'total','id','listings':[...]} z cenami także w chaosach."""
        q = dict(query)
        q.setdefault("sort", {"price": "asc"})
        res = self.search(q)
        ids = list(res.get("result", []))[:limit]
        raw = self.fetch(ids, res["id"]) if ids else []
        return {"total": res.get("total", len(ids)), "id": res["id"], "listings": [self.normalize(r) for r in raw]}

    def normalize(self, r: dict[str, Any]) -> dict[str, Any]:
        lst, item = r.get("listing", {}), r.get("item", {})
        price = lst.get("price") or {}
        amount, cur = price.get("amount"), price.get("currency")
        acc = lst.get("account", {})
        return {
            "id": r.get("id"), "name": (item.get("name") or "") or item.get("typeLine", ""),
            "base": item.get("baseType") or item.get("typeLine", ""), "ilvl": item.get("ilvl"),
            "price": amount, "currency": cur,
            "price_text": f"{amount:g} {cur}" if amount is not None and cur else "brak ceny",
            "price_chaos": self.to_chaos(amount, cur) if amount is not None and cur else None,
            "account": acc.get("name"), "online": bool(acc.get("online")),
            "indexed": lst.get("indexed"), "whisper": lst.get("whisper"),
            "mods": list(item.get("explicitMods", [])), "implicits": list(item.get("implicitMods", [])),
            "corrupted": bool(item.get("corrupted")),
        }
