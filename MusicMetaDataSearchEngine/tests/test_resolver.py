import json

from core import cache, discogs
from core.labelmatch import build_index
from core.musicbrainz import (MBLookup, best_recording, extract_writers,
                               matched_recordings, parse_release_labels, release_quality)
from core.deezer import DeezerResult
from core.resolver import confidence, local_result, resolve, resolve_reliably

IDX = build_index([
    ("00121", "Atlantic"),
    ("57251", "Republic Records"),
    ("07552", "REPUBLIC"),
])


def _mb(**kw):
    def f(artist, title):
        return MBLookup(**kw)
    return f


# --- Confidence ---

def test_confidence_from_corroboration():
    assert confidence(mb_score=100, corroborations=0) == 0.2     # nur MB
    assert confidence(mb_score=90, corroborations=0) == 0.18
    assert confidence(mb_score=100, corroborations=1) == 0.5     # MB + 1 unabhängige Quelle
    assert confidence(mb_score=100, corroborations=2) == 0.8     # MB + 2 -> Auto-Confirm
    assert confidence(mb_score=100, corroborations=5) == 1.0     # gedeckelt
    assert confidence(mb_score=0, corroborations=0, local_verified=True) == 1.0


def test_local_result_from_songsdb():
    row = {"interpret": "AURORA", "titel": "Runaway", "komponist": "X; Y", "texter": "Z",
           "labelname": "Decca", "labelcode": "LC00171"}
    res = local_result("aurora", "runaway", row)
    assert res.matched and res.best.labelcode == "00171"
    assert res.best.confidence == 1.0 and res.band == "auto"
    assert res.composer == "X; Y" and res.lyricist == "Z" and res.artist == "AURORA"


# --- resolve() Orchestrierung (netzfrei, gemockt) ---

def test_resolve_dual_source():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=["111"])
    res = resolve("Artist", "Song", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"])
    assert res.matched
    top = res.candidates[0]
    assert top.labelcode == "00121"
    assert top.dual_source is True
    assert top.confidence == 0.5          # MB + Discogs = 1 Bestätigung
    assert set(top.sources) == {"gvl+musicbrainz", "discogs"}
    assert res.band == "review"


def test_resolve_deezer_corroborates_to_auto():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=["111"])
    dz = lambda a, t: DeezerResult(label="Atlantic", isrc="USX1", track_url="https://deezer.com/track/1")
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"], deezer_lookup=dz)
    best = res.best
    assert best.labelcode == "00121"
    assert set(best.sources) == {"gvl+musicbrainz", "discogs", "deezer"}
    assert best.confidence == 0.8 and res.band == "auto"   # MB + Discogs + Deezer = 2 Bestätigungen
    assert res.isrcs == ["USX1"]                            # ISRC aus Deezer ergänzt (MB war leer)
    assert "Deezer" in {s["name"] for s in best.source_urls}


