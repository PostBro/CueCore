"""Fehlerbehandlung und Sicherheit der Oberfläche (web/index.html) im DOM-Ersatz: Fremddaten als Text statt Markup,
Fehler sichtbar oder wenigstens in der Konsole. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.webui_harness import needs_node, run_page

EVIL = "<img src=x onerror=alert(1)>"


# --- esc() machte nur toString(); Fremddaten landeten als Markup in innerHTML -----------------------------
@needs_node
def test_esc_maskiert_html():
    out = run_page("""return [esc('<b>"x" & \\'y\\'</b>'), esc(null), esc(5)];""")
    assert out["result"] == ["&lt;b&gt;&quot;x&quot; &amp; &#39;y&#39;&lt;/b&gt;", "", "5"]


@needs_node
def test_ordnername_im_zuletzt_chip_bleibt_text():
    folders = json.dumps([{"path": "/Users/x/" + EVIL, "n": 3, "at": 1}])
    out = run_page("return {img: qa('#trecent img').length, chip: txt('#trecent .rc')};", storage={"cuecore_recent_folders": folders})
    assert out["errors"] == []
    assert out["result"] == {"img": 0, "chip": EVIL + "3 Dateien"}


@needs_node
def test_interpret_titel_und_quellen_der_track_suche_bleiben_text():
    best = {"labelcode": "<i>1</i>", "label": "ACME", "sources": ["musicbrainz"], "confidence": 0.5,
            "source_urls": [{"name": "<i>MB</i>", "url": "https://musicbrainz.org/release/x"}], "dual_source": False}
    artist = {"mode": "artist_only", "artist": "<i>Zonk</i>", "album": "", "albums": [], "index": [], "index_present": True,
              "verified": [{"interpret": "<i>Zonk</i>", "titel": EVIL, "labelname": "<b>ACME</b>", "labelcode": "LC24584"}]}
    out = run_page(f"""click('.nav button[data-tool=resolve]');
      renderArtistTracks({json.dumps(artist)}); const r = {{head: txt('#rresult .meta-line'), btn: txt('#rresult .sugbtn')}};
      renderResolve({{artist: 'Zonk', title: 'Moon', matched: true, mb_score: 90, band: '<i>review</i>', best: {json.dumps(best)},
        candidates: [], isrcs: [], local_suggestions: [{{interpret: '<i>Zonk</i>', titel: 'Moon', labelname: '', labelcode: '<i>2</i>'}}]}});
      r.meta = txt('#rresult .meta-line'); r.src = txt('#rresult .chip.srclink'); r.sug = txt('#rresult .sugbtn');
      r.tags = qa('#rresult img').length + qa('#rresult i').filter(e => !e.closest('.meter')).length; return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert r["head"].startswith("Interpret-Suche <i>Zonk</i>")
    assert r["btn"].startswith("<i>Zonk</i> – " + EVIL) and "<b>ACME</b>" in r["btn"]
    assert "<i>review</i>" in r["meta"] and r["src"] == "<i>MB</i> ↗" and "<i>2</i>" in r["sug"]
    assert r["tags"] == 0


@needs_node
def test_textstellen_zeigen_sonderzeichen_ohne_entitaeten():
    out = run_page("""const c = tagCard({path: '/in/a.mp3', name: 'a.mp3', artist: 'Zonk', title: 'Moon', filename_lc: '24584',
        filename_label: 'Zonk & Söhne'}); document.body.appendChild(c.el);
      return txt(c.el.querySelector('.fnlc'));""")
    assert out["errors"] == []
    assert out["result"].endswith("Zonk & Söhne") and "&amp;" not in out["result"]


