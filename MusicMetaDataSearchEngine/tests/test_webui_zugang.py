"""Zugänglichkeit und Bedienbarkeit der Oberfläche (web/index.html): Hover-Hinweise, Screenreader-Namen,
Kontrast, Anführungszeichen, Favicon.

Jeder Icon-Knopf bekommt einen eigenen Hover-Hinweis (höchstens 150 ms
Verzögerung, auch bei Tastatur-Fokus, nicht das native title) plus aria-label."""
from __future__ import annotations

import re

from tests.webui_harness import INDEX, i18n_en_source, needs_node, run_page

ICON_BTNS = ".nav button, #themebtn, #langbtn, #quitbtn, .helpbtn"


@needs_node
def test_icon_knoepfe_haben_aria_label_und_eigenen_hinweis_statt_title():
    out = run_page(f"""return qa('{ICON_BTNS}').map(b => [b.getAttribute('aria-label'), b.hasAttribute('data-tip'), b.getAttribute('title')]);""",
                   lang="en", width=960)
    assert out["errors"] == []
    rows = out["result"]
    assert len(rows) == 7 + 3 + 6
    for aria, tip, title in rows:
        assert aria and tip and not title, (aria, tip, title)
    assert rows[1][0] == "Track Search" or rows[1][0].lower() == "track search"


@needs_node
def test_hinweis_erscheint_schnell_bei_maus_und_sofort_bei_fokus():
    scen = """const tip = () => { const t = $('.tip'); return t && !t.hidden ? t.textContent : null; };
      const nav = $('.nav button[data-tool=resolve]');
      fire(nav, 'mouseover'); await sleep(160); const r = {hover: tip()};
      fire(nav, 'mouseout'); await sleep(10); r.out = tip();
      fire('#tool-home .head h1', 'mouseover'); await sleep(10);
      $('.helpbtn').focus(); await sleep(5); r.focus = tip(); return r;"""
    narrow = run_page(scen, width=960)
    assert narrow["errors"] == []
    assert narrow["result"] == {"hover": "Track-Suche", "out": None, "focus": "Erklärung ein-/ausblenden"}
    wide = run_page(scen, width=1400)["result"]
    assert wide["hover"] is None          # breite Rail: Beschriftung steht schon da, kein doppelter Hinweis
    assert wide["focus"] == "Erklärung ein-/ausblenden"


# --- Anführungszeichen: deutsch „…“, englisch “…” -------------------------------------------------------------
MIXED = re.compile(r'„[^“"\n]*?(?:\\"|&quot;|")')       # deutsches Öffnen, gerades Schließen


def _ui_strings() -> list[str]:
    """Alle Oberflächen-Texte, die nicht im Skript zusammengesetzt werden: Markup vor <script>, I18N_EN (Schlüssel
    und Werte), Gruppen/Felder der Einstellungen aus core/settings.py."""
    from core import settings
    src = INDEX.read_text(encoding="utf-8")
    out = [src.split("<script>")[0], i18n_en_source()]
    for g in settings.GROUPS:
        out += [g.get("label", ""), g.get("hint", "")]
    for d in (settings.KNOWN_KEYS, settings.KNOWN_PATHS, settings.PARTNER_FIELDS):
        for v in d.values():
            out += [v.get("label", ""), v.get("hint", "")] if isinstance(v, dict) else []
    return out


def test_kein_deutsches_oeffnen_mit_geradem_schliessen():
    hits = [m.group(0) for s in _ui_strings() for m in MIXED.finditer(s)]
    assert hits == []


def test_englische_texte_ohne_deutsche_anfuehrungszeichen():
    vals = re.findall(r'"(?:[^"\\]|\\.)*"\s*:\s*\n?\s*"((?:[^"\\]|\\.)*)"', i18n_en_source())
    assert vals and [v for v in vals if "„" in v] == []


@needs_node
def test_anfuehrungszeichen_in_erzeugten_texten():
    scan = {"/api/intake/scan": {"folder": "/in", "count": 2, "files": [
        {"path": "/in/a.wav", "name": "a.wav", "artist": "Zonk", "title": "Moon", "album": ""},
        {"path": "/in/b.wav", "name": "b.wav", "artist": "Zonk", "title": "Sun", "album": ""}]},
        "/api/intake/resolve": {"artist": "Zonk", "title": "Moon", "best": None, "matched": True, "isrcs": []},
        "/api/edl/import-list": {"cards": [], "cues": 0, "total_duration_sec": 0, "summary": {}, "mapping": {"interpret": "Col A"},
                                 "rows": 1, "list_warnings": []}}
    scen = """startTaggerScan('/in'); await settle(); click('#tgroupalbum'); await settle();
      click(qa('#tcards .tactions button')[0]); await settle();
      edlState.list = {filename: 'x.csv', b64: ''}; await runEdlImport(false); await settle();
      return {msg: txt('#tbatchmsg'), btn: txt('#tresolveall'), hint: txt('#tcards .tprop .hint'), list: txt('#eresult > .hint')};"""
    de = run_page(scen, api=scan)
    assert de["errors"] == []
    r = de["result"]
    assert "„Alle 2 auflösen“" in r["msg"] and r["btn"] == "Alle 2 auflösen"
    assert "„Album-LC suchen“" in r["hint"]
    assert "„Col A“" in r["list"]
    en = run_page(scen, lang="en", api=scan)["result"]
    assert "“Resolve all 2”" in en["msg"] and "“Col A”" in en["list"] and "„" not in en["list"]


def test_favicon_im_head_zeigt_auf_vorhandenes_logo():
    from fastapi.testclient import TestClient
    import app
    head = INDEX.read_text(encoding="utf-8").split("</head>")[0]
    icons = re.findall(r'<link rel="icon"[^>]*href="([^"]+)"', head)
    assert icons, "kein <link rel=icon> — Browser fragt /favicon.ico ab (404)"
    client = TestClient(app.app)
    for href in icons:
        r = client.get(href)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png", href
