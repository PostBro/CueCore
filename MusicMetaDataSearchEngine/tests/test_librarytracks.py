"""Library-Lieferungen (core/librarytracks.py): Cezame-xlsx -> CSV-Extrakt -> songs.db + library_files."""
from openpyxl import Workbook

from core import librarytracks, songsdb

HEADER = ["TrackFilepath", "TrackDisplayTitle", "Library", "SubLibrary", "SubLibrary Code", "InternalID", "CatNo", "CDTitle",
          "TrackNo", "TrackSubNo", "TrackTitle", "Version", "Length", "COM:1:NamesBeforeKeyName", "COM:1:KeyName",
          "COM:2:NamesBeforeKeyName", "COM:2:KeyName", "ARR:1:NamesBeforeKeyName", "ARR:1:KeyName", "PUB:1:KeyName",
          "CODE:ISRC", "CODE:ISWC", "CODE:GEMA", "ATT:Artistname", "ReleaseDate"]


def _row(**kw):
    base = {h: "" for h in HEADER}
    base.update({"Library": "Cezame", "SubLibrary": "Cézame", "SubLibrary Code": "10347", "Version": "Main version",
                 "PUB:1:KeyName": "Frederic Leibovitz Editeur", "TrackSubNo": "1"})
    base.update(kw)
    return [base[h] for h in HEADER]


def _xlsx(tmp_path, rows):
    wb = Workbook(); ws = wb.active
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    ws.append([None] * len(HEADER))                                       # Leerzeile wie in der Lieferung
    p = tmp_path / "czesame - metadata.xlsx"; wb.save(p)
    return p


ROWS = [
    _row(TrackFilepath="CEZ4030_1.mp3", CatNo="CEZ4030", CDTitle="Little French Fancies ", TrackNo="1", TrackTitle="A Sunny Day",
         Length="125", **{"COM:1:NamesBeforeKeyName": "Pierre-André", "COM:1:KeyName": "Athané", "CODE:ISRC": "FR26F0303350",
                          "ATT:Artistname": "Athané, Pierre-André", "ReleaseDate": "2007-04-18"}),
    _row(TrackFilepath="CEZ4032_26.mp3", CatNo="CEZ4032", CDTitle="Album A", TrackNo="26", TrackTitle="Nocturne", Length="90",
         **{"COM:1:NamesBeforeKeyName": "A", "COM:1:KeyName": "One", "COM:2:NamesBeforeKeyName": "B", "COM:2:KeyName": "Two",
            "ARR:1:NamesBeforeKeyName": "C", "ARR:1:KeyName": "Arr", "CODE:ISRC": "FRZPA0802030", "ATT:Artistname": "One, A"}),
    _row(TrackFilepath="CEO2008_2.mp3", CatNo="CEO2008", CDTitle="Album B", TrackNo="2", TrackTitle="Nocturne", Length="90",
         **{"COM:1:NamesBeforeKeyName": "A", "COM:1:KeyName": "One", "CODE:ISRC": "FRZPA0802030", "ATT:Artistname": "One, A"}),
    _row(TrackFilepath="IOP001_1.mp3", SubLibrary="Input/Output", **{"SubLibrary Code": "104994"}, CatNo="IOP001", CDTitle="X",
         TrackNo="1", TrackTitle="New Code", Length="60", **{"COM:1:KeyName": "Solo", "CODE:ISRC": "FRZPA9900001",
                                                              "ATT:Artistname": "Pedro Resende"}),
]


def test_flip_name():
    assert librarytracks.flip_name("Athané, Pierre-André") == "Pierre-André Athané"
    assert librarytracks.flip_name("Pedro Resende") == "Pedro Resende"
    assert librarytracks.flip_name("Earth, Wind, Fire") == "Earth, Wind, Fire"        # zwei Kommas: so lassen
    assert librarytracks.flip_name(" Skornik , Elisabeth ") == "Elisabeth Skornik"
    assert librarytracks.flip_name("") == "" and librarytracks.flip_name("Solo,") == "Solo,"


