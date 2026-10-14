"""Tests für die GVL-PDF-Reconcile-UI-Schicht (core/gvlsync): Upload -> Diff-Vorschau
-> Merge -> labels.db. Nutzt eine synthetische GVL-PDF (gleiche Spaltenanker wie der
Parser) statt des 16-MB-Masters."""
import json
import sqlite3

import pymupdf

from core import gvlsync

X = {"labelcode": 17, "label": 114, "kurzname": 211, "plm": 308, "hersteller": 362, "rr": 460}


def _make_pdf(path):
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": "D:20260630080632+02'00'"})
    p = doc.new_page(width=612, height=792)
    for col, head in [("labelcode", "Labelcode"), ("label", "Label"), ("kurzname", "Kurzname"),
                      ("plm", "PLM"), ("hersteller", "Hersteller"), ("rr", "Rechterückrufe")]:
        p.insert_text((X[col], 85), head, fontsize=9)
    # ein neuer Datensatz, den der Master noch nicht kennt
    p.insert_text((X["labelcode"], 110), "00001", fontsize=9)
    p.insert_text((X["label"], 110), "Frisches Label", fontsize=9)
    p.insert_text((X["hersteller"], 110), "Firma GmbH", fontsize=9)
    doc.save(str(path))
    doc.close()


def _write_master(data_dir, version="2026-06-23"):
    csv_path = data_dir / "GVL_Labelcodes_synced.csv"
    meta_path = data_dir / "GVL_Labelcodes_synced.meta.json"
    # ein Alt-Code (00002), der im neuen PDF fehlt -> ENTFERNT im Diff
    csv_path.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n"
                        "00002,Altes Label,,,Alt GmbH,\n", encoding="utf-8")
    meta_path.write_text(json.dumps({"source_version": version, "row_count": 1}), encoding="utf-8")
    return csv_path


def _layout(tmp_path):
    data_dir = tmp_path / "data"; docs_dir = tmp_path / "docs"
    data_dir.mkdir(); docs_dir.mkdir()
    pdf = tmp_path / "GVL-Labelcodes.pdf"; _make_pdf(pdf)
    csv_path = _write_master(data_dir)
    return {"pdf": pdf, "csv": csv_path, "data_dir": data_dir, "docs_dir": docs_dir}


def test_preview_reports_diff_without_writing(tmp_path):
    p = _layout(tmp_path)
    before = p["csv"].read_text(encoding="utf-8")
    res = gvlsync.preview(p["pdf"], p["csv"])
    assert res["token"] and res["source_version"] == "2026-06-30"
    assert res["counts"]["neu"] == 1 and res["counts"]["entfernt"] == 1
    assert res["sample_neu"][0]["labelcode"] == "00001"
    assert res["sample_neu"][0]["label"] == "Frisches Label"
    assert res["sample_entfernt"][0]["labelcode"] == "00002"
    # Trockenlauf: Master unverändert, kein Archiv
    assert p["csv"].read_text(encoding="utf-8") == before
    assert not (p["data_dir"] / "archive").exists()


def test_merge_writes_master_archives_and_builds_labels_db(tmp_path):
    p = _layout(tmp_path)
    prev = gvlsync.preview(p["pdf"], p["csv"])
    labels_db = tmp_path / "labels.db"
    res = gvlsync.merge(prev["token"], csv_path=p["csv"], data_dir=p["data_dir"],
                        docs_dir=p["docs_dir"], labels_db=labels_db)
    assert res["source_version"] == "2026-06-30" and res["rows"] == 1

    # neuer Master enthält den frischen Datensatz, alter Stand ist archiviert
    assert "Frisches Label" in p["csv"].read_text(encoding="utf-8")
    archived = p["data_dir"] / "archive" / "GVL_Labelcodes_synced_2026-06-23.csv"
    assert archived.exists() and "Altes Label" in archived.read_text(encoding="utf-8")
    # Report + Diff + Meta geschrieben
    assert (p["docs_dir"] / "reconcile_report_2026-06-30.md").exists()
    assert (p["data_dir"] / "diff_2026-06-30.csv").exists()
    assert json.loads((p["data_dir"] / "GVL_Labelcodes_synced.meta.json").read_text())["source_version"] == "2026-06-30"
    # labels.db neu gebaut + abfragbar
    con = sqlite3.connect(labels_db)
    row = con.execute("SELECT label FROM labels WHERE labelcode='00001'").fetchone()
    con.close()
    assert row and row[0] == "Frisches Label"


