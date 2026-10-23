"""Lokaler Track-Index (core/trackindex.py): Schlüssel, Aggregation, Lookup, Statistik."""
from core import songsdb, trackindex
from core.labelmatch import build_index
from core.resolver import AUTO_CONFIRM, ResolveResult, index_result, index_settles, merge_index_writers, resolve_from_index

IDX = build_index([("00121", "Atlantic"), ("07552", "REPUBLIC"), ("24584", "MOM & POP")])


def _index(tmp_path):
    return trackindex.open_index(tmp_path / "track_index.db", create=True), tmp_path / "track_index.db"


def test_nkey_matches_songsdb():
    assert trackindex.nkey("AURORA", "Runaway") == songsdb._nkey("AURORA", "Runaway")
    assert trackindex.nkey("Björk & Co", "It's Oh So Quiet") == songsdb._nkey("Björk & Co", "It's Oh So Quiet")


def test_open_without_file_is_none(tmp_path):
    assert trackindex.open_index(tmp_path / "nope.db") is None
    assert trackindex.stats(None, tmp_path / "nope.db")["present"] is False


def test_finalize_aggregates_per_code_and_keeps_filled_writers(tmp_path):
    con, path = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="Adele", titel="Hello", labelcode="00121", label="Atlantic",
                            komponist="", texter="", album="25", jahr="2015", source="discogs", source_id="1"),
        trackindex.make_row(interpret="ADELE", titel="Hello", labelcode="00121", label="Atlantic Records",
                            komponist="Adele Adkins; Greg Kurstin", texter="Adele Adkins", album="25 (Deluxe)",
                            jahr="2016", source="discogs", source_id="2"),
        trackindex.make_row(interpret="Adele", titel="Hello", labelcode="07552", label="Republic",
                            source="discogs", source_id="3"),
        trackindex.make_row(interpret="", titel="Hello", labelcode="00121", source="discogs"),     # leer -> raus
        trackindex.make_row(interpret="X", titel="Y", labelcode="", source="discogs"),              # ohne LC -> raus
    ])
    n = trackindex.finalize(con, "discogs", {"dump": "discogs_20260901"})
    assert n == 2
    hits = trackindex.lookup(con, "adele", "HELLO")
    assert [h["labelcode"] for h in hits] == ["00121", "07552"]        # meist belegte zuerst
    top = hits[0]
    assert top["n_releases"] == 2 and top["komponist"] == "Adele Adkins; Greg Kurstin" and top["texter"] == "Adele Adkins"
    assert top["jahr"] == "2015"                                        # frühestes Release
    st = trackindex.stats(con, path)
    assert st["present"] and st["rows"] == 2 and st["by_source"] == {"discogs": 2, "musicbrainz": 0}
    assert st["meta"]["discogs_dump"] == "discogs_20260901" and st["meta"]["discogs_rows"] == "2"
    assert st["size_bytes"] > 0
    assert con.execute("SELECT name FROM sqlite_master WHERE name='staging'").fetchone() is None


def test_finalize_replaces_only_its_source(tmp_path):
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="A", titel="B", labelcode="00121", source="discogs")])
    trackindex.finalize(con, "discogs")
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="A", titel="B", labelcode="00121", source="musicbrainz",
                                                    komponist="Someone")])
    trackindex.finalize(con, "musicbrainz")
    assert {h["source"] for h in trackindex.lookup(con, "a", "b")} == {"discogs", "musicbrainz"}
    trackindex.begin_staging(con)                                        # zweiter Discogs-Lauf: ersetzt nur Discogs
    trackindex.stage_rows(con, [trackindex.make_row(interpret="A", titel="B", labelcode="07552", source="discogs")])
    trackindex.finalize(con, "discogs")
    hits = trackindex.lookup(con, "a", "b")
    assert {(h["source"], h["labelcode"]) for h in hits} == {("discogs", "07552"), ("musicbrainz", "00121")}