def test_resolve_single_source_no_discogs():
    mb = _mb(matched=True, score=90, label_names=["Atlantic"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    top = res.candidates[0]
    assert top.labelcode == "00121"
    assert top.dual_source is False
    assert top.sources == ["gvl+musicbrainz"]
    assert top.confidence == 0.18


def test_extract_writers():
    work = {"relations": [
        {"type": "composer", "artist": {"name": "Anne Komponistin"}},
        {"type": "lyricist", "artist": {"name": "Tom Texter"}},
        {"type": "writer", "artist": {"name": "Sam Songwriter"}},
        {"type": "performer", "artist": {"name": "Egal"}},
    ]}
    comp, lyr = extract_writers(work)
    assert comp == "Anne Komponistin; Sam Songwriter"
    assert lyr == "Tom Texter; Sam Songwriter"


def test_resolve_returns_metadata_and_source_links():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], label_quality={"Atlantic": 6},
             label_release={"Atlantic": {"url": "https://musicbrainz.org/release/REL1",
                                         "album": "Jagged Little Pill", "date": "1995-06-13"}},
             discogs_release_ids=["111"], composer="A; B", lyricist="C", isrcs=["USX123"])
    res = resolve("X", "Y", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"])
    assert res.composer == "A; B" and res.lyricist == "C"
    assert res.album == "Jagged Little Pill" and res.date == "1995-06-13"
    best = res.best
    assert best.labelcode == "00121" and best.dual_source is True
    names = {s["name"]: s["url"] for s in best.source_urls}
    assert names["MusicBrainz"] == "https://musicbrainz.org/release/REL1"
    assert names["Discogs"] == "https://www.discogs.com/release/111"


def test_release_quality_ordering():
    album = {"status": "Official", "release-group": {"primary-type": "Album"}, "date": "1998"}
    comp = {"status": "Official", "release-group": {"primary-type": "Album", "secondary-types": ["Compilation"]}, "date": "2005"}
    bare = {}
    assert release_quality(album) > release_quality(comp) > release_quality(bare)


def test_resolve_ranks_by_quality():
    # Beide Labels gleiche Confidence (single, mb 100) -> Release-Qualität entscheidet die Reihenfolge
    mb = _mb(matched=True, score=100, label_names=["Atlantic", "REPUBLIC"],
             label_quality={"Atlantic": 6, "REPUBLIC": -3}, discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert [c.labelcode for c in res.candidates] == ["00121", "07552"]
    assert res.candidates[0].quality == 6


def test_resolve_incomplete_flagged():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=[], incomplete=True)
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert res.incomplete is True
    assert any("unvollständig" in n.lower() for n in res.notes)


def test_resolve_republic_major_over_indie():
    # The-Weeknd/Blinding-Lights-Fall: MB liefert 'Republic Records', das exakt aufs
    # Indie (57251 Z Records) matcht. hersteller-Disambig muss den Major (07552) liefern.
    idx = build_index([
        ("57251", "Republic Records", "Z Records Ltd"),
        ("07552", "REPUBLIC", "Universal Music Entertainment GmbH"),
    ])
    mb = _mb(matched=True, score=100, label_names=["Republic Records"], discogs_release_ids=[])
    res = resolve("The Weeknd", "Blinding Lights", idx, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert res.best.labelcode == "07552"
    assert res.best.label == "REPUBLIC"


def test_resolve_no_mb_match():
    res = resolve("A", "B", IDX, mb_lookup=_mb(matched=False), discogs_label_codes=lambda rid: [])
    assert not res.matched
    assert res.candidates == []
    assert res.band == "no_match"


def test_resolve_discogs_code_not_in_gvl_discarded():
    mb = _mb(matched=True, score=100, label_names=[], discogs_release_ids=["111"])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["99999"])
    assert res.candidates == []
    assert any("nicht im GVL" in n for n in res.notes)


# --- Parser (rein) ---

def test_discogs_parse_label_codes():
    release = {"identifiers": [
        {"type": "Barcode", "value": "123"},
        {"type": "Label Code", "value": "LC 0121"},
        {"type": "Label Code", "value": "00121"},   # nach Normalisierung Duplikat
    ]}
    assert discogs.parse_label_codes(release) == ["00121"]


def test_mb_parse_release_labels():
    release = {
        "label-info": [{"label": {"name": "Atlantic"}}, {"label": {"name": "Warner"}}],
        "relations": [{"type": "discogs", "url": {"resource": "https://www.discogs.com/release/249504-x"}}],
    }
    names, dids = parse_release_labels(release)
    assert names == ["Atlantic", "Warner"]
    assert dids == ["249504"]


def test_mb_matched_recordings_collects_all():
    recs = [
        {"id": "studio", "score": 100, "title": "Thank You", "artist-credit": [{"name": "Alanis Morissette"}]},
        {"id": "live", "score": 92, "title": "Thank You", "artist-credit": [{"name": "Alanis Morissette"}]},
        {"id": "wrong", "score": 95, "title": "Thank U", "artist-credit": [{"name": "Alanis Morissette"}]},
        {"id": "lowscore", "score": 50, "title": "Thank You", "artist-credit": [{"name": "Alanis Morissette"}]},
    ]
    ids = [r["id"] for r in matched_recordings("Alanis Morissette", "Thank You", recs)]
    assert ids == ["studio", "live"]   # exakter Titel + Artist, Score >=85


def test_mb_best_recording():
    recs = [
        {"id": "r1", "score": 100, "title": "Thank You", "artist-credit": [{"name": "Alanis Morissette"}]},
        {"id": "r2", "score": 40, "title": "Thank You", "artist-credit": [{"name": "Other"}]},
    ]
    assert best_recording("Alanis Morissette", "Thank You", recs)["id"] == "r1"


# --- Cache ---

def test_cache_roundtrip(tmp_path):
    con = cache.open_cache(tmp_path / "cache.db")
    assert cache.get(con, "musicbrainz", "k") is None
    cache.put(con, "musicbrainz", "k", json.dumps({"a": 1}))
    assert json.loads(cache.get(con, "musicbrainz", "k"))["a"] == 1
    con.close()


# --- Wikidata als 4. Quelle + never-empty + Retry ---
from core.wikidata import WikidataResult
from core.resolver import resolve_reliably, ResolveResult


def _wd(labels=(), composers=(), lyricists=(), url="https://www.wikidata.org/wiki/Q1"):
    def f(a, t):
        return WikidataResult(labels=list(labels), composers=list(composers),
                              lyricists=list(lyricists), item_url=url)
    return f


def test_resolve_wikidata_corroborates_and_links():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  wikidata_lookup=_wd(labels=["Atlantic"]))
    top = res.candidates[0]
    assert top.labelcode == "00121"
    assert "wikidata" in top.sources and top.confidence == 0.5   # MB + Wikidata = 1 Bestätigung
    assert {s["name"]: s["url"] for s in top.source_urls}["Wikidata"] == "https://www.wikidata.org/wiki/Q1"


