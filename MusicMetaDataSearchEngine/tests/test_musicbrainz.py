from core.musicbrainz import fuzzy_recordings, lookup, search_releases, strip_feat

_noop = lambda *a, **k: None


def test_strip_feat_removes_trailing_credit():
    assert strip_feat("Bolo Na (feat. Moor Mother & Joel Ross)") == "Bolo Na"
    assert strip_feat("Saaqi (feat. Vijay Iyer)") == "Saaqi"


def test_strip_feat_leaves_plain_title_untouched():
    assert strip_feat("Whiskey") == "Whiskey"
    assert strip_feat("A Dangerous Thing") == "A Dangerous Thing"


def _recording(rec_id="abc123", title="Bolo Na", artist="Arooj Aftab", score=100):
    return {"id": rec_id, "title": title, "score": score,
            "artist-credit": [{"name": artist}], "releases": []}


def test_lookup_retries_without_feat_suffix_when_full_title_has_no_match():
    calls = []

    def fetch(url):
        calls.append(url)
        if "recording?" in url:
            if "feat" in url:                      # 1. Versuch: voller Klammer-Titel -> nichts
                return {"recordings": []}
            return {"recordings": [_recording()]}  # 2. Versuch: gestrippter Titel -> Treffer
        return None                                 # Release-/ISRC-Detail-Calls: übersprungen

    out = lookup("Arooj Aftab", "Bolo Na (feat. Moor Mother & Joel Ross)", fetch=fetch, sleep=_noop)

    assert out.matched is True
    assert out.recording_id == "abc123"
    recording_searches = [c for c in calls if "recording?" in c]
    assert len(recording_searches) == 2            # voller Titel + gestrippter Titel


def test_lookup_no_retry_when_title_has_no_feat_suffix():
    calls = []

    def fetch(url):
        calls.append(url)
        if "recording?" in url:
            return {"recordings": [_recording(title="Whiskey")]}
        return None

    out = lookup("Arooj Aftab", "Whiskey", fetch=fetch, sleep=_noop)

    assert out.matched is True
    recording_searches = [c for c in calls if "recording?" in c]
    assert len(recording_searches) == 1             # kein unnötiger zweiter Versuch


def test_lookup_no_match_even_after_feat_retry():
    def fetch(url):
        if "recording?" in url:
            return {"recordings": []}                # alle Versuche leer (auch der tolerante)
        return None

    out = lookup("Nobody", "Ghost Track (feat. Nobody Else)", fetch=fetch, sleep=_noop)

    assert out.matched is False
    assert out.recording_id == ""


# --- Fuzzy-/Toleranz-Schicht: kaputte Datei-Schreibweise (ALL CAPS, fehlendes Apostroph) ---

def test_fuzzy_recordings_matches_broken_spelling_and_rejects_cover():
    real = _recording(title="Laura Palmer's Theme", artist="Angelo Badalamenti", score=56)
    cover = _recording(title="Laura Palmers Theme (Angelo Badalamenti)", artist="Daniel Bautista", score=100)
    other = _recording(title="Falling", artist="Angelo Badalamenti", score=80)
    got = fuzzy_recordings("ANGELO BADALAMENTI", "LAURA PALMERS THEME", [real, cover, other])
    assert real in got            # richtiger Track trotz fehlendem Apostroph
    assert cover not in got       # fremder Interpret + zu langer Titel
    assert other not in got       # anderer Titel


def test_lookup_fuzzy_fallback_when_strict_queries_empty():
    calls = []

    def fetch(url):
        calls.append(url)
        if "recording?" in url:
            if "%22" in url:                          # strikte quoted Suche -> nichts
                return {"recordings": []}
            return {"recordings": [_recording(title="Laura Palmer's Theme",
                                              artist="Angelo Badalamenti", score=56)]}
        return None                                    # Release/ISRC-Detailcalls übersprungen

    out = lookup("ANGELO BADALAMENTI", "LAURA PALMERS THEME", fetch=fetch, sleep=_noop)
    assert out.matched is True
    assert out.score == 56                             # niedriger MB-Score -> konservative Confidence
    rec = [c for c in calls if "recording?" in c]
    assert any("%22" in c for c in rec) and any("%28" in c for c in rec)   # strikt + tolerant


