"""Cue-Sheet der Oberfläche (web/index.html) im DOM-Ersatz: Schrittleiste, Library-Abgleich, Zusammenfassen,
Rechterückruf, Zeilen-Editor. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.webui_harness import needs_node, run_page

DB = {"event_num": "001", "clip_name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "labelname": "ACME",
      "labelcode": "LC24584", "duration_sec": 20, "status": "found_in_db", "rechterueckrufe": ""}
OPEN = {"event_num": "002", "clip_name": "ATMO_Wind.wav", "artist": "", "title": "", "labelname": "", "labelcode": "",
        "duration_sec": 5, "status": "unparsable_clip_name", "rechterueckrufe": ""}


def edl(*cards) -> dict:
    return {"cards": [dict(c) for c in cards], "cues": len(cards), "summary": {},
            "total_duration_sec": sum(c["duration_sec"] for c in cards)}


IMPORT = ("click('.nav button[data-tool=edl]'); edlState.content = 'x'; edlState.list = null;"
          "await runEdlImport(false); await settle();\n")
STEPS = "qa('#tool-edl .step').map(s => s.className)"


@needs_node
def test_schrittleiste_steht_nach_dem_einlesen_auf_exportieren_wenn_alles_verifiziert():
    ok = run_page(IMPORT + f"return [{STEPS}, txt('#eresult .asum .hint')];", api={"/api/edl/import": edl(DB)})
    assert ok["errors"] == []
    assert ok["result"] == [["step done", "step done", "step on"], "Alles verifiziert — bereit für die Abgabe"]
    offen = run_page(IMPORT + f"return {STEPS};", api={"/api/edl/import": edl(DB, OPEN)})
    assert offen["result"] == ["step done", "step on", "step"]


SETTINGS_TM = {"groups": [], "paths": [], "texts": [], "keys": [], "partners": [], "partner_fields": {},
               "configured": {"trackmeta": True}}


def _lib_entry(name: str, title: str) -> dict:
    return {"query_filename": name, "fileName": name, "matched": True, "partner": "Partner A", "interpret": "Hans Zimm",
            "titel": title, "komponist": "Hans Zimm", "labelcode": "LC48731", "labelname": "Volta Music", "rechterueckrufe": ""}


@needs_node
def test_library_treffer_landen_auch_bei_clipnamen_mit_pfad_auf_der_karte():
    """Premiere/Resolve schreiben den vollen Pfad in SOURCE FILE; das Backend fragt und schlüsselt nur den Dateinamen."""
    cards = [dict(OPEN, event_num="001", clip_name="/Volumes/Musik/UPM_VOLTA_123_1.wav"),
             dict(OPEN, event_num="002", clip_name="C:\\Musik\\Volta\\UPM_VOLTA_124_1.wav"),
             dict(OPEN, event_num="003", clip_name="UPM_VOLTA_125_1.wav")]
    scan = {"ok": True, "entries": [_lib_entry("UPM_VOLTA_123_1.wav", "Dark Matter"), _lib_entry("UPM_VOLTA_124_1.wav", "Light Matter"),
                                    _lib_entry("UPM_VOLTA_125_1.wav", "No Matter")],
            "partners": [{"partner": "Partner A", "ok": True, "matched": 3}]}
    out = run_page(IMPORT + "await completeMetadata(); await settle();"
                   "return edlState.cards.map(c => [c.status, c.title, lcNum(c.labelcode)]);",
                   api={"/api/edl/import": edl(*cards), "/api/settings": SETTINGS_TM, "/api/edl/trackmeta-scan": scan})
    assert out["errors"] == []
    assert out["result"] == [["library_matched", "Dark Matter", "48731"], ["library_matched", "Light Matter", "48731"],
                             ["library_matched", "No Matter", "48731"]]


# --- Zusammenfassen / Stems arbeiten auf dem Stand der Ansicht, nicht auf einem neuen Import ---------------
def _cue(n: str, title: str, dur: int, **kw) -> dict:
    return dict(DB, event_num=n, clip_name=f"ZONK_{n}.wav", title=title, duration_sec=dur, **kw)


MERGE_CARDS = [_cue("001", "Moonlight Groove", 20), dict(OPEN, event_num="002", clip_name="ATMO_Wind.wav"),
               _cue("003", "Moonlight Groove", 10), _cue("004", "Sunset (Drums)", 4), _cue("005", "Sunset - Bass 2", 6)]
TOGGLE = "const tog = (id, on) => { $(id).checked = on; fire(id, 'change'); };\n"
ROWS = "edlState.cards.map(c => [c.title || c.clip_name, c.duration_sec, c.merged_count || 1])"


@needs_node
def test_zusammenfassen_behaelt_geloeschte_cues_draussen_und_laedt_nicht_neu():
    out = run_page(IMPORT + TOGGLE + f"""
      const atmo = qa('#eresult tr.etrow').find(tr => tr.textContent.includes('ATMO_Wind'));
      click(atmo.querySelector('.ibtn.danger')); await settle();
      const r = {{deleted: edlState.cards.length}};
      tog('#emerge', true); await settle(); r.merged = {ROWS};
      tog('#estems', true); await settle(); r.stems = {ROWS};
      tog('#emerge', false); tog('#estems', false); await settle(); r.off = {ROWS};
      r.table = texts('#eresult tr.etrow .tt'); return r;""", api={"/api/edl/import": edl(*MERGE_CARDS)})
    assert out["errors"] == []
    r = out["result"]
    assert r["deleted"] == 4
    assert r["merged"] == [["Moonlight Groove", 30, 2], ["Sunset (Drums)", 4, 1], ["Sunset - Bass 2", 6, 1]]
    assert r["stems"] == [["Moonlight Groove", 30, 2], ["Sunset", 10, 2]]
    assert r["off"] == [["Moonlight Groove", 20, 1], ["Moonlight Groove", 10, 1], ["Sunset (Drums)", 4, 1], ["Sunset - Bass 2", 6, 1]]
    assert not any("ATMO" in x for x in r["table"])
    assert [f["path"] for f in out["fetches"]].count("/api/edl/import") == 1      # kein Neu-Import beim Umschalten


@needs_node
def test_aenderungen_an_der_zusammengefassten_karte_gelten_danach_fuer_jeden_cue():
    cards = [_cue("001", "Moonlight Groove", 20, status="index_hit", labelcode="24584"),
             _cue("002", "Moonlight Groove", 10, status="index_hit", labelcode="24584", verlag="Pubco")]
    out = run_page(IMPORT + TOGGLE + f"""
      tog('#emerge', true); await settle(); const r = {{merged: {ROWS}, verlag: edlState.cards[0].verlag}};
      click('#eresult .ibtn.accept'); await settle(); r.status = edlState.cards.map(c => c.status);
      edlState.cards[0].komponist = 'Hans Zimm';            // wie nach Speichern im Editor
      tog('#emerge', false); await settle();
      r.off = edlState.cards.map(c => [c.event_num, c.duration_sec, c.status, c.komponist || '', c.verlag || '']); return r;""",
                   api={"/api/edl/import": edl(*cards), "/api/db/verify": {"ok": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["merged"] == [["Moonlight Groove", 30, 2]] and r["verlag"] == "Pubco"
    assert r["status"] == ["confirmed"]
    # was nach dem Zusammenfassen an der Karte geändert wurde („Übernommen“, Komponist), gilt danach für beide
    # Cues; der beim Zusammenfassen nur angezeigte Verlag aus Cue 002 bleibt bei Cue 002
    assert r["off"] == [["001", 20, "confirmed", "Hans Zimm", ""], ["002", 10, "confirmed", "Hans Zimm", "Pubco"]]


@needs_node
def test_haken_beim_einlesen_fasst_ohne_server_merge_zusammen():
    out = run_page("click('.nav button[data-tool=edl]'); $('#emerge').checked = true;" + IMPORT.split(";", 1)[1]
                   + f"return {ROWS};", api={"/api/edl/import": edl(*MERGE_CARDS)})
    assert out["errors"] == []
    assert out["result"] == [["Moonlight Groove", 30, 2], ["ATMO_Wind.wav", 5, 1], ["Sunset (Drums)", 4, 1], ["Sunset - Bass 2", 6, 1]]


@needs_node
def test_stem_regeln_und_zusammenfassen_wie_im_backend():
    """Die Oberfläche fasst selbst zusammen (stripStem/mergeEdlCards) — gleiche Regeln wie edl_import.merge_cards."""
    from core.edl_import import merge_cards, strip_stem
    titles = ["Sunrise (Drums)", "Sunrise - Bass 2", "Sunrise (Live)", "Track - Reprise", "Song_Vocals", "Song (Stem Keys)",
              "Ballad - Strings - Piano", "Fx", "Sunrise [vox]", "Ünder – Synth", "Moon (Drums", "  Padded - Pad  ", ""]
    cards = [{"artist": a, "title": t_, "clip_name": c, "duration_sec": d, "labelcode": lc, "verlag": v}
             for a, t_, c, d, lc, v in [("Zonk", "Sunrise (Drums)", "a.wav", 3, "", ""), ("zonk", "Sunrise - Bass 2", "b.wav", 4, "24584", "Pub"),
                                       ("Zonk", "Sunrise", "c.wav", 5, "11111", ""), ("", "", "LOOP_01.wav", 2, "", ""),
                                       ("", "", "loop_01.wav", 2, "", ""), ("", "", "", 1, "", ""), ("Other", "Sunrise (Drums)", "d.wav", 1, "", "")]]
    out = run_page(f"""const titles = {json.dumps(titles)}, cards = {json.dumps(cards)};
      const pick = cs => cs.map(c => [c.title, c.duration_sec, c.merged_count || null, c.labelcode, c.verlag]);
      return {{titles: titles.map(stripStem), m: [[true, false], [false, true], [true, true]].map(([a, b]) =>
               pick(mergeEdlCards(JSON.parse(JSON.stringify(cards)), a, b)))}};""")
    assert out["errors"] == []
    assert out["result"]["titles"] == [strip_stem(x) for x in titles]
    pick = lambda cs: [[c["title"], c["duration_sec"], c.get("merged_count"), c["labelcode"], c["verlag"]] for c in cs]
    for got, (a, b) in zip(out["result"]["m"], [(True, False), (False, True), (True, True)]):
        assert got == pick(merge_cards(json.loads(json.dumps(cards)), merge_identical=a, unify_stems=b)), (a, b)


# --- Rechterückruf in der Cue-Tabelle, auch für LCs, die erst in der Ansicht auf die Karte kommen ----------
LC_CELL = "qa('#eresult tr.etrow').map(tr => { const td = tr.children[7]; return [txt(td.querySelector('.lcv')), " \
          "td.querySelector('.chip.rr') ? txt(td.querySelector('.chip.rr')) : null, td.querySelector('.lcnote') ? txt(td.querySelector('.lcnote')) : null]; })"


@needs_node
def test_cue_tabelle_zeigt_den_rechterueckruf_neben_dem_lc_und_die_lieferungsnotiz_bleibt():
    cards = [dict(DB, labelcode="LC00121", labelname="Atlantic", rechterueckrufe="R6;R9"),
             dict(DB, event_num="002", clip_name="UPM_BBC_1.wav", title="Sea", labelcode="LC27926", labelname="BBC",
                  rechterueckrufe="R12", label_geliefert="BBC Production Music · LC21110"),
             dict(DB, event_num="003", clip_name="ZONK_03.wav", title="Dawn")]
    scen = IMPORT + f"const r = {{de: {LC_CELL}, tip: $('#eresult .chip.rr').getAttribute('title')}};" \
                    f"click('#langbtn'); await settle(); r.en = {LC_CELL}; r.tipEn = $('#eresult .chip.rr').getAttribute('title'); return r;"
    out = run_page(scen, api={"/api/edl/import": edl(*cards)})
    assert out["errors"] == []
    r = out["result"]
    assert r["de"] == [["00121", "RR R6;R9", None], ["27926", "RR R12", "geliefert: BBC Production Music · LC21110"],
                       ["24584", None, None]]
    assert "labelrecherche.gvl.de" in r["tip"] and "Rechterückruf" in r["tip"]
    assert r["en"][1] == ["27926", "RR R12", "delivered: BBC Production Music · LC21110"] and "Rights recall" in r["tipEn"], r


@needs_node
def test_neuer_lc_aus_netz_library_und_gleichstand_bringt_seinen_rechterueckruf_mit():
    net = dict(OPEN, event_num="001", clip_name="AURORA_Runaway.wav", artist="AURORA", title="Runaway", status="missing_no_resolver",
               komponist="Aurora Aksnes")
    lib = dict(OPEN, event_num="002", clip_name="/Vol/UPM_VOLTA_123_1.wav")
    tie = dict(OPEN, event_num="003", clip_name="ZONK_03.wav", artist="Zonk Band", title="Sunset", status="ambiguous",
               tied=[{"labelcode": "24584", "label": "ACME", "sources": ["discogs"], "confidence": 0.6},
                     {"labelcode": "11111", "label": "BETA", "sources": ["musicbrainz"], "confidence": 0.6}])
    resolved = {"entries": [{"resolved": True, "status": "resolved_and_written", "labelcode": "00171", "labelname": "Decca",
                             "rechterueckrufe": "R6", "band": "auto"}]}
    scan = {"ok": True, "entries": [dict(_lib_entry("UPM_VOLTA_123_1.wav", "Dark Matter"), rechterueckrufe="R13")],
            "partners": [{"partner": "Partner A", "ok": True, "matched": 1}]}
    search = {"mode": "code", "results": [{"labelcode": "24584", "label": "ACME", "rechterueckrufe": "R12"}]}
    out = run_page(IMPORT + f"""await completeMetadata(); await settle();
      const t3 = edlState.cards[2]; await chooseTie(t3, t3.tied[0]); await settle();
      return {{cards: edlState.cards.map(c => [lcNum(c.labelcode), c.rechterueckrufe || '']), table: {LC_CELL}}};""",
                   api={"/api/edl/import": edl(net, lib, tie), "/api/settings": SETTINGS_TM, "/api/edl/trackmeta-scan": scan,
                        "/api/edl/resolve-cues": resolved, "/api/search": search, "/api/db/verify": {"ok": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["cards"] == [["00171", "R6"], ["48731", "R13"], ["24584", "R12"]]
    assert [x[1] for x in r["table"]] == ["RR R6", "RR R13", "RR R12"]


# --- Zeilen-Editor bearbeitet vorhandene songs.db-Zeilen statt sie neu anzulegen (409) -------------------
EDIT = """const row = i => qa('#eresult tr.etrow')[i];
  const openEd = async i => { click([...row(i).querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten'))); await settle(); };
  const ed = () => $('.emodal .dbedit'), field = n => ed().querySelectorAll('input')[n];   // 0 Interpret … 4 Verlag, 6 Labelcode
  const saveBtn = () => [...ed().querySelectorAll('.actions button')][1];
"""
NAMED = dict(DB, verlag="Pubco", komponist="Hans Zimm")                         # Treffer über Interpret+Titel: ohne id
FILEHIT = dict(DB, event_num="002", clip_name="CEZ4030_1.wav", title="Little Fancy", id=42, matched_by="file")


def _sent(out, path):
    return [json.loads(f["body"]) for f in out["fetches"] if f["path"] == path and f["method"] == "POST"]


@needs_node
def test_editor_speichert_eine_vorhandene_zeile_per_update_mit_verlag():
    out = run_page(IMPORT + EDIT + """await openEd(0);
      const r = {seedVerlag: field(4).value, label: saveBtn().textContent};
      field(4).value = 'New Pub'; click(saveBtn()); await settle(); await sleep(700); await settle();
      r.card = [edlState.cards[0].verlag, edlState.cards[0].status, edlState.cards[0].id];
      r.cell = texts('#eresult tr.etrow')[0].includes('New Pub'); return r;""",
                   api={"/api/edl/import": edl(NAMED), "/api/db/lookup": {"db": {}, "db_id": 7},
                        "POST /api/db/song/7": {"ok": True, "fields": {"verlag": "New Pub"}}})
    assert out["errors"] == []
    r = out["result"]
    assert r["seedVerlag"] == "Pubco" and r["label"] == "Änderungen speichern"
    assert _sent(out, "/api/db/lookup") == [{"artist": "Zonk Band", "title": "Moonlight Groove"}]
    # nur das Geänderte geht an die vorhandene Zeile — was die Karte nicht trägt, bleibt in der songs.db
    assert _sent(out, "/api/db/song/7") == [{"fields": {"verlag": "New Pub"}}] and _sent(out, "/api/db/song") == []
    assert r["card"] == ["New Pub", "found_in_db", 7] and r["cell"] is True


@needs_node
def test_library_datei_mit_id_wird_ohne_nachschlagen_bearbeitet_neuer_lc_bringt_rechterueckruf():
    out = run_page(IMPORT + EDIT + """await openEd(0);
      field(6).value = '00171'; click(saveBtn()); await settle(); await sleep(700); await settle();
      return [edlState.cards[0].labelcode, edlState.cards[0].labelname, edlState.cards[0].rechterueckrufe];""",
                   api={"/api/edl/import": edl(FILEHIT),
                        "POST /api/db/song/42": {"ok": True, "fields": {"labelcode": "LC00171", "labelname": "Decca"}},
                        "/api/search": {"mode": "code", "results": [{"labelcode": "00171", "label": "Decca", "rechterueckrufe": "R6"}]}})
    assert out["errors"] == []
    assert out["result"] == ["LC00171", "Decca", "R6"]
    assert _sent(out, "/api/db/lookup") == []
    assert _sent(out, "/api/db/song/42") == [{"fields": {"labelcode": "00171"}}]


@needs_node
def test_neue_zeile_wird_angelegt_und_fehler_kommt_in_der_sprache_der_oberflaeche():
    new = dict(OPEN, artist="Zonk Band", title="Sunset", status="missing_no_resolver")
    detail = "Ein anderer Eintrag mit 'Zonk Band – Dawn' existiert bereits"
    scen = IMPORT + EDIT + """await openEd(0); const r = {label: saveBtn().textContent};
      click(saveBtn()); await settle(); r.created = [edlState.cards[0].id, edlState.cards[0].status];
      await sleep(700); await settle(); await openEd(0); r.label2 = saveBtn().textContent;
      field(1).value = 'Dawn'; click(saveBtn()); await settle(); r.err = txt(ed().querySelector('.actions .hint')); return r;"""
    api = {"/api/edl/import": edl(new), "/api/db/lookup": {"db": None, "db_id": None},
           "POST /api/db/song": {"ok": True, "id": 9, "fields": {}},
           "POST /api/db/song/9": {"__status": 409, "__body": {"detail": detail}}}
    de = run_page(scen, api=dict(api))
    assert de["errors"] == []
    assert de["result"] == {"label": "In Datenbank aufnehmen", "created": [9, "found_in_db"], "label2": "Änderungen speichern",
                            "err": detail}
    en = run_page(scen, lang="en", api=dict(api))["result"]
    assert (en["label"], en["label2"], en["err"]) == ("Add to database", "Save changes", "Another entry 'Zonk Band – Dawn' already exists")


# --- Vorschläge im Editor — nur Library-Tracks bekommen den Komponisten als Interpret, Netzwerte ersetzen
# unberührte Vorschläge, ein Vorschlag wird nie als Interpret ins Netz geschickt ---------------------------------
LIBCARD = dict(OPEN, clip_name="UPM_BEE123_7_DARK_MATTER_JOHNSON_123456.wav", title="Dark Matter", status="library_name",
               library_name="UPM_BEE123", composer_hint="Johnson")
SUGG = "[0, 2].map(n => [field(n).value, field(n).classList.contains('sugg'), field(n).getAttribute('title') || ''])"
RESEARCH = "const research = () => [...ed().querySelectorAll('.actions button')][0];\n"


@needs_node
def test_komponist_als_interpret_nur_bei_library_tracks_mit_passender_herkunft():
    listcard = dict(OPEN, event_num="002", clip_name="", title="Sunset", komponist="Hans Zimm", status="missing_no_resolver")
    catcard = dict(OPEN, event_num="003", clip_name="CEZ4030_1.wav", title="Little Fancy", komponist="Hans Zimm",
                   status="catalog_hint", catalog_hint="CEZ", labelcode="10347")
    out = run_page(IMPORT + EDIT + f"""const r = {{}};
      for (const i of [0, 1, 2]) {{ await openEd(i); r[i] = {{f: {SUGG}, note: txt(ed().querySelector('.hint'))}}; $('.emodal-overlay').remove(); }}
      return r;""", api={"/api/edl/import": edl(LIBCARD, listcard, catcard)})
    assert out["errors"] == []
    r = out["result"]
    # Library-Dateiname: Interpret und Komponist aus dem Dateinamen vorgeschlagen
    assert [x[:2] for x in r["0"]["f"]] == [["Johnson", True], ["Johnson", True]] and "Dateinamen" in r["0"]["f"][0][2]
    # Liste ohne Library: kein Interpret-Vorschlag, der Komponist ist ein geprüfter Wert aus der Liste
    assert r["1"]["f"] == [["", False, ""], ["Hans Zimm", False, ""]]
    # Library über das Katalog-Kürzel: Interpret = Komponist der Karte, Hinweis nennt die GEMA-Regel statt Dateiname
    assert r["2"]["f"][0][:2] == ["Hans Zimm", True] and "GEMA" in r["2"]["f"][0][2] and "Dateinamen" not in r["2"]["f"][0][2]
    assert "Dateinamen" not in r["2"]["note"] and "GEMA" in r["2"]["note"]


@needs_node
def test_vorschlag_geht_nicht_als_interpret_ins_netz():
    out = run_page(IMPORT + EDIT + RESEARCH + """await openEd(0); click(research()); await settle();
      return {msg: txt(ed().querySelector('.actions .hint')), f: [field(0).value, field(2).value]};""",
                   api={"/api/edl/import": edl(LIBCARD), "/api/resolve": {"best": None, "composer": "Robert Johnson"}})
    assert out["errors"] == []
    assert [f["path"] for f in out["fetches"]].count("/api/resolve") == 0
    assert out["result"]["f"] == ["Johnson", "Johnson"] and "Interpret" in out["result"]["msg"], out["result"]


@needs_node
def test_netzwerte_ersetzen_unberuehrte_vorschlaege():
    card = dict(LIBCARD, artist="Zonk Band")
    out = run_page(IMPORT + EDIT + RESEARCH + f"""await openEd(0); click(research()); await settle(); return {SUGG};""",
                   api={"/api/edl/import": edl(card),
                        "/api/resolve": {"best": None, "composer": "Robert Johnson; Mary Smith", "lyricist": "", "isrcs": []}})
    assert out["errors"] == []
    assert [json.loads(f["body"])["artist"] for f in out["fetches"] if f["path"] == "/api/resolve"] == ["Zonk Band"]
    assert out["result"] == [["Zonk Band", False, ""], ["Robert Johnson; Mary Smith", False, ""]]


@needs_node
def test_von_hand_geaenderter_vorschlag_bleibt_und_wird_gesucht():
    out = run_page(IMPORT + EDIT + RESEARCH + f"""await openEd(0);
      field(0).value = 'Zonk Band'; fire(field(0), 'input'); field(2).value = 'Rob Johnson'; fire(field(2), 'input');
      click(research()); await settle(); return {SUGG};""",
                   api={"/api/edl/import": edl(LIBCARD), "/api/resolve": {"best": None, "composer": "Robert Johnson", "isrcs": []}})
    assert out["errors"] == []
    assert [json.loads(f["body"])["artist"] for f in out["fetches"] if f["path"] == "/api/resolve"] == ["Zonk Band"]
    assert out["result"] == [["Zonk Band", False, ""], ["Rob Johnson", False, ""]]


@needs_node
def test_kopierknopf_meldet_kopiert_nur_wenn_es_in_der_zwischenablage_liegt():
    scen = IMPORT + EDIT + """const cp = () => ed().querySelector('.fnrow button'); const r = {};
      globalThis.getSelection = () => ({removeAllRanges() {}, addRange() { r.selected = true; }});
      await openEd(0); click(cp()); await settle(); r.ok = txt(cp());
      navigator.clipboard.writeText = async () => { throw new Error('denied'); };
      document.execCommand = () => false; click(cp()); await settle(); r.denied = txt(cp());
      click('#langbtn'); await settle(); r.deniedEn = txt(cp());
      document.execCommand = c => c === 'copy'; click(cp()); await settle(); r.fallback = txt(cp()); return r;"""
    out = run_page(scen, api={"/api/edl/import": edl(DB)})
    assert out["errors"] == []
    r = out["result"]
    assert r["ok"] == "kopiert" and r["selected"] is True
    assert r["denied"] != "kopiert" and "⌘C" in r["denied"] and "Strg" in r["denied"], r
    assert "Ctrl" in r["deniedEn"] and r["fallback"] == "copied", r
