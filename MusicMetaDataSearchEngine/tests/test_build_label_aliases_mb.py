"""build_label_aliases_mb.py: MusicBrainz label + label_alias (COPY-Text) -> data/label_aliases_mb.csv.
GVL gewinnt: nur Master-Codes, GVL-bekannte Namen und ALIASES bleiben draußen, Mehrdeutiges fliegt."""
import csv
import json

import pytest

import build_label_aliases_mb as mb

UUID = "8f7e0c8a-6a4e-4d5e-9a3b-0123456789ab"
NULL = "\\N"


def _label(id_, name, lc, gid=UUID):
    # id, gid, name, begin(3), end(3), label_code, type, area, comment, edits_pending, last_updated, ended
    return "\t".join([str(id_), gid, name, NULL, NULL, NULL, NULL, NULL, NULL, lc if lc else NULL, "4", NULL,
                      "", "0", "2026-01-01 00:00:00+00", "f"])


def _alias(id_, label_id, name, type_="1"):
    # id, label, name, locale, edits_pending, last_updated, type, sort_name, begin(3), end(3), primary, ended
    return "\t".join([str(id_), str(label_id), name, NULL, "0", "2026-01-01 00:00:00+00", type_, name,
                      NULL, NULL, NULL, NULL, NULL, NULL, "f", "f"])


def _dump(tmp_path, labels, aliases, timestamp="20260912-002318"):
    d = tmp_path / "dump" / "mbdump"
    d.mkdir(parents=True)
    (d / "label").write_text("\n".join(labels) + "\n", encoding="utf-8")
    (d / "label_alias").write_text("\n".join(aliases) + "\n", encoding="utf-8")
    (d / "TIMESTAMP").write_text(timestamp + "\n", encoding="utf-8")
    return tmp_path / "dump"


def _master(tmp_path):
    p = tmp_path / "GVL_Labelcodes_synced.csv"
    p.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n"
                 "00121,Atlantic,,,Warner Music,\n07552,REPUBLIC,,,Universal Music,\n"
                 "00407,ISLAND,,,Universal Music,\n", encoding="utf-8")
    return p


def test_build_keeps_only_new_names_of_gvl_codes(tmp_path):
    dump = _dump(tmp_path, [
        _label(1, "Atlantic Records", "121"),              # GVL kennt 'atlantic' (exakt/ALIASES) -> raus
        _label(2, "Republic Records Nashville", "7552"),   # neuer Name zu Master-Code -> bleibt
        _label(3, "Some Label", "99999"),                  # Code nicht im Master -> raus
        _label(4, "Records", "121"),                       # nur Generikum -> raus
        _label(5, "Isla", "407"), _label(6, "Isla", "7552"),   # ein Name, zwei Codes -> mehrdeutig, raus
        _label(7, "AB", "407"),                            # zu kurz -> raus
        _label(8, "Zé \\\\ Ton", "407"),                   # COPY-Escape '\\\\' -> '\\'
        _label(9, "Ohne Code", ""),                        # label_code NULL
        _label(10, "FAX +49-30/123456", "407"),            # Telefon-/Fax-Muster statt Labelname -> raus
    ], [
        _alias(1, 2, "Republic Nashville"),                # Alias eines Master-Labels -> bleibt
        _alias(2, 3, "Whatever"),                          # Label nicht behalten -> raus
        _alias(3, 2, "Island", "2"),                       # GVL-Name eines anderen Codes -> raus (GVL gewinnt)
        _alias(4, 2, "Republic Records Nashville"),        # gleicher Schlüssel wie der Name -> Name gewinnt, kein Doppel
        _alias(5, 2, "TEL +49 30 555555"),                 # auch als Alias -> raus
    ])
    out = tmp_path / "label_aliases_mb.csv"
    meta = mb.build(dump, _master(tmp_path), out)
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["name"], r["labelcode"], r["kind"], r["mb_label"]) for r in rows] == [
        ("Republic Nashville", "07552", "alias", "2"),
        ("Republic Records Nashville", "07552", "name", "2"),
        ("Zé \\ Ton", "00407", "name", "8"),
    ]
    assert meta["labels_total"] == 10 and meta["labels_with_lc"] == 9 and meta["labels_lc_in_gvl"] == 8
    assert meta["aliases_total"] == 5 and meta["aliases_of_gvl_labels"] == 4 and meta["dropped_contact"] == 2
    assert meta["dropped_ambiguous"] == 1 and meta["dropped_generic"] == 2 and meta["dropped_gvl_known"] == 2
    assert meta["rows"] == 3 and meta["dump_timestamp"] == "20260912-002318"
    assert json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["rows"] == 3
    assert out.read_bytes().count(b"\r") == 0


def test_build_refuses_unexpected_column_order(tmp_path):
    bad = _label(1, "X", "121", gid="kein-uuid")
    dump = _dump(tmp_path, [bad], [_alias(1, 1, "Y")])
    with pytest.raises(SystemExit, match="Spaltenreihenfolge"):
        mb.build(dump, _master(tmp_path), tmp_path / "out.csv")


def test_build_missing_tables_is_explicit(tmp_path):
    with pytest.raises(SystemExit, match="fehlt"):
        mb.build(tmp_path, _master(tmp_path), tmp_path / "out.csv")


def test_unescape_copy_text():
    assert mb.unescape("a\\tb\\\\c\\nd") == "a\tb\\c\nd"
