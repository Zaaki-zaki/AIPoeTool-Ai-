"""Pobieranie z dysku-cache: User-Agent, timeout, TTL, brak wyjątków (błąd = None + powód)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = f"AIPoeTool/0.1 (local personal tool; contact: {os.environ.get('AIPOE_CONTACT', 'zachariaszpozorski@gmail.com')})"
DEFAULT_TTL = 6 * 3600       # ceny i wiki nie zmieniają się co minutę
MIN_INTERVAL = 1.0           # s między zapytaniami do tego samego hosta


class Fetcher:
    def __init__(self, cache_dir: Path | str | None = None, ttl: int = DEFAULT_TTL,
                 client: httpx.Client | None = None, user_agent: str = USER_AGENT) -> None:
        self.dir = Path(cache_dir) if cache_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.client = client or httpx.Client(timeout=15, headers={"User-Agent": user_agent}, follow_redirects=True)
        self._last: dict[str, float] = {}
        self.errors: list[str] = []

    def _path(self, key: str) -> Path | None:
        return self.dir / (hashlib.sha1(key.encode()).hexdigest() + ".json") if self.dir else None

    def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                 ttl: int | None = None) -> Any | None:
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        p = self._path(key)
        ttl = self.ttl if ttl is None else ttl
        if p and p.exists() and time.time() - p.stat().st_mtime < ttl:
            return json.loads(p.read_text(encoding="utf-8"))
        host = httpx.URL(url).host
        wait = MIN_INTERVAL - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.client.get(url, params=params, headers=headers)
            self._last[host] = time.time()
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            self.errors.append(f"{host}: {type(e).__name__}: {str(e)[:120]}")
            if p and p.exists():  # przeterminowany cache lepszy niż nic
                return json.loads(p.read_text(encoding="utf-8"))
            return None
        if p:
            p.write_text(json.dumps(data), encoding="utf-8")
        return data
