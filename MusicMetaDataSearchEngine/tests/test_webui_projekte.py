"""Projekte (D12/D14) und Export-Vorlagen (D2) in der Oberfläche. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.test_webui_cuesheet import DB, IMPORT, edl
from tests.webui_harness import needs_node, run_page

LIB = dict(DB, event_num="002", clip_name="ZZQ_02.wav", title="Südhang", matched_by="file", library="apl",
           quelle="library:apl", duration_sec=30)
OPEN = dict(DB, event_num="003", clip_name="ZZQ_03.wav", title="Talweg", labelcode="", labelname="", status="unresolved",
            duration_sec=10)
PROJ = {"id": "abcdef123456", "name": "Testprojekt Folge 1", "created": "2026-10-20T09:00:00+00:00",
        "updated": "2026-10-22T09:30:00+00:00", "source_file": "folge1.edl",
        "summary": {"cues": 3, "ok": 2, "warn": 0, "bad": 1, "library_sec": 30, "total_sec": 60}}
WAIT = "await sleep(900); await settle();\n"


@needs_node
def test_einlesen_speichert_das_projekt_von_selbst_mit_zusammenfassung():
    out = run_page(IMPORT + "edlState.name = 'Testprojekt Folge 1';" + WAIT + "return txt('#eprojstate');",
                   api={"/api/edl/import": edl(dict(DB, duration_sec=20), LIB, OPEN)})
    assert out["errors"] == []
    post = [f for f in out["fetches"] if f["path"] == "/api/projects" and f["method"] == "POST"][-1]
    body = json.loads(post["body"])
    assert body["name"] == "Testprojekt Folge 1" and len(body["cards"]) == 3
    assert body["summary"] == {"cues": 3, "ok": 2, "warn": 0, "bad": 1, "library_sec": 30, "total_sec": 60}
    assert out["result"].startswith("Gespeichert"), out["result"]


@needs_node
def test_projektliste_zeigt_fertig_offen_und_library_anteil_und_oeffnet_ein_projekt():
    doc = dict(PROJ, options={"merge": True}, cards=[dict(DB, note="Zzq Notiz", query=True)])
    out = run_page("""click('.nav button[data-tool=edl]'); await settle();
      const row = qa('#eprojects tbody tr')[0]; const r = {cells: [...row.querySelectorAll('td')].map(td => txt(td))};
      click([...row.querySelectorAll('button')].find(b => b.textContent === 'Öffnen')); await settle();
      r.cards = edlState.cards.length; r.name = $('#eprojname').value; r.merge = $('#emerge').checked; r.id = edlState.projectId;
      r.note = txt(qa('#eresult tr.etrow')[0].querySelector('.tnote')); return r;""",
                   api={"/api/projects": {"projects": [PROJ]}, "/api/projects/abcdef123456": doc})
    assert out["errors"] == []
    r = out["result"]
    assert r["cells"][0] == "Testprojekt Folge 1" and r["cells"][2:5] == ["3", "2", "1"] and r["cells"][5] == "50 %", r
    assert r["cards"] == 1 and r["name"] == "Testprojekt Folge 1" and r["merge"] is True and r["id"] == "abcdef123456", r
    assert r["note"] == "Zzq Notiz", r


@needs_node
def test_export_nimmt_die_spalten_der_aktiven_vorlage():
    tpl = {"templates": [{"id": "a", "name": "Kurz", "required": [], "columns": ["Titel", "LC", "Länge Sek."]}], "active": "a",
           "columns": ["Interpret", "Titel", "LC", "Länge Sek."], "always": "Länge Sek.", "required": ["labelcode"]}
    out = run_page(IMPORT + """const r = {sel: txt(qa('#eresult .etpl select option')[0])};
      click(qa('#eresult .egroup.export button')[0]); await settle(); return r;""",
                   api={"/api/export-templates": tpl, "/api/edl/import": edl(dict(DB, komponist="Hans Zimm")), "POST /api/edl/export": {"ok": True}})
    assert out["errors"] == [] and out["result"]["sel"] == "Kurz"
    post = [f for f in out["fetches"] if f["path"] == "/api/edl/export"][-1]
    assert json.loads(post["body"])["columns"] == ["Titel", "LC", "Länge Sek."]


@needs_node
def test_vorlagen_bearbeiten_legt_eine_neue_an_und_speichert_auf_dem_server():
    out = run_page(IMPORT + """click(qa('#eresult .etpl .ibtn')[0]); await settle();
      click([...qa('.tpled .tiepick-foot button')].find(b => b.textContent === '+ Vorlage')); await settle();
      const nm = qa('.tpled .tplrow .tplhead input').slice(-1)[0]; nm.value = 'Vorlage Zzq'; nm.dispatchEvent(new Event('input'));
      const r = {rows: qa('.tpled .tplrow').length, always: qa('.tpled .tplrow')[0].querySelectorAll('input[disabled]').length};
      click([...qa('.tpled .tiepick-foot button')].find(b => b.textContent === 'Speichern')); await settle(); return r;""",
                   api={"/api/edl/import": edl(DB)})
    assert out["errors"] == []
    assert out["result"] == {"rows": 2, "always": 1}
    post = [f for f in out["fetches"] if f["path"] == "/api/export-templates" and f["method"] == "POST"][-1]
    assert [t["name"] for t in json.loads(post["body"])["templates"]] == ["Standard", "Vorlage Zzq"]


@needs_node
def test_von_hand_gesetzter_lc_zeigt_von_hand_statt_altem_suchstatus():
    c = dict(OPEN, labelcode="LC24584", labelname="MOM & POP", status="confirmed", confirmed_from="unresolved",
             field_src={"labelcode": {"date": "2026-10-23"}})
    out = run_page(IMPORT + "return txt(qa('#eresult tr.etrow')[0].querySelector('.stt'));", api={"/api/edl/import": edl(c)})
    assert out["errors"] == [] and "von Hand" in out["result"] and "Nicht gefunden" not in out["result"], out["result"]