def test_search_releases_loose_fallback_when_quoted_empty():
    calls = []

    def fetch(url):
        calls.append(url)
        if "%22" in url:                               # quoted -> nichts (generischer Albumname)
            return {"releases": []}
        return {"releases": [{"title": "Soundtrack from Zzq Testserie", "status": "Official",
                              "label-info": [{"label": {"name": "Warner Bros. Records"}}],
                              "date": "1990", "id": "rel1", "score": 90}]}

    hits = search_releases("ZZQ TESTSERIE SOUNDTRACK", fetch=fetch, sleep=_noop)
    assert len(hits) == 1 and hits[0].labels == ["Warner Bros. Records"]
    assert len([c for c in calls if "release?" in c]) == 2   # quoted + toleranter Fallback


def test_lookup_composer_from_second_work_when_first_has_no_writers():
    # Re-Recordings/Compilations verknüpfen oft mehrere Works; nur eines trägt die Writer.
    # Der Lookup muss über das erste leere Work hinaus prüfen (nicht nur work_ids[0]).
    def fetch(url):
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Bad Blood", artist="Taylor Swift")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": [
                {"type": "performance", "work": {"id": "w1"}},
                {"type": "performance", "work": {"id": "w2"}}]}
        if "/work/w1" in url:
            return {"relations": []}                                  # erstes Work: keine Writer
        if "/work/w2" in url:
            return {"relations": [{"type": "composer", "artist": {"name": "Max Martin"}},
                                  {"type": "lyricist", "artist": {"name": "Taylor Swift"}}]}
        return None

    out = lookup("Taylor Swift", "Bad Blood", fetch=fetch, sleep=_noop)
    assert out.composer == "Max Martin"
    assert out.lyricist == "Taylor Swift"


# --- MB-Werk-Titelsuche (Cover -> Original-Autoren) ---
from core.musicbrainz import parse_work_search, search_work_writers


def test_parse_work_search():
    data = {"works": [{"id": "w1", "title": "Skinny Love", "score": 100},
                      {"id": "w2", "title": "Other", "score": 50}]}
    hits = parse_work_search(data)
    assert hits[0] == {"id": "w1", "title": "Skinny Love", "score": 100}
    assert parse_work_search(None) == []


def test_search_work_writers_exact_title_first_with_writers():
    def fake(url):
        if "/work?" in url:
            return {"works": [{"id": "wX", "title": "SKINNY LOVE", "score": 100},   # exakt (norm)
                              {"id": "wY", "title": "Skinny Love (Live)", "score": 90}]}
        if "/work/wX" in url:
            return {"relations": [{"type": "composer", "artist": {"name": "Justin Vernon"}},
                                  {"type": "lyricist", "artist": {"name": "Justin Vernon"}}]}
        return None
    c, l, url = search_work_writers("Skinny Love", fetch=fake, sleep=_noop)
    assert c == "Justin Vernon" and l == "Justin Vernon" and url.endswith("/work/wX")


def test_search_work_writers_skips_when_no_writers_or_no_title():
    assert search_work_writers("", fetch=lambda u: {}, sleep=_noop) == ("", "", "")

    def fake(url):
        if "/work?" in url:
            return {"works": [{"id": "wX", "title": "Skinny Love", "score": 100}]}
        return {"relations": []}                      # exakter Titel, aber keine Autoren
    assert search_work_writers("Skinny Love", fetch=fake, sleep=_noop) == ("", "", "")


def test_search_work_writers_ignores_inexact_titles():
    def fake(url):
        if "/work?" in url:
            return {"works": [{"id": "wZ", "title": "Skinny Love Reprise", "score": 100}]}
        raise AssertionError("darf kein Werk-Detail holen, wenn Titel nicht exakt matcht")
    assert search_work_writers("Skinny Love", fetch=fake, sleep=_noop) == ("", "", "")


# --- ISRC-First (Stufe 0) ---

def test_normalize_isrc_strips_separators_and_rejects_junk():
    from core.musicbrainz import normalize_isrc
    assert normalize_isrc("de-a62-19-00123") == "DEA621900123"
    assert normalize_isrc("USMA1 0100001") == "USMA10100001"
    assert normalize_isrc("") == ""
    assert normalize_isrc("LC01485") == ""              # Labelcode ist kein ISRC
    assert normalize_isrc("USMA1010000") == ""          # zu kurz
    assert normalize_isrc("12MA10100001") == ""         # Länderkennung muss alphabetisch sein
    assert normalize_isrc("USMA1010000X") == ""         # letzte 7 müssen Ziffern sein


