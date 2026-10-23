"""Track-Datenbank der Oberfläche (web/index.html) im DOM-Ersatz: Umfang-Schalter, Löschen mit Rückfrage,
PDF-Export großer Auswahlen. Testdaten frei erfunden."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.webui_harness import needs_node, run_page

SONG = {"id": 7, "interpret": "Zonk Band", "titel": "Moonlight Groove", "labelname": "ACME", "labelcode": "LC24584",
        "komponist": "Hans Zimm", "texter": "", "verlag": "Pubco", "album": "", "track_number": "", "isrc": "", "iswc": "",
        "gema_werknummer": "", "verified": True, "review_status": "verified", "sources": []}
IDX = {"interpret": "Zonk Band", "titel": "Sunset", "labelname": "ACME", "labelcode": "24584", "verified": False,
       "sources": ["discogs"]}

# /api/db/songs wie das Backend: Index-Zeilen und index_total nur bei scope != verified (app.db_songs)
SONGS_JS = """__api['/api/db/songs'] = u => { const sc = u.searchParams.get('scope'), q = u.searchParams.get('q');
  const ix = sc !== 'verified' && q;
  return {total: 2, total_all: 2, total_verified: 2, rows: [SONG, Object.assign({}, SONG, {id: 8, titel: 'Dawn'})],
          index_rows: ix ? [IDX] : [], index_total: ix ? 116 : 0, index_present: true, missing_counts: {}}; };
"""
SEG = "qa('#dbfbar .seg button').map(b => [b.firstChild.textContent, b.querySelector('b') ? txt(b.querySelector('b')) : null])"
OPEN_DB = "click('.nav button[data-tool=db]'); await settle();\n"


def _js_consts() -> str:
    return f"const SONG = {json.dumps(SONG)}, IDX = {json.dumps(IDX)};\n"


@needs_node
def test_index_zahl_bleibt_beim_wechsel_auf_verifiziert_stehen():
    out = run_page(_js_consts() + SONGS_JS + "dbState.q = 'Zonk Band'; $('#dbq').value = 'Zonk Band';" + OPEN_DB + f"""
      const r = {{all: {SEG}}}; click(qa('#dbfbar .seg button')[1]); await settle(); r.verified = {SEG};
      click('#langbtn'); await settle(); r.en = {SEG}; return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert r["all"] == [["Alle", None], ["Verifiziert", "2"], ["Index", "116"]]
    assert r["verified"] == [["Alle", None], ["Verifiziert", "2"], ["Index", "116"]]
    assert r["en"][2] == ["Index", "116"]


@needs_node
def test_ohne_bekannte_index_zahl_zeigt_verifiziert_keine_null():
    out = run_page(_js_consts() + SONGS_JS + "dbState.q = 'Zonk Band'; dbState.scope = 'verified';" + OPEN_DB + f"return {SEG};")
    assert out["errors"] == []
    assert out["result"][2] == ["Index", None]


@needs_node
def test_ohne_track_index_zeigt_der_umfang_keine_index_zahl():
    """Mit Suchtext und ohne gebauten Track-Index stand im Umfang „Alle“ „Index 0“ — ohne Index gibt es keine Zahl
    (der Server meldet dann index_present false und index_total 0). In beiden Umfängen, auch nach dem Sprachwechsel."""
    api = {"/api/db/songs": {"total": 1, "total_all": 1, "total_verified": 1, "rows": [SONG], "index_rows": [],
                             "index_total": 0, "index_present": False, "missing_counts": {}}}
    out = run_page("dbState.q = 'Zonk Band'; $('#dbq').value = 'Zonk Band';" + OPEN_DB + f"""
      const r = {{all: {SEG}}}; click(qa('#dbfbar .seg button')[1]); await settle(); r.verified = {SEG};
      click('#langbtn'); await settle(); r.en = {SEG}; return r;""", api=api)
    assert out["errors"] == []
    r = out["result"]
    assert r["all"] == [["Alle", None], ["Verifiziert", "1"], ["Index", None]]
    assert r["verified"][2] == ["Index", None] and r["en"][2] == ["Index", None]


@needs_node
def test_verifiziert_zaehlt_zeilen_zur_pruefung_nicht_mit():
    rows = [SONG, dict(SONG, id=8, titel="Dawn", review_status="review")]
    out = run_page(OPEN_DB + f"return [{SEG}, texts('#dbresult .dbstat .est')];",
                   api={"/api/db/songs": {"total": 2, "total_all": 2, "total_verified": 1, "rows": rows, "index_rows": [],
                                          "index_total": 0, "index_present": False, "missing_counts": {}}})
    assert out["errors"] == []
    seg, status = out["result"]
    assert seg[1] == ["Verifiziert", "1"] and status == ["Verifiziert", "Prüfen"]


