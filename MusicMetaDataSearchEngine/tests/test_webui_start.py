"""Startseite (web/index.html) im DOM-Ersatz: Kacheln, Datenbestand, Zuletzt.

Regressionen: Zuletzt-Ordner fehlten beim Start (TDZ auf RECENT_KEY, vom catch in recentFolders
verschluckt), doppelte Kacheln bei überlappenden renderHome-Aufrufen."""
from __future__ import annotations

import json
import re

from tests.webui_harness import INDEX, needs_node, run_page

FOLDERS = json.dumps([{"path": "/Users/x/Musik/Eingang", "n": 7, "at": 1}])


@needs_node
def test_zuletzt_gescannter_ordner_steht_beim_start_in_kachel_und_liste():
    out = run_page("return {tile: txt('#hometiles .tile .last'), recent: texts('#homerecent button')};",
                   storage={"cuecore_recent_folders": FOLDERS})
    assert out["errors"] == []
    assert "Musik/Eingang" in out["result"]["tile"], out["result"]
    assert any("Eingang" in r for r in out["result"]["recent"]), out["result"]


@needs_node
def test_ueberlappende_aufrufe_bauen_die_startseite_nur_einmal():
    out = run_page("""
      const count = () => ({tiles: qa('#hometiles .tile').length, rows: qa('#homehealth .hrow').length,
                            recent: qa('#homerecent > *').length});
      const r = {};
      click('.nav button[data-tool=home]'); click('.nav button[data-tool=home]'); await settle(); r.doppelklick = count();
      click('#langbtn'); click('#langbtn'); await settle(); r.sprache = count();
      return r;""", storage={"cuecore_recent_folders": FOLDERS})
    assert out["errors"] == []
    for k, v in out["result"].items():
        assert v == {"tiles": 3, "rows": 5, "recent": 1}, (k, v)


@needs_node
def test_deeplink_tool_home_baut_drei_kacheln():
    out = run_page("return qa('#hometiles .tile').length;", search="?tool=home")
    assert out["result"] == 3


def _css() -> str:
    src = INDEX.read_text(encoding="utf-8")
    return src[src.index("<style>"):src.index("</style>")]


def test_datenbestand_zeile_bricht_um_statt_ueberzulaufen():
    """Layout lässt sich im DOM-Ersatz nicht messen — hier die Regeln, im Browser nachgemessen (1400×900: Pill endet
    innerhalb der Karte, „0 / 7“ einzeilig)."""
    css = re.sub(r"\s+", "", _css())
    assert ".hrow.st:first-child{flex:none}" in css                       # Beschriftung bleibt einzeilig
    assert ".hrow.st:last-child{flex-wrap:wrap;justify-content:flex-end}" in css
    assert ".hrowb{white-space:nowrap}" in css or ".hrowb{" in css and "white-space:nowrap" in css.split(".hrowb{")[1].split("}")[0]
    assert "white-space:normal" in css.split(".hrow.pill{")[1].split("}")[0]


@needs_node
def test_fehlende_dienste_mit_passender_verbform():
    seven = run_page("return texts('#homehealth .hrow .pill').pop();")["result"]
    assert seven.endswith(" fehlen") and seven.count(",") == 6
    one = run_page("return texts('#homehealth .hrow .pill').pop();", api={"/api/health": {
        "ready": True, "rows": 1, "songs": 1, "source_version": "2026-09-15", "index": {"present": False},
        "sources": {"editingtools": True, "trackmeta": True, "discogs": True, "genius": True, "acoustid": True, "audd": True}}})["result"]
    assert one == "ACRCloud fehlt"
    en = run_page("return texts('#homehealth .hrow .pill').pop();", lang="en")["result"]
    assert en.endswith(" missing")


def _contrast(fg: str, bg: str) -> float:
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_zuletzt_eintraege_lesbar_haupttext_in_textfarbe():
    css = re.sub(r"\s+", "", _css())
    assert ".recentbuttonspan{" not in css, "Regel trifft beide Spans (auch den Haupttext)"
    sub = css.split(".recentbuttonspan:last-child{")[1].split("}")[0]
    assert "color:var(--muted)" in sub
    # --muted auf --raised (Knopf-Hintergrund) erreicht in beiden Themes WCAG AA 4,5:1
    dark = dict(re.findall(r"--(muted|raised):(#[0-9A-Fa-f]{6})", css.split("html[data-theme=\"light\"]{")[0]))
    light = dict(re.findall(r"--(muted|raised):(#[0-9A-Fa-f]{6})", css.split("html[data-theme=\"light\"]{")[1].split("}")[0]))
    for theme in (dark, light):
        assert _contrast(theme["muted"], theme["raised"]) >= 4.5, theme


PARTNER = ["APL Music", "Cezame", "Extreme Music", "Fresh Tracks", "RipCue Music"]


@needs_node
def test_partnerleiste_nennt_alle_fuenf_library_partner():
    out = run_page("return {kopf: txt('.partners h4'), namen: qa('.partners li img').map(i => i.getAttribute('alt'))};")
    assert out["errors"] == []
    assert out["result"]["kopf"] == "Library-Partner"
    assert out["result"]["namen"] == PARTNER


def test_partnerlogos_tragen_alt_text_und_liegen_auf_dunkler_plakette():
    """Weiße Logos auf dunkler Plakette (hell und dunkel lesbar); jedes Bild nennt den Partner im Alt-Text,
    die Datei liegt flach in web/ (die Route liefert nur Dateinamen ohne Pfad) und trägt keine Metadaten."""
    src = INDEX.read_text(encoding="utf-8")
    bar = src[src.index('<div class="partners"'):]
    bar = bar[:bar.index("</ul>")]
    imgs = re.findall(r"<img[^>]*>", bar)
    assert len(imgs) == len(PARTNER)
    for img in imgs:
        alt = re.search(r'alt="([^"]+)"', img)
        assert alt and alt.group(1) in PARTNER, img
        name = re.search(r'src="/web/([^"/]+\.png)"', img).group(1)
        data = (INDEX.parent / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[25] == 6, name   # RGBA
        assert not any(c in data for c in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"iCCP")), name
    css = re.sub(r"\s+", "", _css())
    assert "background:#17171A" in css.split(".partnersli{")[1].split("}")[0]