def test_lookup_by_isrc_uses_exact_endpoint_and_full_score():
    from core.musicbrainz import lookup_by_isrc
    seen = []

    def fetch(url):
        seen.append(url)
        if "/isrc/" in url:
            return {"recordings": [{"id": "r1", "title": "LAURA PALMERS THEME",
                                    "artist-credit": [{"name": "Angelo Badalamenti"}],
                                    "releases": [{"id": "rel1", "status": "Official",
                                                  "release-group": {"primary-type": "Album"}}]}]}
        if "/release/" in url:
            return {"title": "Zzq Testserie", "date": "1990",
                    "label-info": [{"label": {"name": "Warner Bros. Records"}}],
                    "status": "Official", "release-group": {"primary-type": "Album"}}
        return {"isrcs": ["USWB19900001"], "relations": []}

    out = lookup_by_isrc("us-wb1-99-00001", fetch=fetch, sleep=_noop)
    assert out.matched and out.via_isrc
    assert out.score == 100          # exakter ID-Treffer, kein Fuzzy-Score
    assert "/isrc/USWB19900001?" in seen[0] and "inc=artists+releases+isrcs" in seen[0]
    assert out.label_names == ["Warner Bros. Records"]   # Release-Bewertung läuft normal dahinter


def test_lookup_by_isrc_returns_unmatched_on_miss_or_bad_code():
    from core.musicbrainz import lookup_by_isrc
    assert lookup_by_isrc("USWB19900001", fetch=lambda u: {"recordings": []}, sleep=_noop).matched is False

    def never(url):
        raise AssertionError("ungültiger ISRC darf keinen Call auslösen")
    assert lookup_by_isrc("kein-isrc", fetch=never, sleep=_noop).matched is False


# --- Verlag aus Work-label-rels (Typ 'publishing') ---
from core.musicbrainz import extract_publishers


def test_extract_publishers_only_publishing_label_rels():
    work = {"relations": [
        {"type": "composer", "artist": {"name": "Max Martin"}},
        {"type": "publishing", "label": {"name": "Sony Music Publishing"}},
        {"type": "publishing", "label": {"name": "Kobalt Music"}},
        {"type": "publishing", "label": {"name": "Sony Music Publishing"}},      # Duplikat
        {"type": "copyright", "label": {"name": "Big Machine Records"}},         # KEIN Verlag
        {"type": "phonographic copyright", "label": {"name": "Republic"}},       # KEIN Verlag
    ]}
    assert extract_publishers(work) == "Sony Music Publishing; Kobalt Music"
    assert extract_publishers({"relations": []}) == ""


def test_lookup_fetches_label_rels_and_sets_publisher():
    calls = []

    def fetch(url):
        calls.append(url)
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Bad Blood", artist="Taylor Swift")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": [{"type": "performance", "work": {"id": "w1"}},
                                               {"type": "performance", "work": {"id": "w2"}}]}
        if "/work/w1" in url:                                          # nur Verlag, keine Writer
            return {"relations": [{"type": "publishing", "label": {"name": "Kobalt Music"}}]}
        if "/work/w2" in url:                                          # Writer, kein Verlag
            return {"relations": [{"type": "composer", "artist": {"name": "Max Martin"}}]}
        return None

    out = lookup("Taylor Swift", "Bad Blood", fetch=fetch, sleep=_noop)
    assert out.composer == "Max Martin"
    assert out.publisher == "Kobalt Music"                     # Verlag vom ersten Work bleibt erhalten
    assert all("label-rels" in c for c in calls if "/work/" in c)   # ein Call pro Work, beide inc


# --- Deutschland/EU vor dem Rest -----------------------------------
from core import musicbrainz as mb                                          # noqa: E402
def _rel(country="", events=(), **kw):
    r = {"status": "Official", "release-group": {"primary-type": "Album", "secondary-types": []},
         "date": "2015-01-01"}
    if country:
        r["country"] = country
    if events:
        r["release-events"] = [{"area": {"iso-3166-1-codes": [c]}} for c in events]
    r.update(kw)
    return r


def test_release_countries_liest_country_und_events():
    assert mb.release_countries(_rel("de")) == {"DE"}
    assert mb.release_countries(_rel("US", events=("DE", "FR"))) == {"US", "DE", "FR"}
    assert mb.release_countries(_rel()) == set()


def test_deutsche_pressung_schlaegt_us_pressung():
    de, us = mb.release_quality(_rel("DE")), mb.release_quality(_rel("US"))
    assert de > us
    # EU liegt dazwischen, Worldwide knapp darüber von „unbekannt"
    assert mb.release_quality(_rel("US")) < mb.release_quality(_rel("FR")) < de
    assert mb.release_quality(_rel()) < mb.release_quality(_rel("XW")) < mb.release_quality(_rel("XE"))


