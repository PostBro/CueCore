"""Korrekturen offensichtlicher Fehler der GVL-Quelle (data/gvl_name_fixes.csv). Testwerte frei erfunden,
bis auf die echte Korrektur im Repo."""
import csv
from pathlib import Path

from core import gvlsync, labeldb, songsdb
from core.labeldb import build_label_db
from core.models import LabelRecord
from reconcile.csv_io import apply_name_fixes, read_name_fixes
from tests.test_gvlsync import _layout

DATA = Path(__file__).resolve().parents[2] / "data"


def _fixes(tmp_path, rows):
    p = tmp_path / "gvl_name_fixes.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["labelcode", "label_gvl", "label", "grund"])
        w.writerows(rows)
    return p


def test_repo_fix_black_label_music_is_applied_to_the_master():
    fixes = read_name_fixes(DATA / "gvl_name_fixes.csv")
    assert fixes["91766"] == ("Black Label Musi", "Black Label Music")
    with open(DATA / "GVL_Labelcodes_synced.csv", encoding="utf-8") as f:
        assert {r["labelcode"]: r["label"] for r in csv.DictReader(f)}["91766"] == "Black Label Music"


def test_apply_only_where_the_source_error_still_stands(tmp_path):
    fixes = read_name_fixes(_fixes(tmp_path, [["99110", "Zzq Musi", "Zzq Music", "abgeschnitten"]]))
    recs = [LabelRecord("99110", "Zzq Musi", "", "", "", ""), LabelRecord("99111", "Zzq Musi", "", "", "", "")]
    assert apply_name_fixes(recs, fixes) == 1 and [r.label for r in recs] == ["Zzq Music", "Zzq Musi"]
    later = [LabelRecord("99110", "ZZQ MUSIC LTD", "", "", "", "")]      # GVL schreibt es selbst anders: GVL gilt
    assert apply_name_fixes(later, fixes) == 0 and later[0].label == "ZZQ MUSIC LTD"
    assert read_name_fixes(tmp_path / "fehlt.csv") == {}


def test_pdf_update_keeps_the_fix_and_shows_no_change(tmp_path):
    p = _layout(tmp_path)                                       # PDF trägt 00001 „Frisches Label“
    fx = _fixes(tmp_path, [["00001", "Frisches Label", "Frisches Label Music", "abgeschnitten"]])
    prev = gvlsync.preview(p["pdf"], p["csv"], name_fixes_path=fx)
    assert prev["sample_neu"][0]["label"] == "Frisches Label Music"
    gvlsync.merge(prev["token"], csv_path=p["csv"], data_dir=p["data_dir"], docs_dir=p["docs_dir"],
                  labels_db=tmp_path / "labels.db")
    assert "Frisches Label Music" in p["csv"].read_text(encoding="utf-8")
    again = gvlsync.preview(p["pdf"], p["csv"], name_fixes_path=fx)    # zweites Update: kein „geändert“
    assert again["counts"]["geaendert"] == 0 and again["counts"]["neu"] == 0


def test_existing_databases_heal_once(tmp_path):
    fixes = {"99110": ("Zzq Musi", "Zzq Music")}
    db = tmp_path / "labels.db"
    build_label_db([LabelRecord("99110", "Zzq Musi", "", "", "Zzq GmbH", "")], db, "2026-01-01")
    con = labeldb.connect(db)
    calls = []
    assert labeldb.apply_name_fixes(con, fixes, before_write=lambda: calls.append(1)) == 1 and calls == [1]
    assert con.execute("SELECT label FROM labels WHERE labelcode='99110'").fetchone()[0] == "Zzq Music"
    assert labeldb.apply_name_fixes(con, fixes, before_write=lambda: calls.append(1)) == 0 and calls == [1]
    con.close()
    s = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(s, interpret="Zzq", titel="Nordwand", labelname="Zzq Musi", labelcode="LC99110")
    s.commit()
    assert songsdb.apply_label_name_fixes(s, fixes) == 1
    assert songsdb.find_row(s, "Zzq", "Nordwand")["labelname"] == "Zzq Music"
    assert songsdb.apply_label_name_fixes(s, fixes) == 0
