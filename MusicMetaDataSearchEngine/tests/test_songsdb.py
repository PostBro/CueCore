import csv
import re
import sqlite3
import unicodedata
from pathlib import Path

from core import songsdb, trackindex


def _mkdb(tmp_path, name="songs.db"):
    """Alte Schema-Variante OHNE nkey (wie von build_songs_db gebaut)."""
    db = tmp_path / name
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE songs (id INTEGER PRIMARY KEY, interpret TEXT, titel TEXT, "
                "komponist TEXT, texter TEXT, labelname TEXT, labelcode TEXT)")
    con.executemany(
        "INSERT INTO songs (interpret,titel,komponist,texter,labelname,labelcode) VALUES (?,?,?,?,?,?)",
        [("AURORA", "Runaway", "X; Y", "Z", "Decca", "LC00171"),
         ("Alanis Morissette", "Thank You", "Glen Ballard", "Alanis", "MAVERICK", "LC01485")])
    con.commit(); con.close()
    return db


def test_open_migrates_and_finds(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))      # altes Schema -> nkey migriert + backfilled
    assert songsdb.count(con) == 2
    hit = songsdb.find(con, "  aurora ", "RUNAWAY")   # normalisiert
    assert hit and hit["labelcode"] == "LC00171" and hit["komponist"] == "X; Y"
    con.close()


