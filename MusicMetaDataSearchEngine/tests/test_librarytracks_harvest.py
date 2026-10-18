"""Library-Lieferung als Harvest-Media-Export (librarytracks.read_harvest_csv). Testdaten frei erfunden."""
import csv

import pytest

from core import librarytracks, songsdb

COLS = ["LIBRARY: Code", "LIBRARY: Name", "ALBUM: Code", "ALBUM: Title", "ALBUM: Release Date", "TRACK: Title",
        "TRACK: Number", "TRACK: Version", "TRACK: Duration", "TRACK: Audio Filename", "TRACK: Custom Audio Filename",
        "TRACK: Harvest Filenames",
        "WRITER:1: First Name", "WRITER:1: Middle Name", "WRITER:1: Last Name", "WRITER:1: Capacity",
        "WRITER:2: First Name", "WRITER:2: Middle Name", "WRITER:2: Last Name", "WRITER:2: Capacity",
        "PUBLISHER:1: Name", "CODE: ISWC", "CODE: ISRC", "CODE: GEMA WORK NUMBER", "CODE: GVL Label Code"]
ROW = ["ZZQ", "Zzq Library", "ZZQ 001", "ZZQ 001 Nordwand", "2026-01-01", "NORDWAND", "1", "Main", "156",
       "ZZQ_001_TK001_NORDWAND_Erika_Testerin", "ZZQ_ZZQ 001_01_Nordwand_Erika Testerin_Main",
       "ZZQ_001_TK001_NORDWAND_Erika_Testerin,_Max_Beispiel",
       "Erika", "", "Testerin", "Composer / Author", "Max", "B.", "Beispiel", "Arranger",
       "Zzq Verlag", "T-000.000.001-0", "DEZZQ0000001", "10000001", "99110"]


def _write(tmp_path, rows):
    p = tmp_path / "harvest.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    return p


def test_reads_writers_codes_and_all_filenames(tmp_path):
    r = librarytracks.read_harvest_csv(_write(tmp_path, [ROW]), "zzq")[0]
    assert r["library"] == "zzq" and r["sublibrary"] == "Zzq Library" and r["labelcode"] == "99110"
    assert r["title"] == "NORDWAND" and r["artist"] == "" and r["composers"] == "Erika Testerin"
    assert r["arranger"] == "Max B. Beispiel" and r["publisher"] == "Zzq Verlag"
    assert r["catno"] == "ZZQ 001" and r["duration_sec"] == "156" and r["gema"] == "10000001"
    assert r["file"] == "ZZQ_001_TK001_NORDWAND_Erika_Testerin"
    assert r["file_alt"].split("|") == ["ZZQ_ZZQ 001_01_Nordwand_Erika Testerin_Main",
                                        "ZZQ_001_TK001_NORDWAND_Erika_Testerin,_Max_Beispiel"]


def test_rejects_other_formats(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Artist,Title\nA,B\n", encoding="utf-8")
    with pytest.raises(ValueError):
        librarytracks.read_harvest_csv(p, "zzq")


def test_every_filename_finds_the_track(tmp_path):
    rows = librarytracks.read_harvest_csv(_write(tmp_path, [ROW]), "zzq")
    out = tmp_path / "zzq_2026-01-01.csv"
    librarytracks.write_csv(rows, out)
    back = librarytracks.read_csv(out)                     # Roundtrip über den Extrakt behält file_alt
    songs, _ = librarytracks.to_song_rows(back, lambda c: {"99110": "ZZQ LABEL"}.get(c))
    con = songsdb.open(tmp_path / "songs.db")
    rep = songsdb.import_library_tracks(con, songs, library="zzq", date_verified="2026-01-01")
    assert rep["inserted"] == 1 and rep["files"] == 3
    for name in ("ZZQ_001_TK001_NORDWAND_Erika_Testerin.wav", "ZZQ_ZZQ 001_01_Nordwand_Erika Testerin_Main.mp3",
                 "ZZQ_001_TK001_NORDWAND_Erika_Testerin,_Max_Beispiel"):
        hit = songsdb.find_by_file(con, name)
        assert hit and hit["titel"] == "NORDWAND" and hit["interpret"] == "Erika Testerin", name
        assert hit["labelcode"] == "LC99110"
    con.close()
