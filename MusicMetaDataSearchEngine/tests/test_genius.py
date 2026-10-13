"""Genius-Client: netzfrei gegen die Antwortform der offiziellen API (docs.genius.com: {"meta","response"};
/search -> response.hits[].result mit title/primary_artist/id/url; /songs/:id -> response.song mit
writer_artists[].name)."""
from core import genius
from core.resolver import ResolveResult, resolve
from core.labelmatch import build_index

SEARCH = {"meta": {"status": 200}, "response": {"hits": [
    {"type": "song", "result": {"id": 101, "title": "Hurt", "url": "https://genius.com/Nine-inch-nails-hurt-lyrics",
                                "primary_artist": {"name": "Nine Inch Nails"}}},
    {"type": "song", "result": {"id": 202, "title": "Salt", "url": "https://genius.com/Ry-x-salt-lyrics",
                                "primary_artist": {"name": "Ry X"}}},
]}}
SONG = {"meta": {"status": 200}, "response": {"song": {"id": 202, "title": "Salt", "url": "https://genius.com/Ry-x-salt-lyrics",
        "primary_artist": {"name": "Ry X"}, "writer_artists": [{"name": "Ry X"}, {"name": "Ry Cuming"}]}}}


def test_pick_hit_requires_title_and_artist_match():
    assert genius.pick_hit(SEARCH, "RY X", "Salt")["id"] == 202                 # Groß/Klein egal
    assert genius.pick_hit(SEARCH, "RY X", "Salt (Instrumental)")["id"] == 202  # Klammer-Zusatz ignoriert
    assert genius.pick_hit(SEARCH, "RY X", "Hurt") is None                      # 'Hurt' gehört hier Nine Inch Nails
    assert genius.pick_hit(SEARCH, "Billie Eilish", "Salt") is None
    assert genius.pick_hit({}, "a", "b") is None and genius.pick_hit(SEARCH, "", "Salt") is None


def test_parse_song_reads_writer_artists():
    r = genius.parse_song(SONG)
    assert r.writers == ["Ry X", "Ry Cuming"] and r.song_url.endswith("salt-lyrics") and r.song_id == 202
    assert genius.parse_song({"meta": {"status": 404}}) is None


def test_lookup_chains_search_and_song_and_needs_writers():
    calls = []
    def fetch(url):
        calls.append(url)
        if "/search?" in url: return SEARCH
        if url.endswith("/songs/202"): return SONG
        return None
    r = genius.lookup("RY X", "Salt", fetch=fetch)
    assert r and r.writers == ["Ry X", "Ry Cuming"]
    assert calls[0].startswith("https://api.genius.com/search?q=RY%20X%20Salt") and calls[1].endswith("/songs/202")
    assert genius.lookup("RY X", "Salt", fetch=lambda u: None) is None          # 401/Netz: fetch liefert None
    nowriters = {"meta": {"status": 200}, "response": {"song": {"id": 202, "title": "Salt", "writer_artists": []}}}
    assert genius.lookup("RY X", "Salt", fetch=lambda u: SEARCH if "/search?" in u else nowriters) is None


def test_auth_header():
    assert genius.auth_header(" tok ") == {"Authorization": "Bearer tok"} and genius.auth_header("") == {}


def _mb(artist, title):
    from core.musicbrainz import MBLookup
    return MBLookup(matched=True, score=100, composer="", lyricist="")


def test_resolver_uses_genius_only_when_mb_and_wikidata_have_no_authors():
    idx = build_index([("00121", "Atlantic", "Warner", "")])
    seen = []
    def g(a, t):
        seen.append((a, t)); return genius.GeniusResult(writers=["Ry X", "Ry Cuming"], song_url="https://genius.com/x")
    res = resolve("RY X", "Salt", idx, mb_lookup=_mb, discogs_label_codes=lambda rid: [], genius_lookup=g, swap_retry=False)
    assert res.composer == "Ry X; Ry Cuming" and res.lyricist == res.composer
    assert res.author_source_url == "https://genius.com/x" and any("Genius" in n for n in res.notes)
    # Hat MusicBrainz schon Autoren, wird Genius nicht gefragt
    def mb_with(a, t):
        r = _mb(a, t); r.composer, r.lyricist = "Trent Reznor", "Trent Reznor"; return r
    seen.clear()
    res2 = resolve("RY X", "Hurt", idx, mb_lookup=mb_with, discogs_label_codes=lambda rid: [], genius_lookup=g, swap_retry=False)
    assert seen == [] and res2.composer == "Trent Reznor" and not res2.author_source_url


def test_conncheck_genius(monkeypatch):
    import io, json, urllib.error
    from core import conncheck
    class R:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({"meta": {"status": 200}, "response": {"hits": []}}).encode()
    assert conncheck.check_genius("tok", urlopen=lambda req, timeout: R())["ok"] is True
    def bad(req, timeout): raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
    r = conncheck.check_genius("tok", urlopen=bad)
    assert r["ok"] is False and r["error"] == "auth" and r["status"] == 401
    assert conncheck.check_genius("")["error"] == "not_configured"


def test_work_title_search_beats_genius_for_covers():
    """RY X „Hurt" ist ein Cover: die MB-Werk-Titelsuche kennt Trent Reznor, Genius nur den Interpreten der Seite —
    Genius ist die letzte Autoren-Quelle, nicht die vorletzte."""
    idx = build_index([("00121", "Atlantic", "Warner", "")])
    seen = []
    def g(a, t):
        seen.append("genius"); return genius.GeniusResult(writers=["RY X"], song_url="https://genius.com/x")
    res = resolve("RY X", "Hurt", idx, mb_lookup=_mb, discogs_label_codes=lambda rid: [],
                  work_lookup=lambda t: ("Trent Reznor", "Trent Reznor", ""), genius_lookup=g, swap_retry=False)
    assert res.composer == "Trent Reznor" and seen == [] and not res.author_source_url
