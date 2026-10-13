"""import_library_tracks.py: extract (xlsx -> CSV) und import (CSV -> songs.db, manuelles Label für fremde Codes)."""
import json

import import_library_tracks as ilt
from core import labeldb, songsdb
from core.labeldb import build_label_db
from core.models import LabelRecord
from tests.test_librarytracks import ROWS, _xlsx


def test_extract_and_import_end_to_end(tmp_path, capsys):
    xlsx = _xlsx(tmp_path, ROWS)
    out = tmp_path / "lib" / "cezame_2026-09.csv"
    assert ilt.main(["--date", "2026-09-20", "extract", "--xlsx", str(xlsx), "--library", "cezame", "--out", str(out)]) == 0
    o = capsys.readouterr().out
    assert "4 Zeilen" in o and "Cézame" in o and "LC10347" in o and "Input/Output" in o
    assert ilt.main(["extract", "--xlsx", str(xlsx), "--library", "nope"]) == 1

    labels = tmp_path / "labels.db"
    build_label_db([LabelRecord("10347", "CMG", "", "ja", "Frederic Leibovitz Editeur Sarl.", "")], labels, "2026-06-30")
    songs = tmp_path / "songs.db"
    assert ilt.main(["--songs", str(songs), "--labels", str(labels), "--date", "2026-09-20", "import", str(out)]) == 0
    o = capsys.readouterr().out
    assert "3 neu" in o and "1 schon vorhanden" in o and "4 Dateinamen" in o and "'Cézame' -> 'CMG'" in o
    assert "LC104994 'Input/Output': nicht im GVL-Master (1 Tracks) -> manuelles Label angelegt" in o
    con = songsdb.open(songs)
    assert songsdb.count(con) == 3
    assert songsdb.find(con, "Pierre-André Athané", "A Sunny Day")["labelname"] == "CMG"
    assert songsdb.find_by_file(con, "IOP001_1")["labelcode"] == "LC104994"
    con.close()
    manual = json.loads((tmp_path / "labels_manual.json").read_text(encoding="utf-8"))
    assert manual[0]["labelcode"] == "104994" and manual[0]["label"] == "Input/Output" and "GVL-PDF-Update" in manual[0]["note"]
    lcon = labeldb.connect(labels)
    assert [r["label"] for r in labeldb.lookup_by_code(lcon, "104994")] == ["Input/Output"]
    lcon.close()
    # zweiter Import: idempotent, Label nicht doppelt
    assert ilt.main(["--songs", str(songs), "--labels", str(labels), "--date", "2026-09-21", "import", str(out)]) == 0
    assert "0 neu" in capsys.readouterr().out
    assert len(json.loads((tmp_path / "labels_manual.json").read_text(encoding="utf-8"))) == 1