def test_read_cezame_xlsx_extract_and_csv_roundtrip(tmp_path):
    rows = librarytracks.read_cezame_xlsx(_xlsx(tmp_path, ROWS))
    assert len(rows) == 4 and list(rows[0]) == list(librarytracks.CSV_COLUMNS)
    r = rows[0]
    assert r["file"] == "CEZ4030_1.mp3" and r["labelcode"] == "10347" and r["artist"] == "Athané, Pierre-André"   # wie geliefert
    assert r["composers"] == "Pierre-André Athané" and r["publisher"] == "Frederic Leibovitz Editeur" and r["duration_sec"] == "125"
    assert rows[1]["composers"] == "A One; B Two" and rows[1]["arranger"] == "C Arr"
    out = tmp_path / "cezame_2026-09.csv"
    assert librarytracks.write_csv(rows, out) == 4
    back = librarytracks.read_csv(out)
    assert back == [{k: str(v) for k, v in r.items()} for r in rows]
    assert librarytracks.library_name(out) == "cezame"


def test_read_cezame_xlsx_refuses_wrong_header(tmp_path):
    wb = Workbook(); wb.active.append(["Foo", "Bar"]); p = tmp_path / "x.xlsx"; wb.save(p)
    import pytest
    with pytest.raises(ValueError, match="Spalten fehlen"):
        librarytracks.read_cezame_xlsx(p)


def test_to_song_rows_maps_names_codes_and_reports(tmp_path):
    rows = librarytracks.read_cezame_xlsx(_xlsx(tmp_path, ROWS))
    names = {"10347": "CMG"}
    songs, rep = librarytracks.to_song_rows(rows, names.get)
    assert songs[0]["interpret"] == "Pierre-André Athané" and songs[0]["titel"] == "A Sunny Day"
    assert songs[0]["labelname"] == "CMG" and songs[0]["labelcode"] == "LC10347"            # GVL-Schreibweise, LC-Präfix
    assert songs[0]["album"] == "Little French Fancies" and songs[0]["verlag"] == "Frederic Leibovitz Editeur"
    assert songs[0]["komponist"] == "Pierre-André Athané" and songs[0]["texter"] == "" and songs[0]["isrc"] == "FR26F0303350"
    assert songs[3]["labelname"] == "Input/Output" and songs[3]["labelcode"] == "LC104994"   # nicht im Master: wie geliefert
    assert songs[0]["sublibrary"] == "Cézame" and songs[3]["sublibrary"] == "Input/Output"     # Sub-Katalog bleibt erhalten
    assert rep["rows"] == 4 and rep["unknown_codes"] == {"104994": {"label": "Input/Output", "n": 1}}
    assert rep["renamed"] == {"Cézame": "CMG"} and rep["no_code"] == 0


def test_ensure_imported_nimmt_jede_lieferung_einzeln(tmp_path):
    """UPM liefert bei jedem Download eine neue CSV; eine Regel „einmal je Library“ würde ab der zweiten
    nichts mehr übernehmen. Gemerkt wird deshalb je Datei."""
    d = tmp_path / "library_tracks"
    librarytracks.write_csv(librarytracks.read_cezame_xlsx(_xlsx(tmp_path, ROWS)), d / "cezame_2026-09.csv")
    librarytracks.write_csv([{"file": "VM_40_11_Little_White_Lies_Anderson_1018801.mp3", "library": "upm",
                              "sublibrary": "Volta Music", "labelcode": "LC 48731", "catno": "VM040",
                              "album": "Witness", "track_number": "11", "title": "Little White Lies",
                              "composers": "Matthew Anderson"}], d / "upm_2026-10.csv")
    con = songsdb.open(tmp_path / "songs.db")
    done = librarytracks.ensure_imported(con, d, {"10347": "CMG"}.get, date_verified="2026-10-08")
    assert sorted(x["file"] for x in done) == ["cezame_2026-09.csv", "upm_2026-10.csv"]
    assert librarytracks.ensure_imported(con, d, {}.get, date_verified="2026-10-09") == []

    # zweite UPM-Lieferung desselben Labels: wird eingepflegt, obwohl „upm" schon Dateien hat
    librarytracks.write_csv([{"file": "VM_40_12_Second_Delivery_Anderson_1018802.mp3", "library": "upm",
                              "sublibrary": "Volta Music", "labelcode": "LC 48731", "catno": "VM040",
                              "album": "Witness", "track_number": "12", "title": "Second Delivery",
                              "composers": "Matthew Anderson"}], d / "upm_2026-11.csv")
    done2 = librarytracks.ensure_imported(con, d, {}.get, date_verified="2026-11-01")
    assert [x["file"] for x in done2] == ["upm_2026-11.csv"]
    assert songsdb.find_by_file(con, "VM_40_12_Second_Delivery_Anderson_1018802.mp3")["titel"] == "Second Delivery"
    con.close()


