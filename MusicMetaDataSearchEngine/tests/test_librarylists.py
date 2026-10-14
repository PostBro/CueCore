"""Nutzer-Upload von Library-Listen (Kürzel -> LC) und das gemeinsame Regelwerk mit dem Generator."""
import csv
from pathlib import Path

from openpyxl import Workbook

from core import librarylists as L
from core.labelmatch import LabelIndex

NAMES = {"00121": "Atlantic", "07552": "REPUBLIC", "10347": "CMG", "84940": "Smalltown Kid Records"}


def _csv(path, header, rows, delim=";"):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delim)
        w.writerow(header)
        w.writerows(rows)
    return path


def test_read_user_list_recognises_columns_and_normalises_codes(tmp_path):
    p = _csv(tmp_path / "Meine_Library.csv", ["Kürzel", "Label", "LC"], [
        ["ABC", "Abc Records", "LC 121"], ["DEF", "Def", "7552"], ["", "", ""], ["GHI", "Ghi", "kaputt"]])
    res = L.read_user_list(p)
    assert res["mapping"] == {"prefix": "Kürzel", "label": "Label", "labelcode": "LC"}
    assert [(r["prefix"], r["labelcode"], r["list_label"], r["library"]) for r in res["rows"]] == [
        ("ABC", "00121", "Abc Records", "Meine Library"), ("DEF", "07552", "Def", "Meine Library"), ("GHI", "", "Ghi", "Meine Library")]
    assert res["warnings"] == ["Zeile 5: LC „kaputt“ nicht lesbar."]
    assert L.read_user_list(p, library="Fresh Tracks")["rows"][0]["library"] == "Fresh Tracks"
    # Library-Spalte in der Datei schlägt den Dateinamen; „Library“ zählt als Label, wenn es kein anderes Label gibt
    p2 = _csv(tmp_path / "x.csv", ["Album-Code", "Name", "GVL", "Anbieter"], [["XCD", "Extreme", "1732", "Extreme Music"]])
    assert L.read_user_list(p2)["rows"][0]["library"] == "Extreme Music"
    p3 = _csv(tmp_path / "y.csv", ["Shortform", "Library", "LC Code"], [["KPM", "KPM Main", "00448"]])
    r3 = L.read_user_list(p3)
    assert r3["mapping"]["label"] == "Library" and "library" not in r3["mapping"]
    # ohne Kürzel/LC-Spalte -> Fehler
    assert "Spalten für Kürzel und LC" in L.read_user_list(_csv(tmp_path / "z.csv", ["Foo"], [["1"]]))["error"]


def test_read_user_list_xlsx(tmp_path):
    wb = Workbook(); ws = wb.active
    ws.append(["Prefix", "Sub-Label", "Label Code"]); ws.append(["CEZ", "Cezame", 10347]); ws.append([None, None, None])
    p = tmp_path / "cez.xlsx"; wb.save(p)
    rows = L.read_user_list(p)["rows"]
    assert rows == [{"prefix": "CEZ", "labelcode": "10347", "list_label": "Cezame", "library": "cez", "source": "cez.xlsx"}]


