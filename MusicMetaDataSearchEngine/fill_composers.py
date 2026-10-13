#!/usr/bin/env python3
"""Füllt komponist/texter in songs.db via MusicBrainz API.

Rate limit: 1 req/sec (MusicBrainz public API).
Lauf: python fill_composers.py [--limit N] [--offset N]
Kann jederzeit unterbrochen werden; bereits gefüllte Songs werden übersprungen.
"""
from __future__ import annotations
import re, sqlite3, sys, time, urllib.parse, urllib.request, json, unicodedata
from pathlib import Path

DB = Path(__file__).parent / "data" / "songs.db"
UA = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"
MB = "https://musicbrainz.org/ws/2"
RATE = 1.2

def get(url: str, retries: int = 3) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                wait = 8 * (attempt + 1)
                print(f"    {e.code}, warte {wait}s ...")
                time.sleep(wait)
            else:
                print(f"    HTTP {e.code}")
                return None
        except Exception as e:
            print(f"    Fehler: {e}")
            return None
    return None

VERSION_RE = re.compile(
    r"\s*[\[\(](?:acoustic|live|demo|radio edit|single|version|edit|mix|remaster|"
    r"bonus|instrumental|stripped|orchestral|feat\..+?)[\]\)].*$", re.I)

def strip_version(title: str) -> str:
    return VERSION_RE.sub("", title).strip()

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.casefold().replace("&", "and")).strip()

def search_recordings(artist: str, title: str) -> list[dict]:
    for q_title in dict.fromkeys([title, strip_version(title)]):
        q = f'artist:"{artist}" AND recording:"{q_title}"'
        params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": "5"})
        data = get(f"{MB}/recording?{params}")
        time.sleep(RATE)
        if data and data.get("recordings"):
            return data["recordings"]
    return []

def recording_detail(mbid: str) -> dict | None:
    data = get(f"{MB}/recording/{mbid}?inc=work-rels&fmt=json")
    time.sleep(RATE)
    return data

def work_detail(mbid: str) -> dict | None:
    data = get(f"{MB}/work/{mbid}?inc=artist-rels&fmt=json")
    time.sleep(RATE)
    return data

def search_work(artist: str, title: str) -> dict | None:
    """Direkte Work-Suche als Fallback wenn Recording kein Work linked hat."""
    for q_title in dict.fromkeys([title, strip_version(title)]):
        q = f'artist:"{artist}" AND work:"{q_title}"'
        params = urllib.parse.urlencode({"query": q, "inc": "artist-rels", "fmt": "json", "limit": "3"})
        data = get(f"{MB}/work?{params}")
        time.sleep(RATE)
        if not data:
            continue
        works = data.get("works", [])
        if not works:
            continue
        # Ersten Work mit Score >= 80 nehmen
        for w in works:
            if int(w.get("score", 0)) >= 80:
                # Vollständige Relations holen (search gibt nur Subset)
                full = work_detail(w["id"])
                if full:
                    return full
    return None

def best_recording(artist: str, title: str, recs: list[dict]) -> dict | None:
    na = norm(artist)
    nt_full = norm(title)
    nt_stripped = norm(strip_version(title))

    def artist_match(r: dict) -> bool:
        artists = [norm(a.get("name", "")) for a in r.get("artist-credit", [])]
        return any(na in a or a in na for a in artists)

    # Erst: exakter Match (voller oder gestrippter Titel)
    for r in recs:
        if int(r.get("score", 0)) < 75:
            continue
        rt = norm(r.get("title", ""))
        if rt in (nt_full, nt_stripped) and artist_match(r):
            return r

    # Fallback: Titel passt ohne Artist-Check (bei feat/collab im Credit)
    for r in recs:
        rt = norm(r.get("title", ""))
        if rt in (nt_full, nt_stripped) and int(r.get("score", 0)) >= 80:
            return r
    return None

def extract_writers(work: dict) -> tuple[str, str]:
    composers, lyricists = [], []
    for rel in work.get("relations", []):
        rtype = rel.get("type", "").lower()
        name = rel.get("artist", {}).get("name", "")
        if not name:
            continue
        if rtype == "composer":
            composers.append(name)
        elif rtype == "lyricist":
            lyricists.append(name)
        elif rtype == "writer":
            # songwriter = Musik + Text
            composers.append(name)
            lyricists.append(name)
        elif rtype == "arranger":
            composers.append(name)
    return "; ".join(dict.fromkeys(composers)), "; ".join(dict.fromkeys(lyricists))

def main():
    args = sys.argv[1:]
    limit = None
    offset = 0
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        if a == "--offset" and i + 1 < len(args):
            offset = int(args[i + 1])

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id, interpret, titel FROM songs WHERE komponist='' AND texter='' ORDER BY interpret, titel"
    ).fetchall()
    con.close()

    total = len(rows)
    rows = rows[offset:]
    if limit:
        rows = rows[:limit]

    print(f"Zu verarbeiten: {len(rows)} (gesamt leer: {total}, offset: {offset})")
    print()

    found = skipped = no_match = 0
    for i, (song_id, artist, title) in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {artist} — {title}")

        work = None

        # Weg 1: Recording → Work
        recs = search_recordings(artist, title)
        if recs:
            rec = best_recording(artist, title, recs)
            if rec:
                detail = recording_detail(rec["id"])
                if detail:
                    rels = detail.get("relations", []) or []
                    work_ids = [r["work"]["id"] for r in rels
                                if r.get("type") == "performance" and r.get("work")]
                    if work_ids:
                        work = work_detail(work_ids[0])

        # Weg 2: Direkte Work-Suche (Fallback)
        if not work:
            work = search_work(artist, title)

        if not work:
            print("    -> kein Work in MB")
            no_match += 1
            continue

        komponist, texter = extract_writers(work)
        if not komponist and not texter:
            print(f"    -> Work '{work.get('title', '?')}': keine Schreiber")
            skipped += 1
            continue

        con = sqlite3.connect(DB)
        con.execute(
            "UPDATE songs SET komponist=?, texter=? WHERE id=?",
            (komponist, texter, song_id))
        con.commit()
        con.close()
        found += 1
        print(f"    OK: {komponist or '–'} / {texter or '–'}")

    print()
    print(f"Fertig: {found} gefüllt | {skipped} Work ohne Schreiber | {no_match} kein MB-Work")

if __name__ == "__main__":
    main()
