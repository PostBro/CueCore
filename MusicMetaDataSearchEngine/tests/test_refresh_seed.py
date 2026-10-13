"""Der Repo-Seed wird aus der Live-DB aufgefrischt, damit er den gesamten eigenen Korpus mitliefert.
Library-Zeilen bleiben draußen — die kommen beim Build aus data/library_tracks/, zweimal im Repo wären
sie 60.000 Zeilen doppelt."""
from __future__ import annotations

import csv

import refresh_seed
from core import songsdb
from core.labelcheck import GvlMaster


def _live(tmp_path):
    con = songsdb.open(tmp_path / "live.db")
    songsdb.upsert(con, interpret="Aphex Twin", titel="Xtal", komponist="Richard D. James",
                   gema_werknummer="123-456", quelle="MASTER")
    songsdb.upsert(con, interpret="Paul Wilkes", titel="Pensive Search", labelcode="LC35188",
                   quelle="cue_sheet (7)")
    songsdb.upsert(con, interpret="Someone", titel="Library-Track", quelle="library:upm")
    con.commit()
    con.close()
    return tmp_path / "live.db"


def test_seed_ohne_library_zeilen(tmp_path):
    src = _live(tmp_path)
    out_db, out_csv = tmp_path / "songs.db", tmp_path / "songs.csv"
    rep = refresh_seed.refresh(src, out_db=out_db, out_csv=out_csv)
    assert rep["source_rows"] == 3 and rep["seed_rows"] == 2 and rep["skipped_library"] == 1
    assert rep["written"] == 2
    con = songsdb.open(out_db)
    rows = {r["titel"]: dict(r) for r in con.execute("SELECT * FROM songs")}
    assert set(rows) == {"Xtal", "Pensive Search"}
    assert rows["Xtal"]["gema_werknummer"] == "123-456"        # Werknummer reist mit
    assert rows["Pensive Search"]["labelcode"] == "LC35188"
    assert songsdb.find(con, "Aphex Twin", "Xtal")             # Schema + nkey stimmen
    con.close()
    with open(out_csv, encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
    assert [r["titel"] for r in csv_rows] == ["Xtal", "Pensive Search"]   # nach Interpret sortiert


def test_dry_run_schreibt_nichts(tmp_path):
    src = _live(tmp_path)
    out_db = tmp_path / "songs.db"
    rep = refresh_seed.refresh(src, out_db=out_db, out_csv=tmp_path / "songs.csv", dry_run=True)
    assert rep["seed_rows"] == 2 and "written" not in rep
    assert not out_db.exists()


def _live_voll(tmp_path):
    """Live-DB mit allen Spalten befüllt, dazu eine Spalte, die das Schema der App (noch) nicht kennt."""
    con = songsdb.open(tmp_path / "live.db")
    songsdb.upsert(con, interpret="Paul Wilkes", titel="Pensive Search", komponist="Paul Wilkes", verlag="TRILOGY",
                   labelname="TRILOGY", labelcode="LC35188", isrc="GBTST0000001", album="Pensive", track_number="3",
                   gema_werknummer="123-456", quelle="cue_sheet (7)", source_type="tagger", confidence_score=0.9,
                   duration_sec=125.0, date_verified="2026-10-01")
    con.execute("UPDATE songs SET sublibraries='Testkatalog', label_geliefert='Trilogy Music · LC35188'")
    con.execute("ALTER TABLE songs ADD COLUMN extra_notiz TEXT")
    con.execute("UPDATE songs SET extra_notiz='aus einer neueren Version'")
    con.commit()
    con.close()
    return tmp_path / "live.db"


def test_seed_traegt_quelle_und_alle_spalten(tmp_path):
    """Die Spaltenliste kommt aus dem Tabellenschema der Quelle, nicht aus _ALL_FIELDS — quelle
    ('MASTER', 'cue_sheet (7)') und sublibraries gingen sonst verloren, eine neue Spalte ebenso. songs.csv bildet
    songs.db vollständig ab (alle Spalten außer id und dem abgeleiteten nkey)."""
    src = _live_voll(tmp_path)
    out_db, out_csv = tmp_path / "songs.db", tmp_path / "songs.csv"
    refresh_seed.refresh(src, out_db=out_db, out_csv=out_csv, master=GvlMaster([("35188", "TRILOGY")]), lib_names={})
    con = songsdb.open(out_db)
    row = dict(con.execute("SELECT * FROM songs").fetchone())
    # quelle kommt mit, aber ohne Dateinamen ("cue_sheet (7)" -> "cue_sheet")
    assert row["quelle"] == "cue_sheet" and row["sublibraries"] == "Testkatalog"
    assert row["extra_notiz"] == "aus einer neueren Version"
    cols = [r[1] for r in con.execute("PRAGMA table_info(songs)") if r[1] not in ("id", "nkey")]
    con.close()
    with open(out_csv, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        assert rd.fieldnames == cols and rd.fieldnames[:6] == ["interpret", "titel", "komponist", "texter", "labelname", "labelcode"]
        (csv_row,) = list(rd)
    assert csv_row == {c: "" if row[c] is None else str(row[c]) for c in cols}
    assert csv_row["confidence_score"] == "0.9" and csv_row["duration_sec"] == "125.0"


def test_auffrischen_laeuft_durch_die_bereinigung(tmp_path):
    """Damit ein späteres Auffrischen die Fehler nicht zurückbringt: LC-Format, PRO-Marker, Wiki-Reste,
    Dubletten, Code-Name-Konflikt — der Seed kommt bereinigt heraus."""
    con = songsdb.open(tmp_path / "live.db")
    rows = [("Arooj Test", "Whisky", "", "Verve", "00383"), ("Carl Test [IMRO]", "Flucht", "Carl Test [IMRO]", "", ""),
            ("Ashe Test", "Moral", "{{hlist", "", ""), ("Jake  Test", "Clear (a)", "", "", ""),
            ("Jessie Test", "Ding", "", "Columbia", "LC60459")]
    for i, t, k, ln, lc in rows:
        con.execute("INSERT INTO songs (interpret, titel, komponist, labelname, labelcode, nkey, quelle) "
                    "VALUES (?,?,?,?,?,?, 'MASTER')", (i, t, k, ln, lc, songsdb._nkey(i, t)))
    con.execute("INSERT INTO songs (interpret, titel, track_number, nkey, quelle) VALUES "
                "('Jake Test', 'Clear (a)', '7', ?, 'cue_sheet (1)')", (songsdb._nkey("Jake Test", "Clear (a)"),))
    con.commit()
    con.close()
    master = GvlMaster([("00383", "Verve"), ("60459", "Colombia (COL)"), ("00162", "COLUMBIA")])
    out_db, out_csv = tmp_path / "songs.db", tmp_path / "songs.csv"
    rep = refresh_seed.refresh(tmp_path / "live.db", out_db=out_db, out_csv=out_csv, master=master, lib_names={})
    assert rep["written"] == 5 and rep["bereinigung"]["rules"]["dubletten"] == 1
    con = songsdb.open(out_db)
    got = {r["interpret"]: (r["komponist"], r["labelname"], r["labelcode"]) for r in con.execute("SELECT * FROM songs")}
    con.close()
    assert got["Arooj Test"] == ("", "Verve", "LC00383")
    assert got["Carl Test"][0] == "Carl Test" and got["Ashe Test"][0] == ""
    assert got["Jessie Test"][1:] == ("COLUMBIA", "LC00162")
    assert "Jake Test" in got and "Jake  Test" not in got
    with open(out_csv, encoding="utf-8") as f:
        assert "[IMRO]" not in f.read()


def test_trockenlauf_zeigt_die_bereinigung(tmp_path):
    src = _live(tmp_path)
    rep = refresh_seed.refresh(src, out_db=tmp_path / "songs.db", out_csv=tmp_path / "songs.csv", dry_run=True,
                               master=GvlMaster([("35188", "TRILOGY")]), lib_names={})
    assert rep["bereinigung"]["rules"]["labelname_ergaenzt"] == 1 and not (tmp_path / "songs.db").exists()



def test_repo_seed_ist_bereinigt_und_vollstaendig():
    """Der ausgelieferte Seed (data/songs.db) ist bereinigt: ein Probelauf findet nichts mehr (PRO-Marker, nackte
    Codes, Dubletten, Colombia/Columbia, Schreibweise, '{{hlist'). Die Herkunft ist da, kein source_type
    'edl_import' führt mehr auf eine Produktion zurück, und songs.csv bildet songs.db Zeile für Zeile ab."""
    import sqlite3

    import pytest

    import cleanup_songs_db
    if not refresh_seed.SEED_DB.exists():
        pytest.skip("Seed fehlt")
    rep = cleanup_songs_db.run(refresh_seed.SEED_DB)                       # Probelauf, schreibt nichts
    assert {k: n for k, n in rep["rules"].items() if n} == {}
    con = sqlite3.connect(refresh_seed.SEED_DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM songs WHERE COALESCE(quelle,'')<>''").fetchone()[0] > 4000
        assert con.execute("SELECT COUNT(*) FROM songs WHERE source_type='edl_import'").fetchone()[0] == 0
        cols = [r[1] for r in con.execute("PRAGMA table_info(songs)") if r[1] not in refresh_seed.CSV_SKIP]
        db_rows = [["" if v is None else str(v) for v in r]
                   for r in con.execute(f"SELECT {','.join(cols)} FROM songs ORDER BY interpret, titel, id")]
    finally:
        con.close()
    with open(refresh_seed.SEED_CSV, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        assert next(rd) == cols
        assert list(rd) == db_rows



def test_neutral_quelle_entfernt_dateinamen_von_cue_sheets():
    """Ein Cue-Sheet-Dateiname als quelle würde auf die Produktion zurückführen und kommt nicht in den Seed."""
    import refresh_seed
    assert refresh_seed.neutral_quelle("Testprojekt_CueListe_XYZ(Tabelle1)") == "cue_sheet"
    assert refresh_seed.neutral_quelle("cue_sheet (13); MASTER") == "cue_sheet; MASTER"
    assert refresh_seed.neutral_quelle("library:cezame") == "library:cezame"
    assert refresh_seed.neutral_quelle(None) == "" and refresh_seed.neutral_quelle("tagger") == "tagger"


def test_seed_neutralisiert_source_type_edl_import(tmp_path):
    """Zeilen aus einem EDL-Import führen auf eine Produktion zurück: source_type wird leer wie bei den übrigen
    Cue-Sheet-Zeilen, die Cue-Dauer (gehört zum Einsatz, nicht zum Song) entfällt. Andere Zeilen bleiben."""
    con = songsdb.open(tmp_path / "live.db")
    songsdb.upsert(con, interpret="Zzq Band", titel="Testsong Eins", labelcode="LC35188", quelle="MASTER",
                   source_type="edl_import", duration_sec=4.0)
    songsdb.upsert(con, interpret="Nordwand", titel="Testsong Zwei", labelcode="LC35188", quelle="tagger",
                   source_type="tagger", duration_sec=200.0)
    con.commit()
    con.close()
    out_db, out_csv = tmp_path / "songs.db", tmp_path / "songs.csv"
    rep = refresh_seed.refresh(tmp_path / "live.db", out_db=out_db, out_csv=out_csv,
                               master=GvlMaster([("35188", "TRILOGY")]), lib_names={})
    assert rep["source_type_neutral"] == 1
    con = songsdb.open(out_db)
    rows = {r["titel"]: dict(r) for r in con.execute("SELECT * FROM songs")}
    con.close()
    assert rows["Testsong Eins"]["source_type"] == "" and rows["Testsong Eins"]["duration_sec"] is None
    assert rows["Testsong Zwei"]["source_type"] == "tagger" and rows["Testsong Zwei"]["duration_sec"] == 200.0
    assert "edl_import" not in out_csv.read_text(encoding="utf-8")