def test_finalize_rejects_unknown_source(tmp_path):
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    import pytest
    with pytest.raises(ValueError):
        trackindex.finalize(con, "deezer")


# --- Resolver-Anbindung -------------------------------------------------------------------

def _hit(**kw):
    base = {"interpret": "Adele", "titel": "Hello", "labelcode": "00121", "label": "Atlantic", "komponist": "",
            "texter": "", "album": "", "jahr": "", "catno": "", "source": "discogs", "source_id": "1", "n_releases": 1}
    base.update(kw)
    return base


def test_index_result_single_source_is_review_never_auto():
    res = index_result("Adele", "Hello", [_hit(komponist="Adele Adkins", album="25", jahr="2015")], IDX)
    assert res.matched and res.band == "review" and res.best.labelcode == "00121"
    assert res.best.confidence == 0.5 and not res.best.dual_source and res.best.sources == ["index:discogs"]
    assert res.best.label == "Atlantic"                                  # GVL-Schreibweise, nicht die Dump-Schreibweise
    assert res.composer == "Adele Adkins" and res.album == "25" and res.date == "2015"
    assert any("Track-Index" in n for n in res.notes)


def test_index_result_both_dumps_agree_is_dual_but_below_auto():
    res = index_result("Adele", "Hello", [_hit(source="discogs"), _hit(source="musicbrainz", komponist="MB Writer")], IDX)
    assert res.best.dual_source and res.best.confidence == 0.65 < AUTO_CONFIRM and res.band == "review"
    assert res.best.sources == ["index:discogs", "index:musicbrainz"]
    assert res.composer == "MB Writer"


def test_index_result_prefers_musicbrainz_writers_over_discogs():
    res = index_result("A", "B", [_hit(komponist="Discogs Writer", n_releases=9),
                                  _hit(source="musicbrainz", komponist="MB Writer")], IDX)
    assert res.composer == "MB Writer"


def test_index_result_two_codes_tie_is_ambiguous_and_ranked_by_releases():
    res = index_result("A", "B", [_hit(labelcode="07552", n_releases=3), _hit(labelcode="00121", n_releases=1)], IDX)
    assert res.ambiguous and res.tied == ["07552", "00121"] and res.band == "review"


def test_index_result_drops_codes_unknown_to_gvl():
    res = index_result("A", "B", [_hit(labelcode="99999")], IDX)
    assert res is not None and not res.candidates and any("nicht im GVL-Master" in n for n in res.notes)
    assert index_result("A", "B", [], IDX) is None


def test_resolve_from_index_swaps_once_and_marks_it():
    calls = []

    def look(a, t):
        calls.append((a, t))
        return [_hit()] if (a, t) == ("Hello", "Adele") else []

    res = resolve_from_index("Adele", "Hello", IDX, index_lookup=look)
    assert res.swapped and res.best.labelcode == "00121" and "vertauscht" in res.notes[0]
    assert calls == [("Adele", "Hello"), ("Hello", "Adele")]
    assert resolve_from_index("Adele", "Hello", IDX, index_lookup=look, swap_retry=False) is None
    assert resolve_from_index("Adele", "Adele", IDX, index_lookup=lambda a, t: []) is None


def test_index_settles_only_when_unambiguous():
    assert index_settles(index_result("A", "B", [_hit()], IDX))
    tie = index_result("A", "B", [_hit(labelcode="07552"), _hit(labelcode="00121")], IDX)
    assert tie.ambiguous and not index_settles(tie)
    assert not index_settles(index_result("A", "B", [_hit(labelcode="99999")], IDX))
    assert not index_settles(None)