def test_us_release_bleibt_gueltig_nur_hinten():
    """Kein Filter, nur ein Bonus: ein US-Label ist besser als gar keins. Ein US-Album steht immer noch
    über einer deutschen Bootleg-Compilation."""
    us_album = mb.release_quality(_rel("US"))
    de_bootleg = mb.release_quality(_rel("DE", status="Bootleg",
                                         **{"release-group": {"primary-type": "Album",
                                                              "secondary-types": ["Compilation"]}}))
    assert us_album > de_bootleg > 0 or us_album > de_bootleg
    assert mb.release_quality(_rel("US")) > 0


def test_ddr_pressung_zaehlt_wie_deutschland():
    """DDR-Pressungen zählen bei der Label-Wahl wie Deutschland.
    MusicBrainz führt die DDR als Gebiet „East Germany“ mit dem Code XG — vorher 0 Punkte wie „unbekannt“."""
    assert mb.country_bonus(_rel("XG")) == mb.country_bonus(_rel("DE")) == 3
    assert mb.country_bonus(_rel("US", events=("XG",))) == 3
    assert mb.release_quality(_rel("XG")) == mb.release_quality(_rel("DE")) > mb.release_quality(_rel("FR"))



# --- „Meintest du …?“ aus MusicBrainz bei Teil-Titel (rolling stones + sympathy) ---

def _rec(title, artist, score=90):
    return {"title": title, "score": score, "artist-credit": [{"name": artist, "joinphrase": ""}]}


def test_title_suggestions_bietet_volle_titel_desselben_interpreten():
    from core.musicbrainz import title_suggestions
    recs = [_rec("Sympathy for the Devil", "The Rolling Stones"), _rec("Sympathy for the Devil", "The Rolling Stones"),
            _rec("Sympathy for the Devil (live)", "The Rolling Stones"), _rec("Sympathy", "Rare Bird"),
            _rec("No Expectations", "The Rolling Stones")]
    seen = []
    sug = title_suggestions("rolling stones", "sympathy", fetch=lambda url: seen.append(url) or {"recordings": recs},
                            sleep=lambda s: None)
    assert [s["titel"] for s in sug] == ["Sympathy for the Devil", "Sympathy for the Devil (live)"]
    assert sug[0]["interpret"] == "The Rolling Stones" and len(seen) == 1


def test_title_suggestions_wortanfang_und_ohne_interpret_nichts():
    from core.musicbrainz import title_suggestions
    recs = [_rec("Sympathy for the Devil", "The Rolling Stones")]
    fetch = lambda url: {"recordings": recs}
    assert [s["titel"] for s in title_suggestions("rolling stones", "sympath dev", fetch=fetch, sleep=lambda s: None)] \
        == ["Sympathy for the Devil"]
    assert title_suggestions("", "sympathy", fetch=fetch, sleep=lambda s: None) == []


# --- ISWC aus dem Work-Abruf (kein Zusatz-Request) ---
from core.musicbrainz import extract_iswcs                                   # noqa: E402


def test_extract_iswcs_all_deduped():
    assert extract_iswcs({"iswcs": ["T-000.000.001-0", " T-000.000.001-0", "T-000.000.002-1", ""]}) == \
        ["T-000.000.001-0", "T-000.000.002-1"]
    assert extract_iswcs({}) == [] and extract_iswcs({"iswcs": None}) == []


def test_lookup_takes_iswcs_from_the_writer_work_without_extra_calls():
    calls = []

    def fetch(url):
        calls.append(url)
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Nordwand", artist="Zzq Band")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": [{"type": "performance", "work": {"id": "w1"}},
                                               {"type": "performance", "work": {"id": "w2"}}]}
        if "/work/w1" in url:                                          # ohne Writer, mit ISWC
            return {"iswcs": ["T-000.000.009-9"], "relations": []}
        if "/work/w2" in url:
            return {"iswcs": ["T-000.000.001-0", "T-000.000.002-1"],
                    "relations": [{"type": "composer", "artist": {"name": "Erika Testerin"}}]}
        return None

    out = lookup("Zzq Band", "Nordwand", fetch=fetch, sleep=_noop)
    assert out.iswcs == ["T-000.000.001-0", "T-000.000.002-1"]          # das Werk mit den Autoren, beide ISWCs
    assert len([c for c in calls if "/work/" in c]) == 2


