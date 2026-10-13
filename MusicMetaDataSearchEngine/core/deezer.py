"""Deezer-Lookup (frei, KEIN Key): Track -> Album-Label + ISRC + Album/Datum.

Dritte unabhängige Quelle für das Label. Deezers öffentliche API gibt pro Album
ein `label`-Feld; der Name wird via labelmatch gegen den GVL-Master gemappt.
Parser sind netzfrei testbar; die Netzschicht (fetch) wird injiziert.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEEZER = "https://api.deezer.com"
USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"


@dataclass
class DeezerResult:
    label: str = ""
    isrc: str = ""
    album: str = ""
    date: str = ""
    track_url: str = ""


def http_get_json(url: str, ua: str = USER_AGENT, *, retries: int = 3) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1)); continue
            return None
        except Exception:
            return None
    return None


def parse_first_track(data: dict) -> dict | None:
    """Erster Track aus /search -> {album_id, isrc, link}."""
    items = data.get("data") or []
    if not items:
        return None
    t = items[0]
    return {"album_id": (t.get("album") or {}).get("id"),
            "isrc": t.get("isrc", "") or "", "link": t.get("link", "") or ""}


def parse_album(data: dict) -> dict:
    """Album -> {label, album, date}."""
    return {"label": data.get("label", "") or "",
            "album": data.get("title", "") or "",
            "date": data.get("release_date", "") or ""}


def lookup(artist: str, title: str, *, fetch=http_get_json, sleep=time.sleep) -> DeezerResult | None:
    q = urllib.parse.quote(f'artist:"{artist}" track:"{title}"')
    data = fetch(f"{DEEZER}/search?q={q}&limit=1")
    sleep(0.3)
    if not data:
        return None
    tr = parse_first_track(data)
    if not tr:
        return None
    res = DeezerResult(isrc=tr["isrc"], track_url=tr["link"])
    if tr["album_id"]:
        alb = fetch(f"{DEEZER}/album/{tr['album_id']}")
        sleep(0.3)
        if alb:
            a = parse_album(alb)
            res.label, res.album, res.date = a["label"], a["album"], a["date"]
    return res
