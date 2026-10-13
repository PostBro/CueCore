"""UPM-Metadaten-CSV (liegt jedem Download bei). Zwei Eigenheiten: Excels 'sep=,' in Zeile 1
und der Dateiname steckt nur im AUDITION LINK. Die Edit-ID am Dateiende ist der Schlüssel fürs Cue-Sheet."""
import csv
from pathlib import Path

import pytest

from core import ingest, librarylists, librarytracks, songsdb

HEADER = ("AUDITION LINK,EDIT ID,WORK TITLE,ALBUM,ALBUM ID,TRACK ID,YEAR OF RELEASE,COMPOSERS,PUBLISHERS,"
          "LABEL NAME,GERMAN LABEL CODE,ISRC")
ROW1 = ('https://example.invalid/VM/VM040/x/VM_40_11_Little_White_Lies_Anderson_Moenks_1018801.mp3'
        '?response-content-type=audio/mpeg,1018801,Little White Lies,Witness,VM040,VM040-11,2018,'
        '"Matthew Anderson [PRS] 50%/Florian Moenks [GEMA] 50%","Atmosphere Music Ltd. [PRS] 100%",'
        'Volta Music,LC 48731,GB-AXQ-18-11011')
ROW2 = ('https://example.invalid/HYPR/HYPR092/y/HYPR_92_1_Hello_Sunshine_Fort_Lester_2262970.mp3,'
        '2262970,Hello Sunshine (Trailer Version),Uncovered,HYPR092,HYPR092-1,2026,'
        '"Anthony Jason Fort [ASCAP] 25%/Kendel Lester [BMI] 75%","Hyperscore Productions [ASCAP] 100%",'
        'Hypersonic Music,LC 97100,QZ-Q88-26-01022')