def test_ensure_imported_zaehlt_altbestand_als_erledigt(tmp_path):
    """Bestehende Installation: Cezame steckt schon in library_files, aber library_deliveries ist leer.
    Der Erstlauf darf die 60.938 Zeilen nicht noch einmal durchkauen."""
    d = tmp_path / "library_tracks"
    librarytracks.write_csv(librarytracks.read_cezame_xlsx(_xlsx(tmp_path, ROWS)), d / "cezame_2026-09.csv")
    con = songsdb.open(tmp_path / "songs.db")
    librarytracks.ensure_imported(con, d, {"10347": "CMG"}.get, date_verified="2026-09-20")
    con.execute("DELETE FROM library_deliveries"); con.commit()           # Stand einer älteren Version
    assert librarytracks.ensure_imported(con, d, {}.get, date_verified="2026-10-08") == []
    assert songsdb.imported_deliveries(con) == {"cezame_2026-09.csv"}
    con.close()


def test_ensure_imported_runs_once_per_library(tmp_path):
    rows = librarytracks.read_cezame_xlsx(_xlsx(tmp_path, ROWS))
    d = tmp_path / "library_tracks"; librarytracks.write_csv(rows, d / "cezame_2026-09.csv")
    con = songsdb.open(tmp_path / "songs.db")
    done = librarytracks.ensure_imported(con, d, {"10347": "CMG"}.get, date_verified="2026-09-20")
    assert [x["library"] for x in done] == ["cezame"] and done[0]["inserted"] == 3 and done[0]["files"] == 4
    assert songsdb.count(con) == 3 and songsdb.find_by_file(con, "CEO2008_2")["titel"] == "Nocturne"
    assert librarytracks.ensure_imported(con, d, {}.get, date_verified="2026-09-21") == []     # schon drin
    assert librarytracks.ensure_imported(con, tmp_path / "nope", {}.get, date_verified="x") == []
    con.close()


def test_to_song_rows_putzt_pro_marker_aus_dem_extrakt():
    """Der Extrakt trägt die Namen wie geliefert ('Carl David Harms [IMRO]', 'Gussy G [NS]'), der Import muss
    sie putzen, bei UPM auch als Interpret, weil dort der Komponist der Interpret ist."""
    rows = [{"file": "X_1_Fight_or_Flight_Harms_1000001.mp3", "library": "upm", "sublibrary": "Volta Music",
             "labelcode": "LC 48731", "title": "Fight or Flight", "artist": "",
             "composers": "Carl David Harms [IMRO]; Gussy G [NS]", "publisher": "Music 2 Air ( NS)"}]
    songs, _ = librarytracks.to_song_rows(rows, {"48731": "Volta Music"}.get)
    assert songs[0]["interpret"] == "Carl David Harms; Gussy G"
    assert songs[0]["komponist"] == "Carl David Harms; Gussy G" and songs[0]["verlag"] == "Music 2 Air"


