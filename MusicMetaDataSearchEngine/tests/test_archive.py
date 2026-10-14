"""Tests für die GVL-Master-Archivierung: reconcile-Läufe dürfen den Master nicht
stillschweigend überschreiben, ohne den Vorgänger-Stand zu sichern."""
import json

import pymupdf
import pytest

from reconcile.cli import main
from reconcile.merge import archive_previous_master


def _write_master(tmp_path, version, rows="labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n00001,Alt,,,,\n"):
    csv_path = tmp_path / "GVL_Labelcodes_synced.csv"
    meta_path = tmp_path / "GVL_Labelcodes_synced.meta.json"
    csv_path.write_text(rows, encoding="utf-8")
    meta_path.write_text(json.dumps({"source_version": version, "row_count": 1}), encoding="utf-8")
    return csv_path, meta_path


def test_archive_previous_master_copies_under_old_version(tmp_path):
    csv_path, meta_path = _write_master(tmp_path, "2026-06-23")
    archive_dir = tmp_path / "archive"

    dest = archive_previous_master(csv_path, meta_path, archive_dir)

    assert dest == str(archive_dir / "GVL_Labelcodes_synced_2026-06-23.csv")
    assert (archive_dir / "GVL_Labelcodes_synced_2026-06-23.csv").read_text(encoding="utf-8") == csv_path.read_text(encoding="utf-8")


def test_archive_previous_master_no_previous_returns_none(tmp_path):
    csv_path = tmp_path / "does_not_exist.csv"
    meta_path = tmp_path / "does_not_exist.meta.json"
    archive_dir = tmp_path / "archive"

    assert archive_previous_master(csv_path, meta_path, archive_dir) is None
    assert not archive_dir.exists()


def test_archive_previous_master_idempotent(tmp_path):
    csv_path, meta_path = _write_master(tmp_path, "2026-06-23")
    archive_dir = tmp_path / "archive"

    first = archive_previous_master(csv_path, meta_path, archive_dir)
    # Master aendert sich zwischen zwei Laeufen (z.B. neuer Re-Sync gegen dieselbe
    # Quell-Version) -> die schon archivierte Kopie darf NICHT ueberschrieben werden.
    csv_path.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n99999,Veraendert,,,,\n", encoding="utf-8")
    second = archive_previous_master(csv_path, meta_path, archive_dir)

    assert first == second
    assert "00001,Alt" in (archive_dir / "GVL_Labelcodes_synced_2026-06-23.csv").read_text(encoding="utf-8")


# --- Integration: voller cli.main()-Lauf archiviert vor dem Overwrite ---

X = {"labelcode": 17, "label": 114, "kurzname": 211, "plm": 308, "hersteller": 362, "rr": 460}


def _make_fixture_pdf(path):
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": "D:20260630080632+02'00'"})
    p0 = doc.new_page(width=612, height=792)
    for col, label in [("labelcode", "Labelcode"), ("label", "Label"), ("kurzname", "Kurzname"),
                       ("plm", "PLM"), ("hersteller", "Hersteller"), ("rr", "Rechterückrufe")]:
        p0.insert_text((X[col], 85), label, fontsize=9)
    p0.insert_text((X["labelcode"], 110), "00001", fontsize=9)
    p0.insert_text((X["label"], 110), "Frisches Label", fontsize=9)
    p0.insert_text((X["hersteller"], 110), "Firma GmbH", fontsize=9)
    doc.save(str(path))
    doc.close()


@pytest.fixture
def project_layout(tmp_path):
    pdf_path = tmp_path / "GVL-Labelcodes.pdf"
    _make_fixture_pdf(pdf_path)
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    data_dir.mkdir()
    docs_dir.mkdir()
    csv_path, meta_path = _write_master(data_dir, "2026-06-23")
    return {"pdf": pdf_path, "csv": csv_path, "data_dir": data_dir, "docs_dir": docs_dir}


def test_cli_archives_old_master_before_overwrite(project_layout):
    p = project_layout
    rc = main(["--pdf", str(p["pdf"]), "--csv", str(p["csv"]),
              "--data-dir", str(p["data_dir"]), "--docs-dir", str(p["docs_dir"])])
    assert rc == 0

    archived = p["data_dir"] / "archive" / "GVL_Labelcodes_synced_2026-06-23.csv"
    assert archived.exists()
    assert "00001,Alt" in archived.read_text(encoding="utf-8")

    new_master = p["data_dir"] / "GVL_Labelcodes_synced.csv"
    assert "Frisches Label" in new_master.read_text(encoding="utf-8")
