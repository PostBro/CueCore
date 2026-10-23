"""Projekte speichern und fortsetzen (Teil D12/D14). Testdaten frei erfunden."""
from __future__ import annotations

import pytest

from core import projects


def test_speichern_laden_liste_loeschen(tmp_path):
    meta = projects.save(tmp_path, {"name": "  Testprojekt   Folge 1 ", "cards": [{"title": "Nordwand", "note": "Zzq"}],
                                    "summary": {"cues": 3, "ok": 2, "warn": 1, "bad": 0, "library_sec": 40, "total_sec": 126}})
    assert meta["name"] == "Testprojekt Folge 1" and meta["summary"]["library_sec"] == 40 and "cards" not in meta
    again = projects.save(tmp_path, {"id": meta["id"], "name": "Testprojekt Folge 1b", "cards": []})
    assert again["id"] == meta["id"] and again["created"] == meta["created"]
    doc = projects.load(tmp_path, meta["id"])
    assert doc["name"] == "Testprojekt Folge 1b" and doc["cards"] == []
    projects.save(tmp_path, {"name": "Zweites"})
    assert [m["name"] for m in projects.list_all(tmp_path)] == ["Zweites", "Testprojekt Folge 1b"]   # zuletzt bearbeitet zuerst
    assert projects.delete(tmp_path, meta["id"]) is True and projects.load(tmp_path, meta["id"]) is None
    assert not list((tmp_path / "projects").glob("*.tmp"))


def test_ids_ausserhalb_des_ordners_werden_abgewiesen(tmp_path):
    for bad in ("../x", "a/b", "", "X" * 40):
        with pytest.raises(ValueError):
            projects.load(tmp_path, bad)


def test_kaputte_datei_erscheint_mit_fehler(tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "abcdef123456.json").write_text("{kaputt", encoding="utf-8")
    [m] = projects.list_all(tmp_path)
    assert m["id"] == "abcdef123456" and "nicht lesbar" in m["error"]