def test_extract_liest_auch_upm_csv(tmp_path, capsys):
    """UPM liefert CSV statt xlsx — der Extrakt muss beides können, sonst landen die Lieferungen nie
    in data/library_tracks und damit nie im Bundle."""
    from core import librarytracks
    src = tmp_path / "music_metadata.csv"
    src.write_text(
        "sep=,\n"
        "AUDITION LINK,EDIT ID,WORK TITLE,ALBUM,ALBUM ID,TRACK ID,YEAR OF RELEASE,COMPOSERS,PUBLISHERS,"
        "LABEL NAME,GERMAN LABEL CODE,ISRC\n"
        "https://x/VM_40_11_Little_White_Lies_Anderson_1018801.mp3,1018801,Little White Lies,Witness,"
        "VM040,VM040-11,2018,Anderson Matthew,Atmosphere Music Ltd.,Volta Music,LC 48731,GB-AXQ-18-11011\n",
        encoding="utf-8")
    out = tmp_path / "upm_2026-10.csv"
    assert ilt.main(["--date", "2026-10-08", "extract", "--library", "upm",
                     "--src", str(src), "--out", str(out)]) == 0
    rows = librarytracks.read_csv(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["file"] == "VM_40_11_Little_White_Lies_Anderson_1018801.mp3"
    assert r["library"] == "upm" and r["sublibrary"] == "Volta Music"
    assert r["title"] == "Little White Lies" and r["track_number"] == "11"
    summary = capsys.readouterr().out
    assert "LC48731" in summary and "LCLC" not in summary      # Code normalisiert, kein doppeltes Präfix
    assert ilt.cmd_extract(src, "gibtsnicht", out, "2026-10") == 1


def _upm_src(path, edit_id, title):
    path.write_text(
        "sep=,\nAUDITION LINK,EDIT ID,WORK TITLE,ALBUM,ALBUM ID,TRACK ID,YEAR OF RELEASE,COMPOSERS,PUBLISHERS,"
        "LABEL NAME,GERMAN LABEL CODE,ISRC\n"
        f"https://x/VM_40_1_{title.replace(' ', '_')}_Testerin_{edit_id}.mp3,{edit_id},{title},Witness,VM040,VM040-1,2026,"
        "Erika Testerin,Atmosphere Music Ltd.,Volta Music,LC 48731,\n", encoding="utf-8")
    return path


def test_zweiter_upm_extrakt_im_selben_monat_ueberschreibt_nicht(tmp_path, monkeypatch, capsys):
    """Der Default-Name trägt das volle Datum (upm_2026-10-08.csv). Eine zweite UPM-Lieferung im selben Monat
    ersetzt die erste nicht still; sonst bekämen bestehende Installationen sie nie (Dateiname schon eingepflegt)."""
    from core import librarytracks
    out_dir = tmp_path / "library_tracks"
    monkeypatch.setattr(ilt, "DEFAULT_OUT_DIR", out_dir)
    l1 = _upm_src(tmp_path / "l1.csv", "9000001", "Erste Probe")
    l2 = _upm_src(tmp_path / "l2.csv", "9100001", "Zweite Probe")
    con = songsdb.open(tmp_path / "songs.db")
    assert ilt.main(["--date", "2026-10-08", "extract", "--library", "upm", "--src", str(l1)]) == 0
    librarytracks.ensure_imported(con, out_dir, {}.get, date_verified="2026-10-08")
    assert ilt.main(["--date", "2026-10-20", "extract", "--library", "upm", "--src", str(l2)]) == 0
    assert sorted(p.name for p in out_dir.glob("*.csv")) == ["upm_2026-10-08.csv", "upm_2026-10-20.csv"]
    librarytracks.ensure_imported(con, out_dir, {}.get, date_verified="2026-10-20")
    assert songsdb.find_by_file(con, "VM_40_1_Erste_Probe_Testerin_9000001")["titel"] == "Erste Probe"
    assert songsdb.find_by_file(con, "VM_40_1_Zweite_Probe_Testerin_9100001")["titel"] == "Zweite Probe"
    con.close()
    # derselbe Tag noch einmal: kein stilles Überschreiben, der erste Extrakt bleibt
    capsys.readouterr()
    assert ilt.main(["--date", "2026-10-20", "extract", "--library", "upm", "--src", str(l1)]) == 1
    assert "gibt es schon" in capsys.readouterr().out
    assert librarytracks.read_csv(out_dir / "upm_2026-10-20.csv")[0]["title"] == "Zweite Probe"


def test_cli_import_gvl_code_statt_manuellem_label(tmp_path, capsys):
    """Im CLI-Weg (und damit im Bundle-Build): kein manuelles Label 'BBC Production Music' LC21110 mehr,
    stattdessen der GVL-Code und eine Zeile im Bericht."""
    from core import librarytracks
    from tests.test_librarytracks import FOREIGN_CODE_ROWS
    csvp = tmp_path / "upm_2026-10-archiv.csv"
    librarytracks.write_csv(FOREIGN_CODE_ROWS, csvp)
    labels = tmp_path / "labels.db"
    build_label_db([LabelRecord("27926", "BBC Production Music", "", "Ja", "Universal Production Music GmbH", ""),
                    LabelRecord("48731", "Volta Music", "", "Ja", "Universal Production Music GmbH", "")], labels, "x")
    songs = tmp_path / "songs.db"
    assert ilt.main(["--songs", str(songs), "--labels", str(labels), "--date", "2026-10-12", "import", str(csvp)]) == 0
    o = capsys.readouterr().out
    assert "LC21110 'BBC Production Music': GVL führt den Namen unter LC27926 -> LC27926" in o
    manual = json.loads((tmp_path / "labels_manual.json").read_text(encoding="utf-8"))
    assert [(m["labelcode"], m["label"]) for m in manual] == [("88888", "Zzq Fantasy Label")]
    con = songsdb.open(songs)
    assert songsdb.find_by_file(con, "BBCPM_117_10_The_Arrival_Of_Autumn_Merrison_1463879")["labelcode"] == "LC27926"
    con.close()
