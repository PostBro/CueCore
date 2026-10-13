import csv

from openpyxl import Workbook

from core import cuelist, songsdb
from core.normalize import normalize_labelcode
from core.cuelist import guess_list_mapping, parse_duration, rows_to_events, seconds_to_tc


def test_parse_duration_formats():
    assert parse_duration("83") == 83.0
    assert parse_duration("83,5") == 83.5
    assert parse_duration("1:23") == 83.0
    assert parse_duration("0:01:23") == 83.0
    assert parse_duration("00:01:23:12", fps=25) == 83.48
    assert parse_duration("1:23.5") == 83.5
    assert parse_duration("83 s") == 83.0 and parse_duration("83 Sek.") == 83.0
    assert parse_duration("") is None and parse_duration("lang") is None


def test_seconds_to_tc_rounds_up_to_frames():
    assert seconds_to_tc(83.0) == "00:01:23:00"
    assert seconds_to_tc(83.48, 25) == "00:01:23:12"
    assert seconds_to_tc(0.01, 25) == "00:00:00:01"          # nie unter der Liste
    assert seconds_to_tc(3600, 25) == "01:00:00:00"


def test_guess_list_mapping_knows_clip_duration_and_song_columns():
    mp = guess_list_mapping(["Artist", "Title", "Dateiname", "Dauer", "Komponist", "LC", "Track-Nr."])
    assert mp["interpret"] == "Artist" and mp["titel"] == "Title" and mp["clip"] == "Dateiname"
    assert mp["duration"] == "Dauer" and mp["komponist"] == "Komponist" and mp["labelcode"] == "LC"
    assert guess_list_mapping(["TC In", "TC Out", "Clip Name"]) == {"clip": "Clip Name", "tc_in": "TC In", "tc_out": "TC Out"}


def test_rows_to_events_builds_clip_names_and_durations():
    mp = guess_list_mapping(["Interpret", "Titel", "Clip", "Dauer", "TC In", "TC Out"])
    rows = [
        {"Interpret": "AURORA", "Titel": "Runaway", "Clip": "", "Dauer": "1:23", "TC In": "", "TC Out": ""},
        {"Interpret": "", "Titel": "", "Clip": "CEZ4030_1.mp3", "Dauer": "", "TC In": "01:00:00:00", "TC Out": "01:00:10:00"},
        {"Interpret": "", "Titel": "Nur Titel", "Clip": "", "Dauer": "kaputt", "TC In": "", "TC Out": ""},
        {"Interpret": "", "Titel": "", "Clip": "", "Dauer": "", "TC In": "", "TC Out": ""},
    ]
    events, extras, warnings = rows_to_events(rows, mp, 25)
    assert [e.clip_name for e in events] == ["AURORA - Runaway", "CEZ4030_1.mp3", "Nur Titel"]
    assert [e.gema_duration_sec(25) for e in events] == [83, 10, 0]
    assert [e.event_num for e in events] == ["001", "002", "003"]
    assert extras[0]["_artist"] == "AURORA" and extras[1]["_artist"] == ""
    assert any("Zeile 3" in w and "kaputt" in w for w in warnings) and any("1 leere" in w for w in warnings)


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)


def test_import_list_csv_matches_db_and_prefills_list_columns(tmp_path):
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", labelname="Decca", labelcode="00171",
                   komponist="Aurora Aksnes")
    con.commit()
    p = tmp_path / "cues.csv"
    _write_csv(p, ["Artist", "Title", "Länge", "Composer", "LC", "Publisher"], [
        ["AURORA", "Runaway", "2:05", "", "", ""],                       # in der DB
        ["Nobody", "Unknown Song", "30", "X. Composer", "00121", "Edition Y"],   # LC aus Liste, im Master
        ["Nobody", "Other Song", "30", "", "99999", ""],                 # LC nicht im Master
        ["", "Nur Titel", "10", "", "", ""],                             # ohne Interpret
    ])
    master = {"00171": ("Decca", ""), "00121": ("Atlantic", "R6")}
    res = cuelist.import_list(p, con, fps=25, label_lookup=lambda c: master.get(c, ("", "")))
    assert "error" not in res and res["rows"] == 4 and res["events"] == 4
    assert res["mapping"]["duration"] == "Länge" and res["mapping"]["verlag"] == "Publisher"
    c = res["cards"]
    assert c[0]["status"] == "found_in_db" and normalize_labelcode(c[0]["labelcode"]) == "00171" and c[0]["duration_sec"] == 125
    assert c[0]["komponist"] == "Aurora Aksnes"                                  # DB schlägt leere Liste
    assert c[1]["status"] == "list_lc" and c[1]["labelcode"] == "00121" and c[1]["labelname"] == "Atlantic"
    assert c[1]["komponist"] == "X. Composer" and c[1]["verlag"] == "Edition Y" and c[1]["rechterueckrufe"] == "R6"
    assert c[2]["labelcode"] == "" and any("LC99999" in n for n in c[2]["notes"])
    assert c[2]["status"] == "missing_no_resolver"                               # kein Resolver übergeben
    assert c[3]["artist"] == "" and c[3]["title"] == "Nur Titel" and c[3]["status"] == "unparsable_clip_name"
    assert songsdb.find(con, "Nobody", "Unknown Song") is None                   # Liste schreibt nicht in die DB


def test_import_list_keeps_list_duration_over_songs_db_duration(tmp_path):
    """Die Dauer aus der Liste gilt, auch wenn die songs.db-Zeile eine eigene Dauer trägt."""
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", labelname="Decca", labelcode="LC00171", duration_sec=125)
    con.commit()
    p = tmp_path / "cues.csv"
    _write_csv(p, ["Interpret", "Titel", "Dauer"], [["AURORA", "Runaway", "1:00"]])
    c = cuelist.import_list(p, con, fps=25)["cards"][0]
    assert c["status"] == "found_in_db" and c["duration_sec"] == 60


def test_import_list_xlsx_with_clip_column_uses_edl_parser(tmp_path):
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="AURORA", titel="Runaway", labelname="Decca", labelcode="LC00171")
    con.commit()
    wb = Workbook(); ws = wb.active
    ws.append(["Clip Name", "Duration"])
    ws.append(["/audio/AURORA - Runaway.wav", 12.0])
    ws.append(["Birdy - Skinny Love - [ATLANTIC_LC00121].mp3", "00:00:04:00"])
    ws.append([None, None])
    p = tmp_path / "cues.xlsx"; wb.save(p)
    res = cuelist.import_list(p, con, fps=25, label_lookup=lambda c: {"00121": ("Atlantic", "")}.get(c, ("", "")))
    c = res["cards"]
    assert len(c) == 2 and c[0]["status"] == "found_in_db" and c[0]["duration_sec"] == 12
    assert c[1]["labelcode"] == "00121" and c[1]["labelname"] == "Atlantic" and c[1]["duration_sec"] == 4
    assert c[0]["clip_name"] == "/audio/AURORA - Runaway.wav"


def test_import_list_rejects_unusable_files(tmp_path):
    con = songsdb.open(tmp_path / "songs.db")
    p = tmp_path / "x.csv"
    _write_csv(p, ["Foo", "Bar"], [["1", "2"]])
    assert "Keine Spalte" in cuelist.import_list(p, con)["error"]
    _write_csv(p, ["Artist", "Title"], [["", ""]])
    assert "Keine Zeilen" in cuelist.import_list(p, con)["error"]
    (tmp_path / "x.txt").write_text("a;b", encoding="utf-8")
    assert "Format" in cuelist.import_list(tmp_path / "x.txt", con)["error"]
