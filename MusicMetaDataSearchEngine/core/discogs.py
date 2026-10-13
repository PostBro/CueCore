"""Discogs-Lookup: Release-Identifiers -> Label Codes. Keyless.

api.discogs.com/releases/<id> braucht kein Auth und umgeht den 403 der
Website. identifiers[].type == 'Label Code' trägt den LC. Gefundene Codes immer
gegen den GVL-Master gegenprüfen (macht der Resolver) — Discogs allein zählt nicht.
"""
from __future__ import annotations
import json, re, time, urllib.error, urllib.parse, urllib.request

from .normalize import normalize_labelcode

DISCOGS = "https://api.discogs.com"
USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"


def auth_header(token: str = "", consumer_key: str = "", consumer_secret: str = "") -> dict[str, str]:
    """Discogs-Auth als Header — hebt das Rate-Limit ohne OAuth-Flow.
    Vorrang: Personal Token ('Discogs token=...') vor Consumer Key+Secret
    ('Discogs key=..., secret=...'). Formate laut Discogs-Doku. Nichts gesetzt -> {}."""
    if token:
        return {"Authorization": f"Discogs token={token}"}
    if consumer_key and consumer_secret:
        return {"Authorization": f"Discogs key={consumer_key}, secret={consumer_secret}"}
    return {}


def http_get_json(url: str, *, ua: str = USER_AGENT, headers: dict | None = None,
                  retries: int = 3) -> dict | None:
    h = {"User-Agent": ua, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            return None
        except Exception:
            return None
    return None


def parse_label_codes(release: dict) -> list[str]:
    """Label Codes aus identifiers, normalisiert (führende Nullen), dedupliziert."""
    out: list[str] = []
    for ident in release.get("identifiers", []):
        if (ident.get("type") or "").strip().lower() == "label code":
            code = normalize_labelcode(ident.get("value", ""))
            if code and code not in out:
                out.append(code)
    return out


def lookup_label_codes(release_id: str, *, fetch=http_get_json, sleep=time.sleep, rate: float = 1.0) -> list[str]:
    data = fetch(f"{DISCOGS}/releases/{release_id}")
    sleep(rate)
    return parse_label_codes(data) if data else []


# Discogs schreibt Länder als Namen aus („Germany", „Europe", „UK"), nicht als ISO-Code.
# Dieselbe Regel wie bei MusicBrainz: deutsche Pressung zuerst, dann Europa,
# dann der Rest — kein Filter, eine US-Pressung bleibt gültig.
# DDR-Pressungen zählen wie deutsche; Discogs schreibt „German Democratic
# Republic (GDR)“, der Klammerzusatz fällt vor dem Vergleich weg.
_DE_NAMES = frozenset({"germany", "deutschland", "german democratic republic"})
_EU_NAMES = frozenset({"europe", "uk", "united kingdom", "austria", "switzerland", "france", "italy",
                       "spain", "netherlands", "belgium", "denmark", "sweden", "norway", "finland",
                       "ireland", "portugal", "poland", "czech republic", "czechia", "greece",
                       "hungary", "romania", "slovakia", "slovenia", "croatia", "bulgaria",
                       "estonia", "latvia", "lithuania", "luxembourg", "malta", "cyprus", "iceland",
                       "liechtenstein", "scandinavia", "benelux"})


def country_rank(country: str) -> int:
    """0 = Deutschland (auch DDR), 1 = Europa, 2 = alles andere (auch ohne Angabe). Kleiner ist besser.

    Mehrländer-Pressungen schreibt Discogs als Text („UK & France“, „UK, Europe & US“), Gebiete auch mit
    Klammerzusatz („German Democratic Republic (GDR)“): jedes Teil-Land zählt, ein europäisches reicht
    für Rang 1. Nur deutsche Teile ergeben Rang 0."""
    c = (country or "").strip().lower()
    parts = {p.strip() for p in re.split(r"[&,/]| and ", re.sub(r"\([^)]*\)", "", c))} - {""}
    if parts and parts <= _DE_NAMES:
        return 0
    if parts & (_EU_NAMES | _DE_NAMES) or "germany" in c:        # „Germany, Austria & Switzerland" o.ä.
        return 1
    return 2


def parse_search_release_ids(data: dict | None, limit: int = 5) -> list[str]:
    """Release-IDs aus einem Discogs-Search-Ergebnis (type=release). Deutsche Pressung zuerst, dann
    europäische, dann der Rest; innerhalb einer Gruppe bleibt die Reihenfolge von Discogs."""
    results = [r for r in (data or {}).get("results", []) if str(r.get("id", "") or "")]
    results.sort(key=lambda r: country_rank(r.get("country", "")))      # stabil: Relevanz bleibt erhalten
    ids: list[str] = []
    for r in results:
        rid = str(r["id"])
        if rid not in ids:
            ids.append(rid)
        if len(ids) >= limit:
            break
    return ids


def search_release_ids(album: str, artist: str = "", *, fetch=http_get_json, sleep=time.sleep,
                       rate: float = 1.0, limit: int = 5) -> list[str]:
    """Album -> Discogs-Release-IDs (Search-API, braucht Auth-Header am Fetcher).
    Der eigentliche Label Code kommt danach über lookup_label_codes(id). Ohne Auth
    liefert Discogs 401 -> fetch=None -> []."""
    album = album.strip()
    if not album:
        return []
    params = {"type": "release", "release_title": album, "per_page": str(limit)}
    if artist.strip():
        params["artist"] = artist.strip()
    ids = parse_search_release_ids(fetch(f"{DISCOGS}/database/search?" + urllib.parse.urlencode(params)), limit)
    sleep(rate)
    return ids
