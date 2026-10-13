"""Zusatz-Labelcodes (data/labelcodes_supplemental.csv) — GVL-Master gewinnt immer."""
import csv

from reconcile.csv_io import read_master_with_supplemental, read_supplemental


def _write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_read_supplemental_skips_master_codes_and_flags_provenance(tmp_path):
    supp = tmp_path / "labelcodes_supplemental.csv"
    _write(supp, ["labelcode", "label", "source"],
           [["00002", "Paetec", "publicdomainproject"],
            ["24584", "Falsches Label", "irgendwas"],    # kollidiert mit Master -> verworfen
            ["99999", "Neue Library", "DNA-MUSIK"]])
    recs = read_supplemental(supp, existing_codes={"24584"})
    codes = {r.labelcode for r in recs}
    assert "24584" not in codes                          # GVL-Master gewinnt
    assert codes == {"00002", "99999"}
    paetec = next(r for r in recs if r.labelcode == "00002")
    assert paetec.label == "Paetec"
    assert paetec.hersteller == "Zusatzquelle: publicdomainproject"   # Provenance sichtbar in der Suche
    assert paetec.plm == "" and paetec.rechterueckrufe == ""


def test_read_supplemental_normalizes_and_dedupes(tmp_path):
    supp = tmp_path / "labelcodes_supplemental.csv"
    _write(supp, ["labelcode", "label", "source"],
           [["LC 2061", "KPM", "x"], ["02061", "KPM Dup", "y"]])   # gleicher Code nach Normalisierung
    recs = read_supplemental(supp, existing_codes=set())
    assert [r.labelcode for r in recs] == ["02061"]      # zero-padded + dedupliziert (erster gewinnt)


def test_read_supplemental_missing_file_returns_empty(tmp_path):
    assert read_supplemental(tmp_path / "nope.csv", set()) == []


def test_read_master_with_supplemental_gvl_wins(tmp_path):
    master = tmp_path / "GVL_Labelcodes_synced.csv"
    _write(master, ["labelcode", "label", "kurzname", "plm", "hersteller", "rechterueckrufe"],
           [["24584", "MOM & POP", "", "", "Sony Music", "R12"]])
    _write(tmp_path / "labelcodes_supplemental.csv", ["labelcode", "label", "source"],
           [["24584", "Falsch", "x"], ["00002", "Paetec", "pdp"]])
    recs = read_master_with_supplemental(master)
    bycode = {r.labelcode: r for r in recs}
    assert bycode["24584"].label == "MOM & POP"          # GVL gewinnt, Supplemental ignoriert
    assert bycode["24584"].hersteller == "Sony Music"
    assert bycode["00002"].label == "Paetec"             # neuer Code aufgenommen
    assert len(recs) == 2


def test_shipped_supplemental_file_is_sorted_unique_and_has_kpm_2022_codes():
    """Mitgelieferte data/labelcodes_supplemental.csv: sortiert, keine Dubletten, KPM-PDF-Codes (2022-05) drin."""
    import csv
    import pytest
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "labelcodes_supplemental.csv"
    if not p.exists():
        pytest.skip("data/labelcodes_supplemental.csv fehlt (Build-Layout)")
    rows = list(csv.DictReader(open(p, encoding="utf-8", newline="")))
    codes = [r["labelcode"] for r in rows]
    assert codes == sorted(codes) and len(codes) == len(set(codes))
    by = {r["labelcode"]: r for r in rows}
    for code, label in [("85216", "VideoHelper Online Exclusives"), ("93093", "Sonor Music Editions"),
                        ("95541", "Intermezzi"), ("95542", "Sonor Production Music")]:
        assert by[code]["label"] == label and by[code]["source"] == "KPM-PDF-2022-05"