def test_find_miss(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    assert songsdb.find(con, "Unknown", "Nope") is None
    con.close()


def test_find_row_includes_id(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    row = songsdb.find_row(con, "AURORA", "Runaway")
    assert row and "id" in row and row["labelcode"] == "LC00171"
    assert songsdb.find_row(con, "Nobody", "Nothing") is None
    con.close()


def test_upsert_insert_and_merge_keeps_verified(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    assert songsdb.upsert(con, interpret="New", titel="Song", labelcode="LC00121") == "inserted"
    con.commit()
    assert songsdb.count(con) == 3
    # bestehender Song: vorhandenes Feld NICHT überschreiben
    assert songsdb.upsert(con, interpret="AURORA", titel="Runaway", komponist="DARF NICHT") == "updated"
    con.commit()
    assert songsdb.find(con, "AURORA", "Runaway")["komponist"] == "X; Y"
    con.close()


def test_upsert_fills_empty(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="Empty", titel="Comp", komponist=""); con.commit()
    songsdb.upsert(con, interpret="Empty", titel="Comp", komponist="Filled"); con.commit()
    assert songsdb.find(con, "Empty", "Comp")["komponist"] == "Filled"
    con.close()


def test_seed(tmp_path):
    src = _mkdb(tmp_path, "src.db")
    dst = tmp_path / "active" / "songs.db"
    songsdb.seed(dst, src)
    assert dst.exists()
    con = songsdb.open(dst); assert songsdb.count(con) == 2; con.close()


# --- Schema-Erweiterung: neue Spalten ---

def test_open_migrates_new_columns(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))  # Alt-Schema ohne jede neue Spalte
    cols = {r[1] for r in con.execute("PRAGMA table_info(songs)")}
    for expected in ("isrc", "source_type", "confidence_score", "review_status",
                     "fingerprint_source", "duration_sec", "file_path", "spotify_url",
                     "apple_music_url", "possible_cover", "iswc", "gema_werknummer",
                     "gema_lookup_pending", "date_added", "date_verified"):
        assert expected in cols
    # Migration ist idempotent -> zweiter open() auf derselben Connection-Quelle bricht nicht
    songsdb.ensure_schema(con)
    con.close()


def test_upsert_sets_date_added_only_on_insert(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="New", titel="Song"); con.commit()
    first = songsdb.find(con, "New", "Song")["date_added"]
    assert first  # gesetzt beim Insert

    songsdb.upsert(con, interpret="New", titel="Song", isrc="US1234567890"); con.commit()
    assert songsdb.find(con, "New", "Song")["date_added"] == first  # unveraendert beim Update
    con.close()


def test_upsert_protect_fields_new_columns_not_overwritten(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="New", titel="Song", isrc="US1234567890", iswc="T-000.000.001-0"); con.commit()
    songsdb.upsert(con, interpret="New", titel="Song", isrc="DARF NICHT", iswc="DARF NICHT"); con.commit()
    hit = songsdb.find(con, "New", "Song")
    assert hit["isrc"] == "US1234567890" and hit["iswc"] == "T-000.000.001-0"
    con.close()


def test_upsert_refresh_fields_always_update(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="New", titel="Song", confidence_score=0.5, review_status="review"); con.commit()
    assert songsdb.find(con, "New", "Song")["confidence_score"] == 0.5

    # Re-Resolve mit mehr Quellen -> hoeherer Score darf den alten ERSETZEN (kein PROTECT)
    songsdb.upsert(con, interpret="New", titel="Song", confidence_score=0.9, review_status="confirmed"); con.commit()
    hit = songsdb.find(con, "New", "Song")
    assert hit["confidence_score"] == 0.9 and hit["review_status"] == "confirmed"
    con.close()


def test_upsert_possible_cover_and_gema_flags_stored_as_int(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="New", titel="Song", possible_cover=True, gema_lookup_pending=True)
    con.commit()
    hit = songsdb.find(con, "New", "Song")
    assert hit["possible_cover"] == 1 and hit["gema_lookup_pending"] == 1
    con.close()


# --- DB-Editor: list_songs / update_song / get_song ---

def test_list_songs_paginates_and_counts(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.list_songs(con, limit=1, offset=0)
    assert res["total"] == 2 and len(res["rows"]) == 1 and "id" in res["rows"][0]
    con.close()


def test_list_songs_search_filters(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.list_songs(con, query="aurora")
    assert res["total"] == 1 and res["rows"][0]["interpret"] == "AURORA"
    # Suche greift auch auf Labelcode
    assert songsdb.list_songs(con, query="LC01485")["rows"][0]["titel"] == "Thank You"
    con.close()


def test_update_song_writes_manual_composer(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="aurora")["rows"][0]["id"]
    assert songsdb.update_song(con, sid, {"komponist": "Manuell Recherchiert"}) == {"ok": True}
    assert songsdb.get_song(con, sid)["komponist"] == "Manuell Recherchiert"
    con.close()


def test_update_song_overwrites_protected_field(tmp_path):
    # Der Editor MUSS auch ein bereits gefülltes (sonst geschütztes) Feld korrigieren können.
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="thank you")["rows"][0]["id"]
    songsdb.update_song(con, sid, {"labelcode": "LC99999"})
    assert songsdb.get_song(con, sid)["labelcode"] == "LC99999"
    con.close()


def test_update_song_rewrites_nkey_on_title_change(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="runaway")["rows"][0]["id"]
    songsdb.update_song(con, sid, {"titel": "Runaway (Live)"})
    assert songsdb.find(con, "AURORA", "Runaway (Live)")   # über neuen nkey findbar
    assert songsdb.find(con, "AURORA", "Runaway") is None
    con.close()


def test_update_song_rejects_empty_required(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="aurora")["rows"][0]["id"]
    assert "error" in songsdb.update_song(con, sid, {"interpret": ""})
    con.close()


def test_update_song_rejects_duplicate(tmp_path):
    # Umbenennen auf ein existierendes (interpret,titel)-Paar verletzt UNIQUE.
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="aurora")["rows"][0]["id"]
    res = songsdb.update_song(con, sid, {"interpret": "Alanis Morissette", "titel": "Thank You"})
    assert "error" in res
    con.close()


def test_update_song_missing_id(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    assert "error" in songsdb.update_song(con, 99999, {"komponist": "X"})
    con.close()


# --- 1b: album + track_number (GEMA-schlank) ---

def test_open_migrates_album_and_track_number(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))  # Alt-Schema ohne album/track_number
    cols = {r[1] for r in con.execute("PRAGMA table_info(songs)")}
    assert "album" in cols and "track_number" in cols
    con.close()


def test_upsert_protects_album_and_track_number(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="New", titel="Song", album="Debut", track_number="3/12"); con.commit()
    songsdb.upsert(con, interpret="New", titel="Song", album="DARF NICHT", track_number="9"); con.commit()
    hit = songsdb.find(con, "New", "Song")
    assert hit["album"] == "Debut" and hit["track_number"] == "3/12"
    con.close()


def test_update_song_edits_album_and_track_number(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    sid = songsdb.list_songs(con, query="aurora")["rows"][0]["id"]
    assert songsdb.update_song(con, sid, {"album": "All My Demons", "track_number": "2"}) == {"ok": True}
    got = songsdb.get_song(con, sid)
    assert got["album"] == "All My Demons" and got["track_number"] == "2"
    con.close()


# --- 1b: create_song ("Neuer Eintrag") ---

def test_create_song_inserts_and_is_findable(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.create_song(con, {"interpret": "Bon Iver", "titel": "Holocene",
                                    "labelcode": "LC00382", "album": "Bon Iver", "track_number": "5"})
    assert res.get("ok") and isinstance(res.get("id"), int)
    hit = songsdb.find(con, "Bon Iver", "Holocene")
    assert hit and hit["labelcode"] == "LC00382" and hit["album"] == "Bon Iver"
    con.close()


def test_create_song_sets_quelle_and_date_added(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.create_song(con, {"interpret": "Bon Iver", "titel": "Holocene"})
    got = songsdb.get_song(con, res["id"])
    row = con.execute("SELECT quelle FROM songs WHERE id=?", (res["id"],)).fetchone()
    assert row["quelle"] == "manuell" and got["date_added"]
    con.close()


def test_create_song_rejects_empty_required(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    assert "error" in songsdb.create_song(con, {"interpret": "", "titel": "X"})
    assert "error" in songsdb.create_song(con, {"interpret": "X", "titel": ""})
    con.close()


def test_create_song_rejects_duplicate(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.create_song(con, {"interpret": "AURORA", "titel": "Runaway"})  # existiert schon
    assert "error" in res
    assert songsdb.count(con) == 2  # nichts eingefügt
    con.close()


def test_find_similar_matches_missing_leading_word(tmp_path):
    # „dangerous thing" muss „A Dangerous Thing" vorschlagen (Substring-Bonus).
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="AURORA", titel="A Dangerous Thing", labelcode="LC00171")
    con.commit()
    sims = songsdb.find_similar(con, "AURORA", "dangerous thing")
    assert sims and sims[0]["titel"] == "A Dangerous Thing" and sims[0]["labelcode"] == "LC00171"
    con.close()


def test_find_similar_handles_typo(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))     # DB hat „Runaway" (AURORA)
    sims = songsdb.find_similar(con, "AURORA", "Runway")   # Tippfehler
    assert any(s["titel"] == "Runaway" for s in sims)
    con.close()


def test_find_similar_empty_on_no_match(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    assert songsdb.find_similar(con, "Zzz Niemand", "Voellig Anderer Song 9x") == []
    con.close()


def test_find_similar_exact_title_still_suggested(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    sims = songsdb.find_similar(con, "AURORA", "Runaway")
    assert sims[0]["titel"] == "Runaway"
    con.close()



def test_find_similar_skips_foreign_artist_but_keeps_spelling_variant(tmp_path):
    """Die Suche „rolling stones“ + „sympathy“ schlug „Bruno Alexiu – Sympathy for Animals“ vor. Mit
    angegebenem Interpret zählt ein ganz anderer Interpret nicht; eine andere Schreibweise desselben schon."""
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="Bruno Alexiu", titel="Sympathy for Animals", labelcode="LC10347")
    songsdb.upsert(con, interpret="The Rolling Stones", titel="Sympathy for the Devil", labelcode="LC00309")
    con.commit()
    sims = songsdb.find_similar(con, "rolling stones", "sympathy")
    assert [s["titel"] for s in sims] == ["Sympathy for the Devil"]
    con.close()

def test_create_song_ignores_non_editable_fields(tmp_path):
    # confidence_score ist kein EDITABLE_FIELD -> darf nicht über create gesetzt werden.
    con = songsdb.open(_mkdb(tmp_path))
    res = songsdb.create_song(con, {"interpret": "X", "titel": "Y", "confidence_score": 0.9})
    assert res.get("ok")
    assert songsdb.get_song(con, res["id"])["confidence_score"] is None
    con.close()


def test_open_migrates_verlag_column_and_upsert_protects_it(tmp_path):
    # Verlag (Musikverlag, GEMA-Seite) — eigene Spalte, getrennt vom Label (GVL-Seite).
    con = songsdb.open(_mkdb(tmp_path))
    assert "verlag" in {r[1] for r in con.execute("PRAGMA table_info(songs)")}
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", labelname="Decca", labelcode="LC00171")
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", verlag="Warner Chappell")   # leeres Feld auffüllen
    assert songsdb.find(con, "AURORA", "Runaway")["verlag"] == "Warner Chappell"
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", verlag="Falscher Verlag")  # PROTECT: nie überschreiben
    assert songsdb.find(con, "AURORA", "Runaway")["verlag"] == "Warner Chappell"
    assert "verlag" in songsdb.EDITABLE_FIELDS                                          # DB-Editor darf korrigieren
    con.close()


def test_list_songs_missing_filter_and_counts(tmp_path):
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="Birdy", titel="Skinny Love", labelname="Atlantic", labelcode="LC00121",
                   komponist="Justin Vernon", verlag="April Base")
    con.commit()
    mc = songsdb.missing_counts(con)
    assert mc["verlag"] == 2 and mc["texter"] == 1 and mc["komponist"] == 0 and mc["labelcode"] == 0
    assert set(mc) == set(songsdb.MISSING_FIELDS)
    assert songsdb.list_songs(con, missing="texter")["total_all"] == 3                 # Chip „Alle": ohne Lücken-Filter
    res = songsdb.list_songs(con, missing="verlag")
    assert res["total"] == 2 and all(not r["verlag"] for r in res["rows"]) and res["missing_counts"] == mc
    assert songsdb.list_songs(con, query="birdy", missing="verlag")["total"] == 0        # Suche UND Lücke
    assert songsdb.list_songs(con, missing="labelcode; DROP TABLE songs")["total"] == 3  # unbekannt -> ignoriert
    assert len(songsdb.all_songs(con, missing="texter")) == mc["texter"]
    con.close()


def test_import_library_tracks_and_find_by_file(tmp_path):
    """Library-Lieferung (Cezame-xlsx): jede Zeile verifiziert in songs, jeder Dateiname in
    library_files — auch wenn derselbe Interpret+Titel auf mehreren Alben liegt (nur EINE songs-Zeile,
    aber jeder Dateiname zeigt auf sie). Lookup über den Clip-Namen aus der EDL, Endung egal."""
    con = songsdb.open(tmp_path / "songs.db")
    rows = [
        {"file": "CEZ4030_1.mp3", "interpret": "Pierre-André Athané", "titel": "A Sunny Day", "komponist": "Pierre-André Athané",
         "verlag": "Frederic Leibovitz Editeur", "labelname": "CMG", "labelcode": "LC10347", "album": "Little French Fancies",
         "track_number": "1", "isrc": "FR26F0303350", "duration_sec": 125.0, "catno": "CEZ4030", "version": "Main version",
         "sublibrary": "Cézame"},
        {"file": "CEZ4032_26.mp3", "interpret": "Someone", "titel": "Nocturne", "komponist": "Someone", "labelname": "CMG",
         "labelcode": "LC10347", "album": "Album A", "track_number": "26", "isrc": "FRZPA0802030", "catno": "CEZ4032", "version": "Main version"},
        {"file": "CEO2008_2.mp3", "interpret": "Someone", "titel": "Nocturne", "komponist": "Someone", "labelname": "Bibliotheque Music",
         "labelcode": "LC50141", "album": "Album B", "track_number": "2", "isrc": "FRZPA0802030", "catno": "CEO2008", "version": "Main version",
         "duration_sec": "''"},                                        # Cezame liefert bei fehlender Länge „''“; anderer LC je Datei
    ]
    rep = songsdb.import_library_tracks(con, rows, library="cezame", date_verified="2026-09-20")
    assert rep == {"inserted": 2, "updated": 1, "files": 3}
    assert songsdb.count(con) == 2
    hit = songsdb.find(con, "pierre-andre athane", "a sunny day")
    assert hit["labelcode"] == "LC10347" and hit["review_status"] == "verified"
    assert con.execute("SELECT quelle FROM songs WHERE nkey=?", (songsdb._nkey("Pierre-André Athané", "A Sunny Day"),)).fetchone()[0] == "library:cezame"
    assert hit["date_verified"] == "2026-09-20" and hit["source_type"] == "library" and hit["file_path"] == "CEZ4030_1.mp3"
    f = songsdb.find_by_file(con, "ceo2008_2")                     # Stem aus der EDL, klein, ohne Endung
    assert f["titel"] == "Nocturne" and f["file"] == "CEO2008_2.mp3" and f["catno"] == "CEO2008" and f["id"]
    assert songsdb.find_by_file(con, "CEZ4032_26.wav")["album"] == "Album A" and f["album"] == "Album B"   # Album je Datei
    assert f["labelcode"] == "LC50141" and f["labelname"] == "Bibliotheque Music"   # LC der getroffenen Datei, nicht der ersten
    assert songsdb.find(con, "someone", "nocturne")["labelcode"] == "LC10347"       # songs-Zeile: erste Lieferung gewinnt (PROTECT)
    assert songsdb.find_by_file(con, "CEZ4032_26.wav")["file_catno"] == "CEZ4032"
    assert songsdb.find_by_file(con, "CEZ4030_1")["sublibrary"] == "Cézame" and songsdb.find_by_file(con, "ceo2008_2")["sublibrary"] == ""
    assert songsdb.find_by_file(con, "nope") is None and songsdb.find_by_file(con, "") is None
    assert songsdb.library_files_count(con, "cezame") == 3 and songsdb.library_files_count(con, "x") == 0
    # zweiter Lauf: idempotent, keine Dubletten
    rep = songsdb.import_library_tracks(con, rows, library="cezame", date_verified="2026-09-21")
    assert rep["inserted"] == 0 and songsdb.count(con) == 2 and songsdb.library_files_count(con, "cezame") == 3
    con.close()


def test_clear_cue_durations_drops_project_durations_only(tmp_path):
    """Bereinigung: ältere Importe haben die Cue-Dauer eines Projekts als duration_sec gespeichert
    (edl_import-Zeilen, mit 4.0 Sekunden). Weg damit; die Tracklänge
    einer Library-Lieferung bleibt, auch wenn ein späterer Lauf source_type auf edl_import gesetzt hat."""
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="Billie Eilish", titel="When The Partys Over", labelcode="LC06406",
                   source_type="edl_import", duration_sec=4.0)
    songsdb.import_library_tracks(con, [{"file": "CEZ4030_1.mp3", "interpret": "Pierre-André Athané", "titel": "A Sunny Day",
                                         "labelcode": "LC10347", "duration_sec": 125}],
                                  library="cezame", date_verified="2026-09-20")
    songsdb.upsert(con, interpret="Pierre-André Athané", titel="A Sunny Day", komponist="X", source_type="edl_import")
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", source_type="tagger", duration_sec=200.0)
    con.commit()
    assert songsdb.clear_cue_durations(con) == 1
    assert songsdb.find(con, "Billie Eilish", "When The Partys Over")["duration_sec"] is None
    assert songsdb.find(con, "Pierre-André Athané", "A Sunny Day")["duration_sec"] == 125
    assert songsdb.find(con, "AURORA", "Runaway")["duration_sec"] == 200
    assert songsdb.clear_cue_durations(con) == 0                    # zweiter Lauf: nichts mehr zu tun
    con.close()


def test_search_uses_fts_trigram_with_like_semantics(tmp_path):
    """Suche im Datenbank-Tab über FTS5-Trigram statt LIKE über vier Spalten (0,5 s bei 61k Zeilen nach der
    Cezame-Lieferung): gleiche Semantik — jedes Wort muss in Interpret, Titel, Label oder Code stehen,
    Teilwort, Groß/klein und Akzente egal; Wörter unter drei Zeichen weiter per LIKE. Trigger halten den
    Index bei Insert/Update/Delete aktuell; eine alte DB bekommt ihn beim Öffnen nachgebaut."""
    db = _mkdb(tmp_path)                                    # altes Schema ohne FTS -> ensure_schema baut nach
    con = songsdb.open(db)
    assert songsdb.has_fts(con)
    # FTS mit altem Spaltensatz (Stand vor sublibraries) wird beim Öffnen ersetzt, Trigger neu
    con.execute("DROP TRIGGER songs_fts_ai"); con.execute("DROP TRIGGER songs_fts_ad"); con.execute("DROP TRIGGER songs_fts_au")
    con.execute("DROP TABLE songs_fts")
    con.execute("CREATE VIRTUAL TABLE songs_fts USING fts5(interpret, titel, content='songs', content_rowid='id', tokenize='trigram')")
    con.commit(); con.close(); con = songsdb.open(db)
    assert tuple(r[1] for r in con.execute("PRAGMA table_info(songs_fts)")) == songsdb._FTS_COLS
    songsdb.upsert(con, interpret="Pierre-André Athané", titel="A Sunny Day", labelname="CMG", labelcode="LC10347")
    con.commit()
    q = lambda s, **k: [r["titel"] for r in songsdb.list_songs(con, query=s, **k)["rows"]]
    assert q("athane") == ["A Sunny Day"] and q("ATHANÉ sunny") == ["A Sunny Day"]      # Akzent, Groß/klein, zwei Felder
    assert q("aurora runaway") == ["Runaway"] and q("aurora thank") == []               # UND über Wörter
    assert q("LC01485") == ["Thank You"] and q("01485") == ["Thank You"] and q("maverick") == ["Thank You"]
    songsdb.import_library_tracks(con, [{"file": "X1_1.mp3", "interpret": "Lib Artist", "titel": "Lib Track"}],
                                  library="cezame", date_verified="2026-09-20")
    assert q("cezame") == ["Lib Track"]                                                   # quelle library:cezame im Index
    assert q("a sunny") == ["A Sunny Day"] and q("x") == [] and q("ay") == ["Runaway", "A Sunny Day"]   # Kurzwörter per LIKE
    assert songsdb.list_songs(con, query="")["total"] == 4
    sid = songsdb.list_songs(con, query="sunny")["rows"][0]["id"]
    songsdb.update_song(con, sid, {"titel": "A Rainy Day"})
    assert q("sunny") == [] and q("rainy") == ["A Rainy Day"]                             # Update-Trigger
    con.execute("DELETE FROM songs WHERE id=?", (sid,)); con.commit()                   # Delete-Trigger
    assert q("rainy") == [] and songsdb.missing_counts(con, query="aurora")["texter"] == 0
    assert len(songsdb.all_songs(con, query="thank")) == 1
    con.close()


def test_missing_filter_ignores_library_rows(tmp_path):
    """Lücken-Chips sind eine Recherche-Warteschlange: Library-Lieferungen (source_type library) sind komplett,
    wie sie kommen — eine Instrumental-Library ohne Texter zählt sonst 60.938 „ohne Texter“ (Cezame)."""
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="Own", titel="Cue", labelcode="LC00121")            # eigene Zeile, Lücken echt
    songsdb.import_library_tracks(con, [{"file": "X1_1.mp3", "interpret": "Lib", "titel": "Track", "labelcode": "LC10347"}],
                                  library="cezame", date_verified="2026-09-20")
    mc = songsdb.missing_counts(con)
    assert mc["texter"] == 1 and mc["komponist"] == 1 and mc["labelcode"] == 0
    assert [r["interpret"] for r in songsdb.list_songs(con, missing="texter")["rows"]] == ["Own"]
    assert songsdb.list_songs(con, missing="texter")["total_all"] == 2                 # Chip „Alle“ zählt alles
    assert len(songsdb.all_songs(con, missing="komponist")) == 1
    assert songsdb.list_songs(con, query="lib")["total"] == 1                          # ohne Lücken-Filter sichtbar
    con.close()


# --- Titel/Interpreten ohne a-z/0-9 dürfen nicht über einen leeren nkey-Teil kollidieren ---

def _weakdb(tmp_path):
    con = songsdb.open(tmp_path / "weak.db")
    songsdb.upsert(con, interpret="Billie Eilish", titel="!!!!!!!", labelname="Interscope", labelcode="LC06406",
                   komponist="Billie Eilish; FINNEAS")
    songsdb.upsert(con, interpret="宇多田ヒカル", titel="花束を君に", labelcode="LC00001")
    songsdb.upsert(con, interpret="Kino", titel="Звезда (Live)", labelcode="LC00002")
    songsdb.upsert(con, interpret="Sigurd Jøhnk-Jensen", titel="Do You Believe in Valhalla ?", labelcode="LC10347")
    con.commit()
    return con


def test_find_does_not_match_foreign_song_over_empty_title_key(tmp_path):
    con = _weakdb(tmp_path)
    assert songsdb.find(con, "Billie Eilish", "ハッピアー・ザン・エヴァー") is None
    assert songsdb.find(con, "Billie Eilish", "???") is None
    assert songsdb.find_row(con, "Billie Eilish", "ハッピアー・ザン・エヴァー") is None
    assert songsdb.find(con, "Кино", "Группа крови") is None            # Interpret UND Titel leer im nkey
    assert songsdb.find(con, "Kino", "Live") is None                     # Rest-Schlüssel 'live' gleich, Song nicht
    con.close()


def test_find_still_matches_same_non_latin_text(tmp_path):
    """Derselbe Wortlaut trifft weiter — Groß/klein und Satzzeichen wie bisher egal."""
    con = _weakdb(tmp_path)
    assert songsdb.find(con, "Billie Eilish", "!!!!!!!")["labelcode"] == "LC06406"
    assert songsdb.find(con, "宇多田ヒカル", "花束を君に")["labelcode"] == "LC00001"
    assert songsdb.find(con, "KINO", "звезда (live)")["labelcode"] == "LC00002"
    assert songsdb.find(con, "SIGURD JØHNK-JENSEN", "Do You Believe in Valhalla?")["labelcode"] == "LC10347"
    con.close()


def test_upsert_and_create_keep_non_latin_songs_apart(tmp_path):
    """Speichern darf eine fremde Zeile weder überschreiben noch einen neuen Song als Dublette ablehnen."""
    con = _weakdb(tmp_path)
    assert songsdb.upsert(con, interpret="Billie Eilish", titel="Счастливее", file_path="/x") == "inserted"
    con.commit()
    assert not songsdb.find(con, "Billie Eilish", "!!!!!!!")["file_path"]
    res = songsdb.create_song(con, {"interpret": "Billie Eilish", "titel": "ハッピアー・ザン・エヴァー"})
    assert res.get("ok"), res
    sid = songsdb.find_row(con, "Billie Eilish", "Счастливее")["id"]
    assert songsdb.update_song(con, sid, {"titel": "???"}) == {"ok": True}
    assert songsdb.find(con, "Billie Eilish", "???")["file_path"] == "/x"
    assert "error" in songsdb.create_song(con, {"interpret": "billie eilish", "titel": "!!!!!!!"})   # echte Dublette
    con.close()


def test_import_library_tracks_keeps_non_latin_titles_apart(tmp_path):
    con = _weakdb(tmp_path)
    rep = songsdb.import_library_tracks(con, [{"file": "LIB_1_1.mp3", "interpret": "Billie Eilish", "titel": "Ψ"}],
                                        library="lib", date_verified="2026-10-12")
    assert rep["inserted"] == 1
    assert songsdb.find_by_file(con, "LIB_1_1.mp3")["titel"] == "Ψ"
    assert songsdb.find(con, "Billie Eilish", "!!!!!!!")["titel"] == "!!!!!!!"
    con.close()


def test_same_song():
    assert songsdb.same_song("AURORA", "Runaway", "aurora", "RUNAWAY!")
    assert songsdb.same_song("Børns", "Electric Love", "BØRNS", "Electric love")
    assert not songsdb.same_song("Billie Eilish", "???", "Billie Eilish", "!!!!!!!")
    assert not songsdb.same_song("Billie Eilish", "ハッピアー", "Billie Eilish", "!!!!!!!")
    assert not songsdb.same_song("Kino", "Live", "Kino", "Звезда (Live)")
    assert not songsdb.same_song("AURORA", "Runaway", "AURORA", "Running")


# --- songs.db speichert 'LC' + Ziffern, die Suche findet beide Schreibweisen ---

def test_write_paths_store_labelcode_as_lc_digits(tmp_path):
    con = songsdb.open(tmp_path / "lc.db")
    songsdb.upsert(con, interpret="A", titel="Upsert", labelcode="00171")
    songsdb.upsert(con, interpret="A", titel="Gruppen", labelcode="LC 24 584")
    songsdb.upsert(con, interpret="A", titel="Ohne", labelcode="")
    sid = songsdb.create_song(con, {"interpret": "A", "titel": "Neu", "labelcode": "121"})["id"]
    songsdb.update_song(con, sid, {"labelcode": "LC 01485"})
    songsdb.import_library_tracks(con, [{"file": "VM_1_1_X_1000001.mp3", "interpret": "B", "titel": "Lib",
                                         "labelcode": "LC 48731"}], library="upm", date_verified="2026-10-12")
    got = dict(con.execute("SELECT titel, labelcode FROM songs").fetchall())
    assert got == {"Upsert": "LC00171", "Gruppen": "LC24584", "Ohne": "", "Neu": "LC01485", "Lib": "LC48731"}
    assert con.execute("SELECT labelcode FROM library_files").fetchone()[0] == "LC48731"
    con.close()


def test_normalize_labelcodes_cleans_old_rows_and_keeps_unreadable_values(tmp_path):
    """Bereinigung für Bestand und Seed — hier nur
    die Funktion: Format vereinheitlichen, nichts ohne Ziffern wegwerfen, zweiter Lauf ändert nichts."""
    con = songsdb.open(tmp_path / "old.db")
    con.executemany("INSERT INTO songs (interpret, titel, labelcode, nkey) VALUES (?,?,?,?)",
                    [("A", "1", "00171", ""), ("A", "2", "LC 01485", ""), ("A", "3", "121", ""),
                     ("A", "4", "unbekannt", ""), ("A", "5", "", ""), ("A", "6", "LC10347", ""), ("A", "7", None, "")])
    con.execute("INSERT INTO library_files (file, file_name, song_id, library, labelcode) VALUES "
                "('x', 'X.mp3', 1, 'upm', 'LC 48731')")
    con.commit()
    assert songsdb.normalize_labelcodes(con) == {"songs": 3, "library_files": 1}
    got = [r[0] for r in con.execute("SELECT labelcode FROM songs ORDER BY titel")]
    assert got == ["LC00171", "LC01485", "LC00121", "unbekannt", "", "LC10347", None]
    assert con.execute("SELECT labelcode FROM library_files").fetchone()[0] == "LC48731"
    assert songsdb.list_songs(con, query="LC01485")["total"] == 1             # FTS folgt per Trigger
    assert songsdb.normalize_labelcodes(con) == {"songs": 0, "library_files": 0}
    con.close()


def test_search_finds_labelcode_with_and_without_lc_prefix(tmp_path):
    """'LC00171' fand nur die Zeilen mit Präfix, Alt-Zeilen tragen den Code ohne 'LC'."""
    con = songsdb.open(tmp_path / "s.db")
    con.executemany("INSERT INTO songs (interpret, titel, labelcode, nkey) VALUES (?,?,?,?)",
                    [("AURORA", "Runaway", "LC00171", "a"), ("AURORA", "A Dangerous Thing", "00171", "b"),
                     ("X", "Six", "104994", "c"), ("Y", "Six Too", "LC104994", "d"), ("Z", "Andere", "LC00121", "e")])
    songsdb.ensure_fts(con)
    con.execute("INSERT INTO songs_fts(songs_fts) VALUES ('rebuild')")
    con.commit()
    for q in ("LC00171", "lc 00171", "LC171", "00171"):
        assert {r["titel"] for r in songsdb.list_songs(con, query=q)["rows"]} == {"Runaway", "A Dangerous Thing"}, q
    assert songsdb.list_songs(con, query="aurora LC00171")["total"] == 2
    assert songsdb.list_songs(con, query="LC104994")["total"] == 2
    assert songsdb.list_songs(con, query="LC1049")["total"] == 2                 # Tippen: Präfix trifft weiter
    assert len(songsdb.all_songs(con, query="LC00171")) == 2
    con.close()


def test_list_songs_sorts_case_insensitive(tmp_path):
    """'AXEL COON' stand vor 'Aaron Albert Srdoc' (binäre Sortierung)."""
    con = songsdb.open(tmp_path / "sort.db")
    for a, t in (("AXEL COON", "x"), ("Aaron Albert Srdoc", "y"), ("ADAM CROSS", "z"), ("aurora", "b"), ("AURORA", "A")):
        songsdb.upsert(con, interpret=a, titel=t)
    con.commit()
    order = [(r["interpret"], r["titel"]) for r in songsdb.list_songs(con)["rows"]]
    assert [a for a, _ in order] == ["Aaron Albert Srdoc", "ADAM CROSS", "AURORA", "aurora", "AXEL COON"]
    assert [a for a, _ in ((r["interpret"], r["titel"]) for r in songsdb.all_songs(con))] == [a for a, _ in order]
    con.close()


def test_edit_id_fallback_needs_a_title_word_in_the_clip_name(tmp_path):
    """Jeder Clip mit 6–9-stelliger Endzahl traf über die Edit-ID einen UPM-Track als verifiziert
    ('Interview Zzq Take 3 1420031.wav' -> 'I'm Going Crazy'). Die ID allein reicht nicht: ein Wort aus
    Titel oder Dateiname der Lieferung muss auch im Clipnamen stehen."""
    con = songsdb.open(tmp_path / "upm.db")
    songsdb.import_library_tracks(con, [
        {"file": "VM_40_11_Little_White_Lies_Anderson_Moenks_1018801.mp3", "interpret": "Matthew Anderson",
         "titel": "Little White Lies", "labelcode": "LC48731"},
        {"file": "XY_12_3_Im_Going_Crazy_Connolly_1420031.mp3", "interpret": "David Thomas Connolly",
         "titel": "I'm Going Crazy", "labelcode": "LC48731"},
        {"file": "EM_3001_2_EM023955_SD_Fay_1574527.mp3", "interpret": "Fay", "titel": "Take My Shine",
         "labelcode": "LC48731"}], library="upm", date_verified="2026-10-12")
    assert songsdb.find_by_file(con, "Interview Zzq Take 3 1420031.wav") is None
    assert songsdb.find_by_file(con, "Fremde Library - Calm Waters 1018801.wav") is None
    hit = songsdb.find_by_file(con, "UPM_VM40_11_LITTLE_WHITE_LIES_ANDERSON_MOENKS_1018801.MP3")
    assert hit and hit["titel"] == "Little White Lies"
    assert songsdb.find_by_file(con, "UPM_XY12_3_GOING_CRAZY_1420031.wav")["titel"] == "I'm Going Crazy"
    assert songsdb.find_by_file(con, "UPM_EM3001_2_EM023955_SD_FAY_1574527.wav")["titel"] == "Take My Shine"
    assert songsdb.find_by_file(con, "Clip 3001 2 1574527.wav") is None                 # nur Zahlen gemeinsam


def test_delete_song_removes_row_and_its_library_files(tmp_path):
    """Songs löschen — die Dateinamen der Lieferung gehen mit, sonst
    zeigte library_files auf eine id, die es nicht mehr gibt (oder später auf eine fremde Zeile)."""
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.import_library_tracks(con, [
        {"file": "CEZ4030_1.wav", "interpret": "Zzq Komponist", "titel": "Weg Damit", "labelcode": "104994"},
        {"file": "CEZ4030_2.wav", "interpret": "Zzq Komponist", "titel": "Bleibt", "labelcode": "104994"}],
        library="cezame", date_verified="2026-10-12")
    sid = songsdb.find_row(con, "Zzq Komponist", "Weg Damit")["id"]
    assert songsdb.find_by_file(con, "CEZ4030_1") is not None
    assert songsdb.delete_song(con, sid) is True
    assert songsdb.find(con, "Zzq Komponist", "Weg Damit") is None
    assert songsdb.find_by_file(con, "CEZ4030_1") is None
    assert con.execute("SELECT COUNT(*) FROM library_files WHERE song_id=?", (sid,)).fetchone()[0] == 0
    assert songsdb.find_by_file(con, "CEZ4030_2") is not None              # die andere Zeile bleibt
    assert songsdb.list_songs(con, query="Weg Damit")["total"] == 0        # Volltextindex nachgezogen
    assert songsdb.delete_song(con, sid) is False                          # zweites Mal: gibt es nicht mehr
    con.close()


def test_revert_upsert_keeps_later_hand_edits_and_foreign_rows(tmp_path):
    """Die Gegenoperation setzt nur zurück, was das upsert geschrieben hat — eine spätere
    Handkorrektur im DB-Editor bleibt, und eine gelöschte und neu vergebene id trifft keine fremde Zeile."""
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.create_song(con, {"interpret": "Zzq A", "titel": "Song"})
    before = songsdb.find_row(con, "Zzq A", "Song")
    songsdb.upsert(con, interpret="Zzq A", titel="Song", labelcode="00121", labelname="Atlantic", album="Alb")
    info = songsdb.upsert_undo(before, songsdb.find_row(con, "Zzq A", "Song"))
    assert info["inserted"] is False and set(info["changed"]) == {"labelcode", "labelname", "album"}
    songsdb.update_song(con, before["id"], {"album": "Von Hand"})          # Nutzerin korrigiert danach
    songsdb.revert_upsert(con, info)
    row = songsdb.find(con, "Zzq A", "Song")
    assert not row["labelcode"] and not row["labelname"] and row["album"] == "Von Hand"

    songsdb.upsert(con, interpret="Zzq B", titel="Neu", labelcode="00121")
    info_b = songsdb.upsert_undo(None, songsdb.find_row(con, "Zzq B", "Neu"))
    assert info_b["inserted"] is True
    songsdb.delete_song(con, info_b["song_id"])
    songsdb.upsert(con, interpret="Zzq C", titel="Fremd", labelcode="10192")   # bekommt dieselbe id
    assert songsdb.find_row(con, "Zzq C", "Fremd")["id"] == info_b["song_id"]
    songsdb.revert_upsert(con, info_b)
    assert songsdb.find(con, "Zzq C", "Fremd") is not None
    con.close()


def test_list_songs_total_verified_counts_without_review_rows(tmp_path):
    """Umfang-Schalter „Verifiziert N“ der Track-Datenbank: Zeilen zur Prüfung (review_status 'review', Status
    „Prüfen“) zählen nicht als verifiziert; total_all bleibt die Trefferzahl der Suche."""
    con = songsdb.open(_mkdb(tmp_path))
    songsdb.upsert(con, interpret="AURORA", titel="Exist For Love", labelcode="LC00171", review_status="review")
    con.commit()
    res = songsdb.list_songs(con)
    assert (res["total_all"], res["total_verified"]) == (3, 2)
    res = songsdb.list_songs(con, query="aurora", missing="labelcode")
    assert (res["total"], res["total_all"], res["total_verified"]) == (0, 2, 1)


# --- Unicode-fähiger Schlüssel, ohne den vorhandenen Track-Index zu brechen ---
_REPO = Path(__file__).resolve().parents[2]


def _old_part(s: str) -> str:
    """Die Formel, mit der songs.db und der 6,5-GB-Track-Index gebaut sind (früherer Stand von labelmatch.norm),
    hier eingefroren — der neue Schlüssel wird gegen sie geprüft, nicht gegen sich selbst."""
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and ").replace("+", " and "))
    return re.sub(r"\s+", " ", s).strip()


LATIN = ["AURORA", "Runaway", "Björk & Co", "It's Oh So Quiet", "Motörhead", "Mötley Crüe", "Sigur Rós",
         "Beyoncé", "Café del Mar", "Françoise Hardy", "Ærøskøbing", "Œuvre complète", "Straße", "Weißwurst",
         "ﬁnal ﬂight", "Ĳsselmeer", "Łódź", "Dvořák", "Smetana: Má vlast", "Ça plane pour moi", "Señorita",
         "São Paulo", "Ålesund", "Røyksopp", "BØRNS", "Mr. Brightside", "P!nk", "Ke$ha", "AC/DC", "Guns N' Roses",
         "Simon + Garfunkel", "Earth, Wind & Fire", "What's Up?", "(I Can't Get No) Satisfaction", "99 Luftballons",
         "Señor « Ñoño »", "A–Z — „Zitat“ ‚x‘ “y” ‘z’", "  Leer  zeichen  ", "Tab\tTrenner", "Ünïcödé Äpfel",
         "ǅemal", "Æsir", "ﬀ ligatur", "x²", "½ Stunde", "Nº 5", "Ⅳ", "!!!!!!! (Remix)", "Ὀδύσσεια feat. Homer"]
NON_LATIN = ["宇多田ヒカル", "花束を君に", "Кино", "Группа крови", "ハッピアー・ザン・エヴァー", "Ψ", "Ελληνικά",
             "עברית", "العربية", "한국어", "ไทย", "Ø", "!!!!!!!", "???", "…", "♪♫"]


def _corpus_texts():
    """Interpret und Titel aus Seed und Library-Lieferungen: die Texte, die heute in songs.db und daneben stehen."""
    out = []
    seed = _REPO / "MusicMetaDataSearchEngine" / "data" / "songs.db"
    if seed.exists():
        con = sqlite3.connect(f"file:{seed}?mode=ro", uri=True)
        out += [x for r in con.execute("SELECT interpret, titel FROM songs") for x in r]
        con.close()
    for p in sorted((_REPO / "data" / "library_tracks").glob("*.csv")):
        with open(p, newline="", encoding="utf-8") as f:
            out += [r[k] for r in csv.DictReader(f) for k in ("artist", "title", "composers") if r.get(k)]
    return out


def test_schluessel_bleibt_fuer_jeden_bisher_nicht_leeren_wert_gleich():
    """Für jeden Text, dem die alte Formel einen nicht leeren Schlüssel gab, liefert key() EXAKT denselben Wert —
    sonst passte der vorhandene Track-Index (6,5 GB, Neubau später) nicht mehr. Stichprobe lateinischer Texte mit
    Umlauten, Akzenten, Ligaturen, Satzzeichen, dazu jeder Interpret und Titel aus Seed und Lieferungen."""
    real = _corpus_texts()
    texts = LATIN + real
    assert len(real) > 50_000
    checked = 0
    for s in texts:
        old = _old_part(s)
        if old:
            assert songsdb.key(s) == old, s
            checked += 1
    assert checked > 50_000
    for a, t in zip(LATIN, reversed(LATIN)):
        if _old_part(a) and _old_part(t):
            assert songsdb._nkey(a, t) == _old_part(a) + "\x1f" + _old_part(t) == trackindex.nkey(a, t)


def test_text_ohne_a_z_bekommt_einen_schluessel_aus_seiner_schrift():
    """Text ohne a-z/0-9 fiel auf '' zusammen (alle japanischen Titel eines Interpreten auf einen Schlüssel).
    Jetzt: nicht leer, aus den Buchstaben der jeweiligen Schrift, verschiedene Texte verschiedene Schlüssel."""
    keys = {}
    for s in NON_LATIN:
        assert _old_part(s) == "", s
        k = songsdb.key(s)
        assert k and "\x1f" not in k, s
        keys.setdefault(k, s)
    assert len(keys) == len(NON_LATIN), keys
    assert songsdb.key("Кино") == "кино" and songsdb.key("Группа крови") == "группа крови"
    assert songsdb.key("ハッピアー・ザン・エヴァー") == "ハッピアー ザン エヴァー"
    assert songsdb.key("Ø") == "ø" and songsdb.key("!!!") == "!!!" and songsdb.key("") == "" == songsdb.key("  ")
    assert songsdb._nkey("Billie Eilish", "ハッピアー・ザン・エヴァー") == "billie eilish\x1fハッピアー ザン エヴァー"


def test_alte_songs_db_bekommt_die_neuen_schluessel_beim_oeffnen(tmp_path):
    """Zeilen, deren gespeicherter nkey einen leeren Teil hat (alter Stand), rechnet open() einmal nach — sonst fände
    _match sie mit dem neuen Schlüssel nicht mehr. Lateinische Zeilen bleiben unberührt; zweiter Lauf ändert nichts."""
    p = tmp_path / "songs.db"
    con = songsdb.open(p)
    con.executemany("INSERT INTO songs (interpret, titel, nkey, labelcode) VALUES (?,?,?,?)", [
        ("Billie Eilish", "!!!!!!!", "billie eilish\x1f", "LC06406"),
        ("宇多田ヒカル", "花束を君に", "\x1f", "LC00001"),
        ("AURORA", "Runaway", "aurora\x1frunaway", "LC00171")])
    con.commit()
    con.close()
    con = songsdb.open(p)
    keys = dict(con.execute("SELECT titel, nkey FROM songs").fetchall())
    assert keys == {"!!!!!!!": "billie eilish\x1f!!!!!!!", "花束を君に": "宇多田ヒカル\x1f花束を君に",
                    "Runaway": "aurora\x1frunaway"}
    assert songsdb.find(con, "宇多田ヒカル", "花束を君に")["labelcode"] == "LC00001"
    assert songsdb.find(con, "Billie Eilish", "!!!!!!!")["labelcode"] == "LC06406"
    assert songsdb.find(con, "Billie Eilish", "???") is None
    con.close()
    con = songsdb.open(p)
    assert dict(con.execute("SELECT titel, nkey FROM songs").fetchall()) == keys
    con.close()
