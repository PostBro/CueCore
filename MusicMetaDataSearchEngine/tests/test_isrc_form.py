"""ISRC-Schreibweise: Datenform 12-stellig ohne Bindestriche (BVMI für Datenbanken), schriftliche Form im PDF mit
Bindestrichen. Testwerte frei erfunden."""
import csv

from core import dbexport, edl_import, songsdb, tags
from core.normalize import isrc_display, isrc_plain


def test_plain_and_display_form():
    for raw in ("DE-ZZQ-26-00001", "dezzq2600001", "ISRC DE-ZZQ-26-00001", " DE ZZQ 26 00001 "):
        assert isrc_plain(raw) == "DEZZQ2600001", raw
        assert isrc_display(raw) == "DE-ZZQ-26-00001", raw
    for bad in ("", "kein isrc", "DE-ZZQ-26-0001", "DEZZQ26000011"):      # kein gültiger ISRC: bleibt
        assert isrc_plain(bad) == bad.strip() and isrc_display(bad) == bad.strip()


def test_cue_sheet_csv_plain_pdf_hyphenated(tmp_path):
    cards = [{"artist": "Zzq", "title": "Nordwand", "isrc": "DE-ZZQ-26-00001", "duration_sec": 3}]
    col = edl_import.EXPORT_COLUMNS.index("ISRC")
    assert edl_import._export_rows(cards)[0][0][col] == "DEZZQ2600001"
    assert edl_import._export_rows(cards, pdf=True)[0][0][col] == "DE-ZZQ-26-00001"
    edl_import.export_pdf(cards, tmp_path / "c.pdf")
    import fitz
    assert "DE-ZZQ-26-00001" in "".join(fitz.open(tmp_path / "c.pdf")[0].get_text().split())


def test_db_export_and_tag_use_the_data_form(tmp_path):
    dbexport.export_csv([{"interpret": "Zzq", "titel": "Nordwand", "isrc": "de-zzq-26-00001"}], tmp_path / "s.csv")
    with open(tmp_path / "s.csv", encoding="utf-8-sig") as f:
        assert next(csv.DictReader(f))["ISRC"] == "DEZZQ2600001"
    p = tmp_path / "x.mp3"
    p.write_bytes(b"")
    tags.write_tags(p, {"isrc": "DE-ZZQ-26-00001"})
    assert tags.read_tags(p)["isrc"] == "DEZZQ2600001"


def test_songsdb_stores_plain_and_heals_old_rows(tmp_path):
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="Zzq", titel="Nordwand", isrc="DE-ZZQ-26-00001")
    assert songsdb.find_row(con, "Zzq", "Nordwand")["isrc"] == "DEZZQ2600001"
    con.execute("INSERT INTO songs (interpret, titel, nkey, isrc) VALUES ('Zzq', 'Alt', 'zzq|alt', 'DE-ZZQ-26-00002')")
    con.execute("INSERT INTO songs (interpret, titel, nkey, isrc) VALUES ('Zzq', 'Kaputt', 'zzq|kaputt', 'DE-ZZQ-1')")
    con.commit()
    assert songsdb.normalize_stored_isrcs(con) == 1
    isrc = lambda t: con.execute("SELECT isrc FROM songs WHERE titel=?", (t,)).fetchone()[0]   # noqa: E731
    assert isrc("Alt") == "DEZZQ2600002" and isrc("Kaputt") == "DE-ZZQ-1"            # ungültig: bleibt
    assert songsdb.normalize_stored_isrcs(con) == 0