def test_lookup_keeps_iswc_of_work_without_writers():
    def fetch(url):
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Nordwand", artist="Zzq Band")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": [{"type": "performance", "work": {"id": "w1"}}]}
        if "/work/w1" in url:
            return {"iswcs": ["T-000.000.009-9"], "relations": []}
        return None

    assert lookup("Zzq Band", "Nordwand", fetch=fetch, sleep=_noop).iswcs == ["T-000.000.009-9"]


# --- Gast-Credits (C4) ---
import urllib.parse                                                           # noqa: E402

from core.musicbrainz import primary_artist                                  # noqa: E402


def test_strip_feat_variants():
    for t in ("Nordwand (feat. X)", "Nordwand [ft. X]", "Nordwand (featuring X & Y)", "Nordwand feat. X", "Nordwand ft. X"):
        assert strip_feat(t) == "Nordwand", t
    assert strip_feat("Defeated") == "Defeated" and strip_feat("Left Behind") == "Left Behind"


def test_primary_artist_keeps_band_names():
    assert primary_artist("Zzq feat. Erika") == "Zzq" and primary_artist("Zzq ft. Erika") == "Zzq"
    assert primary_artist("Zzq featuring Erika") == "Zzq"
    for a in ("Simon & Garfunkel", "Earth, Wind & Fire", "Malcolm X", "Ft. Lauderdale"):
        assert primary_artist(a) == a, a


def test_lookup_retries_with_the_lead_artist():
    def fetch(url):
        if "recording?" in url and "Erika" in urllib.parse.unquote_plus(url):
            return {"recordings": []}
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Nordwand", artist="Zzq")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": []}
        return None
    assert lookup("Zzq feat. Erika", "Nordwand", fetch=fetch, sleep=_noop).matched


# --- GEMA ID am Werk (Vorschlag, Entscheidung 22.10.2026) ---
from core.musicbrainz import extract_gema_ids                                # noqa: E402


def test_extract_gema_ids_with_version_suffix():
    w = {"attributes": [{"type": "GEMA ID", "value": "10000001-002"}, {"type": "SACEM ID", "value": "x"},
                        {"type": "GEMA ID", "value": " 10000001-002 "}, {"type": "GEMA ID", "value": ""}]}
    assert extract_gema_ids(w) == ["10000001-002"] and extract_gema_ids({}) == []


def test_lookup_takes_gema_id_from_the_writer_work():
    def fetch(url):
        if "recording?" in url:
            return {"recordings": [_recording(rec_id="rec1", title="Nordwand", artist="Zzq Band")]}
        if "/recording/rec1" in url:
            return {"isrcs": [], "relations": [{"type": "performance", "work": {"id": "w1"}}]}
        if "/work/w1" in url:
            return {"attributes": [{"type": "GEMA ID", "value": "10000001-001"}],
                    "relations": [{"type": "composer", "artist": {"name": "Erika Testerin"}}]}
        return None
    assert lookup("Zzq Band", "Nordwand", fetch=fetch, sleep=_noop).gema_ids == ["10000001-001"]


def test_feat_variants_remove_guests_from_the_back():
    from core.musicbrainz import feat_variants
    assert feat_variants("Zzq feat. Erika feat. Max") == ["Zzq feat. Erika", "Zzq"]
    assert feat_variants("Zzq ft. Erika") == ["Zzq"] and feat_variants("Simon & Garfunkel") == []


def test_lookup_records_the_best_release_area_per_label():
    """Je Label das beste Veröffentlichungsgebiet (DE 3, EU 2, weltweit 1, sonst 0) für den Hinweis
    „keine deutsche oder europäische Veröffentlichung“."""
    from core.musicbrainz import lookup_by_isrc
    rels = [{"id": "us", "status": "Official", "release-group": {"primary-type": "Album"}},
            {"id": "de", "status": "Official", "release-group": {"primary-type": "Album"}}]

    def fetch(url):
        if "/isrc/" in url:
            return {"recordings": [{"id": "r1", "title": "Nordwand", "artist-credit": [{"name": "Zzq"}], "releases": rels}]}
        if "/release/us" in url:
            return {"country": "US", "status": "Official", "label-info": [{"label": {"name": "Zzq US"}}]}
        if "/release/de" in url:
            return {"country": "DE", "status": "Official", "label-info": [{"label": {"name": "Zzq Deutschland"}}]}
        return {"isrcs": [], "relations": []}

    out = lookup_by_isrc("DEZZQ2600001", fetch=fetch, sleep=_noop)
    assert out.label_country == {"Zzq US": 0, "Zzq Deutschland": 3}
