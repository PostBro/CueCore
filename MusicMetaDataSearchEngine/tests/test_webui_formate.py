"""Zahlen, Datum und Konfidenz in der Oberfläche (web/index.html) folgen der Sprache:
66.423 / 66,423, 15.09.2026 / 15/09/2026, 0,85 / 0.85."""
from __future__ import annotations

import json

from tests.webui_harness import needs_node, run_page

FOLDERS = json.dumps([{"path": "/Users/x/Musik/Eingang", "n": 1234, "at": 1}])
HEALTH_IX = {"ready": True, "rows": 85224, "songs": 66423, "source_version": "2026-09-15", "sources": {},
             "index": {"present": True, "rows": 17037079}}
DB = {"total": 66423, "total_all": 66423, "rows": [{"id": 1, "interpret": "Mom Pop Test", "titel": "X", "labelcode": "LC24584",
       "verified": True, "date_verified": "2026-10-11T08:00:00"}], "index_rows": [], "missing_counts": {}, "index_present": False}


@needs_node
def test_zaehler_mit_tausendertrennzeichen():
    scen = """const r = {tile: txt('#hometiles .tile .last'), recent: texts('#homerecent button'), chips: texts('#trecent .rc')};
      click('.nav button[data-tool=db]'); await settle(); r.page = txt('#dbpage'); return r;"""
    de = run_page(scen, storage={"cuecore_recent_folders": FOLDERS}, api={"/api/db/songs": DB})
    assert de["errors"] == []
    r = de["result"]
    assert "1.234 Dateien" in r["tile"] and any("1.234 Dateien" in x for x in r["recent"]), r
    assert r["chips"] == ["Eingang1.234 Dateien"] and r["page"] == "1–50 von 66.423"
    en = run_page(scen, lang="en", storage={"cuecore_recent_folders": FOLDERS}, api={"/api/db/songs": DB})["result"]
    assert "1,234 files" in en["tile"] and en["page"] == "1–50 of 66,423"


@needs_node
def test_datum_nach_sprache():
    scen = """const r = {rail: txt('#st-ver'), home: texts('#homehealth .hrow').slice(0, 1).concat(texts('#homehealth .hrow')[3])};
      click('.nav button[data-tool=db]'); await settle(); r.verified = $('#dbresult .dbstat .est').getAttribute('title'); return r;"""
    api = {"/api/health": HEALTH_IX, "/api/db/songs": DB,
           "/api/index/status": {"present": True, "meta": {"discogs_dump": "discogs_20260901_releases.xml.gz"}}}
    de = run_page(scen, api=api)
    assert de["errors"] == []
    assert de["result"]["rail"] == "15.09.2026"
    assert de["result"]["home"][0].startswith("GVL-Master15.09.2026")
    assert de["result"]["home"][1].endswith("Stand 01.09.")
    assert de["result"]["verified"] == "Verifiziert am 11.10.2026"
    en = run_page(scen, lang="en", api=api)["result"]
    assert en["rail"] == "15/09/2026" and en["home"][1].endswith("as of 01/09")
    assert en["verified"] == "Verified on 11/10/2026"


@needs_node
def test_konfidenz_mit_dezimalkomma_und_nach_wechsel():
    out = run_page("""const m = meter(0.85, 'review'); document.body.appendChild(m);
      const r = [txt(m.querySelector('.num'))]; click('#langbtn'); await settle(); r.push(txt(m.querySelector('.num'))); return r;""")
    assert out["result"] == ["conf 0,85", "conf 0.85"]