# --- HTTP-Fehlerantworten (503/500) erschienen als „kein Treffer“ — sieben fetch-Stellen ohne r.ok --------
LABELS_MISSING = "Die GVL-Labelcode-Liste ist nicht geladen. CueCore beenden und neu starten; bleibt die Meldung, steht der Grund in cuecore.log im Datenordner (Einstellungen > Datenordner > Ordner öffnen)."
SONGS_MISSING = "Die Track-Datenbank ist nicht geladen. CueCore beenden und neu starten; bleibt die Meldung, steht der Grund in cuecore.log im Datenordner (Einstellungen > Datenordner > Ordner öffnen)."
DOWN = {"__status": 503, "__body": {"detail": LABELS_MISSING}}
WHY = "Labelcode-Liste ist nicht geladen. CueCore beenden und neu starten"
SEARCH = ("click('.nav button[data-tool=resolve]'); $('#art').value = 'Zonk Band'; $('#tit').value = 'Moonlight Groove';"
          "$('#rform').requestSubmit(); await settle();\n")
BEST = {"labelcode": "24584", "label": "ACME", "sources": ["musicbrainz"], "source_urls": [], "dual_source": False,
        "confidence": 0.9, "rechterueckrufe": ""}
RESOLVED = {"artist": "Zonk Band", "title": "Moonlight Groove", "matched": True, "mb_score": 100, "isrcs": [], "band": "review",
            "best": BEST, "candidates": [BEST], "notes": [], "local": False, "index": False,
            "stats": {"calls": 2, "cache_hits": 0, "failures": 0}}
SONG = {"interpret": "Zonk Band", "titel": "Moonlight Groove", "labelname": "ACME", "labelcode": "LC24584"}
TITLE_ONLY = {"mode": "title_only", "title": "Moonlight Groove", "verified": [SONG], "index": [], "index_present": True,
              "title_candidates": [], "notes": []}


@needs_node
def test_track_suche_zeigt_den_serverfehler_statt_kein_match():
    out = run_page(SEARCH + "return txt('#rresult');", api={"/api/resolve": DOWN})
    assert out["errors"] == []
    assert WHY in out["result"] and "undefined" not in out["result"] and "Match" not in out["result"], out["result"]


@needs_node
def test_db_eintrag_bearbeiten_zeigt_den_serverfehler_statt_leerem_formular():
    out = run_page(SEARCH + f"""__api['/api/db/lookup'] = {{__status: 503, __body: {{detail: {json.dumps(SONGS_MISSING)}}}}};
      click(qa('#rresult .mergeaff button')[0]); await settle();
      return {{text: txt('#mergehost'), inputs: qa('#mergehost input').length}};""", api={"/api/resolve": RESOLVED})
    assert out["errors"] == []
    assert "Track-Datenbank ist nicht geladen" in out["result"]["text"] and out["result"]["inputs"] == 0