# --- Löschen im Editor der Track-Datenbank, nur nach Rückfrage -----------
DELETE_SCEN = _js_consts() + """let gone = false;
  __api['/api/db/songs'] = () => ({total: gone ? 1 : 2, total_all: gone ? 1 : 2, total_verified: gone ? 1 : 2,
    rows: gone ? [Object.assign({}, SONG, {id: 8, titel: 'Dawn'})] : [SONG, Object.assign({}, SONG, {id: 8, titel: 'Dawn'})],
    index_rows: [], index_total: 0, index_present: false, missing_counts: {}});
  __api['DELETE /api/songs/7'] = () => { gone = true; return {deleted: true, id: 7}; };
  __api['/api/health'] = () => ({ready: true, rows: 85224, songs: gone ? 66422 : 66423, source_version: '2026-09-15', sources: {}, index: {present: false}});
  await refreshHealth();
""" + OPEN_DB + """fire(qa('#dbresult tr.etrow')[0], 'dblclick'); await settle();
  const edBtn = s => [...$('.emodal .dbedit .actions').querySelectorAll('button')].find(b => b.textContent === s);
  const askBtn = s => [...$('.askbox').querySelectorAll('button')].find(b => b.textContent === s);
  const r = {songs0: txt('#st-songs'), del: edBtn(t('Löschen')) ? [edBtn(t('Löschen')).className] : null};
  click(edBtn(t('Löschen'))); await settle();
  r.ask = [txt('.askbox h3'), txt('.askbox p'), qa('.askbox button').map(b => b.textContent), qa('.emodal-overlay').length];
"""