def test_resolve_wikidata_fills_writers_when_mb_empty():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  wikidata_lookup=_wd(labels=["Atlantic"], composers=["Max Martin", "Shellback"],
                                      lyricists=["Taylor Swift"]))
    assert res.composer == "Max Martin; Shellback" and res.lyricist == "Taylor Swift"


def test_resolve_wikidata_when_mb_not_matched():
    # never-empty: MB findet nichts, Wikidata liefert trotzdem einen GVL-Code.
    mb = _mb(matched=False)
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  wikidata_lookup=_wd(labels=["Atlantic"]))
    assert res.matched is False
    assert res.best.labelcode == "00121"
    assert res.best.confidence == 0.3 and res.band == "review_low"   # nur Wikidata (mb_score 0)


def test_resolve_label_hints_when_no_gvl_code():
    mb = _mb(matched=True, score=90, label_names=["Ganz Unbekanntes Indie XYZ"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert not res.candidates
    assert res.label_hints == ["Ganz Unbekanntes Indie XYZ"]
    assert any("kein GVL-Code zuordenbar" in n for n in res.notes)


def test_resolve_label_hints_dedupe_across_sources():
    mb = _mb(matched=True, score=90, label_names=["Foobar Sound"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  deezer_lookup=lambda a, t: DeezerResult(label="Foobar Sound"),
                  wikidata_lookup=_wd(labels=["foobar sound"]))
    assert res.label_hints == ["Foobar Sound"]   # 3 Quellen, gleicher Name -> ein Hinweis


def _rr(incomplete, ncand=0, conf=0.2):
    r = ResolveResult(artist="A", title="B", matched=True, incomplete=incomplete)
    from core.resolver import Candidate
    r.candidates = [Candidate(labelcode="00121", label="Atlantic", confidence=conf) for _ in range(ncand)]
    return r


def test_resolve_reliably_retries_on_incomplete():
    seq = [_rr(True, ncand=0), _rr(False, ncand=1)]     # 1. Lauf lückenhaft, 2. vollständig
    calls = {"n": 0}
    def run():
        r = seq[calls["n"]]; calls["n"] += 1; return r
    res = resolve_reliably(run, max_passes=2)
    assert calls["n"] == 2 and res.incomplete is False and len(res.candidates) == 1


def test_resolve_reliably_no_retry_when_complete():
    calls = {"n": 0}
    def run():
        calls["n"] += 1; return _rr(False, ncand=1)
    resolve_reliably(run, max_passes=2)
    assert calls["n"] == 1                              # vollständig -> kein 2. Call


def test_resolve_reliably_gives_up_after_max_passes():
    calls = {"n": 0}
    def run():
        calls["n"] += 1; return _rr(True, ncand=0)
    res = resolve_reliably(run, max_passes=2)
    assert calls["n"] == 2 and res.incomplete is True   # nie leer hängen, aber gedeckelt


# --- Titel-only-Suche ---
from core.wikidata import WikidataItem
from core.resolver import resolve_by_title


def _items(*items):
    return lambda title: list(items)


def test_resolve_by_title_ranks_mapped_first_and_previews_gvl_name():
    items = _items(
        WikidataItem(performers=["Bon Iver"], labels=["Zzz Unmappbares Indie"],
                     item_url="https://www.wikidata.org/wiki/Q1"),
        WikidataItem(performers=["Birdy"], labels=["Atlantic"], composers=["W"],
                     item_url="https://www.wikidata.org/wiki/Q2"),
    )
    res = resolve_by_title("Skinny Love", IDX, wikidata_items_lookup=items)
    assert [c.artist for c in res.candidates] == ["Birdy", "Bon Iver"]   # mit GVL-Code zuerst
    top = res.candidates[0]
    assert top.labelcode == "00121" and top.label == "Atlantic" and top.source_url.endswith("Q2")
    assert res.candidates[1].labelcode == "" and res.candidates[1].label == "Zzz Unmappbares Indie"


def test_resolve_by_title_dedupes_artists():
    items = _items(
        WikidataItem(performers=["Birdy"], labels=["Atlantic"], item_url="u1"),
        WikidataItem(performers=["birdy"], labels=["Atlantic"], item_url="u2"),   # gleiche Person
    )
    res = resolve_by_title("Skinny Love", IDX, wikidata_items_lookup=items)
    assert len(res.candidates) == 1


def test_resolve_by_title_empty_gives_note():
    res = resolve_by_title("Nichts Da", IDX, wikidata_items_lookup=lambda t: [])
    assert res.candidates == []
    assert any("manuell" in n for n in res.notes)


# --- Cover -> Werk-Autoren (MB-Werk-Titelsuche) ---

def test_resolve_work_lookup_fills_writers_when_none():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=[])   # keine Writer
    calls = []
    def work(title):
        calls.append(title)
        return ("Justin Vernon", "Justin Vernon", "https://musicbrainz.org/work/wX")
    res = resolve("Birdy", "Skinny Love", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  work_lookup=work)
    assert res.composer == "Justin Vernon" and res.lyricist == "Justin Vernon"
    assert calls == ["Skinny Love"]
    assert any("bitte prüfen" in n for n in res.notes)


def test_resolve_work_lookup_skipped_when_writers_present():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=[],
             composer="Vorhanden")
    called = []
    resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
            work_lookup=lambda t: called.append(t) or ("X", "Y", ""))
    assert called == []                       # schon Autoren da -> kein teurer Werk-Call