def test_merge_index_writers_fills_only_empty_fields_with_note():
    tie = index_result("A", "B", [_hit(labelcode="07552", komponist="Index Comp", texter="Index Lyr"),
                                  _hit(labelcode="00121")], IDX)
    net = ResolveResult(artist="A", title="B", matched=True, composer="Net Comp")
    out = merge_index_writers(net, tie)
    assert out.composer == "Net Comp" and out.lyricist == "Index Lyr"
    assert any("Texter aus dem lokalen Track-Index" in n for n in out.notes)
    assert any("Track-Index uneindeutig (LC00121 / LC07552)" in n for n in out.notes)
    assert merge_index_writers(ResolveResult(artist="A", title="B"), None).notes == []


def test_schema_has_no_extra_nkey_index_and_lookup_uses_unique_autoindex(tmp_path):
    con, _ = _index(tmp_path)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_tracks_nkey" not in names
    plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN SELECT * FROM tracks WHERE nkey=?", ("x",)))
    assert "sqlite_autoindex_tracks_1" in plan and "nkey=?" in plan
    plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM tracks WHERE source=?", ("discogs",)))
    assert "COVERING INDEX sqlite_autoindex_tracks_1" in plan


def test_compact_drops_legacy_index_and_shrinks_file(tmp_path):
    con, path = _index(tmp_path)
    con.execute("CREATE INDEX idx_tracks_nkey ON tracks(nkey)")     # älterer Build-Stand
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret=f"A{i}", titel="T" * 200, labelcode="00121",
                                                    label="Atlantic", source="discogs") for i in range(3000)])
    trackindex.finalize(con, "discogs")
    con.execute("DROP TABLE artists")                                   # alter Build ohne Interpreten-Tabelle
    con.execute("DROP TABLE title_fts"); con.execute("DROP TABLE titles")   # … und ohne Titel-Tabelle
    assert trackindex.stats(con, path)["titles"] is False
    r = trackindex.compact(con, path)
    assert r["dropped_index"] is True and r["bytes_after"] < r["bytes_before"]
    assert trackindex.has_artists(con) and con.execute("SELECT COUNT(*) FROM artists").fetchone()[0] == 3000
    st = trackindex.stats(con, path)
    assert st["artists"] is True and st["titles"] is True and trackindex.search_title(con, "T" * 200)["total"] == 3000
    assert "idx_tracks_nkey" not in {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert len(trackindex.lookup(con, "A7", "T" * 200)) == 1
    assert trackindex.compact(con, path)["dropped_index"] is False       # idempotent


def test_find_similar_matches_partial_title_for_known_artist(tmp_path):
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="Ian Pooley", titel="What's Your Number", labelcode="00121", label="V2", source="discogs"),
        trackindex.make_row(interpret="Ian Pooley", titel="Balmes", labelcode="07552", label="Republic", source="discogs"),
        trackindex.make_row(interpret="Daughter", titel="Numbers", labelcode="24584", label="Mom+Pop", source="discogs")])
    trackindex.finalize(con, "discogs")
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Ian Pooley", titel="What's Your Number", labelcode="00121",
                                                    label="V2 Records", source="musicbrainz")])
    trackindex.finalize(con, "musicbrainz")
    hits = trackindex.find_similar(con, "ian pooley", "Number")
    assert [h["titel"] for h in hits] == ["What's Your Number"]              # nicht Daughter, nicht Balmes
    assert hits[0]["labelcodes"] == ["00121"] and sorted(hits[0]["sources"]) == ["discogs", "musicbrainz"]
    assert trackindex.find_similar(con, "", "Number") == []                  # ohne Interpret kein Voll-Scan
    assert trackindex.find_similar(con, "Ian Pooley", "zzz") == []
    # Interpret unscharf über die artists-Tabelle (finalize baut sie): Teil-Name, Tippfehler, feat.-Zusatz
    assert trackindex.has_artists(con)
    assert [h["titel"] for h in trackindex.find_similar(con, "Pooley", "number")] == ["What's Your Number"]
    assert [h["titel"] for h in trackindex.find_similar(con, "Ian Poley", "whats your number")] == ["What's Your Number"]
    assert [h["titel"] for h in trackindex.find_similar(con, "Ian Pooley feat. Someone", "Balmes")] == ["Balmes"]
    assert trackindex.find_similar(con, "Daughtr", "Numbers")[0]["interpret"] == "Daughter"
    # Tippfehler neben einem echten Treffer: „daughtr“ trifft „Daughtry“ als Teilwort, „Daughter“ nur über die
    # Token-Hälften — die laufen deshalb immer mit, nicht nur wenn sonst nichts kommt (Recall vor Präzision)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Daughtry", titel="September", labelcode="00121", source="musicbrainz")])
    trackindex.finalize(con, "musicbrainz")
    assert {h["interpret"] for h in trackindex.find_similar(con, "Daughtr", "Numbers")} >= {"Daughter"}
    # Gleichstand der Ähnlichkeit: der meistbelegte Interpret bleibt in den Top 8 (vorher Set-Zufall)
    trackindex.begin_staging(con)
    feats = [trackindex.make_row(interpret=f"Ian Pooley feat Guest{i}", titel=f"Song {i}", labelcode="00121", source="discogs")
             for i in range(12)]
    trackindex.stage_rows(con, feats + [trackindex.make_row(interpret="Ian Pooley", titel="Balmes", labelcode="00121",
                                                            source="discogs", source_id=str(k)) for k in range(5)])
    trackindex.finalize(con, "discogs")
    for _ in range(3):
        assert [h["titel"] for h in trackindex.find_similar(con, "Pooley", "balmes")] == ["Balmes"]


