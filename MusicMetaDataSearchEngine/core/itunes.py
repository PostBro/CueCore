"""iTunes-Search-Lookup (frei, KEIN Key): Album/Jahr/Genre als Anreicherung.

iTunes liefert KEIN Label und KEINE ISRC — taugt nicht zur LC-Bestätigung, nur als
Album/Jahr-Fallback, wenn MB/Deezer nichts liefern. Bewusst niedrige Priorität.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ITUNES = "https://itunes.apple.com/search"


@dataclass
class ItunesResult:
    album: str = ""
    date: str = ""
    genre: str = ""


def http_get_json(url: str, ua: str = "CueCore/0.1", *, retries: int = 2) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2); continue
            return None
        except Exception:
            return None
    return None


def parse_first(data: dict) -> ItunesResult | None:
    items = data.get("results") or []
    if not items:
        return None
    t = items[0]
    return ItunesResult(album=t.get("collectionName", "") or "",
                        date=(t.get("releaseDate", "") or "")[:10],
                        genre=t.get("primaryGenreName", "") or "")


def lookup(artist: str, title: str, *, fetch=http_get_json, sleep=time.sleep) -> ItunesResult | None:
    q = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 1})
    data = fetch(f"{ITUNES}?{q}")
    sleep(0.2)
    return parse_first(data) if data else None