FOREIGN_CODE_ROWS = [
    {"file": "BBCPM_117_10_The_Arrival_Of_Autumn_Merrison_1463879.mp3", "library": "upm",
     "sublibrary": "BBC Production Music", "labelcode": "LC 21110", "title": "The Arrival Of Autumn",
     "composers": "Erika Merrison"},
    {"file": "ZZQ_1_1_Nordwand_Testerin_1000002.mp3", "library": "upm", "sublibrary": "Zzq Fantasy Label",
     "labelcode": "LC 88888", "title": "Nordwand", "composers": "Erika Testerin"},
    {"file": "VM_40_11_Little_White_Lies_Anderson_1018801.mp3", "library": "upm", "sublibrary": "Volta Music",
     "labelcode": "LC 48731", "title": "Little White Lies", "composers": "Matthew Anderson"},
]


def _master_fremd():
    from core.labelcheck import GvlMaster
    return GvlMaster([("27926", "BBC Production Music"), ("48731", "Volta Music")])


def test_to_song_rows_gvl_code_gilt_gelieferter_bleibt_als_notiz():
    """UPM liefert BBC Production Music mit LC21110, der GVL-Master führt das Label unter LC27926.
    Der GVL-Code gilt, der gelieferte bleibt sichtbar an der Zeile; fremde Codes ohne GVL-Namen bleiben."""
    songs, rep = librarytracks.to_song_rows(FOREIGN_CODE_ROWS, _master_fremd())
    assert songs[0]["labelcode"] == "LC27926" and songs[0]["labelname"] == "BBC Production Music"
    assert songs[0]["label_geliefert"] == "BBC Production Music · LC21110"
    assert rep["corrected"] == {"21110": {"label": "BBC Production Music", "gvl_code": "27926", "n": 1}}
    assert songs[1]["labelcode"] == "LC88888" and songs[1]["label_geliefert"] == ""
    assert rep["unknown_codes"] == {"88888": {"label": "Zzq Fantasy Label", "n": 1}}
    assert songs[2]["labelcode"] == "LC48731" and songs[2]["label_geliefert"] == ""


def test_import_und_heilung_fremder_codes(tmp_path):
    """Eine Installation, die die Lieferung vor der Korrektur eingepflegt hat (LC21110 an Song und Datei),
    bekommt die Korrektur beim Start (heal_library_codes) — Datei, Song und Notiz; ein zweiter Lauf ändert
    nichts. Fremde Codes ohne GVL-Namen werden gemeldet, damit die App das manuelle Label anlegt."""
    con = songsdb.open(tmp_path / "songs.db")
    old, _ = librarytracks.to_song_rows(FOREIGN_CODE_ROWS, {"48731": "Volta Music"}.get)      # alter Weg: wie geliefert
    songsdb.import_library_tracks(con, old, library="upm", date_verified="2026-10-08")
    assert songsdb.find_by_file(con, "BBCPM_117_10_The_Arrival_Of_Autumn_Merrison_1463879")["labelcode"] == "LC21110"
    rep = librarytracks.heal_library_codes(con, _master_fremd())
    assert rep["corrected"] == [{"from": "21110", "to": "27926", "label": "BBC Production Music", "files": 1, "songs": 1}]
    assert rep["unknown_codes"] == {"88888": {"label": "Zzq Fantasy Label", "n": 1, "library": "upm"}}
    hit = songsdb.find_by_file(con, "BBCPM_117_10_The_Arrival_Of_Autumn_Merrison_1463879")
    assert hit["labelcode"] == "LC27926" and hit["labelname"] == "BBC Production Music"
    assert hit["label_geliefert"] == "BBC Production Music · LC21110"
    assert songsdb.find(con, "Erika Merrison", "The Arrival Of Autumn")["labelcode"] == "LC27926"
    assert songsdb.find_by_file(con, "VM_40_11_Little_White_Lies_Anderson_1018801")["label_geliefert"] == ""
    assert librarytracks.heal_library_codes(con, _master_fremd())["corrected"] == []
    # neuer Import mit Master: gleich richtig, Notiz an Datei und Song
    con2 = songsdb.open(tmp_path / "songs2.db")
    new, _ = librarytracks.to_song_rows(FOREIGN_CODE_ROWS, _master_fremd())
    songsdb.import_library_tracks(con2, new, library="upm", date_verified="2026-10-12")
    assert songsdb.find(con2, "Erika Merrison", "The Arrival Of Autumn")["label_geliefert"] == "BBC Production Music · LC21110"
    assert librarytracks.heal_library_codes(con2, _master_fremd())["corrected"] == []
    con.close(); con2.close()


