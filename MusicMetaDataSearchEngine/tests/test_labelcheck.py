"""core/labelcheck.py: gelieferte Codes gegen den GVL-Master (für Lieferungen und fremde Listen)."""
import json

from core import labelcheck, labeldb
from core.labeldb import build_label_db
from core.models import LabelRecord

MASTER = labelcheck.GvlMaster([
    ("27926", "BBC Production Music"), ("86328", "Nova Production Music"),
    ("10347", "CMG"), ("04952", "CEZAME"), ("24584", "MOM & POP"), ("00121", "Atlantic"),
    ("04369", "BRUTON MUSIC"), ("00617", "FOCUS MUSIC LIBRARY"), ("18626", "Master Source"),
    ("07552", "Republic Records"), ("57251", "Republic Records"),
])


def test_check_label_statuses():
    c = labelcheck.check_label
    assert c("LC 21110", "BBC Production Music", MASTER) == {
        "status": "corrected", "code": "27926", "label": "BBC Production Music", "gvl_codes": ["27926"]}
    assert c("26766", "Nova Production Music", MASTER)["code"] == "86328"
    assert c("88888", "Zzq Fantasy Label", MASTER) == {
        "status": "unknown", "code": "88888", "label": "Zzq Fantasy Label", "gvl_codes": []}
    assert c("99999", "Republic Records", MASTER)["status"] == "ambiguous"          # 1:n — nicht raten
    # Code im Master: der Name ersetzt ihn nie (Cézame -> LC10347, obwohl 'CEZAME' unter LC04952 steht)
    r = c("10347", "Cézame", MASTER)
    assert r["status"] == "renamed" and r["code"] == "10347" and r["label"] == "CMG"
    assert c("24584", "Atlantic", MASTER)["status"] == "renamed"
    assert c("LC00121", "atlantic", MASTER) == {"status": "ok", "code": "00121", "label": "Atlantic", "gvl_codes": ["00121"]}
    assert c("00121", "", MASTER)["label"] == "Atlantic"                             # leerer Name: GVL-Name
    assert c("", "Atlantic", MASTER)["status"] == "no_code"


def test_names_match_tolerates_spelling_not_content():
    m = labelcheck.names_match
    assert m("Bruton", ["BRUTON MUSIC"]) and m("Focus Music", ["FOCUS MUSIC LIBRARY"]) and m("MasterSource", ["Master Source"])
    assert not m("Atlantic", ["MOM & POP"]) and not m("MYMA", ["Stereoscopic"]) and not m("Megatrax", ["Track Distillery"])


def test_master_from_db_ignores_manual_labels(tmp_path):
    """Ein früher automatisch angelegtes LC21110 (source 'manuell') darf nicht als bekannt gelten."""
    db = tmp_path / "labels.db"
    build_label_db([LabelRecord("27926", "BBC Production Music", "", "Ja", "Universal Production Music GmbH", "")], db, "x")
    con = labeldb.connect(db)
    labeldb.add_manual_labels(con, [{"labelcode": "21110", "label": "BBC Production Music"}])
    m = labelcheck.GvlMaster.from_db(con)
    assert m("21110") is None and m("27926") == "BBC Production Music"
    assert labelcheck.check_label("21110", "BBC Production Music", m)["code"] == "27926"
    con.close()


def test_manual_labels_add_is_idempotent_and_obsolete_auto_labels_go(tmp_path):
    db = tmp_path / "labels.db"
    build_label_db([LabelRecord("27926", "BBC Production Music", "", "Ja", "", "")], db, "x")
    con = labeldb.connect(db)
    mp = tmp_path / "labels_manual.json"
    mp.write_text(json.dumps([{"labelcode": "21110", "label": "BBC Production Music", "note":
                               "Aus Library-Lieferung upm (upm_2026-10-archiv.csv), 16 Tracks; nicht im GVL-Master"},
                              {"labelcode": "21110", "label": "BBC Production Music Eigene", "note": "vom Anbieter bestätigt"}]),
                  encoding="utf-8")
    entries = labelcheck.manual_label_entries({"88888": {"label": "Zzq Fantasy Label", "n": 3}},
                                              library="upm", source="music_metadata.csv", stamp="2026-10-12")
    assert "Aus Library-Lieferung upm (music_metadata.csv), 3 Tracks" in entries[0]["note"]
    assert [e["labelcode"] for e in labelcheck.add_manual_labels(con, mp, entries)] == ["88888"]
    assert labelcheck.add_manual_labels(con, mp, entries) == []                    # zweiter Lauf: nichts doppelt
    assert [r["label"] for r in labeldb.lookup_by_code(con, "88888")] == ["Zzq Fantasy Label"]
    m = labelcheck.GvlMaster.from_db(con)
    rows = json.loads(mp.read_text(encoding="utf-8"))
    old = labelcheck.obsolete_delivery_labels(rows, m)
    assert [(e["labelcode"], e["label"]) for e in old] == [("21110", "BBC Production Music")]   # von Hand angelegte bleiben
    assert labelcheck.remove_manual_labels(con, mp, old) == 1
    left = json.loads(mp.read_text(encoding="utf-8"))
    assert sorted(e["label"] for e in left) == ["BBC Production Music Eigene", "Zzq Fantasy Label"]
    con.close()