@needs_node
def test_loeschen_fragt_mit_interpret_und_titel_und_raeumt_danach_auf():
    out = run_page(DELETE_SCEN + """click(askBtn(t('Löschen'))); await settle();
      r.after = [qa('.emodal-overlay').length, texts('#dbresult tr.etrow .ell').filter(x => x === 'Moonlight Groove').length,
                 txt('#st-songs'), qa('#dbresult tr.etrow').length]; return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert r["del"] and "danger" in r["del"][0]
    assert r["ask"][0] == "Eintrag löschen?" and "„Zonk Band – Moonlight Groove“" in r["ask"][1], r["ask"]
    assert r["ask"][2] == ["Löschen", "Abbrechen"] and r["ask"][3] == 2           # Rückfrage liegt über dem Editor
    assert [f["path"] for f in out["fetches"] if f["method"] == "DELETE"] == ["/api/songs/7"]
    assert r["after"] == [0, 0, "66.422", 1], r["after"]                          # Editor zu, Liste und Rail neu
    assert r["songs0"] == "66.423"


@needs_node
def test_abbrechen_loescht_nichts_und_der_editor_bleibt_offen():
    out = run_page(DELETE_SCEN + """click(askBtn(t('Abbrechen'))); await settle();
      r.after = [qa('.askbox').length, qa('.emodal-overlay').length, !!$('.emodal .dbedit')]; return r;""")
    assert out["errors"] == []
    assert out["result"]["after"] == [0, 1, True]
    assert [f for f in out["fetches"] if f["method"] == "DELETE"] == []


@needs_node
def test_rueckfrage_in_englisch_und_fehler_404_uebersetzt():
    out = run_page(DELETE_SCEN.replace("return {deleted: true, id: 7};", "return {__status: 404, __body: {detail: 'Song 7 nicht gefunden'}};")
                   + """click(askBtn(t('Löschen'))); await settle(); r.msg = txt('.emodal .dbedit .actions .hint'); return r;""", lang="en")
    assert out["errors"] == []
    r = out["result"]
    assert r["ask"][0] == "Delete entry?" and "“Zonk Band – Moonlight Groove”" in r["ask"][1] and r["ask"][2] == ["Delete", "Cancel"]
    assert r["msg"] == "Song 7 not found"


@needs_node
def test_index_zeile_zum_verifizieren_hat_keinen_loeschen_knopf():
    out = run_page(_js_consts() + SONGS_JS + "dbState.q = 'Zonk Band';" + OPEN_DB + """
      click('#dbresult .ibtn.verifybtn'); await settle();
      return [...$('.emodal .dbedit .actions').querySelectorAll('button')].map(b => b.textContent);""")
    assert out["errors"] == []
    assert "Löschen" not in out["result"]


# --- PDF der Track-Datenbank ab 10.000 Zeilen nur nach Rückfrage, Empfehlung XLSX -------------------------
def _export_scen(n: int, choice: str | None) -> str:
    pick = "" if choice is None else f"click([...$('.askbox').querySelectorAll('button')].find(b => b.textContent === {json.dumps(choice)})); await settle();"
    return (f"__api['/api/db/songs'] = u => ({{total: {n}, total_all: {n}, total_verified: {n}, rows: [], index_rows: [],"
            " index_total: 0, index_present: false, missing_counts: {}});\n" + OPEN_DB
            + "click('.dbexport button[data-fmt=pdf]'); await settle();"
              "const r = {ask: $('.askbox') ? [txt('.askbox h3'), txt('.askbox p'), qa('.askbox button').map(b => b.textContent)] : null};"
            + pick + "r.left = qa('.askbox').length; return r;")


def _exports(out) -> list[str]:
    return [f["search"] for f in out["fetches"] if f["path"] == "/api/db/export"]


@needs_node
def test_grosses_pdf_nur_nach_rueckfrage_mit_zeilenzahl_und_xlsx_empfehlung():
    out = run_page(_export_scen(66421, "Als Excel (XLSX) exportieren"), api={"/api/db/export": {}})
    assert out["errors"] == []
    r = out["result"]
    assert "66.421" in r["ask"][0] + r["ask"][1] and "XLSX" in r["ask"][1], r["ask"]
    assert r["ask"][2] == ["Als Excel (XLSX) exportieren", "Trotzdem als PDF", "Abbrechen"] and r["left"] == 0
    assert _exports(out) == ["?fmt=xlsx"]
    pdf = run_page(_export_scen(66421, "Trotzdem als PDF"), api={"/api/db/export": {}})
    assert _exports(pdf) == ["?fmt=pdf"]
    cancel = run_page(_export_scen(66421, "Abbrechen"), api={"/api/db/export": {}})
    assert _exports(cancel) == [] and cancel["result"]["left"] == 0


@needs_node
def test_kleines_pdf_ohne_rueckfrage_und_englischer_dialog():
    small = run_page(_export_scen(9999, None), api={"/api/db/export": {}})
    assert small["errors"] == [] and small["result"]["ask"] is None and _exports(small) == ["?fmt=pdf"]
    en = run_page(_export_scen(10000, "Cancel"), lang="en", api={"/api/db/export": {}})
    assert en["errors"] == []
    assert "10,000" in en["result"]["ask"][0] + en["result"]["ask"][1] and en["result"]["ask"][2] == [
        "Export as Excel (XLSX)", "PDF anyway", "Cancel"]


def test_hidden_gilt_auch_fuer_klassen_mit_display_flex_und_grid():
    """Unter der Aktionsleiste des Editors stand ein leerer Kasten: .autobox{display:flex} schlug das hidden-Attribut
    (ebenso .warnlist an der Tagger-Karte). Eine allgemeine Regel mit !important deckt jede Klasse ab, auch künftige
    und solche mit Nachfahren-Selektor; im Browser prüft das test_webui_runtime (hier ohne CSS-Engine nur die Regel)."""
    src = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    css = src[src.index("<style>"):src.index("</style>")]
    assert re.search(r"^\s*\[hidden\]\{display:none!important\}", css, re.M), "allgemeine [hidden]-Regel fehlt"


@needs_node
def test_team_datei_zeigt_neue_ergaenzte_und_abweichungen_vor_dem_uebernehmen():
    """D16: Team-Datei beim Import — Zahlen, „zu prüfen“-Hinweis und die Abweichungen, eigene Werte bleiben."""
    pre = {"team": True, "rows": 4, "new": 2, "filled": 1, "same": 1, "review": 1, "committed": False, "conflicts_total": 1,
           "conflicts": [{"interpret": "Erika Testerin", "titel": "Südhang", "diffs": [{"field": "komponist", "own": "Anna Zonk", "team": "Hans Zimm"}]}]}
    out = run_page("""click('.nav button[data-tool=db]'); await settle(); renderImportPreview('cuecore_team.csv', __api['/api/ingest/upload']);
      return {text: txt('#dbimportbox'), btn: [...qa('#dbimportbox .actions button')][0].textContent};""",
                   api={"/api/ingest/upload": pre})
    assert out["errors"] == []
    r = out["result"]
    assert "2 neue Songs, 1 ergänzen leere Felder, 1 schon gleich" in r["text"] and "zu prüfen" in r["text"], r
    assert "deins: Anna Zonk" in r["text"] and "Team: Hans Zimm" in r["text"] and r["btn"] == "2 neue übernehmen, 1 ergänzen", r
