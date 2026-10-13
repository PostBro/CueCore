"""Sprache der Oberfläche (web/index.html): Wörterbuch I18N_EN, Sprachwechsel zur Laufzeit.

Laufzeit-Prüfungen im DOM-Ersatz
(tests/webui_dom.js), statische Prüfungen am Quelltext."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.webui_harness import INDEX, i18n_en_source, needs_node, run_page


@needs_node
def test_html_lang_folgt_der_sprache():
    out = run_page("""const r = [document.documentElement.lang];
      click('#langbtn'); await settle(); r.push(document.documentElement.lang);
      click('#langbtn'); await settle(); r.push(document.documentElement.lang); return r;""", lang="en")
    assert out["result"] == ["en", "de", "en"]


@needs_node
def test_statische_aria_labels_werden_uebersetzt():
    scen = """const a = () => [$('#tdrop').getAttribute('aria-label'), $('#tool-edl .steps').getAttribute('aria-label')];
      const r = {start: a()}; click('#langbtn'); await settle(); r.switched = a(); return r;"""
    en = run_page(scen, lang="en")["result"]
    assert en["start"] == ["Choose folder", "Workflow"]
    assert en["switched"] == ["Ordner wählen", "Ablauf"]
    de = run_page(scen, lang="de")["result"]
    assert de["switched"] == ["Choose folder", "Workflow"]


@needs_node
def test_erklaerknoepfe_ueberleben_den_sprachwechsel():
    out = run_page("""const n = () => qa('.helpbtn').length, h1 = () => texts('main h1');
      const r = {start: n()}; click('#langbtn'); await settle(); r.en = n(); r.h1en = h1();
      click('#langbtn'); await settle(); r.de = n(); r.h1de = h1();
      // Erklärungen waren eingeklappt: nach dem Wechsel lassen sie sich wieder aufklappen
      click('#tool-resolve .helpbtn'); r.expl = !$('#tool-resolve .head p:not(.sub)').hidden; return r;""",
                   storage={"cuecore_help": "0"})
    assert out["errors"] == []
    r = out["result"]
    assert (r["start"], r["en"], r["de"]) == (6, 6, 6), r
    assert "track search?" in [h.lower() for h in r["h1en"]] and "Track-Suche?" in r["h1de"], r
    assert r["expl"] is True


def _scan_api(n: int) -> dict:
    files = [{"path": f"/in/{i}.mp3", "name": f"Artist {i} - Title {i}.mp3", "artist": f"Artist {i}",
              "title": f"Title {i}", "album": "", "already_named": False} for i in range(n)]
    return {"/api/intake/scan": {"folder": "/in", "count": n, "files": files}}


@needs_node
def test_dynamische_knopftexte_ueberleben_den_sprachwechsel():
    out = run_page("""const b = () => [txt('#tresolveall'), txt('#tmore'), $('#tmore').hidden];
      startTaggerScan('/in'); await settle(); const r = {de: b()};
      click('#langbtn'); await settle(); r.en = b();
      click('#tmore'); await settle(); r.en2 = b();
      click('#langbtn'); await settle(); r.de2 = b();
      click('.nav button[data-tool=db]'); await settle();
      click('#dbnew'); r.dbnew = [txt('#dbnew')]; click('#langbtn'); await settle(); r.dbnew.push(txt('#dbnew'));
      click('#dbnew'); r.dbnew.push(txt('#dbnew'), $('#dbcreatebox').hidden); return r;""", api=_scan_api(7))
    assert out["errors"] == []
    r = out["result"]
    assert r["de"] == ["Alle 5 auflösen", "Nächste 5 (2 übrig)", False]
    assert r["en"] == ["Resolve all 5", "Next 5 (2 left)", False]
    assert r["en2"][:1] == ["Resolve all 2"] and r["en2"][2] is True
    assert r["de2"][:1] == ["Alle 2 auflösen"]
    assert r["dbnew"] == ["Schließen", "Close", "+ New entry", True]


SETTINGS = json.loads((Path(__file__).parent / "fixtures" / "webui_settings.json").read_text(encoding="utf-8"))


@needs_node
def test_sprachwechsel_in_den_einstellungen_behaelt_eingaben():
    out = run_page("""click('.nav button[data-tool=settings]'); await settle();
      const inp = k => $('#settings-list input[data-key=' + k + ']');
      inp('lc_verified_dir').value = '/tmp/ungespeichert'; inp('discogs_token').value = 'UNGESPEICHERT';
      click(qa('#settings-list .sgroup button').find(b => b.textContent.includes('Partner hinzufügen')));
      const rows = () => qa('#settings-list .prow');
      rows()[1].querySelector('input[data-pfield=name]').value = 'Neu (ungespeichert)';
      click('#langbtn'); await settle();
      return {dir: inp('lc_verified_dir').value, tok: inp('discogs_token').value, rows: rows().length,
              name: rows()[1].querySelector('input[data-pfield=name]').value,
              heads: texts('#settings-list .sgroup h2'), visible: visibleText().join(' | ')};""",
                   api={"/api/settings": SETTINGS})
    assert out["errors"] == []
    r = out["result"]
    assert (r["dir"], r["tok"], r["rows"], r["name"]) == ("/tmp/ungespeichert", "UNGESPEICHERT", 2, "Neu (ungespeichert)")
    assert r["heads"][0] == "Folders", r["heads"]
    for de in ("Zielordner für getaggte", "Schlüssel holen", "nicht eingerichtet", "Partner entfernen", "Verbindung prüfen"):
        assert de not in r["visible"], de


EXAMPLE_DE = "Interpret;Titel;Komponist;Texter;Verlag;Label;Labelcode;Album;Track-Nr.;ISRC;ISWC;GEMA-Werknr."


def test_beispiel_kopfzeile_je_sprache_wird_vom_import_erkannt():
    from core import ingest
    m = re.search(r'"' + re.escape(EXAMPLE_DE) + r'"\s*:\s*"([^"]+)"', i18n_en_source())
    assert m, "EN-Eintrag für die Beispiel-Kopfzeile fehlt"
    for header in (EXAMPLE_DE, m.group(1)):
        assert set(ingest.guess_mapping(header.split(";"))) == set(ingest.FIELDS), header


@needs_node
def test_beispiel_kopfzeile_folgt_der_sprache():
    out = run_page("""click('.nav button[data-tool=db]'); await settle(); click('#dbimport');
      const r = [txt('#dbimportbox .iexample')]; click('#langbtn'); await settle(); r.push(txt('#dbimportbox .iexample')); return r;""",
                   lang="en")
    assert out["result"][0].startswith("Artist;Title;") and out["result"][1] == EXAMPLE_DE


@needs_node
def test_englische_beispiel_kopfzeile_nennt_die_feldnamen_der_oberflaeche():
    """Das englische Beispiel hieß am Ende „GEMA work number“, das Feld in der englischen Oberfläche
    „GEMA work no.“ — der Import erkennt auch diesen Namen. Jede Spalte des Beispiels ist jetzt der
    Feldname, den die englische Oberfläche zeigt."""
    out = run_page(f"return [t({EXAMPLE_DE!r}), {EXAMPLE_DE!r}.split(';').map(x => t(x)).join(';')];", lang="en")
    assert out["errors"] == []
    example, fields = out["result"]
    assert example == fields and example.endswith(";GEMA work no.")


# --- jede sichtbare Ansicht steht nach einem Wechsel komplett in der neuen Sprache ----------------------
# Erkennung über Marker-Wörter der jeweils anderen Sprache in sichtbarem Text und in title/placeholder/aria-label.
# Testdaten sind erfunden und frei von Markern (Zonk Band, Moonlight Groove, ACME).
DE_MARK = re.compile(r"[äöüßÄÖÜ„]|z\. B\.|\b(und|oder|nicht|kein|keine|keinen|Interpret|Titel|Suchen|Labelcode|Verlag|"
                     r"Komponist|Texter|Treffer|Zeilen?|Dateien|Datei|Netz|bearbeiten|Abbrechen|Anlegen|anlegen|Vorschau|"
                     r"gefunden|Liste|Spalten|Lieferung|verifiziert|Verifiziert|Vorschläge|Vorschlag|offen|Offen|Länge|"
                     r"Bearbeiten|Speichern|Eintrag|zuletzt|Stand|Alle|weitere|wählen|prüfen|Metadaten|aus|der|die|das|"
                     r"mit|von|für|im|Im)\b")
EN_MARK = re.compile(r"”|e\.g\.|\b(the(?! box)|and|or|not|no|Artist|Title|Search|Label code|Publisher|Composer|Lyricist|hits?|"
                     r"rows?|files?|Cancel|Close|Apply|Save|Delete|Edit|found|(?<!LC[- ])verified|Verified|proposals?|Length|of|with|"
                     r"from|Choose|Show|more|Add|Create|Back|Next|Look up|Discard|Refresh|choose|pick|database|Database|"
                     r"Network|Web|Composers?|missing|open|Open)\b")

SWITCH = """
  const snap = () => ({vis: visibleText(), attrs: attrTexts(), n: qa('main *, .emodal-overlay *').length});
  const r = {a: snap()}; click('#langbtn'); await settle(); r.b = snap(); click('#langbtn'); await settle(); r.c = snap();
  r.keep = typeof keep === 'function' ? keep() : null; return r;"""

SONG = {"id": 7, "interpret": "Zonk Band", "titel": "Moonlight Groove", "labelname": "ACME", "labelcode": "LC24584",
        "komponist": "Hans Zimm", "texter": "", "verlag": "Pubco", "album": "Blue Era", "track_number": "", "isrc": "",
        "iswc": "", "gema_werknummer": "", "verified": True, "sources": [], "date_verified": "2026-10-11"}
BEST = {"labelcode": "24584", "label": "ACME", "sources": ["musicbrainz", "discogs"],
        "source_urls": [{"name": "MusicBrainz", "url": "https://musicbrainz.org/release/x"}], "dual_source": True,
        "confidence": 0.92, "quality": 80, "rechterueckrufe": "R6"}
RESOLVE = {"artist": "Zonk Band", "title": "Moonlight Groove", "matched": True, "mb_score": 100, "isrcs": ["DEX111"],
           "band": "auto", "ambiguous": False, "tied": [], "composer": "Hans Zimm", "lyricist": "", "publisher": "Pubco",
           "album": "Blue Era", "date": "2001", "incomplete": True, "local": False, "index": False, "best": BEST,
           "label_hints": ["Zonk Records"], "candidates": [BEST, BEST], "notes": [],
           "stats": {"calls": 3, "cache_hits": 1, "failures": 0},
           "local_suggestions": [{"interpret": "Zonk Band", "titel": "Moonlight Groove", "labelname": "ACME", "labelcode": "24584"}],
           "index_suggestions": [{"interpret": "Zonk Band", "titel": "Moonlight", "labels": ["ACME"], "labelcodes": ["24584"],
                                  "sources": ["discogs"]}]}
IDX = {"interpret": "Zonk Band", "titel": "Moonlight Groove", "labels": ["ACME"], "labelcodes": ["24584"], "sources": ["discogs"], "album": "Blue Era"}
TITLE_ONLY = {"mode": "title_only", "title": "Moonlight Groove", "verified": [SONG], "index": [IDX] * 3, "index_total": 1879,
              "index_capped": True, "index_next": 3, "index_present": True, "title_candidates": [], "notes": []}
ARTIST_ONLY = {"mode": "artist_only", "artist": "Zonk Band", "album": "", "verified": [SONG], "index": [IDX], "index_capped": True,
               "index_present": True, "albums": [{"album": "Blue Era", "n": 1}, {"album": "Red Era", "n": 4}]}
CARD = {"event_num": "001", "clip_name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "labelname": "ACME",
        "labelcode": "LC24584", "duration_sec": 20, "status": "found_in_db", "komponist": "Hans Zimm"}
TIE = {"event_num": "002", "clip_name": "ZONK_02.wav", "artist": "Zonk Band", "title": "Sunset", "labelname": "",
       "labelcode": "", "duration_sec": 12, "status": "ambiguous",
       "tied": [{"labelcode": "24584", "label": "ACME", "sources": ["discogs"], "confidence": 0.6},
                {"labelcode": "11111", "label": "BETA", "sources": ["musicbrainz"], "confidence": 0.6}]}
OPEN = {"event_num": "003", "clip_name": "ZONK_03.wav", "artist": "", "title": "", "labelname": "", "labelcode": "",
        "duration_sec": 5, "status": "missing_no_resolver", "composer_hint": "Hans Zimm"}
EDL_LIST = {"cards": [CARD, TIE, OPEN], "cues": 3, "total_duration_sec": 37, "summary": {}, "mapping": {"interpret": "Col A", "titel": "Col B"},
            "rows": 3, "list_warnings": []}
INTAKE = {"artist": "Zonk Band", "title": "Moonlight Groove", "best": BEST, "band": "auto", "matched": True, "isrcs": [],
          "stats": {"calls": 2, "cache_hits": 0, "failures": 0},
          "plan": {"proposed_name": "Zonk Band - Moonlight Groove - [ACME_LC24584].wav", "warnings": [], "can_apply": True}}
DBSONGS = {"total": 1, "total_all": 1, "rows": [SONG], "index_rows": [dict(IDX, verified=False, labelname="ACME", labelcode="24584")],
           "index_total": 1, "index_present": True, "missing_counts": {"texter": 1, "isrc": 1}}
LLIST = {"rows": 2, "mapping": {"prefix": "Code", "labelcode": "LC"}, "warnings": [],
         "kept": [{"prefix": "ZB", "labelcode": "24584", "label_gvl": "ACME", "library": "Zonk"}],
         "excluded": [{"prefix": "XX", "labelcode": "", "list_label": "Nope", "why": "leer"}], "infos": []}
GVLPREV = {"token": "t", "source_version": "2026-06-30", "pdf_version": "2026-06-30", "master_version": "2026-09-15",
           "older_than_master": True, "counts": {"neu": 1, "geaendert": 1, "entfernt": 0, "duplikate": 0},
           "pdf_count": 85300, "csv_count": 85224, "sample_neu": [{"labelcode": "24584", "label": "ACME", "hersteller": "Zonk"}],
           "sample_entfernt": [], "sample_geaendert": [{"labelcode": "11111", "csv": [{"label": "ACME"}], "pdf": [{"label": "BETA"}]}]}
IMPORT = {"rows": 2, "mapping": {"interpret": "Col A", "titel": "Col B"}, "fieldnames": ["Col A", "Col B", "Col C"], "artist_from_composer": 0}
DELIVERY = {"rows": 2, "delivery": True, "library": "upm", "unknown_codes": {"99999": 1},
            "labels": {"24584": {"label": "ACME", "n": 2}, "—": {"label": "", "n": 1, "names": {"Zonk Sub": 1}}},
            "no_code": 1, "sample": [{"titel": "Moonlight Groove", "interpret": "Hans Zimm", "komponist": "Hans Zimm",
                                      "labelname": "ACME", "labelcode": "24584", "file": "ZONK_01.wav"}]}
SCANF = {"found": 1, "rows": 2, "labels": {"24584": {"label": "ACME", "n": 2}, "—": {"label": "", "n": 2, "names": {"Zonk Sub": 1, "": 1}}},
         "items": [{"parent": "dl", "name": "music_metadata.csv", "rows": 2, "error": ""}]}

VIEWS = {
    "resolve": ("click('.nav button[data-tool=resolve]'); $('#art').value='Zonk Band'; $('#tit').value='Moonlight Groove';"
                "$('#rform').requestSubmit(); await settle(); click(qa('#rresult .mergeaff button')[0]); await settle();",
                {"/api/resolve": RESOLVE, "/api/db/lookup": {"db": SONG, "db_id": 7, "fields": [
                    {"key": "verlag", "status": "conflict", "net": "Other Pub"}], "artist": "Zonk Band", "title": "Moonlight Groove"}}),
    "resolve_titel": ("click('.nav button[data-tool=resolve]'); $('#tit').value='Moonlight Groove'; $('#rform').requestSubmit(); await settle();",
                      {"/api/resolve": TITLE_ONLY}),
    "resolve_interpret": ("click('.nav button[data-tool=resolve]'); $('#art').value='Zonk Band'; $('#rform').requestSubmit(); await settle();",
                          {"/api/resolve": ARTIST_ONLY}),
    "tagger": ("click('.nav button[data-tool=tagger]'); startTaggerScan('/in'); await settle();"
               "click('#tresolveall'); await settle(); click(qa('#tcards .tactions button')[0]); await settle();"
               "const ed = qa('#tcards .tprop .tactions button')[0]; click(ed);"
               "const keep = () => qa('#tcards .tcard input')[0].value;",
               {"/api/intake/scan": {"folder": "/in", "count": 2, "files": [
                   {"path": "/in/a.wav", "name": "ZONK_01.wav", "artist": "Zonk Band", "title": "Moonlight Groove", "album": "Blue Era",
                    "already_named": True, "filename_lc": "24584", "filename_label": "ACME"},
                   {"path": "/in/b.wav", "name": "ZONK_02.wav", "artist": "", "title": "", "album": ""}]},
                "/api/intake/resolve": INTAKE}),
    "tagger_drop": ("click('.nav button[data-tool=tagger]'); fire('#tdrop', 'drop', {dataTransfer: {files: []}});", {}),
    "cuesheet": ("click('.nav button[data-tool=edl]'); edlState.list = {filename: 'zonk.csv', b64: ''}; await runEdlImport(false); await settle();"
                 "click(qa('#eresult .ibtn')[0]); await settle(); click('#eresult .lcv.tie'); await settle();",
                 {"/api/edl/import-list": EDL_LIST}),
    "trackdb": ("click('.nav button[data-tool=db]'); await settle(); click('#dbnew'); qa('#dbcreatebox input')[0].value='Zonk Band';"
                "click('#dbimport'); fire(qa('#dbresult tr.etrow')[0], 'dblclick'); await settle();"
                "const keep = () => qa('#dbcreatebox input')[0].value;",
                {"/api/db/songs": DBSONGS}),
    "trackdb_import": ("click('.nav button[data-tool=db]'); await settle(); renderImportPreview('zonk.csv', __import);", {}),
    "trackdb_lieferung": ("click('.nav button[data-tool=db]'); await settle(); renderImportPreview('zonk.csv', __delivery);", {}),
    "trackdb_scan": ("click('.nav button[data-tool=db]'); await settle(); renderScanPreview('/dl', __scan);", {}),
    "labeldb": ("click('.nav button[data-tool=gvl]'); $('#gq').value='24584'; runGvl('24584'); await settle();"
                "renderLibraryListPreview({name: 'zonk.csv'}, __llist, ''); renderGvlPreview('gvl.pdf', __gvlprev); click('#gadd');",
                {"/api/search": {"mode": "code", "results": [{"labelcode": "24584", "label": "ACME", "hersteller": "Zonk",
                                                               "rechterueckrufe": "R6", "plm": "Ja", "source": "manuell"}]}}),
}
GLOBALS = (f"const __import={json.dumps(IMPORT)}, __delivery={json.dumps(dict(IMPORT, **DELIVERY))}, __scan={json.dumps(SCANF)},"
           f" __llist={json.dumps(LLIST)}, __gvlprev={json.dumps(GVLPREV)};\n")


def _foreign(texts: list[str], mark: re.Pattern) -> list[str]:
    return [x for x in texts if mark.search(x)]


@needs_node
@pytest.mark.parametrize("view", sorted(VIEWS))
@pytest.mark.parametrize("start", ["de", "en"])
def test_ansicht_wechselt_vollstaendig_die_sprache(view, start):
    scen, api = VIEWS[view]
    out = run_page(GLOBALS + scen + SWITCH, lang=start, api=dict(api), storage={"cuecore_onboarded": "1"})
    assert out["errors"] == []
    r = out["result"]
    own, other = (DE_MARK, EN_MARK) if start == "de" else (EN_MARK, DE_MARK)
    assert _foreign(r["a"]["vis"] + r["a"]["attrs"], other) == [], "schon vor dem Wechsel gemischt"
    assert _foreign(r["b"]["vis"] + r["b"]["attrs"], own) == []
    assert _foreign(r["c"]["vis"] + r["c"]["attrs"], other) == []
    assert r["a"]["n"] == r["b"]["n"] == r["c"]["n"], "Knotenzahl geändert — doppelt gebaut oder verloren"
    if r["keep"] is not None:
        assert r["keep"] == "Zonk Band"


@needs_node
def test_edl_importfehler_vom_backend_wird_uebersetzt():
    detail = ("Keine Cues erkannt — CMX3600-EDL, Avid Assemble List, Final Cut Pro XML (xmeml), FCPXML oder "
              "Pro Tools „Session Info as Text“?")
    out = run_page("click('.nav button[data-tool=edl]'); edlState.content='x'; edlState.list=null; await runEdlImport(false); await settle();"
                   "const r=[txt('#eresult .empty')]; click('#langbtn'); await settle(); r.push(txt('#eresult .empty')); return r;",
                   lang="en", api={"/api/edl/import": {"__status": 422, "__body": {"detail": detail}}})
    assert out["result"][0].startswith("No cues recognised")
    assert out["result"][1] == detail


# --- jeder Text, der in der Oberfläche erscheinen kann, hat eine englische Fassung -----------------------
def _js_str(lit: str) -> str:
    return json.loads('"' + lit.replace('\\\'', "'").replace('"', '\\"') + '"')


def _frontend_keys() -> set[str]:
    """Deutsche Texte, die das Skript über t()/tSet/tHtml oder data-i18n* übersetzen lässt (Literale und die
    Beschriftungs-Tabellen, deren Werte zur Laufzeit durch t() laufen)."""
    import html as _html
    src = INDEX.read_text(encoding="utf-8")
    markup, script = src.split("<script>")[0], src.split("<script>")[1]
    keys = {_html.unescape(m) for m in re.findall(r'data-i18n(?:-html|-ph|-title|-aria)?="([^"]*)"', markup)}
    keys |= {_js_str(m) for m in re.findall(r"(?<![\w.$])(?:t|tSet\([^,()]+|tHtml\([^,()]+),\s*'((?:[^'\\]|\\.)*)'", "," + script)}
    keys |= {_js_str(m) for m in re.findall(r"(?<![\w.$])t\(\s*'((?:[^'\\]|\\.)*)'", script)}
    keys |= {_js_str(m) for m in re.findall(r"\|\|'((?:[^'\\]|\\.)*)'\)", script)
             if re.search(r"[A-Za-zÄÖÜäöü]{3}", m) and m not in ("Library", "GVL", "null")}     # t(x||'Fallback')
    for name in ("_EST", "_ET_ERR", "_LLWHY", "_AMPEL", "_LISTFIELDS", "_DBFIELDS", "_TAGFIELDS", "_EDLFIELDS", "_DBMISSING",
                 "_LIBSRC", "KIND", "fieldLabels", "names", "bandLabels", "labels"):
        for m in re.finditer(r"\b(?:const|let)?\s*" + name + r"\s*=\s*([\[{])", script):
            end = script.index(";\n", m.end())
            body = script[m.end():end]
            vals = re.findall(r"(?::|,|\[)\s*'((?:[^'\\]|\\.)*)'", body)
            keys |= {_js_str(v) for v in vals if re.search(r"[A-Za-zÄÖÜäöü]{3}", v) and not re.fullmatch(r"[a-z_]+", v)
                     and not v.startswith("/api/")}
    return keys


NEUTRAL = {"ISRC", "ISWC", "MusicBrainz", "Discogs", "editingtools", "GVL", "PLM", "Excel", "CSV", "PDF", "Review",
           "AUTO", "REVIEW", "Library", "Index", "Status", "Label", "Album", "Export", "Name", "Host", "Clip", "Cues",
           "Tagger", "Auto-Confirm", "Genius", "AcoustID", "AudD", "ACRCloud", "Deezer", "Wikidata", "fps", "API-Key"}


def _backend_messages() -> set[str]:
    """Meldungen des Backends, die als detail/notes/warnings/why in der Oberfläche landen. f-Strings und
    zusammengesetzte Texte als Beispiel mit X1 an den eingesetzten Stellen."""
    import ast
    root = INDEX.parent.parent
    out: set[str] = set()
    for rel in ("app.py", "core/resolver.py", "core/albumresolve.py", "core/conncheck.py", "core/cuelist.py",
                "core/ingest.py", "core/librarylists.py", "core/songsdb.py", "core/tagging.py", "core/gvlsync.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}

        def samples(n) -> list[str]:
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                return [n.value]
            if isinstance(n, ast.JoinedStr):
                return ["".join(v.value if isinstance(v, ast.Constant) else "X1" for v in n.values)]
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
                return [a + b for a in samples(n.left) for b in samples(n.right)]
            if isinstance(n, ast.IfExp):
                return samples(n.body) + samples(n.orelse)
            if isinstance(n, ast.Name) and n.id in consts:
                return [consts[n.id]]
            return ["X1"]

        for n in ast.walk(tree):
            msg = None
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "HTTPException" and len(n.args) > 1:
                msg = n.args[1]
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("append", "insert") \
                    and isinstance(n.func.value, (ast.Attribute, ast.Name)) \
                    and getattr(n.func.value, "attr", getattr(n.func.value, "id", "")) in ("notes", "warnings") and n.args:
                msg = n.args[-1]
            elif isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in ("error", "detail", "why"):
                        out.update(s for s in samples(v) if " " in s)
            if msg is not None:
                out.update(s for s in samples(msg) if " " in s)
    out.add("lokale DB (verifiziert)")        # Quellen-Chip (core/resolver.py, Candidate.sources)
    return {s for s in out if s.strip() not in ("X1", "HTTP X1", "X1: X1")}


@needs_node
def test_jeder_oberflaechentext_hat_eine_englische_fassung():
    keys = sorted(_frontend_keys() - NEUTRAL)
    msgs = sorted(_backend_messages())
    assert len(keys) > 300 and len(msgs) > 40, (len(keys), len(msgs))
    out = run_page(f"""const keys = {json.dumps(keys)}, msgs = {json.dumps(msgs)};
      return {{keys: keys.filter(k => I18N_EN[k] == null), msgs: msgs.filter(m => t(m) === m)}};""", lang="en")
    assert out["errors"] == []
    assert out["result"]["keys"] == [], "ohne I18N_EN-Eintrag"
    assert out["result"]["msgs"] == [], "Backend-Meldung bleibt in EN deutsch"


@needs_node
def test_backend_meldungen_erscheinen_in_en_englisch():
    local = dict(RESOLVE, local=True, incomplete=False, label_hints=[], best=dict(BEST, source_urls=[], sources=["lokale DB (verifiziert)"]),
                 notes=["Treffer in verifizierter lokaler DB (songs.db) — kein Netz nötig"])
    out = run_page("""click('.nav button[data-tool=resolve]'); $('#art').value='Zonk Band'; $('#tit').value='Moonlight Groove';
      $('#rform').requestSubmit(); await settle(); const r = {chip: txt('#rresult .chip.src'), note: txt('#rresult .notes')};
      startTaggerScan('/gibt/es/nicht'); await settle(); r.scan = txt('#tcards .empty'); return r;""",
                   lang="en", api={"/api/resolve": local,
                                   "/api/intake/scan": {"__status": 400, "__body": {"detail": "Kein Ordner: /gibt/es/nicht"}}})
    assert out["errors"] == []
    assert out["result"] == {"chip": "local DB (verified)", "note": "Hit in the verified local DB (songs.db) — no network needed",
                             "scan": "Not a folder: /gibt/es/nicht"}


# --- Lücken-Gruppe der Track-Datenbank ---------------------------------------------------------------------
GAPS = {"total": 1048, "total_all": 66423, "rows": [SONG], "index_rows": [], "index_present": False,
        "missing_counts": {"labelcode": 1048, "komponist": 619, "texter": 4757, "verlag": 1490, "isrc": 1617, "gema_werknummer": 3660}}
GAP_SCEN = """click('.nav button[data-tool=db]'); await settle();
  return {chips: qa('#dbfbar .gap').map(g => [g.firstChild.textContent, g.getAttribute('title')]), none: txt('#dbfbar .gaps .none')};"""


@needs_node
def test_luecken_chips_einheitlich_und_tooltip_ohne_doppelte_verneinung():
    de = run_page(GAP_SCEN, api={"/api/db/songs": GAPS})["result"]["chips"]
    assert de[0] == ["Labelcode", "Nur Zeilen ohne Labelcode zeigen"] and de[5] == ["GEMA-Werknr.", "Nur Zeilen ohne GEMA-Werknr. zeigen"]
    en = run_page(GAP_SCEN, lang="en", api={"/api/db/songs": GAPS})["result"]["chips"]
    assert [c[0] for c in en] == ["Label code", "Composer", "Lyricist", "Publisher", "ISRC", "GEMA work no."]
    for _, tip in en:
        assert tip.startswith("Show only rows without ") and "without no" not in tip and "without without" not in tip, tip


@needs_node
def test_luecken_meldet_nicht_alles_gefuellt_ohne_verifizierte_zeilen():
    empty = dict(GAPS, total=0, total_all=0, rows=[], missing_counts={})
    full = dict(GAPS, missing_counts={})
    assert run_page(GAP_SCEN, api={"/api/db/songs": empty})["result"]["none"] == "keine verifizierten Zeilen"
    assert run_page(GAP_SCEN, lang="en", api={"/api/db/songs": empty})["result"]["none"] == "no verified rows"
    assert run_page(GAP_SCEN, api={"/api/db/songs": full})["result"]["none"] == "keine — alles gefüllt"


@needs_node
def test_englische_einzahl_bei_anzahl_eins():
    out = run_page("""const one = {n: 1, rows: 1, c: 1, m: 3, album: 'Blue Era', p: 'x', o: 0, h: 0, name: 'z.csv', mode: 'Label code'};
      const k = ['{album} · {n} Titel im Index', 'Würde übernommen: {n} Kürzel',
        'Übernommen: {n} Kürzel aus dieser Liste, {m} aus allen deinen Listen aktiv.',
        'Netz nicht erreichbar ({n} Fehler) — kein Treffer heißt hier nicht „kein Code“. Verbindung prüfen und erneut suchen.',
        'Komponisten-Vorschläge für {n} Titel (nicht gespeichert)', '<b>{n}</b> Treffer<span class="mode-tag">{mode}</span>',
        '{n} weitere Kandidaten mit niedrigerer Confidence/Qualität verworfen.', '{n} Dateien'];
      return {one: k.map(x => t(x, one)), two: k.map(x => t(x, Object.assign({}, one, {n: 2})))};""", lang="en")
    assert out["errors"] == []
    one, two = out["result"]["one"], out["result"]["two"]
    assert one == ["Blue Era · 1 title in the index", "Would be taken over: 1 prefix",
                   "Taken over: 1 prefix from this list, 3 active from all your lists.",
                   "Network unreachable (1 error) — no hit does not mean “no code” here. Check the connection and search again.",
                   "composer suggestion for 1 title (not saved)", '<b>1</b> match<span class="mode-tag">Label code</span>',
                   "1 more candidate with lower confidence/quality discarded.", "1 file"]
    assert two[0] == "Blue Era · 2 titles in the index" and two[-1] == "2 files"


@needs_node
def test_einzahl_auch_in_der_oberflaeche():
    folders = json.dumps([{"path": "/Users/x/Musik/Eingang", "n": 1, "at": 1}])
    out = run_page("""const r = {tile: txt('#hometiles .tile .last'), chip: txt('#trecent .rc')};
      click('.nav button[data-tool=gvl]'); $('#gq').value='24584'; runGvl('24584'); await settle(); r.gvl = txt('#gresult .meta-line'); return r;""",
                   lang="en", storage={"cuecore_recent_folders": folders},
                   api={"/api/search": {"mode": "code", "results": [{"labelcode": "24584", "label": "ACME"}]}})
    assert out["result"] == {"tile": "last · Musik/Eingang · 1 file", "chip": "Eingang1 file", "gvl": "1 matchLabel code"}


@needs_node
def test_jede_einzahl_gehoert_zu_einem_echten_schluessel():
    out = run_page("return Object.keys(I18N_EN_ONE).filter(k => I18N_EN[k] == null);")
    assert out["result"] == []


def test_keine_doppelten_schluessel_im_woerterbuch():
    keys = re.findall(r'"((?:[^"\\]|\\.)*)"\s*:\s*\n?\s*"(?:[^"\\]|\\.)*"', i18n_en_source())
    dup = sorted({k for k in keys if keys.count(k) > 1})
    assert dup == []


@needs_node
def test_gleicher_deutscher_text_je_zusammenhang_eigene_uebersetzung():
    out = run_page("""const r = {nav: txt('.nav button[data-tool=resolve] span'), health: txt('#homehealth').length > 0, rail: txt('.status-label')};
      click('#gadd'); r.label = txt('.emodal .actions .btn.sm');
      click('.nav button[data-tool=db]'); await settle(); click('#dbnew'); r.create = txt('#dbcreatebox .actions .btn.sm');
      r.de = [t('Übernehme …|liste'), t('Übernehme …|verify'), t('Anlegen|label'), t('Prüfe …|test')];
      return r;""", lang="en")
    r = out["result"]
    assert (r["nav"], r["rail"], r["label"], r["create"]) == ("Track Search", "Data status", "Add", "Create")
    assert r["de"] == ["Applying …", "Adopting …", "Add", "Testing …"]
    de = run_page("return [t('Übernehme …|liste'), t('Übernehme …|verify'), t('Anlegen|label'), t('Prüfe …|test')];")
    assert de["result"] == ["Übernehme …", "Übernehme …", "Anlegen", "Prüfe …"]


# --- Deutsche Einzahl bei Anzahl 1 („1 Dateien“, „1 Tage“, „1 Zeilen“, „1 Tracks“), analog zur englischen Einzahl ---------
@needs_node
def test_deutsche_einzahl_bei_anzahl_eins():
    out = run_page("""const v = n => ({n, rows: n, c: n, o: 0, p: 'x', name: 'z.csv', shown: n, h: 0, calls: n, hits: n, fail: n,
        ins: 0, upd: 0, files: n, filtered: ''});
      const k = ['{n} Dateien', 'zuletzt · {p} · {n} Dateien', '{n} Tage alt — PDF laden', 'aktuell · {n} Tage', '{name} — {rows} Zeilen',
        '{n} Tracks', '{n} Tracks übernehmen', '{c} Cues · {o} offen', 'Musik gesamt · {n} Cues', '<b>{n}</b> Songs{filtered}',
        '{n} weitere Kandidaten mit niedrigerer Confidence/Qualität verworfen.', '{n} Vorschläge — vor dem Export bestätigen',
        '{n} Zeilen ohne Interpret: dort tritt der Komponist ein.', '{n} ohne Interpret/Titel bleiben (Haken oder Editor)',
        'Netz: {h} von {n} Treffern', 'Netz: {calls} Calls · {hits} Cache-Hits · {fail} Fehler', '{ins} neu, {upd} ergänzt, {files} Dateinamen.'];
      return {one: k.map(x => t(x, v(1))), two: k.map(x => t(x, v(2))), nfmt: t('{n} Dateien', {n: nfmt(1)})};""")
    assert out["errors"] == []
    one, two = out["result"]["one"], out["result"]["two"]
    assert one == ["1 Datei", "zuletzt · x · 1 Datei", "1 Tag alt — PDF laden", "aktuell · 1 Tag", "z.csv — 1 Zeile",
                   "1 Track", "1 Track übernehmen", "1 Cue · 0 offen", "Musik gesamt · 1 Cue", "<b>1</b> Song",
                   "1 weiterer Kandidat mit niedrigerer Confidence/Qualität verworfen.", "1 Vorschlag — vor dem Export bestätigen",
                   "1 Zeile ohne Interpret: dort tritt der Komponist ein.", "1 ohne Interpret/Titel bleibt (Haken oder Editor)",
                   "Netz: 0 von 1 Treffer", "Netz: 1 Call · 1 Cache-Hit · 1 Fehler", "0 neu, 0 ergänzt, 1 Dateiname."]
    assert two[0] == "2 Dateien" and two[4] == "z.csv — 2 Zeilen" and two[14] == "Netz: 0 von 2 Treffern"
    assert two[15] == "Netz: 2 Calls · 2 Cache-Hits · 2 Fehler"
    assert out["result"]["nfmt"] == "1 Datei"


@needs_node
def test_deutsche_einzahl_in_der_oberflaeche():
    folders = json.dumps([{"path": "/Users/x/Musik/Eingang", "n": 1, "at": 1}])
    out = run_page("return {tile: txt('#hometiles .tile .last'), chip: txt('#trecent .rc')};", storage={"cuecore_recent_folders": folders})
    assert out["errors"] == []
    assert out["result"] == {"tile": "zuletzt · Musik/Eingang · 1 Datei", "chip": "Eingang1 Datei"}


@needs_node
def test_jede_deutsche_einzahl_gehoert_zu_einem_echten_schluessel():
    out = run_page("return Object.keys(I18N_DE_ONE).filter(k => I18N_EN[k] == null);")
    assert out["result"] == []


@needs_node
def test_englische_einzahl_auch_bei_mehreren_zaehlern():
    out = run_page("""return [t('Netz: {calls} Calls · {hits} Cache-Hits · {fail} Fehler', {calls: 1, hits: 2, fail: 1}),
      t('{ins} neu, {upd} ergänzt, {files} Dateinamen.', {ins: 1, upd: 0, files: 1}), t('✓ {n} Quellen', {n: 1}),
      t('{n} Vorschläge — vor dem Export bestätigen', {n: 1}), t('Netz: {h} von {n} Treffern', {h: 1, n: 1})];""", lang="en")
    assert out["result"] == ["Network: 1 call · 2 cache hits · 1 error", "1 new, 0 completed, 1 file name.", "✓ 1 source",
                             "1 proposal — confirm before exporting", "online: 1 of 1 hit"]
