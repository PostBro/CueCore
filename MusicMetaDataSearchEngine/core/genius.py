"""Genius-Lookup: Song -> Songwriter (Autoren), als VORSCHLAG mit Quellenlink.

Warum: MusicBrainz und Discogs führen für viele Singer-Songwriter keine Werk-Relation bzw. keine
Writer-Credits, Genius hat sie community-gepflegt („Written by").

Offizielle API (docs.genius.com): OAuth2, für Apps ohne Nutzer reicht der
**Client Access Token** („Generate Access Token" auf der API-Client-Seite), nur Lese-Endpunkte;
Header `Authorization: Bearer <token>`; `GET /search?q=` und `GET /songs/:id`; jede Antwort
`{"meta": {"status": n}, "response": {...}}`. Die Doku beschreibt die Felder der Song-Antwort nicht —
`writer_artists` (Liste von Artists mit `name`) ist das Feld, das die API tatsächlich liefert; die
Song-Antwort trägt außerdem `artist_names`, `primary_artist`, `featured_artists` und `producer_artists`.
Die Doku weist ausdrücklich darauf hin, dass die kommerzielle Nutzung der Genius-API eine Lizenz
voraussetzt — im Sendebetrieb ist das ein Lizenzthema, nicht nur ein Token (Notiz in den Einstellungen).

Nur Autoren, nie Label: Genius kennt keine Labelcodes. Parser sind netzfrei testbar, die
Netzschicht (fetch) wird injiziert — derselbe Fetcher wie MusicBrainz/Discogs (Cache + Limiter),
der Bearer-Header hängt am Host api.genius.com.
"""
from __future__ import annotations
import re
import urllib.parse
from dataclasses import dataclass, field

GENIUS = "https://api.genius.com"
HOST = "api.genius.com"
SONG_URL_PREFIX = "https://genius.com/"


def auth_header(token: str) -> dict:
    token = (token or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass
class GeniusResult:
    writers: list[str] = field(default_factory=list)   # Songwriter in Genius-Reihenfolge
    song_url: str = ""                                  # Quellenlink für die Karte
    title: str = ""
    artist: str = ""
    song_id: int = 0


_PUNCT = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _PUNCT.sub(" ", (s or "").lower()).strip()


def _base(s: str) -> str:
    """Titel ohne Klammer-Zusätze ('(Instrumental)', '[Live]') zum Vergleich."""
    return _norm(re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", s or ""))


def pick_hit(data: dict, artist: str, title: str) -> dict | None:
    """Treffer aus /search, dessen Titel UND Interpret passen — sonst None (lieber nichts als der
    falsche Song: 'Hurt' gibt es hundertfach). Interpret: Genius-`primary_artist.name` enthält den
    gesuchten oder umgekehrt (Ry X / RY X, 'Billie Eilish' / 'Billie Eilish & FINNEAS')."""
    hits = ((data or {}).get("response") or {}).get("hits") or []
    a, t = _norm(artist), _base(title)
    if not a or not t:
        return None
    for h in hits:
        r = h.get("result") or {}
        if h.get("type", "song") != "song":
            continue
        rt = _base(r.get("title") or "")
        ra = _norm(((r.get("primary_artist") or {}).get("name")) or "")
        if rt == t and ra and (ra == a or a in ra or ra in a):
            return r
    return None


def parse_song(data: dict) -> GeniusResult | None:
    """/songs/:id -> Songwriter. `writer_artists` ist das Feld der Song-Antwort (Liste von Artists)."""
    song = ((data or {}).get("response") or {}).get("song") or {}
    if not song:
        return None
    writers = [w.get("name", "").strip() for w in (song.get("writer_artists") or []) if w.get("name")]
    return GeniusResult(writers=writers, song_url=song.get("url") or "", title=song.get("title") or "",
                        artist=((song.get("primary_artist") or {}).get("name")) or "", song_id=int(song.get("id") or 0))


def lookup(artist: str, title: str, *, fetch, sleep=None) -> GeniusResult | None:
    """Suche + Song-Details. fetch(url) -> dict|None (Fetcher mit Bearer-Header für api.genius.com).
    None ohne passenden Treffer, ohne Token (401 -> fetch liefert None) oder ohne Songwriter."""
    q = urllib.parse.quote(f"{artist} {title}")
    data = fetch(f"{GENIUS}/search?q={q}")
    if not data:
        return None
    hit = pick_hit(data, artist, title)
    if not hit or not hit.get("id"):
        return None
    song = fetch(f"{GENIUS}/songs/{hit['id']}")
    res = parse_song(song) if song else None
    if res is None:
        return None
    if not res.writers:
        return None
    return res