def test_search_mixes_artist_title_and_code_tokens(tmp_path):
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="AURORA", titel="Runaway", labelcode="00121", label="Decca", source="discogs"),
        trackindex.make_row(interpret="AURORA", titel="Runaway", labelcode="07552", label="Glassnote", source="discogs"),
        trackindex.make_row(interpret="AURORA", titel="Cure For Me", labelcode="00121", label="Decca", source="discogs"),
        trackindex.make_row(interpret="Aurora Aksnes", titel="Runaway (Acoustic)", labelcode="24584", label="X", source="discogs")])
    trackindex.finalize(con, "discogs")
    r = trackindex.search(con, "aurora runaway")
    assert [(x["interpret"], x["titel"], x["labelcode"]) for x in r["rows"]] == [
        ("AURORA", "Runaway", "00121"), ("AURORA", "Runaway", "07552"), ("Aurora Aksnes", "Runaway (Acoustic)", "24584")]
    assert r["total"] == 3 and r["capped"] is False
    assert [x["labelcode"] for x in trackindex.search(con, "aurora LC07552")["rows"]] == ["07552"]
    assert [x["titel"] for x in trackindex.search(con, "aurora")["rows"]][0] in ("Runaway", "Cure For Me")
    assert trackindex.search(con, "runaway zzz")["needs_artist"] is True     # kein Interpret im Text
    assert trackindex.search(con, "")["rows"] == []


