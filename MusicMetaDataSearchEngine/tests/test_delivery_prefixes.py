"""Katalog-Kürzel aus den Library-Lieferungen lernen (C8/C1). Testdaten frei erfunden, bis auf den Abgleich mit der
erzeugten Datei im Repo."""
import csv
from pathlib import Path

from core import librarytracks
from core.labelmatch import attach_delivery_prefixes, build_index, catalog_lookup

DATA = Path(__file__).resolve().parents[2] / "data"


def _delivery(tmp_path, name, rows):
    d = tmp_path / "tracks"
    d.mkdir(exist_ok=True)
    with open(d / name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "library", "labelcode"])
        w.writeheader()
        w.writerows({"file": fn, "library": lib, "labelcode": lc} for fn, lib, lc in rows)
    return d


def test_learns_only_unambiguous_prefixes_with_enough_files(tmp_path):
    d = _delivery(tmp_path, "zzq_2026-01-01.csv", [
        ("ZZQ101_1", "cezame", "99110"), ("ZZQ102_1", "cezame", "99110"), ("ZZQ_103_1", "cezame", "99110"),
        ("NRD201_1", "cezame", "99111"), ("NRD202_1", "cezame", "99112"), ("NRD203_1", "cezame", "99111"),  # 1:n
        ("FEW301_1", "cezame", "99113"), ("FEW302_1", "cezame", "99113"),                                     # zu wenige
        ("Ohne Kürzel", "cezame", "99114")])
    rows = librarytracks.delivery_prefixes(d)
    assert [(r["prefix"], r["labelcode"], r["files"]) for r in rows] == [("ZZQ", "99110", "3")]
    assert librarytracks.delivery_prefixes(d, exclude=frozenset({"ZZQ101_1"})) == []        # zurückgehalten


def test_lists_win_and_partner_name_shows(tmp_path):
    idx = build_index([("99110", "Zzq Label"), ("99111", "Nordwand Label")])
    idx.prefix2code = {"NRD": {"labelcode": "99111", "library": "Liste", "prefix": "NRD"}}
    p = tmp_path / "p.csv"
    librarytracks.write_prefixes([{"prefix": "ZZQ", "labelcode": "99110", "library": "cezame", "files": "3"},
                                  {"prefix": "NRD", "labelcode": "99110", "library": "cezame", "files": "9"},
                                  {"prefix": "UNK", "labelcode": "99999", "library": "cezame", "files": "9"},
                                  {"prefix": "XQ", "labelcode": "99110", "library": "andere", "files": "9"}], p)
    assert attach_delivery_prefixes(idx, p) == 2
    assert catalog_lookup("ZZQ", idx) == ("99110", "Library-Kürzel ZZQ (Lieferung Cezame)")
    assert catalog_lookup("NRD", idx)[0] == "99111"                                           # Liste gewinnt
    assert catalog_lookup("XQ", idx)[1] == "Library-Kürzel XQ (aus Lieferung)"


def test_repo_file_matches_the_deliveries():
    """data/library_prefixes_lieferungen.csv ist aktuell (`import_library_tracks.py prefixes` nach jeder Lieferung)."""
    with open(DATA / "library_prefixes_lieferungen.csv", newline="", encoding="utf-8") as f:
        assert list(csv.DictReader(f)) == librarytracks.delivery_prefixes(DATA / "library_tracks")
