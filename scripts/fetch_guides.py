"""Dokłada do data/wiki.db treść poradników: transkrypcje filmów YouTube wybranych twórców i strony poradników (Maxroll, Mobalytics...).

Uruchom na swoim komputerze (potrzebny internet i klucz YOUTUBE_API_KEY w .env):

    uv run --with youtube-transcript-api python scripts/fetch_guides.py
    uv run --with youtube-transcript-api --with yt-dlp python scripts/fetch_guides.py --browser firefox --max-videos 300 --channels @Alliee @Palsteron
    uv run python scripts/fetch_guides.py --urls https://mobalytics.gg/poe/profile/allie/builds/allies-bob-and-friends-3-29-starter-guide

Można przerywać i wznawiać (pobrane wcześniej pozycje są pomijane). Napisy generowane automatycznie bywają niedokładne,
dlatego wyniki w aplikacji są oznaczone źródłem [YouTube]/[Poradnik] i traktowane jako niezweryfikowane.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import wiki  # noqa: E402
from app.env import load_env  # noqa: E402

CHANNELS = ["@Alliee", "@Palsteron", "@Ben_", "@Mathil", "@Subtractem", "@ZiggyD", "@Steelmage", "@Tytykiller", "@CaptainLance", "@Raizqt"]
SITEMAPS = ["https://maxroll.gg/sitemap.xml"]
UA = "Mozilla/5.0 (compatible; AIPoeTool/1.0; osobista baza wiedzy)"
YT = "https://www.googleapis.com/youtube/v3"
POE = re.compile(r"path of exile|\bpoe\b|\b3\.\d{2}\b|league start|\bbuild\b|guide", re.I)


# ----------------------------------------------------------------- YouTube
def channel_uploads(http: httpx.Client, key: str, handle: str, limit: int) -> tuple[str, list[dict]]:
    r = http.get(f"{YT}/channels", params={"part": "contentDetails,snippet", "forHandle": handle, "key": key}).json()
    items = r.get("items") or []
    if not items:
        return handle, []
    pl = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    name = items[0]["snippet"]["title"]
    out, token = [], None
    while len(out) < limit:
        d = http.get(f"{YT}/playlistItems", params={"part": "snippet", "playlistId": pl, "maxResults": 50, "pageToken": token, "key": key}).json()
        for it in d.get("items", []):
            sn = it["snippet"]
            out.append({"id": sn["resourceId"]["videoId"], "title": sn["title"], "desc": sn.get("description", ""), "date": sn.get("publishedAt", "")[:10]})
        token = d.get("nextPageToken")
        if not token:
            break
    return name, out[:limit]


def transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        sys.exit("Brak pakietu youtube-transcript-api. Uruchom: uv run --with youtube-transcript-api python scripts/fetch_guides.py")
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            parts = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
            texts = [p["text"] for p in parts]
        else:
            texts = [s.text for s in YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])]
    except Exception:  # noqa: BLE001 - brak napisów / blokada
        return None
    return re.sub(r"\s+", " ", " ".join(texts)).strip()


_COOKIES_BROKEN = False


def transcript_ytdlp(video_id: str, browser: str | None) -> str | None:
    """Rezerwa, gdy youtube-transcript-api jest blokowane: yt-dlp (opcjonalnie z ciasteczkami przeglądarki)."""
    try:
        import yt_dlp
    except ImportError:
        return None
    global _COOKIES_BROKEN
    if _COOKIES_BROKEN:
        browser = None
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignore_no_formats_error": True, "format": "all/best"}
    if browser and browser.lower().endswith(".txt"):
        opts["cookiefile"] = browser  # plik cookies.txt wyeksportowany z przeglądarki (rozszerzenie "Get cookies.txt LOCALLY")
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        tracks = (info.get("subtitles") or {}).get("en") or (info.get("automatic_captions") or {}).get("en") or []
        url = next((t["url"] for t in tracks if t.get("ext") == "json3"), None)
        if not url:
            return None
        data = httpx.get(url, timeout=30).json()
        text = " ".join(seg.get("utf8", "") for ev in data.get("events", []) for seg in ev.get("segs", []) or [])
        return re.sub(r"\s+", " ", text).strip() or None
    except Exception as e:  # noqa: BLE001
        if browser and not _COOKIES_BROKEN and ("cookie" in str(e).lower() or "DPAPI" in str(e)):
            _COOKIES_BROKEN = True
            print("\nNie da się odczytać ciasteczek przeglądarki (szyfrowanie Chromium). Dalej bez ciasteczek; "
                  "użyj pliku cookies.txt (--browser C:\\ścieżka\\cookies.txt).")
            return transcript_ytdlp(video_id, None)
        return None


def fetch_youtube(c, key: str, handles: list[str], limit: int, browser: str | None = None) -> None:
    with httpx.Client(timeout=30) as http:
        for h in handles:
            name, vids = channel_uploads(http, key, h, limit)
            if not vids:
                print(f"{h}: nie znaleziono kanału (pomijam)")
                continue
            new = fails = 0
            for v in vids:
                if not POE.search(v["title"] + " " + v["desc"][:300]):
                    continue
                title = f"[YouTube] {name}: {v['title']} ({v['date']}) https://youtu.be/{v['id']}"
                if c.execute("SELECT 1 FROM pages WHERE title=?", (title,)).fetchone():
                    continue
                tx = transcript(v["id"]) or transcript_ytdlp(v["id"], browser)
                fails = 0 if tx else fails + 1
                if fails >= 15:
                    print(f"\n{name}: 15 filmów z rzędu bez napisów. YouTube prawdopodobnie blokuje pobieranie z tego IP, "
                          "albo kanał nie ma napisów. Przerywam ten kanał; spróbuj ponownie później (pobrane filmy zostają).")
                    break
                if tx and len(tx) > 400:
                    c.execute("INSERT OR REPLACE INTO pages VALUES (?,?)", (title, tx)); new += 1
                    c.commit()
                print(f"\r{name}: +{new} transkrypcji ({v['date']})", end="", flush=True)
                time.sleep(1.0)
            print(f"\n{name}: razem nowych {new}")


# ----------------------------------------------------------------- strony poradników
class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._t = False
        self._json = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") == "application/json" or a.get("id") == "__NEXT_DATA__"):
            self._json = True
        elif tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._t = True
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "br", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._json:
            self._json = False
        elif tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        if tag == "title":
            self._t = False

    def handle_data(self, data):
        if self._t:
            self.title += data
        elif self._json:  # strony SPA (Mobalytics, Next.js) trzymają treść w JSON; bierzemy długie napisy
            try:
                self.parts += ["\n" + s for s in _json_strings(json.loads(data))]
            except ValueError:
                pass
        elif not self.skip and data.strip():
            self.parts.append(data.strip() + " ")


def _json_strings(o, out=None):
    out = [] if out is None else out
    if isinstance(o, str):
        if len(o) > 40 and " " in o and not o.startswith(("http", "{", "/")):
            out.append(re.sub(r"<[^>]+>", " ", o))
    elif isinstance(o, dict):
        for v in o.values():
            _json_strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _json_strings(v, out)
    return out


def page_text(html: str) -> tuple[str, str]:
    p = _Text()
    p.feed(html)
    text = re.sub(r"\n\s*\n+", "\n", "".join(p.parts)).strip()
    return p.title.strip(), text


def discover(http: httpx.Client) -> list[str]:
    urls: list[str] = []
    for sm in SITEMAPS:
        try:
            r = http.get(sm)
            locs = re.findall(r"<loc>([^<]+)</loc>", r.text)
            subs = [u for u in locs if u.endswith(".xml") and "poe" in u.lower()]
            for sub in subs[:10]:
                locs += re.findall(r"<loc>([^<]+)</loc>", http.get(sub).text)
            urls += [u for u in locs if "/poe/" in u and not u.endswith(".xml")]
        except httpx.HTTPError:
            continue
    return urls


def fetch_pages(c, urls: list[str]) -> None:
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as http:
        urls = urls or discover(http)
        print(f"Stron do pobrania: {len(urls)}")
        new = 0
        for u in urls:
            if c.execute("SELECT 1 FROM pages WHERE title LIKE ?", (f"%{u}",)).fetchone():
                continue
            try:
                r = http.get(u)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            title, text = page_text(r.text)
            if len(text) > 800:
                c.execute("INSERT OR REPLACE INTO pages VALUES (?,?)", (f"[Poradnik] {title[:90]} {u}", text)); new += 1
                c.commit()
            print(f"\rporadniki: +{new}", end="", flush=True)
            time.sleep(1.0)
        print()


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", nargs="*", default=CHANNELS)
    ap.add_argument("--max-videos", type=int, default=150)
    ap.add_argument("--urls", nargs="*", default=None)
    ap.add_argument("--browser", default=None, help="przeglądarka z zalogowanym YouTube (firefox, edge, chrome): jej ciasteczka omijają blokadę IP")
    ap.add_argument("--no-youtube", action="store_true")
    ap.add_argument("--no-pages", action="store_true")
    a = ap.parse_args()
    c = wiki.connect()
    key = os.environ.get("YOUTUBE_API_KEY", "")
    if not a.no_youtube and a.urls is None:
        if not key:
            sys.exit("Brak YOUTUBE_API_KEY w .env.")
        fetch_youtube(c, key, a.channels, a.max_videos, a.browser)
    if not a.no_pages:
        fetch_pages(c, a.urls or [])
    for prefix in ("[YouTube]", "[Poradnik]"):
        n = wiki.build_index(c, prefix=prefix)
        print(f"Indeks {prefix}: {n} fragmentów")
    print("Gotowe.")


if __name__ == "__main__":
    main()