def test_search_album_filter_and_albums_of_artist(tmp_path):
    """Album-Suche: Interpret + Album listet die Tracks des Albums; albums() liefert die
    Album-Chips der Interpret-only-Liste. Beides über den Album-Text je Zeile, kein neuer Index."""
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="Adele", titel="Hello", labelcode="00121", label="XL", album="25", source="discogs"),
        trackindex.make_row(interpret="Adele", titel="Hello", labelcode="00121", label="XL", album="25 (Deluxe)", source="discogs", source_id="2"),
        trackindex.make_row(interpret="Adele", titel="Send My Love", labelcode="00121", label="XL", album="25", source="discogs"),
        trackindex.make_row(interpret="Adele", titel="Rolling In The Deep", labelcode="00121", label="XL", album="21", source="discogs"),
        trackindex.make_row(interpret="Adele", titel="Someone Like You", labelcode="07552", label="Rep", album="", source="discogs")])
    trackindex.finalize(con, "discogs")
    r = trackindex.search(con, "adele", album="25")
    assert sorted(x["titel"] for x in r["rows"]) == ["Hello", "Send My Love"]      # „25 (Deluxe)“ zählt zu „25“
    assert trackindex.search(con, "adele", album="21")["rows"][0]["titel"] == "Rolling In The Deep"
    assert trackindex.search(con, "adele", album="zzz")["rows"] == []
    assert trackindex.search(con, "adele", album="")["total"] == 4                  # ohne Album: alles (je Code eine Zeile)
    albums = trackindex.albums(con, "adele")
    assert albums[0] == {"album": "25", "n": 2} and {"album": "21", "n": 1} in albums
    assert all(a["album"] for a in albums)                                          # leere Alben sind kein Chip
    assert trackindex.albums(con, "adel")[0]["album"] == "25"                       # unscharf, wenn kein exakter Interpret
    assert trackindex.albums(con, "zzz") == []
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Madeleine", titel="X", labelcode="00121", album="Not Adele",
                                                    source="musicbrainz")])
    trackindex.finalize(con, "musicbrainz")
    assert all(a["album"] != "Not Adele" for a in trackindex.albums(con, "adele"))  # exakter Interpret gewinnt allein
    assert trackindex.albums(con, "madel")[0]["album"] == "Not Adele"


def test_search_title_without_artist_uses_trigram_fts(tmp_path):
    """Titel-only: Tabelle titles + FTS5-Trigram über den normierten Titel, gebaut nach
    finalize. Teilwort trifft („Number“ -> „What's Your Number“), exakter Titel zuerst, dann Präfix, dann
    Releases; je Interpret+Titel eine Zeile mit allen Codes/Quellen. Recall vor Präzision."""
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="Ian Pooley", titel="What's Your Number", labelcode="00121", label="V2", source="discogs"),
        trackindex.make_row(interpret="Steven Julien", titel="Number", labelcode="07552", label="Rep", source="discogs"),
        trackindex.make_row(interpret="Daughter", titel="Numbers", labelcode="24584", label="Mom+Pop", album="If You Leave", source="discogs"),
        trackindex.make_row(interpret="Daughter", titel="Numbers", labelcode="00121", label="4AD", source="discogs", source_id="2"),
        trackindex.make_row(interpret="AURORA", titel="Runaway", labelcode="00121", label="Decca", source="discogs"),
        trackindex.make_row(interpret="Tellurian", titel="Dont Stop", labelcode="00121", label="X", source="discogs"),
        trackindex.make_row(interpret="Fleetwood Mac", titel="Don't Stop", labelcode="00121", label="X", source="discogs"),
        trackindex.make_row(interpret="Fleetwood Mac", titel="Don't Stop", labelcode="00121", label="X", source="discogs", source_id="2"),
        trackindex.make_row(interpret="Ab", titel="Ü", labelcode="00121", label="X", source="discogs")])
    trackindex.finalize(con, "discogs")
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Ian Pooley", titel="What's Your Number", labelcode="00121",
                                                    label="V2 Records", source="musicbrainz")])
    trackindex.finalize(con, "musicbrainz")
    assert trackindex.has_titles(con)
    r = trackindex.search_title(con, "Number")
    assert [(h["interpret"], h["titel"]) for h in r["rows"]] == [
        ("Steven Julien", "Number"), ("Daughter", "Numbers"), ("Ian Pooley", "What's Your Number")]   # exakt, Präfix, Rest
    assert r["total"] == 3 and r["capped"] is False
    pooley = r["rows"][2]
    assert pooley["labelcodes"] == ["00121"] and sorted(pooley["sources"]) == ["discogs", "musicbrainz"] and pooley["n_releases"] == 2
    daughter = r["rows"][1]
    assert sorted(daughter["labelcodes"]) == ["00121", "24584"] and daughter["album"] == "If You Leave"
    assert [h["titel"] for h in trackindex.search_title(con, "whats your number")["rows"]] == ["What's Your Number"]
    assert [h["titel"] for h in trackindex.search_title(con, "number your")["rows"]] == ["What's Your Number"]  # Wortreihenfolge egal
    assert [h["titel"] for h in trackindex.search_title(con, "your nu")["rows"]] == ["What's Your Number"]     # Kurz-Token per LIKE
    assert trackindex.search_title(con, "ü")["rows"][0]["interpret"] == "Ab"     # unter 3 Zeichen: exakter Titel per Scan
    # Kontraktion ohne Apostroph: „dont stop“ findet „Don't Stop“ (Index „don t stop“) und zählt es als exakt -> Releases entscheiden
    assert [h["interpret"] for h in trackindex.search_title(con, "dont stop")["rows"]] == ["Fleetwood Mac", "Tellurian"]
    assert trackindex.search_title(con, "zzz")["rows"] == [] and trackindex.search_title(con, "")["rows"] == []
    assert trackindex.search_title(con, "Number", limit=2)["capped"] is True
    # „mehr zeigen“: Offset läuft über dieselbe Reihenfolge weiter, capped erst am Ende falsch
    page2 = trackindex.search_title(con, "Number", limit=2, offset=2)
    assert [h["titel"] for h in page2["rows"]] == ["What's Your Number"] and page2["offset"] == 2 and page2["capped"] is False
    assert trackindex.search_title(con, "Number", limit=2, offset=9)["rows"] == []


