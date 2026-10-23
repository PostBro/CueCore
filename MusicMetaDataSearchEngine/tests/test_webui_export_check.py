"""Cue-Sheet vor der Abgabe (Teil D): Prüfung vor dem Export mit Pflichtfeldern und Rechterückrufen, „Alle sicheren
übernehmen“, eine Entscheidung gilt für alle gleichen Cues. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.test_webui_cuesheet import DB, EDIT, IMPORT, edl
from tests.webui_harness import needs_node, run_page

FULL = dict(DB, komponist="Hans Zimm", gema_werknummer="10000001-001")
RR = dict(FULL, event_num="002", clip_name="ZZQ_02.wav", title="Nordwand", labelcode="LC00121", labelname="Atlantic",
          rechterueckrufe="R6;R9")
NOAUTH = dict(DB, event_num="003", clip_name="ZZQ_03.wav", title="Ohne Autor")
SAFE_NET = dict(DB, event_num="004", clip_name="ZZQ_04.wav", title="Sicher Netz", status="resolved_and_written",
                band="review", confidence=0.5, dual_source=True, labelcode="LC00121")
WEAK_NET = dict(SAFE_NET, event_num="005", clip_name="ZZQ_05.wav", title="Schwach", confidence=0.3, dual_source=False)
INDEX_HIT = dict(DB, event_num="006", clip_name="ZZQ_06.wav", title="Index", status="index_hit")
CATALOG = dict(DB, event_num="007", clip_name="CCB1070_24.wav", title="", artist="", status="catalog_hint",
               catalog_hint="Library-Kürzel CCB (Lieferung Cezame)", labelcode="10347")

EXPORT = "const xbtn = () => qa('#eresult .egroup.export button')[0]; click(xbtn()); await settle();\n"
DIALOG = "const dlg = () => $('.emodal.xcheck');\n"


@needs_node
def test_alles_gefuellt_ohne_rechterueckruf_exportiert_ohne_dialog():
    out = run_page(IMPORT + DIALOG + EXPORT + "return !!dlg();",
                   api={"/api/edl/import": edl(FULL), "POST /api/edl/export": {"ok": True}})
    assert out["errors"] == [] and out["result"] is False
    assert any(f["path"] == "/api/edl/export" for f in out["fetches"])


@needs_node
def test_dialog_listet_fehlende_pflichtfelder_und_verlangt_die_rechterueckruf_bestaetigung():
    out = run_page(IMPORT + DIALOG + EXPORT + """const go = () => [...dlg().querySelectorAll('.tiepick-foot button')][1];
      const r = {heads: texts('.xcheck-sec h4'), blocked: go().disabled, label: go().textContent};
      const confirm = dlg().querySelector('.xcheck-confirm input'); confirm.checked = true;
      confirm.dispatchEvent(new Event('change')); await settle(); r.after = go().disabled;
      // Pflichtfeld abwählen: Komponist zählt nicht mehr, die Wahl bleibt gespeichert
      const kb = [...dlg().querySelectorAll('.xcheck-req input')][1]; kb.checked = false; kb.dispatchEvent(new Event('change'));
      await settle(); r.heads2 = texts('.xcheck-sec h4'); r.stored = localStorage.getItem('cuecore.exportRequired');
      return r;""", api={"/api/edl/import": edl(RR, NOAUTH)})
    sent = [f for f in out["fetches"] if f["path"] == "/api/export-templates" and f["method"] == "POST"]
    assert out["errors"] == []
    r = out["result"]
    assert r["heads"] == ["Pflichtfelder fehlen bei 1 Cue(s)", "Rechterückrufe bei 1 Cue(s)"]
    assert r["blocked"] is True and r["after"] is False and r["label"] == "Trotzdem exportieren (XLSX)"
    assert r["heads2"] == ["Pflichtfelder fehlen bei 1 Cue(s)", "Rechterückrufe bei 1 Cue(s)"]   # GEMA-Werknr. fehlt noch
    assert r["stored"] is None                                    # D2: die Wahl gehört der Vorlage, nicht dem Browser
    import json
    assert json.loads(sent[-1]["body"])["templates"][0]["required"] == ["labelcode", "gema_werknummer"]


@needs_node
def test_dialog_auf_englisch():
    out = run_page(IMPORT + DIALOG + EXPORT + "return [txt('.xcheck .tiepick-head h3'), texts('.xcheck-sec h4')];",
                   lang="en", api={"/api/edl/import": edl(RR)})
    assert out["errors"] == []
    assert out["result"] == ["Check before export", ["Rights recalls in 1 cue(s)"]]


@needs_node
def test_alle_sicheren_uebernehmen_nimmt_nur_zwei_quellen_und_library_kuerzel():
    out = run_page(IMPORT + """const sb = [...qa('#eresult .asum .act button')].find(b => b.textContent.startsWith('Alle sicheren'));
      const r = {label: sb && sb.textContent}; click(sb); await settle();
      r.list = texts('.emodal.xcheck .xcheck-list li .num');
      click([...$('.emodal.xcheck').querySelectorAll('.tiepick-foot button')][1]); await settle(); await settle();
      r.status = edlState.cards.map(c => [c.event_num, c.status]); return r;""",
                   api={"/api/edl/import": edl(SAFE_NET, WEAK_NET, INDEX_HIT, CATALOG), "/api/db/verify": {"ok": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["label"] == "Alle sicheren übernehmen (2)" and r["list"] == ["004", "007"]
    assert r["status"] == [["004", "confirmed"], ["005", "resolved_and_written"], ["006", "index_hit"],
                           ["007", "confirmed"]]


@needs_node
def test_editor_aenderung_gilt_fuer_jedes_vorkommen_des_titels():
    twin = dict(DB, event_num="002", clip_name="ZONK_01_b.wav", status="index_hit")
    other = dict(DB, event_num="003", clip_name="ANDERS.wav", title="Anderer Titel")
    out = run_page(IMPORT + EDIT + """await openEd(0);
      field(4).value = 'New Pub'; click(saveBtn()); await settle(); await sleep(700); await settle();
      return edlState.cards.map(c => [c.event_num, c.verlag || '', c.status]);""",
                   api={"/api/edl/import": edl(DB, twin, other), "/api/db/lookup": {"db": {}, "db_id": 7},
                        "POST /api/db/song/7": {"ok": True, "fields": {"verlag": "New Pub"}}})
    assert out["errors"] == []
    assert out["result"] == [["001", "New Pub", "found_in_db"], ["002", "New Pub", "found_in_db"],
                             ["003", "", "found_in_db"]]


@needs_node
def test_alter_gvl_master_macht_den_punkt_in_der_seitenleiste_gelb():
    """D17: wie die Pille auf der Startseite wird der GVL-Punkt in der Seitenleiste ab 90 Tagen gelb."""
    old = run_page("await refreshHealth(); await settle(); return $('#dot').className;",
                   api={"/api/health": {"ready": True, "rows": 1, "songs": 1, "source_version": "2026-01-01", "index": {}}})
    new = run_page("await refreshHealth(); await settle(); return $('#dot').className;",
                   api={"/api/health": {"ready": True, "rows": 1, "songs": 1, "source_version": "2099-01-01", "index": {}}})
    assert old["errors"] == [] and old["result"] == "dot warn" and new["result"] == "dot ok"


# --- D13: Notiz und Rückfrage pro Cue ---------------------------------------------------------------------
NOTE_SCEN = """click([...qa('#eresult tr.etrow')[0].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten')));
  await settle(); const ta = $('.emodal .cuenote textarea'); ta.value = 'Lizenz bei Erika Testerin nachfragen';
  ta.dispatchEvent(new Event('input')); const cb = $('.emodal .cuenote input[type=checkbox]'); cb.checked = true;
  cb.dispatchEvent(new Event('change')); click([...$('.emodal .dbedit .actions').querySelectorAll('button')].find(b => b.textContent === t('Schließen')));
  await settle();
"""


@needs_node
def test_notiz_und_rueckfrage_erscheinen_in_der_zeile_ohne_speichern_in_die_datenbank():
    out = run_page(IMPORT + NOTE_SCEN + """const row = qa('#eresult tr.etrow')[0];
      return {chip: txt(row.querySelector('.chip.ask')), note: txt(row.querySelector('.tnote')), card: [edlState.cards[0].note, edlState.cards[0].query]};""",
                   api={"/api/edl/import": edl(FULL)})
    assert out["errors"] == []
    r = out["result"]
    assert r["chip"] == "Rückfrage" and r["note"] == "Lizenz bei Erika Testerin nachfragen", r
    assert r["card"] == ["Lizenz bei Erika Testerin nachfragen", True], r
    assert not any(f["path"].startswith("/api/db/song") for f in out["fetches"])


@needs_node
def test_offene_rueckfrage_holt_den_pruefdialog_vor_den_export():
    out = run_page(IMPORT + NOTE_SCEN + DIALOG + EXPORT + """return {heads: texts('.xcheck-sec h4'), list: txt('.xcheck-sec .xcheck-list'),
      go: [...dlg().querySelectorAll('.tiepick-foot button')][1].textContent};""",
                   api={"/api/edl/import": edl(FULL), "POST /api/edl/export": {"ok": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["heads"] == ["Offene Rückfragen: 1"] and "Lizenz bei Erika Testerin nachfragen" in r["list"], r
    assert r["go"] == "Trotzdem exportieren (XLSX)", r


# --- D10: Herkunft und Prüfdatum je Feld im Cue-Editor ------------------------------------------------------
SRC_SCEN = """click([...qa('#eresult tr.etrow')[0].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten')));
  await settle(); const src = {}; qa('.emodal .dbedit .field').forEach(f => { const s = f.querySelector('.fsrc');
  if (s) src[txt(f.querySelector('label')).replace(' *', '')] = [txt(s), s.classList.contains('chk')]; });
"""


@needs_node
def test_lieferung_mit_datum_und_musicbrainz_gema_als_bitte_pruefen():
    lib = dict(FULL, quelle="library:apl", date_verified="2026-10-19", matched_by="file", library="apl",
               gema_source="musicbrainz", label_geliefert="LC aus dem GVL-Master über den Labelnamen")
    out = run_page(IMPORT + SRC_SCEN + "return src;", api={"/api/edl/import": edl(lib)})
    assert out["errors"] == []
    src = out["result"]
    assert src["Komponist"] == ["Lieferung APL Music · geprüft 19.10.2026", False], src
    assert src["GEMA-Werknr."] == ["laut MusicBrainz, bitte prüfen", True], src
    assert src["Labelcode"][0] == "Lieferung APL Music · geprüft 19.10.2026 · LC aus dem GVL-Master über den Labelnamen", src


@needs_node
def test_upm_heisst_in_der_herkunft_nicht_upm_und_netz_autoren_sind_vorschlag():
    upm = dict(FULL, quelle="library:upm", sublibrary="Zzq Production Music", date_verified="2026-10-08")
    net = dict(FULL, status="resolved_and_written", band="auto", author_new=True, author_source="genius")
    out = run_page(IMPORT + SRC_SCEN + "return src;", api={"/api/edl/import": edl(upm)})
    assert out["result"]["Titel"][0] == "Lieferung Zzq Production Music · geprüft 08.10.2026" and "UPM" not in str(out["result"])
    out = run_page(IMPORT + SRC_SCEN + "return src;", api={"/api/edl/import": edl(net)})
    r = out["result"]
    assert r["Komponist"] == ["Vorschlag aus Genius, nicht gespeichert — bitte prüfen", True], r
    assert r["Labelcode"][0] == "Netzsuche (MusicBrainz, Discogs, Deezer, Wikidata)", r


@needs_node
def test_von_hand_geaendert_steht_nach_dem_speichern_am_feld():
    saved = {"ok": True, "id": 7, "fields": {}}
    out = run_page(IMPORT + SRC_SCEN + """const inp = [...qa('.emodal .dbedit .field')].find(f => txt(f.querySelector('label')) === 'Komponist').querySelector('input');
      inp.value = 'Erika Testerin'; inp.dispatchEvent(new Event('input'));
      const live = txt(inp.parentNode.querySelector('.fsrc'));
      click([...$('.emodal .dbedit .actions').querySelectorAll('button')].find(b => b.textContent === t('Änderungen speichern') || b.textContent === t('In Datenbank aufnehmen')));
      await settle(); await new Promise(r => setTimeout(r, 700)); await settle();
      return {live, card: edlState.cards[0].field_src};""",
                   api={"/api/edl/import": edl(dict(FULL, quelle="manuell", date_verified="2026-10-01")), "POST /api/db/lookup": {"db_id": 7},
                        "POST /api/db/song/7": saved})
    assert out["errors"] == []
    r = out["result"]
    assert r["live"] == "von Hand geändert — gilt mit „Speichern“", r
    assert list(r["card"]) == ["komponist"], r


@needs_node
def test_zuletzt_bestaetigt_steht_am_cue_und_uebernehmen_sendet_das_projekt():
    seen = dict(FULL, confirmed_at="2026-10-12T09:00:00+00:00", confirmed_project="Testprojekt Folge 1")
    out = run_page(IMPORT + """edlState.name = 'Testprojekt Folge 2';
      return txt(qa('#eresult tr.etrow')[0].querySelector('.stt'));""", api={"/api/edl/import": edl(seen)})
    assert out["errors"] == [] and "zuletzt bestätigt 12.10.2026 · Testprojekt Folge 1" in out["result"], out["result"]
    out = run_page(IMPORT + """edlState.name = 'Testprojekt Folge 2'; click(qa('#eresult tr.etrow')[0].querySelector('.ibtn.accept'));
      await settle(); return null;""", api={"/api/edl/import": edl(SAFE_NET), "POST /api/db/verify": {"ok": True}})
    post = [f for f in out["fetches"] if f["path"] == "/api/db/verify"][-1]
    assert json.loads(post["body"])["project"] == "Testprojekt Folge 2"
