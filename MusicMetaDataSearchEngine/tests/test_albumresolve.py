from core import albumresolve, discogs, musicbrainz, musiksammler
from core.labelmatch import build_index
from core.musicbrainz import MBReleaseHit

# Mini-GVL-Index (Rhino/Atlantic/Capitol reichen für die Album-Fälle)
IDX = build_index([("02982", "Rhino", "Warner Music"),
                   ("00121", "Atlantic", "Warner Music"),
                   ("00148", "Capitol", "Universal Music")])


# --- MusicBrainz-Release-Suche ---

def test_parse_release_search_dedupes_labels():
    data = {"releases": [{"id": "rel-1", "title": "Twin Peaks", "status": "Official", "score": 100,
                          "date": "2017", "label-info": [{"label": {"name": "Rhino"}},
                                                         {"label": {"name": "Rhino"}}]}]}
    hits = musicbrainz.parse_release_search(data)
    assert len(hits) == 1
    assert hits[0].labels == ["Rhino"] and hits[0].status == "Official"
    assert hits[0].release_id == "rel-1" and hits[0].score == 100


def test_search_releases_builds_query():
    seen = {}
    def fake(url):
        seen["url"] = url
        return {"releases": [{"id": "r1", "title": "A", "status": "Official",
                              "label-info": [{"label": {"name": "Rhino"}}]}]}
    hits = musicbrainz.search_releases("Twin Peaks Soundtrack", fetch=fake, sleep=lambda *_: None)
    assert hits[0].labels == ["Rhino"]
    assert "/release?" in seen["url"] and "Twin" in seen["url"]


# --- Discogs-Album-Suche ---

def test_parse_search_release_ids_dedupes_and_limits():
    data = {"results": [{"id": 123, "title": "A"}, {"id": 456}, {"id": 123}]}
    assert discogs.parse_search_release_ids(data, limit=5) == ["123", "456"]


def test_search_release_ids_uses_search_endpoint():
    seen = []
    ids = discogs.search_release_ids("Album X", fetch=lambda url: (seen.append(url) or {"results": [{"id": 7}]}),
                                     sleep=lambda *_: None)
    assert ids == ["7"] and "database/search" in seen[0]


# --- musik-sammler.de Bestätigung ---

def test_musiksammler_confirms_when_label_present():
    html = "<h1>Labelcode: 002982</h1><div>Label: Rhino Entertainment Company</div>"
    assert musiksammler.confirms("02982", "Rhino", get=lambda url: html) is True


def test_musiksammler_rejects_when_label_absent():
    assert musiksammler.confirms("99999", "Rhino", get=lambda url: "<div>keine Treffer</div>") is False


def test_musiksammler_ignores_too_short_label():
    assert musiksammler.confirms("02982", "AB", get=lambda url: "<div>AB</div>") is False


def test_musiksammler_url():
    assert musiksammler.label_url("02982") == "https://www.musik-sammler.de/label/02982/"


# --- Orchestrator ---

def test_resolve_album_mb_only():
    hits = [MBReleaseHit(title="Twin Peaks", status="Official", labels=["Rhino"], score=100, release_id="rel-1")]
    res = albumresolve.resolve_album("Twin Peaks", "Angelo Badalamenti", IDX, mb_search=lambda alb: hits)
    assert res.best.labelcode == "02982" and res.best.label == "Rhino"
    assert res.best.sources == ["musicbrainz"] and res.best.dual_source is False
    assert res.best.confidence == 0.5
    assert res.best.source_urls[0]["url"] == "https://musicbrainz.org/release/rel-1"


def test_resolve_album_dual_source_with_musiksammler():
    hits = [MBReleaseHit(title="X", status="Official", labels=["Rhino"], score=100, release_id="rel-1")]
    res = albumresolve.resolve_album("X", "", IDX, mb_search=lambda alb: hits,
                                     ms_confirm=lambda code, label: code == "02982")
    assert res.best.dual_source is True and res.best.confidence == 0.7
    assert set(res.best.sources) == {"musicbrainz", "musik-sammler"}
    assert any(u["name"] == "musik-sammler" for u in res.best.source_urls)


def test_resolve_album_discogs_drops_code_not_in_master():
    res = albumresolve.resolve_album("Y", "", IDX, mb_search=lambda alb: [],
                                     discogs_search=lambda alb: ["r1"],
                                     discogs_codes=lambda rid: ["88888"])
    assert res.best is None
    assert any("nicht im GVL-Master" in n for n in res.notes)