def test_merge_keeps_supplemental_codes_in_labels_db(tmp_path):
    """Nach einem PDF-Update dürfen die Zusatz-Codes (labelcodes_supplemental.csv) nicht aus der
    labels.db verschwinden. GVL gewinnt."""
    p = _layout(tmp_path)
    (p["data_dir"] / "labelcodes_supplemental.csv").write_text(
        "labelcode,label,kurzname,plm,hersteller,rechterueckrufe,quelle\n"
        "99001,Zusatz Library,,,Some GmbH,,KPM-PDF\n00001,Im Master schon,,,X,,Y\n", encoding="utf-8")
    prev = gvlsync.preview(p["pdf"], p["csv"])
    labels_db = tmp_path / "labels.db"
    res = gvlsync.merge(prev["token"], csv_path=p["csv"], data_dir=p["data_dir"],
                        docs_dir=p["docs_dir"], labels_db=labels_db)
    assert res["rows"] == 2
    con = sqlite3.connect(labels_db)
    rows = dict(con.execute("SELECT labelcode, label FROM labels").fetchall())
    con.close()
    assert rows == {"00001": "Frisches Label", "99001": "Zusatz Library"}     # Master gewinnt bei 00001
    assert "99001" not in p["csv"].read_text(encoding="utf-8")                # Master-CSV bleibt reiner GVL-Stand


def test_merge_with_stale_token_errors(tmp_path):
    p = _layout(tmp_path)
    res = gvlsync.merge("nicht-existent", csv_path=p["csv"], data_dir=p["data_dir"],
                        docs_dir=p["docs_dir"], labels_db=tmp_path / "labels.db")
    assert "error" in res


def test_preview_supersedes_previous_token(tmp_path):
    p = _layout(tmp_path)
    first = gvlsync.preview(p["pdf"], p["csv"])["token"]
    second = gvlsync.preview(p["pdf"], p["csv"])["token"]
    assert first != second
    # nur der jüngste Token ist noch gültig (bounded memory)
    assert gvlsync.merge(first, csv_path=p["csv"], data_dir=p["data_dir"],
                         docs_dir=p["docs_dir"], labels_db=tmp_path / "l.db").get("error")


def test_preview_compares_pdf_with_master_version(tmp_path):
    """Die Vorschau nennt PDF- und Master-Stand und ob das PDF älter ist."""
    p = _layout(tmp_path)                                         # Master 2026-06-23, PDF 2026-06-30
    res = gvlsync.preview(p["pdf"], p["csv"])
    assert (res["pdf_version"], res["master_version"], res["older_than_master"]) == ("2026-06-30", "2026-06-23", False)
    _write_master(p["data_dir"], version="2026-06-30")            # gleicher Stand ist nicht älter
    assert gvlsync.preview(p["pdf"], p["csv"])["older_than_master"] is False
    _write_master(p["data_dir"], version="2026-09-15")
    res = gvlsync.preview(p["pdf"], p["csv"])
    assert (res["master_version"], res["older_than_master"]) == ("2026-09-15", True)


def test_merge_refuses_older_pdf_unless_allowed(tmp_path):
    """Ein älteres PDF darf den neueren Master nicht ohne Warnung ersetzen (Juni-PDF über September-Master:
    Codes gingen verloren, darunter LC104994). Ohne allow_older nichts schreiben, der Token bleibt gültig."""
    p = _layout(tmp_path)
    _write_master(p["data_dir"], version="2026-09-15")
    token = gvlsync.preview(p["pdf"], p["csv"])["token"]
    kw = dict(csv_path=p["csv"], data_dir=p["data_dir"], docs_dir=p["docs_dir"], labels_db=tmp_path / "labels.db")
    res = gvlsync.merge(token, **kw)
    assert res.get("older_than_master") is True and "2026-06-30" in res["error"] and "2026-09-15" in res["error"]
    assert "Altes Label" in p["csv"].read_text(encoding="utf-8")                 # Master unberührt
    assert not (p["data_dir"] / "archive").exists() and not (tmp_path / "labels.db").exists()
    res = gvlsync.merge(token, allow_older=True, **kw)
    assert "error" not in res and res["source_version"] == "2026-06-30"
    assert "Frisches Label" in p["csv"].read_text(encoding="utf-8")


def test_merge_counts_supplemental_from_given_path(tmp_path):
    """In der gebündelten App liegt labelcodes_supplemental.csv nicht im gvl/-Ordner, sondern im Bundle.
    merge muss die Zusatz-Codes aus dem übergebenen Pfad mitzählen."""
    p = _layout(tmp_path)
    supp = tmp_path / "bundle" / "labelcodes_supplemental.csv"
    supp.parent.mkdir()
    supp.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe,quelle\n"
                    "99001,Zusatz Library,,,Some GmbH,,KPM-PDF\n", encoding="utf-8")
    token = gvlsync.preview(p["pdf"], p["csv"])["token"]
    res = gvlsync.merge(token, csv_path=p["csv"], data_dir=p["data_dir"], docs_dir=p["docs_dir"],
                        labels_db=tmp_path / "labels.db", supplemental_path=supp)
    assert res["rows"] == 2