# --- ISRC-First als Stufe 0 ---

def test_resolve_isrc_stage0_wins_and_skips_text_search():
    def never(a, t):
        raise AssertionError("Fuzzy-Textsuche darf bei ISRC-Treffer nicht laufen")

    hit = MBLookup(matched=True, score=100, label_names=["Atlantic"],
                   label_quality={"Atlantic": 5}, isrcs=["USMA10100001"])
    res = resolve("ALL CAPS ARTIST", "kaputter titel", IDX,
                  isrc="US-MA1-01-00001", isrc_lookup=lambda i: hit,
                  mb_lookup=never, discogs_label_codes=lambda rid: [])
    assert res.via_isrc and res.matched and res.best.labelcode == "00121"
    assert any("ISRC" in n for n in res.notes)


def test_resolve_isrc_miss_falls_back_to_text_search():
    called = {"text": False}

    def mb(a, t):
        called["text"] = True
        return MBLookup(matched=True, score=90, label_names=["Atlantic"], label_quality={"Atlantic": 5})

    res = resolve("A", "B", IDX, isrc="USMA10100001",
                  isrc_lookup=lambda i: MBLookup(),          # kein Treffer
                  mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert called["text"] and res.via_isrc is False and res.best.labelcode == "00121"


def test_resolve_isrc_hit_alone_is_no_auto_confirm():
    """Der ISRC identifiziert die AUFNAHME — der Labelcode hängt am Release. Ohne
    zweite Quelle bleibt es Review, egal wie exakt der Recording-Treffer war."""
    def hit(_i):
        return MBLookup(matched=True, score=100, label_names=["Atlantic"], label_quality={"Atlantic": 5})

    res = resolve("A", "B", IDX, isrc="USMA10100001", isrc_lookup=hit,
                  mb_lookup=_mb(), discogs_label_codes=lambda rid: [])
    assert res.best.dual_source is False and res.band == "review_low"   # 0.2, nur MB

    def hit_with_discogs(_i):
        h = hit(_i)
        h.discogs_release_ids = ["111"]
        return h

    res2 = resolve("A", "B", IDX, isrc="USMA10100001", isrc_lookup=hit_with_discogs,
                   mb_lookup=_mb(), discogs_label_codes=lambda rid: ["00121"])
    assert res2.best.dual_source and res2.band == "review"              # 0.5, eine Zweitquelle

    res3 = resolve("A", "B", IDX, isrc="USMA10100001", isrc_lookup=hit_with_discogs,
                   mb_lookup=_mb(), discogs_label_codes=lambda rid: ["00121"],
                   deezer_lookup=lambda a, t: DeezerResult(label="Atlantic"))
    assert res3.band == "auto"                                          # 0.8, erst mit zwei


def test_resolve_without_isrc_lookup_is_unchanged():
    mb = _mb(matched=True, score=90, label_names=["Atlantic"], label_quality={"Atlantic": 5})
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert res.via_isrc is False and res.best.labelcode == "00121"


# --- Vertauschte Felder (Export mit vertauschten Spalten): Retry mit getauschter Reihenfolge ---

def _swap_mb(hits: dict):
    """mb_lookup-Fake über eine {(artist,title): MBLookup}-Tabelle + Aufrufprotokoll."""
    calls: list[tuple[str, str]] = []

    def f(artist, title):
        calls.append((artist, title))
        return hits.get((artist, title), MBLookup())
    return f, calls


_HIT = dict(matched=True, score=100, label_names=["Atlantic"],
            label_quality={"Atlantic": 5}, discogs_release_ids=["111"])


def test_resolve_swaps_when_first_pass_is_completely_empty():
    """'Runaway - AURORA' aus einem Export mit vertauschten Spalten: erst leer, getauscht ein Treffer."""
    mb, calls = _swap_mb({("AURORA", "Runaway"): MBLookup(**_HIT)})
    res = resolve("Runaway", "AURORA", IDX, mb_lookup=mb,
                  discogs_label_codes=lambda rid: ["00121"])
    assert res.swapped is True
    assert (res.artist, res.title) == ("AURORA", "Runaway")   # getauscht zurückgegeben
    assert res.best.labelcode == "00121" and res.best.dual_source
    assert res.notes[0].startswith("Interpret und Titel kamen vertauscht an")
    assert calls == [("Runaway", "AURORA"), ("AURORA", "Runaway")]   # Probe genau einmal


def test_resolve_does_not_swap_when_mb_matched_without_label():
    """MB kennt die Aufnahme in DIESER Reihenfolge — dem Track fehlt nur ein GVL-Label.
    Ein Tausch wäre hier eine Verschlechterung, also gar nicht erst probieren."""
    mb, calls = _swap_mb({("A", "B"): MBLookup(matched=True, score=90)})
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert res.swapped is False and (res.artist, res.title) == ("A", "B")
    assert calls == [("A", "B")]                              # keine Probe


def test_resolve_does_not_swap_when_first_pass_found_a_candidate():
    mb, calls = _swap_mb({("A", "B"): MBLookup(**_HIT)})
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"])
    assert res.swapped is False and calls == [("A", "B")]


def test_resolve_swap_probe_does_not_double_the_other_sources():
    """Trifft die Probe nicht, bleibt es beim ersten Ergebnis — und Deezer/Wikidata/
    Werk-Suche werden NICHT ein zweites Mal befragt (sonst kostet jedes leere Feldpaar
    doppelt Rate-Limit)."""
    mb, calls = _swap_mb({})                                  # beide Richtungen leer
    seen = {"deezer": 0, "wikidata": 0, "work": 0}

    def dz(a, t):
        seen["deezer"] += 1
        return None

    def wd(a, t):
        seen["wikidata"] += 1
        return None

    def wl(t):
        seen["work"] += 1
        return "", "", ""

    res = resolve("Runaway", "AURORA", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [],
                  deezer_lookup=dz, wikidata_lookup=wd, work_lookup=wl)
    assert res.swapped is False and (res.artist, res.title) == ("Runaway", "AURORA")
    assert calls == [("Runaway", "AURORA"), ("AURORA", "Runaway")]   # nur die billige Probe
    assert seen == {"deezer": 1, "wikidata": 1, "work": 1}


def test_resolve_swap_retry_can_be_switched_off():
    """Kontrollschalter: mit swap_retry=False verhält sich resolve()
    exakt wie vor der Heuristik."""
    mb, calls = _swap_mb({("AURORA", "Runaway"): MBLookup(**_HIT)})
    res = resolve("Runaway", "AURORA", IDX, mb_lookup=mb,
                  discogs_label_codes=lambda rid: ["00121"], swap_retry=False)
    assert res.swapped is False and res.candidates == []
    assert calls == [("Runaway", "AURORA")]


def test_resolve_no_swap_when_fields_are_the_same():
    mb, calls = _swap_mb({})
    resolve("Rosalía", "ROSALIA", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert calls == [("Rosalía", "ROSALIA")]     # norm() gleich -> kann nicht vertauscht sein


def test_resolve_swap_keeps_the_isrc_stage_out_of_the_retry():
    """Stufe 0 lief im ersten Durchlauf und ist feldreihenfolge-unabhängig — der
    Retry darf den ISRC-Endpoint nicht ein zweites Mal aufrufen."""
    isrc_calls = []

    def isrc_lookup(i):
        isrc_calls.append(i)
        return MBLookup()                       # ISRC unbekannt -> Textsuche

    mb, calls = _swap_mb({("AURORA", "Runaway"): MBLookup(**_HIT)})
    res = resolve("Runaway", "AURORA", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"],
                  isrc="GBUM71501189", isrc_lookup=isrc_lookup)
    assert res.swapped is True and isrc_calls == ["GBUM71501189"]


def test_resolve_swap_survives_the_reliably_wrapper():
    """resolve_reliably nimmt den besseren Lauf — ein getauschter Treffer ist besser
    als ein leeres Ergebnis und darf dabei nicht verloren gehen."""
    mb, _calls = _swap_mb({("AURORA", "Runaway"): MBLookup(**_HIT)})
    run = lambda: resolve("Runaway", "AURORA", IDX, mb_lookup=mb,
                          discogs_label_codes=lambda rid: ["00121"])
    res = resolve_reliably(run)
    assert res.swapped is True and res.best.labelcode == "00121"


def test_resolve_passes_publisher_through_from_musicbrainz():
    # Verlag (GEMA-Seite) kommt aus den MB-Work-label-rels und bleibt vom Label (GVL) getrennt.
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], label_quality={"Atlantic": 6},
             composer="A", lyricist="B", publisher="Warner Chappell; Kobalt Music")
    res = resolve("X", "Y", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert res.publisher == "Warner Chappell; Kobalt Music"
    assert res.best is None or res.best.label != res.publisher


# --- Margin-Gate: Gleichstand wird gemeldet, nie still aufgebrochen ---

def _tie_lookups():
    """Zwei Label derselben Veröffentlichung, je EINE Bestätigung: Atlantic (MB+Discogs)
    gegen REPUBLIC (MB+Deezer) -> beide 0.5. Das Ranking bricht das heute still auf."""
    mb = _mb(matched=True, score=100, label_names=["Atlantic", "REPUBLIC"], discogs_release_ids=["111"])
    dz = lambda a, t: DeezerResult(label="REPUBLIC")
    return dict(mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"], deezer_lookup=dz)


def test_margin_gate_flags_tie_and_names_both():
    from core.resolver import MARGIN, AMBIGUOUS_NOTE
    res = resolve("A", "B", IDX, **_tie_lookups())
    assert [c.confidence for c in res.candidates[:2]] == [0.5, 0.5]
    assert res.ambiguous is True
    assert res.tied == ["00121", "07552"]                  # in Rang-Reihenfolge, Top zuerst
    assert res.band == "review"
    assert any(n.startswith(AMBIGUOUS_NOTE) for n in res.notes)
    note = next(n for n in res.notes if n.startswith(AMBIGUOUS_NOTE))
    assert "00121" in note and "07552" in note and "Atlantic" in note and "REPUBLIC" in note
    assert 0 < MARGIN < 0.3


def test_margin_gate_demotes_auto_to_review():
    """Zwei Kandidaten mit je zwei Bestätigungen (0.8/0.8): ohne Gate wäre das Band 'auto'
    und der Tagger würde den Zufallssieger schreiben."""
    from core.wikidata import WikidataResult
    mb = _mb(matched=True, score=100, label_names=["Atlantic", "REPUBLIC"], discogs_release_ids=["111"])
    res = resolve("A", "B", IDX, mb_lookup=mb,
                  discogs_label_codes=lambda rid: ["00121", "07552"],
                  deezer_lookup=lambda a, t: DeezerResult(label="Atlantic"),
                  wikidata_lookup=lambda a, t: WikidataResult(labels=["REPUBLIC"], item_url="https://www.wikidata.org/wiki/Q1"))
    assert [c.confidence for c in res.candidates[:2]] == [0.8, 0.8]
    assert res.best.dual_source is True                    # Fakt über die Quellen bleibt
    assert res.ambiguous is True and res.band == "review"  # aber kein Auto-Confirm


def test_margin_gate_silent_when_margin_is_clear():
    mb = _mb(matched=True, score=100, label_names=["Atlantic", "REPUBLIC"], discogs_release_ids=["111"])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"],
                  deezer_lookup=lambda a, t: DeezerResult(label="Atlantic"))
    assert [c.confidence for c in res.candidates[:2]] == [0.8, 0.2]
    assert res.ambiguous is False and res.tied == [] and res.band == "auto"
    assert not any("Gleichstand" in n for n in res.notes)


def test_margin_gate_single_candidate_and_local_hit_untouched():
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], discogs_release_ids=["111"])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: ["00121"])
    assert res.ambiguous is False and res.tied == []
    loc = local_result("A", "B", {"labelcode": "LC00121", "labelname": "Atlantic"})
    assert loc.ambiguous is False and loc.band == "auto"


