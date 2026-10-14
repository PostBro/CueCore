"""Track-Suche der Oberfläche (web/index.html) im DOM-Ersatz: Netzausfall und Abbrechen, Rechterückruf,
Band der Ergebniskarte. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.webui_harness import needs_node, run_page

SEARCH = ("click('.nav button[data-tool=resolve]'); $('#art').value = 'Zonk Band'; $('#tit').value = 'Moonlight Groove';"
          "$('#rform').requestSubmit(); await settle();\n")
BEST = {"labelcode": "24584", "label": "ACME", "sources": ["musicbrainz"], "source_urls": [], "dual_source": False,
        "confidence": 0.9, "rechterueckrufe": ""}
# Alle Netzaufrufe gescheitert (das Backend gibt nach etwa 60 s auf und zählt stats.failures)
NETDOWN = {"artist": "Zonk Band", "title": "Moonlight Groove", "matched": False, "mb_score": 0, "isrcs": [], "band": "no_match",
           "best": None, "candidates": [], "notes": [], "incomplete": True, "local": False, "index": False, "label_hints": [],
           "stats": {"calls": 14, "cache_hits": 0, "failures": 14}}
NETWARN = "Netz nicht erreichbar (14 Fehler)"


# --- Netzausfall erschien als „kein Match“ / „Rate-Limit“, und die Suche ließ sich nicht abbrechen ---------
@needs_node
def test_track_suche_meldet_netzausfall_statt_kein_match():
    out = run_page(SEARCH + "return {warn: texts('#rresult .warnbox'), all: txt('#rresult')};", api={"/api/resolve": NETDOWN})
    assert out["errors"] == []
    r = out["result"]
    assert len(r["warn"]) == 1 and r["warn"][0].startswith(NETWARN), r
    for wrong in ("Rate-Limit", "Kein Recording-Match", "KEIN MATCH", "kein Match"):
        assert wrong not in r["all"], (wrong, r["all"])
    en = run_page(SEARCH + "return txt('#rresult .warnbox');", lang="en", api={"/api/resolve": NETDOWN})
    assert en["result"].startswith("Network unreachable (14 errors)")


@needs_node
def test_einzelne_fehler_mit_treffer_oder_ohne_fehler_bleiben_wie_bisher():
    hit = dict(NETDOWN, matched=True, band="review", best=BEST, candidates=[BEST], incomplete=False,
               stats={"calls": 9, "cache_hits": 0, "failures": 2})
    out = run_page(SEARCH + "return txt('#rresult');", api={"/api/resolve": hit})
    assert "Netz nicht erreichbar" not in out["result"]
    none = dict(NETDOWN, incomplete=False, stats={"calls": 5, "cache_hits": 0, "failures": 0})
    out = run_page(SEARCH + "return txt('#rresult');", api={"/api/resolve": none})
    assert "Netz nicht erreichbar" not in out["result"] and "Kein Recording-Match in MusicBrainz." in out["result"]


@needs_node
def test_titel_suche_meldet_netzausfall_statt_kein_titel_treffer():
    res = {"mode": "title_only", "title": "Moonlight Groove", "verified": [], "index": [], "index_present": False,
           "title_candidates": [], "notes": [], "stats": {"calls": 1, "cache_hits": 0, "failures": 1}}
    out = run_page(f"click('.nav button[data-tool=resolve]'); renderTitleCandidates({json.dumps(res)}); return txt('#rresult');")
    assert out["errors"] == []
    assert "Netz nicht erreichbar (1 Fehler)" in out["result"] and "Kein Titel-Treffer" not in out["result"], out["result"]


MERGE_DOWN = {"db": None, "db_id": None, "matched": False, "band": "no_match", "artist": "Zonk Band", "title": "Moonlight Groove",
              "net": {}, "fields": [{"key": k, "label": k, "db": "", "net": "", "status": "empty"}
                                    for k in ("komponist", "texter", "verlag", "labelname", "labelcode", "album", "isrc")],
              "candidates": [], "notes": [], "stats": {"calls": 6, "cache_hits": 0, "failures": 6}}


@needs_node
def test_im_netz_abgleichen_und_auto_suche_melden_netzausfall():
    res = dict(NETDOWN, matched=True, band="review", best=BEST, candidates=[BEST], incomplete=False,
               stats={"calls": 2, "cache_hits": 0, "failures": 0})
    out = run_page(SEARCH + """click(qa('#rresult .mergeaff button')[1]); await settle();
      const r = {merge: txt('#mergehost .warnbox')};
      const ed = dbEditor({id: 7, interpret: 'Zonk Band', titel: 'Moonlight Groove', verified: true}, () => {}, () => {}, {});
      document.body.appendChild(ed); click([...ed.querySelectorAll('button')].find(b => b.textContent === t('Auto-Suche'))); await settle();
      r.auto = txt(ed.querySelector('.autobox')); return r;""",
                   api={"/api/resolve": res, "/api/resolve/merge": MERGE_DOWN})
    assert out["errors"] == []
    r = out["result"]
    assert r["merge"].startswith("Netz nicht erreichbar (6 Fehler)"), r
    assert "Netz nicht erreichbar (6 Fehler)" in r["auto"] and "nichts gefunden" not in r["auto"], r


@needs_node
def test_cue_editor_und_vervollstaendigen_melden_netzausfall():
    card = {"event_num": "001", "clip_name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "labelname": "",
            "labelcode": "", "duration_sec": 20, "status": "missing_no_resolver", "rechterueckrufe": ""}
    entry = {"clip_name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "status": "unresolved", "resolved": False,
             "stats": {"calls": 4, "cache_hits": 0, "failures": 4}}
    out = run_page("""click('.nav button[data-tool=edl]'); edlState.content = 'x'; edlState.list = null; await runEdlImport(false); await settle();
      await completeMetadata(); await settle(); const r = {progress: txt(edlState.progEl), status: edlState.cards[0].status};
      click([...qa('#eresult tr.etrow')[0].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten'))); await settle();
      click([...$('.emodal .dbedit').querySelectorAll('.actions button')][0]); await settle();
      r.editor = txt($('.emodal .dbedit .actions .hint')); return r;""",
                   api={"/api/edl/import": {"cards": [card], "cues": 1, "summary": {}, "total_duration_sec": 20},
                        "/api/edl/resolve-cues": {"entries": [entry], "stats": {"calls": 4, "cache_hits": 0, "failures": 4}},
                        "/api/resolve": NETDOWN})
    assert out["errors"] == []
    r = out["result"]
    assert "Netz nicht erreichbar (4 Fehler)" in r["progress"], r
    assert r["status"] == "missing_no_resolver", r          # nicht „Nicht gefunden“: gesucht wurde gar nicht
    assert r["editor"].startswith(NETWARN) and "Rate-Limit" not in r["editor"], r


@needs_node
def test_suche_laesst_sich_abbrechen():
    out = run_page("""click('.nav button[data-tool=resolve]'); $('#art').value = 'Zonk Band'; $('#tit').value = 'Moonlight Groove';
      $('#rform').requestSubmit(); await settle();
      const cancel = qa('#rresult .loading button')[0]; const r = {label: cancel && txt(cancel), busy: $('#rbtn').disabled};
      click(cancel); await settle(); r.after = txt('#rresult'); r.busy2 = $('#rbtn').disabled;
      // eine neue Suche danach läuft normal
      __api['/api/resolve'] = __ok; $('#rform').requestSubmit(); await settle(); r.next = txt('#rresult .lc'); return r;""".replace(
        "__ok", json.dumps(dict(NETDOWN, matched=True, band="review", best=BEST, candidates=[BEST], incomplete=False,
                                stats={"calls": 2, "cache_hits": 0, "failures": 0}))),
                   api={"/api/resolve": {"__pending": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["label"] == "Abbrechen" and r["busy"] is True, r
    assert r["after"] == "Suche abgebrochen." and r["busy2"] is False, r
    assert r["next"] == "24584"
    signals = [f for f in out["fetches"] if f["path"] == "/api/resolve"]
    assert len(signals) == 2


@needs_node
def test_im_netz_abgleichen_laesst_sich_abbrechen():
    res = dict(NETDOWN, matched=True, band="review", best=BEST, candidates=[BEST], incomplete=False,
               stats={"calls": 2, "cache_hits": 0, "failures": 0})
    out = run_page(SEARCH + """click(qa('#rresult .mergeaff button')[1]); await settle();
      const cancel = qa('#mergehost .loading button')[0]; const r = {label: cancel && txt(cancel)};
      click(cancel); await settle(); r.after = txt('#mergehost'); return r;""",
                   lang="en", api={"/api/resolve": res, "/api/resolve/merge": {"__pending": True}})
    assert out["errors"] == []
    assert out["result"] == {"label": "Cancel", "after": "Reconciliation cancelled."}


# --- Rechterückruf zum gefundenen LC in der Track-Suche (/api/resolve trägt rechterueckrufe an best) -------
HIT = dict(NETDOWN, matched=True, mb_score=100, band="review", incomplete=False, stats={"calls": 3, "cache_hits": 0, "failures": 0})
RR_SCEN = SEARCH + """return {chip: txt('#rresult .card .chip.rr'), tip: ($('#rresult .card .chip.rr') || {getAttribute: () => null}).getAttribute('title'),
  warn: texts('#rresult .warnbox')};"""


@needs_node
def test_track_suche_zeigt_den_rechterueckruf_zum_gefundenen_lc():
    best = dict(BEST, labelcode="00171", label="Decca", rechterueckrufe="R6;R12")
    out = run_page(RR_SCEN, api={"/api/resolve": dict(HIT, best=best, candidates=[best])})
    assert out["errors"] == []
    r = out["result"]
    assert r["chip"] == "RR R6;R12" and "labelrecherche.gvl.de" in r["tip"], r
    assert r["warn"] == ["Rechterückruf R6;R12. Vor Nutzung manuell auf labelrecherche.gvl.de prüfen."], r
    en = run_page(RR_SCEN, lang="en", api={"/api/resolve": dict(HIT, best=best, candidates=[best])})["result"]
    assert en["warn"][0].startswith("Rights recall R6;R12.") and "labelrecherche.gvl.de" in en["tip"], en


@needs_node
def test_ohne_rechterueckruf_kein_chip_und_kein_hinweis():
    out = run_page(RR_SCEN, api={"/api/resolve": dict(HIT, best=BEST, candidates=[BEST])})
    assert out["errors"] == []
    assert out["result"] == {"chip": None, "tip": None, "warn": []}


# --- Band der Karte widersprach der Kopfzeile (REVIEW oben, AUTO-CONFIRM und grüner Rahmen bei conf 0,50) ----
BAND_SCEN = SEARCH + """const card = $('#rresult .card');
  return {head: txt('#rresult .meta-line .band'), card: txt(card.querySelector('.band')), locked: card.className.includes('locked')};"""


def _band(**kw) -> dict:
    best = dict(BEST, **{k: v for k, v in kw.items() if k in ("confidence", "dual_source")})
    res = dict(HIT, best=best, candidates=[best], **{k: v for k, v in kw.items() if k not in ("confidence", "dual_source")})
    return run_page(BAND_SCEN, api={"/api/resolve": res})


@needs_node
def test_karte_folgt_dem_band_des_servers():
    out = _band(confidence=0.5, dual_source=True, band="review")          # beatles / help: 0,50 mit Dual-Source
    assert out["errors"] == []
    assert out["result"] == {"head": "REVIEW", "card": "Review", "locked": False}
    assert _band(confidence=0.92, dual_source=True, band="auto")["result"] == {"head": "AUTO", "card": "Auto-Confirm", "locked": True}
    assert _band(confidence=0.3, dual_source=False, band="review_low")["result"] == {"head": "PRÜFEN", "card": "Prüfen", "locked": False}


@needs_node
def test_auto_nur_mit_dual_source_im_netz_lokale_db_bleibt_auto():
    # Dual-Source ist notwendig, nicht hinreichend: ohne sie kein Auto, auch wenn die Confidence reicht
    assert _band(confidence=0.85, dual_source=False, band="auto")["result"] == {"head": "REVIEW", "card": "Review", "locked": False}
    # Track-Index (zwei Dumps) bleibt Review, Gleichstand nie Auto
    assert _band(confidence=0.9, dual_source=True, band="auto", index=True)["result"]["card"] == "Review"
    assert _band(confidence=0.9, dual_source=True, band="auto", ambiguous=True, tied=["24584", "11111"])["result"]["card"] == "Review"
    # verifizierte lokale DB: Auto (vom Menschen geprüft), auch ohne Netz-Quellen
    assert _band(confidence=1.0, dual_source=False, band="auto", local=True)["result"] == {"head": "AUTO", "card": "Auto-Confirm", "locked": True}


@needs_node
def test_tagger_karte_nutzt_dasselbe_band():
    scen = """const out = [];
      for (const [conf, dual, band] of [[0.5, true, 'review'], [0.92, true, 'auto'], [0.85, false, 'auto']]) {
        const prop = document.createElement('div'); document.body.appendChild(prop);
        const best = {labelcode: '24584', label: 'ACME', sources: ['musicbrainz'], source_urls: [], confidence: conf, dual_source: dual, rechterueckrufe: ''};
        renderProposal(prop, {path: '/in/x.mp3', name: 'x.mp3'}, {artist: 'Zonk', title: 'Moon', best, band, isrcs: [], matched: true,
          plan: {proposed_name: 'Zonk - Moon - [ACME_LC24584].mp3', warnings: [], can_apply: true}});
        out.push(txt(prop.querySelector('.conf .band')));
      }
      return out;"""
    out = run_page(scen)
    assert out["errors"] == []
    assert out["result"] == ["Review", "Auto-Confirm", "Review"]