def test_artists_fts_speeds_up_candidates_with_like_semantics(tmp_path):
    """Interpreten-Kandidaten über FTS5-Trigram statt LIKE über 903k Zeilen (spart die restlichen 0,3 s
    je Suche im Datenbank-Tab). Gleiche Semantik: Teilwort, ohne Akzente; Wörter unter drei Zeichen weiter
    per LIKE. Fehlt die FTS (alter Build), läuft LIKE wie bisher; ensure_artists baut sie mit."""
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="Ian Pooley", titel="Balmes", labelcode="00121", source="discogs"),
        trackindex.make_row(interpret="Pierre-André Athané", titel="A Sunny Day", labelcode="00121", source="discogs"),
        trackindex.make_row(interpret="AB", titel="Short", labelcode="00121", source="discogs")])
    trackindex.finalize(con, "discogs")
    assert trackindex.has_artists_fts(con)
    assert trackindex._artist_candidates(con, ["pooley"]) == ["ian pooley"]
    assert trackindex._artist_candidates(con, ["athane"]) == ["pierre andre athane"]        # ohne Akzent
    assert trackindex._artist_candidates(con, ["ab"]) == ["ab"]                             # Kurzwort per LIKE
    assert trackindex.search(con, "athane")["rows"][0]["titel"] == "A Sunny Day"
    con.execute("DROP TABLE artists_fts"); con.commit()                                     # alter Build
    assert not trackindex.has_artists_fts(con) and trackindex._artist_candidates(con, ["pooley"]) == ["ian pooley"]
    assert trackindex.ensure_artists_fts(con) == 3 and trackindex.has_artists_fts(con)
    # Kandidaten-Deckel: kurze Allerweltswörter kommen nur dazu, wenn die längeren unter 20 Kandidaten liefern
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Ian Pooley", titel="Balmes", labelcode="00121", source="discogs")]   # finalize ersetzt die Quelle
                          + [trackindex.make_row(interpret=f"Ian Other{i}", titel="X", labelcode="00121", source="discogs") for i in range(30)]
                          + [trackindex.make_row(interpret=f"Pooley Twin{i}", titel="Balmes", labelcode="00121", source="discogs") for i in range(25)])
    trackindex.finalize(con, "discogs")
    c = trackindex._artist_candidates(con, ["ian", "pooley", "balmes"])
    assert "ian pooley" in c and not any(x.startswith("ian other") for x in c)          # „ian“ nicht mehr expandiert
    assert any(x.startswith("ian other") for x in trackindex._artist_candidates(con, ["ian", "zzz"]))      # zu wenige -> doch