def test_margin_gate_below_floor_tie_stays_review_low():
    """Gleichstand unter dem Review-Floor: Band bleibt review_low, der Hinweis kommt trotzdem."""
    mb = _mb(matched=True, score=100, label_names=["Atlantic", "REPUBLIC"], discogs_release_ids=[])
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [])
    assert [c.confidence for c in res.candidates[:2]] == [0.2, 0.2]
    assert res.ambiguous is True and res.band == "review_low"


# --- ISWC und GEMA-Werknr. ---

def test_resolve_passes_iswcs_and_notes_several():
    one = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, iswcs=["T-000.000.001-0"]),
                  discogs_label_codes=lambda rid: [])
    assert one.iswcs == ["T-000.000.001-0"] and not any("ISWCs" in n for n in one.notes)
    two = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, iswcs=["T-000.000.001-0", "T-000.000.002-1"]),
                  discogs_label_codes=lambda rid: [])
    assert two.iswcs == ["T-000.000.001-0", "T-000.000.002-1"]
    assert any("2 ISWCs" in n for n in two.notes)                         # nicht raten, alle zeigen


def test_local_result_carries_iswc_and_gema_from_songsdb():
    row = {"interpret": "Zzq", "titel": "Nordwand", "labelcode": "LC00121", "isrc": "DEZZQ2600001",
           "iswc": "T-000.000.001-0", "gema_werknummer": "10000001-001"}
    res = local_result("zzq", "nordwand", row)
    assert res.isrcs == ["DEZZQ2600001"] and res.iswcs == ["T-000.000.001-0"]
    assert res.gema_werknummer == "10000001-001"
    assert local_result("zzq", "nordwand", {"interpret": "Zzq", "titel": "Nordwand"}).iswcs == []


