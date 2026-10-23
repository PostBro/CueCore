"""Tastatur im Cue-Sheet (Teil D5): ↑/↓ wandern, Enter übernimmt einen Vorschlag, Escape verwirft ihn, der Fokus
steht danach auf dem nächsten Cue. Testdaten frei erfunden."""
from __future__ import annotations

from tests.test_webui_cuesheet import DB, IMPORT, edl
from tests.webui_harness import needs_node, run_page

WARN1 = dict(DB, event_num="001", clip_name="ZZQ_01.wav", title="Nordwand", status="resolved_and_written", band="review",
             labelcode="LC00121", labelname="Atlantic")
WARN2 = dict(WARN1, event_num="002", clip_name="ZZQ_02.wav", title="Südhang")
OK3 = dict(DB, event_num="003", clip_name="ZZQ_03.wav", title="Talweg")
KEY = """const key = (k) => { const ev = new Event('keydown', {bubbles: true, cancelable: true, init: {key: k}});
  document.activeElement.dispatchEvent(ev); return ev.defaultPrevented; };
const rows = () => qa('#eresult tr.etrow'); const at = () => rows().indexOf(document.activeElement);
"""


@needs_node
def test_pfeiltasten_wandern_und_genau_eine_zeile_ist_im_tab_ablauf():
    out = run_page(IMPORT + KEY + """rows()[0].focus(); const r = {start: at()};
      key('ArrowDown'); r.down = at(); key('ArrowDown'); key('ArrowDown'); r.end = at(); key('ArrowUp'); r.up = at();
      key('Home'); r.home = at(); r.tabs = rows().map(x => x.tabIndex); r.keys = txt('#eresult .ekeys'); return r;""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert (r["start"], r["down"], r["end"], r["up"], r["home"]) == (0, 1, 2, 1, 0), r
    assert r["tabs"] == [0, -1, -1] and "Enter" in r["keys"] and "Esc" in r["keys"], r


@needs_node
def test_enter_uebernimmt_den_vorschlag_und_springt_weiter():
    out = run_page(IMPORT + KEY + """rows()[0].focus(); key('Enter'); await settle();
      return {status: edlState.cards.map(c => c.status), focus: at(), title: document.activeElement && txt(document.activeElement.querySelector('.tt'))};""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3), "POST /api/db/verify": {"ok": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["status"][0] == "confirmed" and r["status"][1] == "resolved_and_written", r   # nur der eine Cue (anderer Titel)
    assert r["focus"] == 1 and r["title"] == "Südhang", r


@needs_node
def test_escape_verwirft_den_vorschlag_sichtbar_und_behaelt_ihn_am_cue():
    out = run_page(IMPORT + KEY + """rows()[1].focus(); const prevented = key('Escape'); await settle();
      const c = edlState.cards[1];
      return {prevented, lc: c.labelcode, status: c.status, kept: c.rejected && c.rejected.labelcode, focus: at(),
              row: txt(rows()[1].querySelector('.stt')), prog: txt(edlState.progEl), okEsc: (rows()[2].focus(), key('Escape'))};""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert r["prevented"] is True and r["lc"] == "" and r["status"] == "rejected" and r["kept"] == "LC00121", r
    assert r["row"].startswith("Offen") and "Vorschlag verworfen" in r["row"] and "Vorschlag verworfen" in r["prog"], r
    assert r["focus"] == 2 and r["okEsc"] is False, r   # verifizierter Cue: Escape tut nichts


@needs_node
def test_verwerfen_auch_per_knopf_mit_tooltip():
    out = run_page(IMPORT + """const b = rows => qa('#eresult tr.etrow')[0].querySelector('.ibtn.discard');
      const r = {label: b().getAttribute('aria-label'), okRow: !!qa('#eresult tr.etrow')[1].querySelector('.ibtn.discard')};
      click(b()); await settle(); r.status = edlState.cards[0].status; return r;""",
                   api={"/api/edl/import": edl(WARN1, OK3)})
    assert out["errors"] == []
    assert out["result"] == {"label": "Vorschlag verwerfen (Esc) — Cue wird offen", "okRow": False, "status": "rejected"}


@needs_node
def test_enter_ohne_vorschlag_oeffnet_den_editor():
    out = run_page(IMPORT + KEY + "rows()[0].focus(); key('Enter'); await settle(); return !!$('.emodal .dbedit');",
                   api={"/api/edl/import": edl(OK3)})
    assert out["errors"] == [] and out["result"] is True


# --- D6: Zellen direkt bearbeiten, aus Excel einfügen; D11: Rückgängig und Verlauf -----------------------------
CELL = """const cell = (i, f) => qa('#eresult tr.etrow')[i].querySelector('td.cedit[data-field="' + f + '"]');
const editKey = (k, shift) => { const ev = new Event('keydown', {bubbles: true, cancelable: true, init: {key: k, shiftKey: !!shift}});
  document.activeElement.dispatchEvent(ev); };
const dbl = td => td.dispatchEvent(new Event('dblclick', {bubbles: true}));
"""
SEARCH = {"/api/search": {"mode": "code", "results": [{"labelcode": "00121", "label": "Atlantic", "rechterueckrufe": "R6;R9"}]}}


@needs_node
def test_doppelklick_bearbeitet_die_zelle_enter_uebernimmt_esc_bricht_ab():
    out = run_page(IMPORT + KEY + CELL + """dbl(cell(2, 'komponist')); const inp = document.activeElement; inp.value = 'Erika Testerin';
      editKey('Enter'); await settle(); const r = {k: edlState.cards[2].komponist, cell: txt(cell(2, 'komponist')), focus: at()};
      dbl(cell(2, 'verlag')); document.activeElement.value = 'Zzq Edition'; editKey('Escape'); await settle();
      r.verlag = edlState.cards[2].verlag || ''; r.rowStatus = edlState.cards[2].status; return r;""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert r["k"] == "Erika Testerin" and r["cell"] == "Erika Testerin" and r["focus"] == 2, r
    assert r["verlag"] == "" and r["rowStatus"] == "found_in_db", r   # Escape in der Zelle verwirft nichts am Cue


@needs_node
def test_labelcode_in_der_zelle_nur_mit_gvl_master_und_tab_springt_weiter():
    out = run_page(IMPORT + KEY + CELL + """rows()[2].focus(); key('F2'); const r = {first: document.activeElement.dataset.field};
      editKey('Tab'); await settle(); r.next = document.activeElement.dataset.field; editKey('Escape'); await settle();
      dbl(cell(2, 'labelcode')); document.activeElement.value = 'LC 00121'; editKey('Enter'); await settle();
      const c = edlState.cards[2]; r.lc = [c.labelcode, c.labelname, c.rechterueckrufe, c.status];
      dbl(cell(2, 'labelcode')); document.activeElement.value = '99999'; editKey('Enter'); await settle();
      r.kept = edlState.cards[2].labelcode; r.prog = txt(edlState.progEl); return r;""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3), **SEARCH, "/api/search?q=99999": {"mode": "code", "results": []}})
    assert out["errors"] == []
    r = out["result"]
    assert r["first"] == "komponist" and r["next"] == "texter", r
    assert r["lc"] == ["LC00121", "Atlantic", "R6;R9", "confirmed"], r
    assert r["kept"] == "LC00121" and "LC99999 steht nicht im GVL-Master" in r["prog"], r


@needs_node
def test_einfuegen_aus_excel_fuellt_mehrere_zellen_ab_der_gewaehlten_zeile():
    out = run_page(IMPORT + KEY + CELL + """rows()[1].focus();
      const ev = new Event('paste', {bubbles: true, cancelable: true, init: {clipboardData: {getData: () => 'Hans Zimm\\tErika Testerin\\tZzq Edition\\nAnna Zonk\\t\\tNordwand Verlag\\nÜberzählig\\n'}}});
      document.activeElement.dispatchEvent(ev); await settle();
      return {cards: edlState.cards.map(c => [c.komponist || '', c.texter || '', c.verlag || '']), prog: txt(edlState.progEl), undo: edlState.undo.length};""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert r["cards"][1] == ["Hans Zimm", "Erika Testerin", "Zzq Edition"] and r["cards"][2][0] == "Anna Zonk", r
    assert r["cards"][2][2] == "Nordwand Verlag" and r["cards"][0][0] == WARN1.get("komponist", ""), r
    assert "Zelle(n) eingefügt" in r["prog"] and "1 Zeile(n) passten nicht mehr" in r["prog"] and r["undo"] == 1, r


@needs_node
def test_rueckgaengig_nimmt_den_letzten_schritt_zurueck_auch_ein_geloeschtes_cue():
    out = run_page(IMPORT + KEY + """rows()[0].focus(); key('Escape'); await settle();
      click(qa('#eresult tr.etrow')[2].querySelector('.ibtn.danger')); await settle();
      const r = {n: edlState.cards.length, hist: (edlState.cards[0]._hist || []).map(h => h.label)};
      const z = new Event('keydown', {bubbles: true, cancelable: true, init: {key: 'z', metaKey: true}}); document.body.dispatchEvent(z); await settle();
      r.back = edlState.cards.map(c => c.title); document.body.dispatchEvent(new Event('keydown', {bubbles: true, cancelable: true, init: {key: 'z', ctrlKey: true}}));
      await settle(); r.lc = edlState.cards[0].labelcode; r.status = edlState.cards[0].status; r.prog = txt(edlState.progEl);
      r.btn = qa('#eresult .asum .act button').find(b => b.textContent === 'Rückgängig').disabled; return r;""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert r["n"] == 2 and r["hist"] == ["Vorschlag verworfen"], r
    assert r["back"] == ["Nordwand", "Südhang", "Talweg"], r
    assert r["lc"] == "LC00121" and r["status"] == "resolved_and_written" and "Rückgängig: Vorschlag verworfen" in r["prog"], r
    assert r["btn"] is True, r


@needs_node
def test_verlauf_im_editor_und_letzte_aenderung_nur_dieses_cues_zuruecknehmen():
    out = run_page(IMPORT + KEY + CELL + """dbl(cell(1, 'komponist')); document.activeElement.value = 'Anna Zonk'; editKey('Enter'); await settle();
      dbl(cell(2, 'komponist')); document.activeElement.value = 'Hans Zimm'; editKey('Enter'); await settle();
      click([...qa('#eresult tr.etrow')[1].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten'))); await settle();
      const r = {hist: txt('.emodal .cuehist')}; click([...qa('.emodal .cuehist button')][0]); await settle();
      r.k = edlState.cards.map(c => c.komponist || ''); r.undo = edlState.undo.length; return r;""",
                   api={"/api/edl/import": edl(WARN1, WARN2, OK3)})
    assert out["errors"] == []
    r = out["result"]
    assert "Verlauf (1)" in r["hist"] and "In der Tabelle geändert" in r["hist"], r
    assert r["k"][1] == WARN2.get("komponist", "") and r["k"][2] == "Hans Zimm" and r["undo"] == 1, r


# --- D7: Recherche im Hintergrund — fertige Cues bearbeitbar, Nutzeränderungen bleiben, Abbrechen --------------
OPEN_A = dict(DB, event_num="011", clip_name="ZZQ_11.wav", artist="Erika Testerin", title="Talweg", labelcode="", labelname="",
              komponist="", status="unresolved")
OPEN_B = dict(OPEN_A, event_num="012", clip_name="ZZQ_12.wav", title="Gratweg")
NET = {"entries": [{"resolved": True, "labelcode": "LC00121", "labelname": "Atlantic", "komponist": "Hans Zimm", "band": "review"},
                   {"resolved": True, "labelcode": "LC00121", "labelname": "Atlantic", "komponist": "Hans Zimm", "band": "review"}],
       "stats": {"calls": 2, "failures": 0}}


@needs_node
def test_waehrend_des_laufs_geaenderter_cue_bleibt_und_rueckgaengig_nimmt_ihn_nicht_mit():
    out = run_page(IMPORT + """const [a, b] = edlState.cards; const run = completeMetadata();
      cueChange('Im Editor gespeichert', [a], () => { a.komponist = 'Erika Testerin'; });   // Mensch ändert A, Lauf läuft
      await run; await settle();
      const r = {a: [a.labelcode || '', a.komponist], b: [b.labelcode, b.komponist]};
      undoEdl(); await settle(); r.afterUndo = [a.komponist, b.labelcode || '']; return r;""",
                   api={"/api/edl/import": edl(OPEN_A, OPEN_B), "/api/settings": {"configured": {}}, "POST /api/edl/resolve-cues": NET})
    assert out["errors"] == []
    r = out["result"]
    assert r["a"] == ["", "Erika Testerin"] and r["b"] == ["LC00121", "Hans Zimm"], r
    assert r["afterUndo"] == ["Erika Testerin", ""], r


@needs_node
def test_abbrechen_beendet_den_lauf_und_sagt_es():
    out = run_page(IMPORT + """const run = completeMetadata(); const x = [...qa('#eresult .asum .act button')].find(b => b.textContent === 'Abbrechen');
      const shown = !!x && !x.hidden; click(x); await run; await settle();
      return {shown, prog: txt(edlState.progEl), lc: edlState.cards[0].labelcode || '', busy: edlState.busy};""",
                   api={"/api/edl/import": edl(OPEN_A, OPEN_B), "/api/settings": {"configured": {}}, "POST /api/edl/resolve-cues": NET})
    assert out["errors"] == []
    r = out["result"]
    assert r["shown"] is True and r["lc"] == "" and r["busy"] is False and r["prog"].startswith("Abgebrochen"), r


@needs_node
def test_kuerzel_hinweis_in_zeile_und_editor_fuellt_nur_auf_klick():
    c = dict(OPEN_A, artist="", title="", clip_name="ZQX1234_05.wav", status="unparsable_clip_name",
             prefix_hints={"prefix": "ZQX", "candidates": [{"labelcode": "24584", "label": "MOM & POP", "sources": "Liste A"},
                                                          {"labelcode": "00100", "label": "LUMAS RECORDS", "sources": "Liste B"}]})
    out = run_page(IMPORT + """const r = {row: txt(qa('#eresult tr.etrow')[0].querySelector('.stt'))};
      click([...qa('#eresult tr.etrow')[0].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten'))); await settle();
      const lc = () => [...qa('.emodal .dbedit .field')].find(f => txt(f.querySelector('label')) === 'Labelcode').querySelector('input').value;
      r.head = txt('.emodal .suggests .sug-h'); r.before = lc(); click(qa('.emodal .suggests .sugbtn')[1]); r.after = lc();
      r.card = edlState.cards[0].labelcode || ''; return r;""", api={"/api/edl/import": edl(c)})
    assert out["errors"] == []
    r = out["result"]
    assert "Kürzel ZQX: 2 mögliche(s) Label(s)" in r["row"] and "ZQX" in r["head"], r
    assert r["before"] == "" and r["after"] == "00100" and r["card"] == "", r     # erst Speichern übernimmt