def _csv(tmp_path, *, sep_line=True, rows=(ROW1, ROW2)) -> Path:
    p = tmp_path / "music_metadata.csv"
    p.write_text(("sep=,\n" if sep_line else "") + HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _code2name(code):
    return {"48731": "Volta Music", "97100": "Hypersonic Music"}.get(code)


def test_reads_upm_export_with_and_without_sep_line(tmp_path):
    for sep in (True, False):
        rows = librarytracks.read_upm_csv(_csv(tmp_path, sep_line=sep))
        assert len(rows) == 2, sep
    r = rows[0]
    assert r["file"] == "VM_40_11_Little_White_Lies_Anderson_Moenks_1018801.mp3"   # Query-String abgeschnitten
    assert r["edit_id"] == "1018801" and r["labelcode"] == "LC 48731"
    assert r["composers"] == "Matthew Anderson; Florian Moenks"                    # PRO-Marker und Anteile raus
    assert r["publisher"] == "Atmosphere Music Ltd." and r["track_number"] == "11"
    assert r["album"] == "Witness" and r["catno"] == "VM040" and r["artist"] == ""


def test_composer_becomes_the_artist_when_delivery_has_none(tmp_path):
    """Bei Production Libraries ohne Interpret IST der Komponist der Interpret —
    so läuft die GEMA-Meldung. Er bleibt zusätzlich im Komponist-Feld."""
    songs, rep = librarytracks.to_song_rows(librarytracks.read_upm_csv(_csv(tmp_path)), _code2name)
    assert rep["unknown_codes"] == {} and rep["no_code"] == 0
    s = songs[0]
    assert s["interpret"] == "Matthew Anderson; Florian Moenks" == s["komponist"]
    assert s["titel"] == "Little White Lies" and s["labelcode"] == "LC48731"
    assert songs[1]["interpret"] == "Anthony Jason Fort; Kendel Lester"
    # gelieferter Interpret gewinnt weiter (Cezame) und wird gedreht
    rows = librarytracks.read_upm_csv(_csv(tmp_path))
    rows[0]["artist"] = "Athané, Pierre-André"
    assert librarytracks.to_song_rows(rows, _code2name)[0][0]["interpret"] == "Pierre-André Athané"
    # ohne Komponist UND ohne Interpret: Label als letzter Ausweg (Schlüssel darf nicht leer sein)
    rows[0]["artist"] = ""; rows[0]["composers"] = ""
    assert librarytracks.to_song_rows(rows, _code2name)[0][0]["interpret"] == "Volta Music"


def test_edit_id_matches_cue_with_different_catalogue_spelling(tmp_path):
    """Lieferung 'VM_40_11_…_1018801', EDL-Clip 'UPM_VM40_11_…_1018801': verschiedene Schreibweise, gleiche
    Nummer am Ende — darüber trifft die Zuordnung trotzdem."""
    con = songsdb.open(tmp_path / "songs.db")
    songs, _ = librarytracks.to_song_rows(librarytracks.read_upm_csv(_csv(tmp_path)), _code2name)
    songsdb.import_library_tracks(con, songs, library="upm", date_verified="2026-10-08")
    exact = songsdb.find_by_file(con, "VM_40_11_Little_White_Lies_Anderson_Moenks_1018801.mp3")
    viaid = songsdb.find_by_file(con, "UPM_VM40_11_LITTLE_WHITE_LIES_ANDERSON_MOENKS_1018801.MP3")
    assert exact and viaid and exact["id"] == viaid["id"] and viaid["labelcode"] == "LC48731"
    assert songsdb.find_by_file(con, "UPM_XX_1_SOMETHING_9999999.mp3") is None      # fremde Nummer: nichts
    assert songsdb._edit_id("A_B_1018801.mp3") == "1018801" and songsdb._edit_id("A_B_12.mp3") == ""


def test_ingest_routes_delivery_and_list_upload_points_the_right_way(tmp_path):
    p = _csv(tmp_path)
    assert ingest.library_delivery_kind(p) == "upm"
    assert ingest.library_delivery_kind(tmp_path / "fehlt.csv") == ""
    pv = ingest.preview_library_delivery(p, _code2name)
    assert pv["rows"] == 2 and pv["labels"]["LC48731"]["label"] == "Volta Music" and len(pv["sample"]) == 2
    con = songsdb.open(tmp_path / "s.db")
    res = ingest.import_library_delivery(con, p, _code2name, date_verified="2026-10-08")
    assert res["inserted"] == 2 and res["files"] == 2 and res["library"] == "upm"
    # Im Kürzel-Listen-Upload gelandet: Hinweis statt Spalten-Meckerei
    err = librarylists.read_user_list(p)["error"]
    assert "Track-Lieferung" in err and "Track-Datenbank" in err


def test_plain_csv_still_goes_through_column_mapping(tmp_path):
    """Normale Tabellen laufen unverändert über das Mapping — und 'sep=,' stört dort jetzt auch nicht mehr."""
    p = tmp_path / "liste.csv"
    p.write_text("sep=,\nInterpret,Titel,Label\nAURORA,Runaway,Decca\n", encoding="utf-8")
    assert ingest.library_delivery_kind(p) == ""
    pv = ingest.preview_file(p)
    assert pv["rows"] == 1 and pv["required_ok"] and pv["mapping"]["interpret"] == "Interpret"


def test_folder_scan_finds_all_deliveries_and_is_idempotent(tmp_path):
    """UPM legt bei JEDEM Download eine music_metadata.csv dazu, sie sammeln sich in den
    Download-Ordnern. Der Ordner-Scan findet sie rekursiv; ein zweiter Lauf legt nichts doppelt an."""
    (tmp_path / "Album A").mkdir(); (tmp_path / "tief/er/Album B").mkdir(parents=True)
    _csv(tmp_path / "Album A", rows=(ROW1,))
    _csv(tmp_path / "tief/er/Album B", rows=(ROW2,))
    (tmp_path / "egal.csv").write_text("Interpret,Titel\nX,Y\n", encoding="utf-8")   # keine Lieferung
    found = ingest.find_deliveries(tmp_path)
    assert len(found) == 2 and all(f.name == "music_metadata.csv" for f in found)
    pv = ingest.preview_delivery_folder(tmp_path, _code2name)
    assert pv["found"] == 2 and pv["rows"] == 2 and set(pv["labels"]) == {"LC48731", "LC97100"}
    con = songsdb.open(tmp_path / "songs.db")
    r1 = ingest.import_delivery_folder(con, tmp_path, _code2name, date_verified="2026-10-08")
    assert r1["files"] == 2 and r1["inserted"] == 2 and r1["updated"] == 0
    r2 = ingest.import_delivery_folder(con, tmp_path, _code2name, date_verified="2026-10-08")
    assert r2["inserted"] == 0 and r2["updated"] == 2                    # idempotent
    assert songsdb.count(con) == 2
    assert ingest.find_deliveries(tmp_path / "gibtsnicht") == []


ROW_KOMMA = ('https://example.invalid/VM/VM041/z/VM_41_2_Nordwand_Zwei_Testerin_1018900.mp3,'
             '1018900,Nordwand Zwei,Witness,VM041,VM041-2,2026,"Testerin, Erika","Atmosphere Music Ltd.",'
             'Volta Music,LC 48731,GB-AXQ-26-00001')


def test_komponist_nachname_vorname_wird_gedreht_nicht_geteilt(tmp_path):
    """'Testerin, Erika' wurde am Komma geteilt — Interpret und Komponist hießen 'Testerin; Erika', also
    zwei Personen. Eine Person im Format 'Nachname, Vorname' wird gedreht; zwei volle Namen bleiben zwei."""
    rows = librarytracks.read_upm_csv(_csv(tmp_path, rows=(ROW_KOMMA, ROW1)))
    songs, _ = librarytracks.to_song_rows(rows, _code2name)
    assert songs[0]["interpret"] == "Erika Testerin" == songs[0]["komponist"]
    assert songs[1]["komponist"] == "Matthew Anderson; Florian Moenks"                 # bleibt zwei Personen
    f = tmp_path / "liste.csv"
    f.write_text('Titel;Komponist;Texter\nNordwand Drei;Testerin, Erika;"Muster, Max; Doe, Jane"\n', encoding="utf-8")
    v = ingest.preview_file(f)["sample"][0]
    assert v["komponist"] == "Erika Testerin" and v["texter"] == "Max Muster; Jane Doe"
    assert librarytracks.flip_person_list("Anna Bell, Tom Roe") == "Anna Bell, Tom Roe"   # zwei volle Namen: so lassen