@needs_node
def test_titel_suche_weitere_und_wikidata_zeigen_den_serverfehler():
    capped = dict(TITLE_ONLY, verified=[], index_capped=True, index_total=40, index_next=20,
                  index=[{"interpret": "Zonk Band", "titel": "Moonlight", "labels": ["ACME"], "labelcodes": ["24584"], "sources": ["discogs"]}])
    out = run_page(f"""click('.nav button[data-tool=resolve]'); __api['/api/resolve'] = {json.dumps(DOWN)};
      renderTitleCandidates({json.dumps(capped)}); const more = qa('#rresult button').find(b => b.textContent === '20 weitere zeigen');
      click(more); await settle(); const r = {{more: txt('#rresult'), moreOn: !more.disabled}};
      renderTitleCandidates({json.dumps(TITLE_ONLY)}); click(qa('#rresult > button').slice(-1)[0]); await settle();
      r.wd = txt('#rresult'); return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert WHY in r["more"] and r["moreOn"] is True, r
    assert WHY in r["wd"] and "Kein Titel-Treffer" not in r["wd"], r


@needs_node
def test_album_lc_suche_im_tagger_zeigt_den_serverfehler():
    f = {"path": "/in/zonk.mp3", "name": "zonk.mp3", "artist": "Zonk Band", "title": "Moonlight Groove", "album": "Blue Era"}
    intake = {"artist": "Zonk Band", "title": "Moonlight Groove", "best": BEST, "band": "review", "matched": True, "isrcs": [],
              "stats": {"calls": 1, "cache_hits": 0, "failures": 0},
              "plan": {"proposed_name": "Zonk Band - Moonlight Groove - [ACME_LC24584].mp3", "warnings": [], "can_apply": True}}
    out = run_page(f"""const ctrl = tagCard({json.dumps(f)}); document.body.appendChild(ctrl.el);
      await ctrl.resolveAlbum(); await settle(); const r = {{card: txt(ctrl.el)}};
      await ctrl.resolve(); await settle();
      click([...ctrl.el.querySelectorAll('button')].filter(b => b.textContent === t('Album-LC suchen')).slice(-1)[0]); await settle();
      r.prop = txt(ctrl.el); return r;""",
                   api={"/api/album/resolve": DOWN, "/api/intake/resolve": intake})
    assert out["errors"] == []
    r = out["result"]
    assert WHY in r["card"] and "Kein Album-Label gefunden" not in r["card"], r
    assert WHY in r["prop"] and "Kein Album-Label gefunden" not in r["prop"], r


@needs_node
def test_cue_editor_recherche_zeigt_den_serverfehler_statt_kein_netz_treffer():
    card = {"event_num": "001", "clip_name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "labelname": "",
            "labelcode": "", "duration_sec": 20, "status": "unresolved", "rechterueckrufe": ""}
    out = run_page("""click('.nav button[data-tool=edl]'); edlState.content = 'x'; edlState.list = null; await runEdlImport(false); await settle();
      click([...qa('#eresult tr.etrow')[0].querySelectorAll('.ibtn')].find(b => b.getAttribute('aria-label') === t('Bearbeiten'))); await settle();
      click([...$('.emodal .dbedit').querySelectorAll('.actions button')][0]); await settle();
      return txt($('.emodal .dbedit .actions .hint'));""",
                   api={"/api/edl/import": {"cards": [card], "cues": 1, "summary": {}, "total_duration_sec": 20}, "/api/resolve": DOWN})
    assert out["errors"] == []
    assert WHY in out["result"] and "Kein Netz-Treffer" not in out["result"], out["result"]


@needs_node
def test_serverfehler_erscheint_in_en_englisch():
    out = run_page(SEARCH + "return txt('#rresult');", lang="en", api={"/api/resolve": DOWN})
    assert out["errors"] == []
    assert "The GVL label code list is not loaded. Quit and restart CueCore" in out["result"], out["result"]


# --- Verbindungstest und Library-Scan zeigten r.detail nie, Fehlercodes roh ('network', 'http_500') ---------
SETTINGS = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures" / "webui_settings.json").read_text(encoding="utf-8"))
SETTINGS = dict(SETTINGS, configured=dict(SETTINGS.get("configured") or {}, acoustid=True, genius=True, discogs=True, editingtools=True))
TEST_SCEN = """click('.nav button[data-tool=settings]'); await settle();
  const card = qa('#settings-list .sgroup').find(c => (txt(c.querySelector('h2')) || '').startsWith(__svc));
  click(card.querySelector('.sgroup-test button')); await settle(); return txt(card.querySelector('.sgroup-test .hint'));"""
ACOUSTID_AUTH = {"ok": False, "status": 401, "error": "auth",
                 "detail": "invalid API key — brauchst du den Application-Key von acoustid.org/new-application? "
                           "Der Schlüssel aus dem Konto ist der User-Key und gilt nur fürs Einsenden."}


def _test(svc: str, answer: dict, lang: str = "de") -> dict:
    return run_page(f"const __svc = {json.dumps(svc)};\n" + TEST_SCEN, lang=lang,
                    api={"/api/settings": SETTINGS, f"/api/settings/test/{svc.lower()}": answer})


@needs_node
def test_acoustid_hinweis_auf_den_application_key_erscheint():
    out = _test("AcoustID", ACOUSTID_AUTH)
    assert out["errors"] == []
    assert out["result"].startswith("✗ API-Key ungültig (401)") and "acoustid.org/new-application" in out["result"], out["result"]
    en = _test("AcoustID", ACOUSTID_AUTH, lang="en")["result"]
    assert "application key from acoustid.org/new-application" in en, en


@needs_node
def test_netz_und_http_fehler_erscheinen_als_text_statt_kennung():
    net = _test("Genius", {"ok": False, "status": 0, "error": "network", "detail": "timed out"})["result"]
    assert net.startswith("✗ Keine Verbindung (Host/Netz prüfen)") and "network" not in net and "timed out" in net, net
    http = _test("Discogs", {"ok": False, "status": 500, "error": "http_500", "detail": ""})["result"]
    assert "http_500" not in http and "(500)" in http, http
    acou = _test("AcoustID", {"ok": False, "status": 400, "error": "http", "detail": "unbekannte Antwort"})["result"]
    assert acou.startswith("✗ ") and "✗ http" not in acou and "unbekannte Antwort" in acou, acou
    et = _test("editingtools", {"ok": False, "status": 0, "error": "unknown", "detail": ""})["result"]
    assert et == "✗ Unbekannter Fehler", et


@needs_node
def test_library_scan_fehler_zeigt_den_grund_des_servers():
    card = {"event_num": "001", "clip_name": "UPM_VOLTA_123_1.wav", "artist": "", "title": "", "labelname": "", "labelcode": "",
            "duration_sec": 5, "status": "unparsable_clip_name", "rechterueckrufe": ""}
    settings = {"groups": [], "paths": [], "texts": [], "keys": [], "partners": [], "partner_fields": {},
                "configured": {"trackmeta": True, "editingtools": True}}
    out = run_page("""click('.nav button[data-tool=edl]'); edlState.content = 'x'; edlState.list = null; await runEdlImport(false); await settle();
      await completeMetadata(); await settle(); return txt(edlState.progEl);""",
                   api={"/api/edl/import": {"cards": [card], "cues": 1, "summary": {}, "total_duration_sec": 5}, "/api/settings": settings,
                        "/api/edl/library-scan": {"ok": False, "error": "create_failed", "status": 500},
                        "/api/edl/trackmeta-scan": {"__status": 503, "__body": {"detail": LABELS_MISSING}}})
    assert out["errors"] == []
    r = out["result"]
    assert "Track-API-Fehler: Die GVL-Labelcode-Liste ist nicht geladen" in r and "unbekannt" not in r, r
    assert "create_failed" not in r and "editingtools-Fehler: " in r, r


# --- catch-Blöcke verschluckten Laufzeitfehler oder deuteten sie als Backend-/Netzfehler um, nichts im Log ---
BROKEN = "const __tls = Number.prototype.toLocaleString; Number.prototype.toLocaleString = function(){ throw new Error('kaputt'); };\n"
FIXED = "Number.prototype.toLocaleString = __tls;\n"


def _logged(out, needle: str) -> bool:
    return any(e.startswith("console.error:") and needle in e for e in out["errors"])


@needs_node
def test_unbehandelte_fehler_landen_in_der_konsole():
    out = run_page("""dispatchEvent(new Event('error', {init: {error: new Error('boom1'), message: 'boom1'}}));
      dispatchEvent(new Event('unhandledrejection', {init: {reason: new Error('boom2')}})); return null;""")
    assert _logged(out, "boom1") and _logged(out, "boom2"), out["errors"]


@needs_node
def test_startseite_zeigt_skriptfehler_nicht_als_backend_nicht_erreichbar():
    out = run_page(BROKEN + "await renderHome(); await settle(); const r = txt('#homehealth');" + FIXED + "return r;")
    assert "antwortet nicht" not in out["result"] and "kaputt" in out["result"], out["result"]
    assert _logged(out, "kaputt"), out["errors"]
    down = run_page("await renderHome(); await settle(); return txt('#homehealth');", api={"/api/health": {"__throw": True}})
    assert down["result"] == "CueCore-Dienst antwortet nicht. CueCore beenden und neu starten."


@needs_node
def test_track_index_karte_verschwindet_nicht_still():
    out = run_page("click('.nav button[data-tool=settings]'); await settle(); return texts('#settings-list > *').slice(-1)[0];",
                   api={"/api/index/status": {"__status": 500, "__body": {"detail": "Index-Datei defekt"}}})
    assert "Lokaler Track-Index" in out["result"] and "Index-Datei defekt" in out["result"], out["result"]
    present = {"present": True, "rows": 1234, "by_source": {"discogs": 1234}, "meta": {}, "size_bytes": 1e9, "path": "/x/tracks.db"}
    out = run_page(BROKEN + "click('.nav button[data-tool=settings]'); await settle(); const r = texts('#settings-list > *').slice(-1)[0];"
                   + FIXED + "return r;", api={"/api/index/status": present})
    assert "Lokaler Track-Index" in out["result"] and "kaputt" in out["result"], out["result"]
    assert _logged(out, "kaputt")


@needs_node
def test_kuerzel_listen_ohne_backend_sichtbar_und_im_log():
    out = run_page("click('.nav button[data-tool=gvl]'); await loadLibraryLists(); await settle(); return txt('#lllist');",
                   api={"/api/labels/lists": {"__throw": True}})
    assert "nicht ladbar" in out["result"], out["result"]


def test_kein_stummer_catch_mehr():
    """Leere catch-Blöcke und .catch(()=>{}/null) nur noch dort, wo das Scheitern erwartet ist: localStorage (privater
    Modus), :focus-visible (alte Browser), r.json() einer Fehlerantwort ohne JSON, /api/quit (Server schon weg)."""
    from tests.webui_harness import INDEX
    import re
    script = INDEX.read_text(encoding="utf-8").split("<script>")[1].split("</script>")[0]
    silent = re.compile(r"catch\s*\(\s*\w*\s*\)\s*\{\s*(?:/\*.*?\*/\s*)?(?:return[^;}]*;?\s*)?\}|\.catch\(\s*\(\s*\w*\s*\)\s*=>\s*(?:\{\s*\}|null|\(\{\}\))\s*\)")
    allowed = ("localStorage", ":focus-visible", ".json().catch", "/api/quit")
    bad = [line.strip()[:120] for line in script.splitlines() if silent.search(line) and not any(a in line for a in allowed)]
    assert bad == []


@needs_node
def test_in_datenbank_anlegen_uebernimmt_die_werte_der_karte():
    """Ein Netz-Treffer (Beatles – Help, LC00299) soll sich nicht nur mit Interpret/Titel anlegen lassen, Label,
    LC und Autoren sollen nicht neu getippt werden müssen. Ist der Song noch nicht in der eigenen DB, belegt die Karte das Formular vor;
    gespeichert wird erst auf Klick. Bei Gleichstand bleiben Label und LC leer (Margin-Gate)."""
    hit = dict(RESOLVED, composer="John Lennon; Paul McCartney", album="Help!")
    code = SEARCH + """__api['/api/db/lookup'] = {db: null, db_id: null, net: {}, fields: [], artist: 'Zonk Band', title: 'Moonlight Groove'};
      click(qa('#rresult .mergeaff button')[0]); await settle();
      return Object.fromEntries(qa('#mergehost input').map((i, n) => [n, i.value]));"""
    out = run_page(code, api={"/api/resolve": hit})
    assert out["errors"] == []
    vals = list(out["result"].values())
    assert "ACME" in vals and "24584" in vals and "John Lennon; Paul McCartney" in vals and "Help!" in vals, vals
    tie = run_page(code, api={"/api/resolve": dict(hit, ambiguous=True)})
    tvals = list(tie["result"].values())
    assert "ACME" not in tvals and "24584" not in tvals and "John Lennon; Paul McCartney" in tvals, tvals


@needs_node
def test_track_index_download_aus_den_einstellungen():
    """Index per Knopf laden, Fortschritt sehen, Fehler mit „Erneut versuchen“; darunter der
    Datenordner mit „Ordner öffnen“. Ein laufender Download erscheint beim Öffnen der Einstellungen wieder."""
    status = {"present": False, "rows": 0, "by_source": {}, "meta": {}, "size_bytes": 0, "path": "/d/track_index.db",
              "data_dir": "/d/MusicMeta", "download": {"size": 1258964296, "unpacked": 6496002048, "stand": "2026-09"}}
    page = ("click('.nav button[data-tool=settings]'); await settle();"
            "const cards = qa('#settings-list > section');"
            "return {idx: txt(cards[cards.length-2]), dir: txt(cards[cards.length-1]),"
            " bar: !cards[cards.length-2].querySelector('progress').hidden};")
    running = {"state": "running", "phase": "download", "done": 629482148, "total": 1258964296, "error": ""}
    out = run_page(page, api={"/api/index/status": status, "/api/index/download": running})
    assert out["errors"] == []
    r = out["result"]
    assert "Lade" in r["idx"] and "Abbrechen" in r["idx"] and r["bar"] is True, r
    assert "/d/MusicMeta" in r["dir"] and "Ordner öffnen" in r["dir"], r
    failed = dict(running, state="error", error="Zu wenig freier Platz: 1,0 GB frei, rund 8,1 GB nötig.")
    out = run_page(page, api={"/api/index/status": status, "/api/index/download": failed})
    r = out["result"]
    assert "Zu wenig freier Platz" in r["idx"] and "Erneut versuchen" in r["idx"] and r["bar"] is False, r
    idle = dict(running, state="idle", done=0)
    r = run_page(page, api={"/api/index/status": status, "/api/index/download": idle})["result"]
    assert "Track-Index herunterladen" in r["idx"] and "eigenen Tracks bleiben unberührt" in r["idx"], r


@needs_node
def test_teil_titel_zeigt_musicbrainz_vorschlag_und_sucht_damit_neu():
    """Bei „rolling stones“ + „sympathy“ stand früher „kein Match“ mit einem fremden Interpreten als
    Vorschlag. Jetzt steht „The Rolling Stones – Sympathy for the Devil“ als Vorschlag da; der Klick sucht neu."""
    miss = dict(RESOLVED, matched=False, best=None, candidates=[], band="none",
                net_suggestions=[{"interpret": "The Rolling Stones", "titel": "Sympathy for the Devil"}])
    out = run_page(SEARCH + """const r = {text: txt('#rresult')};
      click(qa('#rresult .suggests .sugbtn')[0]); await settle();
      r.art = $('#art').value; r.tit = $('#tit').value; return r;""", api={"/api/resolve": miss})
    assert out["errors"] == []
    r = out["result"]
    assert "Meintest du diesen Song? (MusicBrainz)" in r["text"] and "Sympathy for the Devil" in r["text"], r
    assert r["art"] == "The Rolling Stones" and r["tit"] == "Sympathy for the Devil", r


# --- Meldungen in Alltagssprache mit nächstem Schritt (keine Dateinamen interner DBs, keine Kodierungen, kein „HTTP 500“) ---
def test_meldungen_ohne_technikjargon_mit_naechstem_schritt():
    from tests.test_webui_sprache import _backend_messages
    from tests.webui_harness import INDEX
    import app
    msgs = _backend_messages()
    jargon = ("labels.db nicht", "songs.db nicht", "build_label_db", "Base64", "Settings konfiguriert", "Backend nicht")
    assert [m for m in msgs if any(j in m for j in jargon)] == []
    for m in (app.LABELS_DB_MISSING, app.SONGS_DB_MISSING):
        assert "neu starten" in m and "cuecore.log" in m
    html = INDEX.read_text(encoding="utf-8")
    assert "'Backend nicht erreichbar" not in html and "||('HTTP '+r.status)" not in html


@needs_node
def test_serverfehler_ohne_detail_nennt_den_naechsten_schritt():
    out = run_page(SEARCH + "return txt('#rresult');", api={"/api/resolve": {"__status": 500, "__body": {}}})
    assert out["errors"] == []
    assert "HTTP 500" not in out["result"] and "cuecore.log" in out["result"], out["result"]


@needs_node
def test_eingebauter_partner_in_den_einstellungen_nur_pruefbar():
    """Der eingebaute Zugang erscheint mit Namen und „eingebaut“, ohne Eingabefelder und ohne „Entfernen“; Speichern
    schickt ihn nicht mit (nie in settings.json)."""
    s = dict(SETTINGS, partners=[{"id": "builtin", "name": "Zzq Library", "host": "", "client_id": "", "secret_set": True,
                                  "complete": True, "builtin": True}])
    out = run_page("""click('.nav button[data-tool=settings]'); await settle();
      const row = $('#settings-list .prow.builtin');
      const r = {text: txt(row), inputs: row.querySelectorAll('input').length, buttons: [...row.querySelectorAll('button')].map(b => b.textContent)};
      click('#save-settings'); await settle(); return r;""", api={"/api/settings": s, "POST /api/settings": s})
    assert out["errors"] == []
    r = out["result"]
    post = [f for f in out["fetches"] if f["path"] == "/api/settings" and f["method"] == "POST"][-1]
    r["sent"] = json.loads(post["body"])["partners"]
    assert "Zzq Library" in r["text"] and "eingebaut" in r["text"].lower() and r["inputs"] == 0, r
    assert r["buttons"] == ["Verbindung prüfen"], r
    assert r["sent"] == [], r


# --- Track-Index lädt beim ersten Start von selbst: Balken auf jeder Seite, Stand in der Seitenleiste ---
@needs_node
def test_index_balken_zeigt_den_automatischen_download_auf_jeder_seite():
    running = {"state": "running", "phase": "download", "done": 430, "total": 1000, "error": "", "auto": True}
    out = run_page("""await settle(); const r = {home: txt('#ixbanner'), hidden: $('#ixbanner').hidden, side: txt('#st-index')};
      click('.nav button[data-tool=resolve]'); await settle(); r.other = !$('#ixbanner').hidden; return r;""",
                   api={"/api/index/download": running})
    assert out["errors"] == []
    r = out["result"]
    assert r["hidden"] is False and r["other"] is True, r
    assert "Track-Index wird geladen (einmalig)" in r["home"] and "43 %" in r["home"] and "Abbrechen" in r["home"], r
    assert r["side"] == "lädt 43 %", r


@needs_node
def test_index_balken_bei_fehler_mit_erneut_versuchen_und_sonst_unsichtbar():
    failed = {"state": "error", "phase": "download", "done": 0, "total": 0, "error": "Zu wenig freier Platz", "auto": True}
    out = run_page("await settle(); return {text: txt('#ixbanner'), hidden: $('#ixbanner').hidden};",
                   api={"/api/index/download": failed})
    assert out["errors"] == [] and out["result"]["hidden"] is False
    assert "Zu wenig freier Platz" in out["result"]["text"] and "Erneut versuchen" in out["result"]["text"]
    idle = run_page("await settle(); return $('#ixbanner').hidden;",
                    api={"/api/index/download": {"state": "idle", "phase": "", "done": 0, "total": 0, "error": ""}})
    assert idle["errors"] == [] and idle["result"] is True
    manual = run_page("await settle(); return $('#ixbanner').hidden;", api={"/api/index/download": dict(failed, auto=False)})
    assert manual["result"] is True    # von Hand gestartet: der Fehler steht in den Einstellungen, kein Balken