def test_reduce_rows_applies_all_rules_and_reports():
    rows = [
        {"prefix": "abc", "labelcode": "00121", "list_label": "Abc Records", "library": "L", "source": "s"},
        {"prefix": "NEW", "labelcode": "07552", "list_label": "REPUBLIC", "library": "L", "source": "s"},
        {"prefix": "DUP", "labelcode": "00121", "list_label": "Atlantic", "library": "L", "source": "s"},   # bekannt, gleicher Code
        {"prefix": "CEZ", "labelcode": "07552", "list_label": "Cezame", "library": "L", "source": "s"},     # bekannt, anderer Code
        {"prefix": "AMB", "labelcode": "00121", "list_label": "A", "library": "L", "source": "s"},
        {"prefix": "AMB", "labelcode": "07552", "list_label": "B", "library": "L", "source": "s"},          # mehrdeutig
        {"prefix": "DEV", "labelcode": "84940", "list_label": "Devil Cut", "library": "L", "source": "s"},  # KNOWN_CONFLICTS
        {"prefix": "NIX", "labelcode": "99999", "list_label": "Nix", "library": "L", "source": "s"},        # nicht im Master
        {"prefix": "", "labelcode": "00121", "list_label": "", "library": "L", "source": "s"},              # leer
    ]
    known = {"DUP": {"labelcode": "00121", "library": "Shipped", "prefix": "DUP"},
             "CEZ": {"labelcode": "10347", "library": "Cezame", "prefix": "CEZ"}}
    rep = L.reduce_rows(rows, NAMES, known=known)
    assert [(r["key"], r["labelcode"], r["label_gvl"]) for r in rep["kept"]] == [("ABC", "00121", "Atlantic"), ("NEW", "07552", "REPUBLIC")]
    why = {(e["prefix"], e["labelcode"]): e["why"] for e in rep["excluded"]}
    assert why[("CEZ", "07552")].startswith("Kürzel schon vergeben: LC10347 CMG")
    assert why[("AMB", "00121")] == "Kürzel mehrdeutig: 00121, 07552" and ("AMB", "07552") in why
    assert why[("DEV", "84940")] == "GVL widerspricht der Liste"
    assert why[("NIX", "99999")] == "Code weder im Master noch im Supplemental"
    assert why[("", "00121")] == "leer"
    infos = {i["prefix"]: i["why"] for i in rep["infos"]}
    assert infos["DUP"].startswith("schon bekannt (Shipped)")
    assert "GVL „Atlantic“" in infos["abc"]                                       # Namensabweichung, GVL-Name gewinnt


def test_rebuild_and_attach_user_prefixes(tmp_path):
    d = L.user_lists_dir(tmp_path); d.mkdir()
    _csv(d / "2026-09-13_a.csv", ["Kürzel", "Label", "LC"], [["ABC", "Abc", "121"], ["OLD", "Old", "10347"]])
    _csv(d / "2026-09-13_b.csv", ["Kürzel", "Label", "LC"], [["ABC", "Abc", "7552"], ["NEW", "New", "7552"]])
    (d / "notes.txt").write_text("ignore", encoding="utf-8")
    shipped = {"OLD": {"labelcode": "10347", "library": "Cezame", "prefix": "OLD"}}
    L.write_manifest(tmp_path, {"2026-09-13_b.csv": {"library": "Zwei GmbH", "original": "b.csv", "uploaded": "2026-09-13"}})
    rep = L.rebuild_user_prefixes(tmp_path, NAMES, shipped)
    assert [(f["name"], f["library"], f["original"]) for f in rep["files"]] == [
        ("2026-09-13_a.csv", "", "2026-09-13_a.csv"), ("2026-09-13_b.csv", "Zwei GmbH", "b.csv")]
    assert [r["key"] for r in rep["kept"]] == ["NEW"]                             # ABC mehrdeutig über zwei Listen
    assert rep["kept"][0]["library"] == "Zwei GmbH"                                # Name aus dem Manifest
    assert any(e["prefix"] == "ABC" and e["library"] == "a" for e in rep["excluded"])   # ohne Manifest: Dateiname ohne Datum
    assert any(e["prefix"] == "ABC" and "mehrdeutig" in e["why"] for e in rep["excluded"])
    assert any(i["prefix"] == "OLD" and "schon bekannt (Cezame)" in i["why"] for i in rep["infos"])
    out = Path(tmp_path / L.USER_PREFIXES).read_text(encoding="utf-8").splitlines()
    assert out[0] == ",".join(L.COLUMNS) and out[1].startswith("NEW,07552,REPUBLIC,")
    idx = LabelIndex(); idx.code2name.update(NAMES); idx.prefix2code = {"OLD": shipped["OLD"]}
    assert L.attach_user_prefixes(idx, tmp_path) == 1
    assert idx.prefix2code["NEW"]["user"] is True and idx.prefix2code["NEW"]["labelcode"] == "07552"
    # Rebuild ohne Listen -> leere Datei, nichts hängt mehr im Index
    for f in d.glob("*.csv"):
        f.unlink()
    assert L.rebuild_user_prefixes(tmp_path, NAMES, shipped)["kept"] == []
    idx2 = LabelIndex(); idx2.code2name.update(NAMES)
    assert L.attach_user_prefixes(idx2, tmp_path) == 0
    assert L.safe_name("../../evil.csv") == "evil.csv" and L.safe_name("a b/c.xlsx") == "c.xlsx"