# --- C1: Quellen gewichten statt zählen ---

def test_isrc_identification_weighs_more_but_never_auto_alone():
    assert confidence(mb_score=100, corroborations=1, via_isrc=True) == 0.6
    assert confidence(mb_score=100, corroborations=0, via_isrc=True) == 0.3       # unter der Review-Schwelle
    assert confidence(mb_score=0, corroborations=1, via_isrc=True) == 0.3          # kein MB-Beleg: kein Bonus


def test_label_only_deezer_knows_gets_no_musicbrainz_share():
    """Deezer nennt ein Label, das MusicBrainz nicht belegt: das bestätigt nicht die Aufnahme -> 0.3, unter dem
    von MusicBrainz + Deezer belegten Kandidaten."""
    mb = _mb(matched=True, score=100, label_names=["Atlantic"], label_quality={"Atlantic": 5})
    dz = lambda a, t: DeezerResult(label="Republic Records", isrc="", track_url="u")   # noqa: E731
    res = resolve("A", "B", IDX, mb_lookup=mb, discogs_label_codes=lambda rid: [], deezer_lookup=dz)
    by = {c.labelcode: c.confidence for c in res.candidates}
    assert by["00121"] == 0.2 and by["57251"] == 0.3
    assert res.best.labelcode == "57251" and res.best.confidence < 0.5           # nur „prüfen“, kein Treffer