# --- Lieferungszeilen OHNE Labelcode, deren Labelname im Master eindeutig ist ---
NO_CODE_ROWS = [
    {"file": "ZQA_1_1_Nordlicht_Eins_9100001.mp3", "library": "upm", "sublibrary": "Zonk Sounds", "labelcode": "",
     "title": "Nordlicht Eins", "composers": "Erika Testerin"},
    {"file": "ZQA_1_2_Nordlicht_Zwei_9100002.mp3", "library": "upm", "sublibrary": "ZONK SOUNDS", "labelcode": "",
     "title": "Nordlicht Zwei", "composers": "Erika Testerin"},
    {"file": "ZQB_1_1_Nordlicht_Drei_9100003.mp3", "library": "upm", "sublibrary": "Doppel Records", "labelcode": "",
     "title": "Nordlicht Drei", "composers": "Erika Testerin"},
    {"file": "ZQC_1_1_Nordlicht_Vier_9100004.mp3", "library": "upm", "sublibrary": "Niemand Kennt Mich", "labelcode": "",
     "title": "Nordlicht Vier", "composers": "Erika Testerin"},
    {"file": "ZQD_1_1_Nordlicht_Fuenf_9100005.mp3", "library": "upm", "sublibrary": "", "labelcode": "",
     "title": "Nordlicht Fuenf", "composers": "Erika Testerin"},
]


def _master_namen():
    from core.labelcheck import GvlMaster
    return GvlMaster([("99011", "ZONK SOUNDS"), ("99012", "Doppel Records"), ("99013", "DOPPEL RECORDS"),
                      ("48731", "Volta Music"), ("99014", "UPM")])


def test_to_song_rows_lc_ueber_den_eindeutigen_labelnamen():
    """Eine Lieferungszeile ohne Labelcode, deren Labelname im GVL-Master unter genau EINEM Code steht, bekommt
    diesen Code mit sichtbarer Notiz. Mehrdeutige Namen (1:n) und unbekannte bleiben offen — nicht raten. Der
    Library-Name ('upm') ist kein gelieferter Labelname und zählt nicht."""
    from core.labelcheck import LC_BY_NAME_NOTE
    songs, rep = librarytracks.to_song_rows(NO_CODE_ROWS, _master_namen())
    assert [(s["labelcode"], s["labelname"], s["label_geliefert"]) for s in songs] == [
        ("LC99011", "ZONK SOUNDS", LC_BY_NAME_NOTE), ("LC99011", "ZONK SOUNDS", LC_BY_NAME_NOTE),
        ("", "", ""), ("", "", ""), ("", "", "")]
    assert LC_BY_NAME_NOTE == "LC aus dem GVL-Master über den Labelnamen"
    assert rep["by_name"] == {"Zonk Sounds": {"gvl_code": "99011", "n": 1}, "ZONK SOUNDS": {"gvl_code": "99011", "n": 1}}
    assert rep["name_ambiguous"] == {"Doppel Records": {"gvl_codes": ["99012", "99013"], "n": 1}}
    assert rep["no_code"] == 3 and rep["unknown_codes"] == {} and rep["corrected"] == {}
    # ohne Master-Namen (code2name als dict.get) bleibt es beim alten Verhalten
    old, rep_old = librarytracks.to_song_rows(NO_CODE_ROWS, {"48731": "Volta Music"}.get)
    assert all(s["labelcode"] == "" for s in old) and rep_old["no_code"] == 5 and rep_old["by_name"] == {}