def _beatles_index(tmp_path):
    con, _ = _index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [
        trackindex.make_row(interpret="The Beatles", titel="Беби, Ты Такой Занятный Человек", labelcode="00091",
                            label="ZAUBERKÜRBIS", source="discogs"),
        trackindex.make_row(interpret="The Beatles", titel="グッド・デイ・サンシャイン", labelcode="00287",
                            label="Odeon", source="discogs"),
        trackindex.make_row(interpret="The Beatles", titel="Yesterday", labelcode="00287", label="Odeon", source="discogs"),
        trackindex.make_row(interpret="Кино", titel="Бездельник 2", labelcode="00121", label="Atlantic", source="discogs")])
    trackindex.finalize(con, "discogs")
    return con


def test_find_similar_skips_titles_without_latin_key(tmp_path):
    """Ein leerer Titelschlüssel steckte als Teilstring in jedem Suchtitel — „Help!“ bekam
    „グッド・デイ・サンシャイン · Odeon / ZAUBERKÜRBIS“ mit den Codes zweier fremder Songs als Vorschlag."""
    con = _beatles_index(tmp_path)
    assert trackindex.find_similar(con, "The Beatles", "Help!") == []
    assert [h["titel"] for h in trackindex.find_similar(con, "beatles", "yesterday")] == ["Yesterday"]


def test_lookup_does_not_mix_songs_under_empty_title_key(tmp_path):
    con = _beatles_index(tmp_path)
    assert trackindex.lookup(con, "The Beatles", "Ψ") == []
    hits = trackindex.lookup(con, "The Beatles", "グッド・デイ・サンシャイン")
    assert [(h["titel"], h["labelcode"]) for h in hits] == [("グッド・デイ・サンシャイン", "00287")]


def test_search_without_usable_artist_says_so(tmp_path):
    """'LC00171' oder '宇多田ヒカル' meldeten 'Track-Index: 0 Treffer' statt des Hinweises,
    dass der Index nur über Interpreten durchsucht wird (needs_artist)."""
    con = _beatles_index(tmp_path)
    for q in ("LC00287", "00287", "宇多田ヒカル", "Кино"):
        r = trackindex.search(con, q)
        assert r["rows"] == [] and r["needs_artist"] is True, q
    assert trackindex.search(con, "")["needs_artist"] is False
    assert trackindex.search(con, "beatles LC00287")["needs_artist"] is False


def test_search_reads_lc_with_space_as_one_code(tmp_path):
    """'aurora LC 07552' zerfiel in das Wort 'lc' (muss dann im Titel stehen) und den Code."""
    con = _beatles_index(tmp_path)
    assert {x["titel"] for x in trackindex.search(con, "beatles LC 00287")["rows"]} == {"グッド・デイ・サンシャイン", "Yesterday"}
    assert trackindex.search(con, "LC 00287")["needs_artist"] is True


def test_nicht_lateinische_titel_haben_eigene_schluessel_im_index(tmp_path):
    """Im neu gebauten Index liegen Titel und Interpreten ohne a-z/0-9 unter eigenen Schlüsseln (vorher alle
    unter 'the beatles\\x1f' bzw. '\\x1f…'); ein Interpret wie „Кино“ ist über find_similar erreichbar. Text mit
    a-z/0-9 behält den alten Schlüssel, auch wenn norm() etwas verschluckt („Бездельник 2“ -> „2“)."""
    con = _beatles_index(tmp_path)
    trackindex.begin_staging(con)
    trackindex.stage_rows(con, [trackindex.make_row(interpret="Кино", titel="Группа крови", labelcode="00121",
                                                    label="Atlantic", source="musicbrainz")])
    trackindex.finalize(con, "musicbrainz")
    keys = {r[0] for r in con.execute("SELECT nkey FROM tracks")}
    assert keys == {"the beatles\x1fбеби ты такой занятный человек", "the beatles\x1fグッド デイ サンシャイン",
                    "the beatles\x1fyesterday", "кино\x1f2", "кино\x1fгруппа крови"}
    assert [h["titel"] for h in trackindex.find_similar(con, "Кино", "Группа")] == ["Группа крови"]
    assert trackindex.find_similar(con, "The Beatles", "Help!") == []