# --- C4: Gast-Credits ---

def test_secondary_sources_get_the_query_without_guest_credits():
    seen = []
    def dz(a, t):
        seen.append((a, t))
        return None
    resolve("Zzq feat. Erika Testerin", "Nordwand (feat. Erika Testerin)", IDX, mb_lookup=_mb(),
            discogs_label_codes=lambda rid: [], deezer_lookup=dz, swap_retry=False)
    assert seen == [("Zzq", "Nordwand")]


def test_musicbrainz_gema_id_is_a_suggestion_never_verified():
    one = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, gema_ids=["10000001-002"]),
                  discogs_label_codes=lambda rid: [])
    assert one.gema_werknummer == "10000001-002" and one.gema_source == "musicbrainz"
    assert any("laut MusicBrainz" in n for n in one.notes)
    two = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, gema_ids=["10000001-001", "10000001-002"]),
                  discogs_label_codes=lambda rid: [])
    assert two.gema_werknummer == "" and any("2 GEMA-Werknummern" in n for n in two.notes)
    local = local_result("a", "b", {"interpret": "A", "titel": "B", "gema_werknummer": "10000003"})
    assert local.gema_source == "songs.db"


def test_note_when_the_label_comes_only_from_a_release_outside_de_eu():
    """DE/EU gewinnt, internationale Labels gelten (Regel): ohne DE/EU-Veröffentlichung steht ein Hinweis da."""
    us = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, label_names=["Atlantic"],
                                             label_quality={"Atlantic": 5}, label_country={"Atlantic": 1}),
                 discogs_label_codes=lambda rid: [])
    assert us.best.labelcode == "00121" and any(n.startswith("Keine deutsche oder europäische") for n in us.notes)
    de = resolve("A", "B", IDX, mb_lookup=_mb(matched=True, score=90, label_names=["Atlantic"],
                                             label_quality={"Atlantic": 5}, label_country={"Atlantic": 3}),
                 discogs_label_codes=lambda rid: [])
    assert not any(n.startswith("Keine deutsche") for n in de.notes)