def test_resolve_album_discogs_valid_code():
    res = albumresolve.resolve_album("Y", "", IDX, mb_search=lambda alb: [],
                                     discogs_search=lambda alb: ["r1"],
                                     discogs_codes=lambda rid: ["00121"])
    assert res.best.labelcode == "00121" and res.best.sources == ["discogs"]


def test_resolve_album_triple_source_ranks_first():
    # Rhino von MB + Discogs + musik-sammler; Capitol nur MB -> Rhino zuerst
    hits = [MBReleaseHit(title="X", status="Official", labels=["Rhino", "Capitol"], score=100, release_id="r1")]
    res = albumresolve.resolve_album("X", "", IDX, mb_search=lambda alb: hits,
                                     discogs_search=lambda alb: ["d1"],
                                     discogs_codes=lambda rid: ["02982"],
                                     ms_confirm=lambda code, label: code == "02982")
    assert res.best.labelcode == "02982"
    assert len(set(res.best.sources)) == 3 and res.best.confidence == 0.9


def test_resolve_album_prefers_official_release_url():
    hits = [MBReleaseHit(title="X", status="", labels=["Rhino"], score=90, release_id="r0"),
            MBReleaseHit(title="X", status="Official", labels=["Rhino"], score=95, release_id="r1")]
    res = albumresolve.resolve_album("X", "", IDX, mb_search=lambda alb: hits)
    assert res.best.source_urls[0]["url"].endswith("/r1")   # Official zuerst verarbeitet


def test_resolve_album_empty_album():
    res = albumresolve.resolve_album("  ", "", IDX, mb_search=lambda alb: [])
    assert res.best is None and any("Kein Albumname" in n for n in res.notes)


# --- DE/EU zuerst auch im Album-Pfad ---

IDX_DE = build_index([("03098", "Polydor", "Universal Music"),
                      ("00121", "Atlantic", "Warner Music")])


def _mb_release(rid, label, country="", events=()):
    r = {"id": rid, "title": "X", "status": "Official", "score": 100,
         "label-info": [{"label": {"name": label}}]}
    if country:
        r["country"] = country
    if events:
        r["release-events"] = [{"area": {"iso-3166-1-codes": [c]}} for c in events]
    return r


def test_parse_release_search_traegt_den_laenderbonus():
    hits = musicbrainz.parse_release_search({"releases": [
        _mb_release("a", "Polydor", "DE"), _mb_release("b", "Atlantic", "US"),
        _mb_release("c", "Atlantic", events=("FR",)), _mb_release("d", "Atlantic")]})
    assert [h.country_bonus for h in hits] == [3, 0, 2, 0]


def test_album_gleichstand_deutsche_pressung_vor_us_pressung():
    """Zwei Official-Releases, gleiche Belegung: die deutsche Pressung gewinnt, nicht der kleinere Labelcode
    (00121 Atlantic, US), egal in welcher Reihenfolge MusicBrainz liefert."""
    for order in (("de", "us"), ("us", "de")):
        rel = {"de": _mb_release("r-de", "Polydor", "DE"), "us": _mb_release("r-us", "Atlantic", "US")}
        hits = musicbrainz.parse_release_search({"releases": [rel[k] for k in order]})
        res = albumresolve.resolve_album("X", "", IDX_DE, mb_search=lambda alb: hits)
        assert [c.labelcode for c in res.candidates] == ["03098", "00121"], order
    # Europa (ohne DE) liegt ebenfalls vor den USA
    hits = musicbrainz.parse_release_search({"releases": [_mb_release("r-us", "Atlantic", "US"),
                                                          _mb_release("r-eu", "Polydor", "XE")]})
    assert albumresolve.resolve_album("X", "", IDX_DE, mb_search=lambda alb: hits).best.labelcode == "03098"


def test_album_land_ist_kein_filter_doppelte_quelle_gewinnt():
    """Das Land ist kein Filter: Bonus nur bei gleicher Belegung — eine US-Pressung, die zwei
    Quellen bestätigen, steht vor einer deutschen mit nur einer."""
    hits = musicbrainz.parse_release_search({"releases": [_mb_release("r-de", "Polydor", "DE"),
                                                          _mb_release("r-us", "Atlantic", "US")]})
    res = albumresolve.resolve_album("X", "", IDX_DE, mb_search=lambda alb: hits,
                                     discogs_search=lambda alb: ["d1"], discogs_codes=lambda rid: ["00121"])
    assert res.best.labelcode == "00121" and res.best.dual_source is True