def test_migration_fuer_bestehende_installationen(tmp_path):
    """Für schon eingepflegte Lieferungen: fill_codes_by_name trägt Code, GVL-Namen und Notiz in Datei- und
    songs-Zeile nach — nur Lieferungs-Zeilen ohne Code, nur bei eindeutigem Namen. Idempotent."""
    from core.labelcheck import LC_BY_NAME_NOTE
    con = songsdb.open(tmp_path / "songs.db")
    old, _ = librarytracks.to_song_rows(NO_CODE_ROWS, {}.get)                              # Stand vor der Zuordnung über den Namen
    songsdb.import_library_tracks(con, old, library="upm", date_verified="2026-10-08")
    songsdb.create_song(con, {"interpret": "Hand Eintrag", "titel": "Ohne Code", "labelname": "Zonk Sounds"})
    rep = librarytracks.fill_codes_by_name(con, _master_namen())
    assert rep["filled"] == [{"label": "ZONK SOUNDS", "code": "99011", "files": 1, "songs": 1},
                             {"label": "Zonk Sounds", "code": "99011", "files": 1, "songs": 1}]
    assert rep["ambiguous"] == {"Doppel Records": {"gvl_codes": ["99012", "99013"], "n": 1}}
    hit = songsdb.find_by_file(con, "ZQA_1_2_Nordlicht_Zwei_9100002")
    assert (hit["labelcode"], hit["labelname"], hit["label_geliefert"]) == ("LC99011", "ZONK SOUNDS", LC_BY_NAME_NOTE)
    row = songsdb.find(con, "Erika Testerin", "Nordlicht Eins")
    assert (row["labelcode"], row["labelname"], row["label_geliefert"]) == ("LC99011", "ZONK SOUNDS", LC_BY_NAME_NOTE)
    assert songsdb.find_by_file(con, "ZQB_1_1_Nordlicht_Drei_9100003")["labelcode"] == ""
    assert not songsdb.find(con, "Hand Eintrag", "Ohne Code")["labelcode"]               # keine Lieferungs-Zeile
    again = librarytracks.fill_codes_by_name(con, _master_namen())
    assert again["filled"] == []
    con.close()


def test_mit_den_master_daten():
    """Auf der UPM-Lieferung gegen den GVL-Master: die Zeilen ohne Code bekommen den Code, unter dem
    der Master ihren Labelnamen eindeutig führt; geprüft gegen die CSV, nicht aus dem Gedächtnis. Was der Master
    nicht kennt, bleibt offen."""
    import csv
    from pathlib import Path

    import pytest

    from core.labelcheck import GvlMaster, LC_BY_NAME_NOTE
    root = Path(__file__).resolve().parents[2] / "data"
    master_csv, upm = root / "GVL_Labelcodes_synced.csv", root / "library_tracks" / "upm_2026-10-archiv.csv"
    if not master_csv.exists() or not upm.exists():
        pytest.skip("Master oder UPM-Extrakt fehlt")
    with open(master_csv, encoding="utf-8") as f:
        master = GvlMaster([(r["labelcode"], r["label"]) for r in csv.DictReader(f)])
    rows = librarytracks.read_csv(upm)
    without = [r for r in rows if not (r.get("labelcode") or "").strip()]
    songs, rep = librarytracks.to_song_rows(without, master)
    assert rep["by_name"], "keine Zeile ohne Code mit eindeutigem GVL-Namen?"
    for name, info in rep["by_name"].items():
        assert master.codes_for_name(name) == [info["gvl_code"]], name
    filled = [s for s in songs if s["labelcode"]]
    assert len(filled) == sum(i["n"] for i in rep["by_name"].values())
    assert all(s["label_geliefert"] == LC_BY_NAME_NOTE and master.names(s["labelcode"][2:]) for s in filled)
    assert rep["no_code"] == len(without) - len(filled)
    for s, r in zip(songs, without):
        if not s["labelcode"]:
            assert len(master.codes_for_name(r["sublibrary"])) != 1, r["sublibrary"]
