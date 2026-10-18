"""Library-Lieferung im CueCore-Exportformat (librarytracks.read_cuecore_csv). Testdaten frei erfunden."""
import pytest

from core import librarytracks

HEADER = "Interpret,Titel,Komponist,Texter,Verlag,Album,Track-Nr.,Label,LC,ISRC,ISWC,GEMA-Werknr.,File Name"
ROW1 = ("Erika Testerin,Nordwand,Erika Testerin,,Zzq Verlag GmbH,Zzq Album,3,Zzq Music,50845,DEZZQ0000001,"
        "T-000.000.001-0,10000001,ZZQ0001_03_Nordwand (Main)_Erika Testerin_Zzq Album_Zzq Music_LC50845.mp3")
ROW_NO_FILE = "Erika Testerin,Talweg (Alt),Erika Testerin,,Zzq Verlag GmbH,Zzq Album,4,Zzq Music,50845,,,,0"
EMPTY = ",,,,,,,,,,,,"


def _write(tmp_path, *lines):
    p = tmp_path / "lieferung.csv"
    p.write_text("﻿" + "\n".join((HEADER,) + lines) + "\n", encoding="utf-8")
    return p


def test_reads_rows_and_skips_empty_lines(tmp_path):
    rows = librarytracks.read_cuecore_csv(_write(tmp_path, ROW1, EMPTY, EMPTY, ROW_NO_FILE), "zzq")
    assert len(rows) == 2
    r = rows[0]
    assert r["library"] == "zzq" and r["sublibrary"] == "Zzq Music" and r["labelcode"] == "50845"
    assert r["artist"] == "Erika Testerin" and r["title"] == "Nordwand" and r["composers"] == "Erika Testerin"
    assert r["catno"] == "ZZQ0001" and r["track_number"] == "3" and r["album"] == "Zzq Album"
    assert r["isrc"] == "DEZZQ0000001" and r["iswc"] == "T-000.000.001-0" and r["gema"] == "10000001"
    assert r["publisher"] == "Zzq Verlag GmbH" and r["file"].endswith("_LC50845.mp3")


def test_filename_without_extension_stays_empty(tmp_path):
    rows = librarytracks.read_cuecore_csv(_write(tmp_path, ROW_NO_FILE), "zzq")
    assert rows[0]["file"] == "" and rows[0]["catno"] == "" and rows[0]["title"] == "Talweg (Alt)"


def test_rejects_other_formats(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Artist,Title\nA,B\n", encoding="utf-8")
    with pytest.raises(ValueError):
        librarytracks.read_cuecore_csv(p, "zzq")


def test_roundtrip_through_extract_columns(tmp_path):
    rows = librarytracks.read_cuecore_csv(_write(tmp_path, ROW1), "zzq")
    out = tmp_path / "zzq_2026-10-18.csv"
    assert librarytracks.write_csv(rows, out) == 1
    back = librarytracks.read_csv(out)
    assert back[0]["file"] == rows[0]["file"] and back[0]["labelcode"] == "50845"
    assert librarytracks.library_name(out) == "zzq"