def test_alter_index_mit_leerem_schluesselteil_trifft_weiter(tmp_path):
    """Der vorhandene Index ist noch nicht neu gebaut. Bis dahin liegen solche Titel dort unter dem alten
    Schlüssel ('the beatles\\x1f'); lookup fragt den alten mit, solange er vom neuen abweicht — nur Zeilen mit
    demselben Wortlaut zählen."""
    con, _ = _index(tmp_path)
    con.executemany("INSERT INTO tracks (nkey, interpret, titel, labelcode, label, source) VALUES (?,?,?,?,?,?)", [
        ("the beatles\x1f", "The Beatles", "グッド・デイ・サンシャイン", "00287", "Odeon", "discogs"),
        ("the beatles\x1f", "The Beatles", "Беби, Ты Такой Занятный Человек", "00091", "ZAUBERKÜRBIS", "discogs")])
    con.commit()
    hits = trackindex.lookup(con, "The Beatles", "グッド・デイ・サンシャイン")
    assert [(h["titel"], h["labelcode"]) for h in hits] == [("グッド・デイ・サンシャイン", "00287")]
    assert trackindex.lookup(con, "The Beatles", "Ψ") == []


def test_index_result_traegt_pruefstufe():
    """Mehrere Zeilen eines Songs sind Belege, keine Dubletten: die Prüfstufe fasst sie zusammen."""
    assert index_result("A", "B", [_hit(source="discogs"), _hit(source="musicbrainz")], IDX).index_tier == "zwei_quellen"
    assert index_result("A", "B", [_hit(n_releases=3)], IDX).index_tier == "mehrere_releases"
    assert index_result("A", "B", [_hit(n_releases=1)], IDX).index_tier == "einzelbeleg"
    assert index_result("A", "B", [_hit(labelcode="07552"), _hit(labelcode="00121")], IDX).index_tier == "widerspruechlich"
    # ein Code, den die GVL nicht kennt, zählt nicht als Widerspruch
    assert index_result("A", "B", [_hit(), _hit(labelcode="99999")], IDX).index_tier == "einzelbeleg"


def test_regel_vorschlag_bei_widerspruch():
    """Original vor Compilation, dann die meisten Veröffentlichungen, dann das früheste Jahr; Codes unter 00100 zählen
    nicht. Nur Vorschlag: das Ergebnis bleibt uneindeutig."""
    from core.resolver import index_rule_pick
    orig = _hit(labelcode="07552", album="Debut Album", n_releases=1)
    comp = _hit(labelcode="00121", album="Greatest Hits Vol. 2", n_releases=5)
    assert index_rule_pick([orig, comp]) == "07552"
    assert index_rule_pick([_hit(labelcode="07552", n_releases=3), _hit(labelcode="00121", n_releases=1)]) == "07552"
    assert index_rule_pick([_hit(labelcode="07552", jahr="1999"), _hit(labelcode="00121", jahr="1985")]) == "00121"
    assert index_rule_pick([_hit(labelcode="07552"), _hit(labelcode="00121")]) == ""
    assert index_rule_pick([_hit(labelcode="00006"), _hit(labelcode="00121")]) == "00121"
    res = index_result("A", "B", [orig, comp], IDX)
    assert res.ambiguous and res.index_rule_pick == "07552"
