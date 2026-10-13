"""Label-Datenbank der Oberfläche (web/index.html) im DOM-Ersatz: GVL-PDF-Vorschau und Merge, Suche im Master.
Testdaten frei erfunden."""
from __future__ import annotations

import json
import re

from tests.webui_harness import INDEX, needs_node, run_page

PREV = {"token": "tok1", "source_version": "2026-10-01", "pdf_version": "2026-10-01", "master_version": "2026-09-15",
        "older_than_master": False, "pdf_count": 85300, "csv_count": 85224,
        "counts": {"neu": 1, "geaendert": 1, "entfernt": 1, "duplikate": 0},
        "sample_neu": [{"labelcode": "24584", "label": "ACME", "hersteller": "Zonk", "rechterueckrufe": ""}],
        "sample_entfernt": [{"labelcode": "11111", "label": "BETA", "hersteller": "", "rechterueckrufe": "R6"}],
        "sample_geaendert": [{"labelcode": "00121", "csv": [{"label": "Zonk Alt"}], "pdf": [{"label": "Zonk Neu"}]}]}
GVL = "click('.nav button[data-tool=gvl]'); await settle();\n"


# --- lokale Konstante t verdeckte t() — jede Vorschau mit geänderten Codes brach ab ---------------------
@needs_node
def test_vorschau_mit_geaenderten_codes_rendert_in_beiden_sprachen():
    scen = GVL + f"""renderGvlPreview('gvl.pdf', {json.dumps(PREV)}); await settle();
      return {{heads: texts('#gvlbox .gvlsample-h'), changed: qa('#gvlbox .gvlsample')[2].querySelectorAll('tr').map(r => txt(r)),
              buttons: texts('#gvlbox .actions button')}};"""
    de = run_page(scen)
    assert de["errors"] == []
    r = de["result"]
    assert r["heads"] == ["Neu im PDF", "Entfernt (im Master, nicht mehr im PDF)", "Geändert (Master → PDF)"]
    assert r["changed"] == ["00121Zonk Alt→Zonk Neu"]
    assert r["buttons"] == ["Master jetzt zusammenführen", "Abbrechen"]
    en = run_page(scen, lang="en")
    assert en["errors"] == []
    assert en["result"]["buttons"] == ["Merge master now", "Cancel"]
    assert en["result"]["heads"][2] == "Changed (master → PDF)"


def test_kein_lokaler_name_verdeckt_die_helfer():
    """Die ganze Klasse (z.B. eine LOC-Rekursion oder t in renderGvlPreview): eingerückte Deklarationen und Parameter
    mit dem Namen eines globalen Helfers verdecken ihn im Block — ein Aufruf davor oder darin wirft."""
    script = INDEX.read_text(encoding="utf-8").split("<script>")[1].split("</script>")[0]
    helpers = r"(?:t|el|esc|LOC|lcNum|nfmt|dfmt|tFn|tSet|tHtml|\$)"
    shadow = [m.group(0).strip() for m in re.finditer(r"^[ \t]+.*?\b(?:const|let|var)\s+" + helpers + r"\s*=", script, re.M)]
    shadow += [m.group(0) for m in re.finditer(r"\(\s*" + helpers + r"\s*,[^()]*\)\s*=>", script)]
    assert shadow == []


# --- PDF älter als der Master — Warnung in der Vorschau, Merge nur nach ausdrücklicher Rückfrage ---
OLDER = dict(PREV, source_version="2026-06-30", pdf_version="2026-06-30", older_than_master=True)
MERGED = {"rows": 84775, "source_version": "2026-06-30"}


def _merge_scen(prev: dict, choice: str | None) -> str:
    pick = "" if choice is None else f"click(qa('.askbox button').find(b => b.textContent === {json.dumps(choice)})); await settle();"
    return GVL + f"""renderGvlPreview('gvl.pdf', {json.dumps(prev)}); await settle();
      const r = {{warn: txt('#gvlbox .warnbox')}};
      click(qa('#gvlbox .actions button')[0]); await settle();
      r.ask = $('.askbox') ? [txt('.askbox h3'), txt('.askbox p'), texts('.askbox button')] : null;
      {pick}
      r.left = qa('.askbox').length; r.box = txt('#gvlbox'); return r;"""


def _merges(out) -> list:
    return [json.loads(f["body"]) for f in out["fetches"] if f["path"] == "/api/gvl/merge"]


@needs_node
def test_aelteres_pdf_warnt_in_der_vorschau_und_fragt_vor_dem_merge():
    out = run_page(_merge_scen(OLDER, "Älteres PDF übernehmen"), api={"/api/gvl/merge": MERGED})
    assert out["errors"] == []
    r = out["result"]
    assert "30.06.2026" in r["warn"] and "15.09.2026" in r["warn"] and "älter" in r["warn"], r["warn"]
    assert r["ask"][0] == "Älteres PDF übernehmen?" and "30.06.2026" in r["ask"][1] and "15.09.2026" in r["ask"][1]
    assert r["ask"][2] == ["Älteres PDF übernehmen", "Abbrechen"] and r["left"] == 0
    assert _merges(out) == [{"token": "tok1", "allow_older": True}]
    assert "Master aktualisiert" in r["box"]


@needs_node
def test_abbrechen_fuehrt_nichts_zusammen():
    out = run_page(_merge_scen(OLDER, "Abbrechen"), api={"/api/gvl/merge": MERGED})
    assert out["errors"] == []
    assert _merges(out) == [] and out["result"]["left"] == 0
    assert "Master jetzt zusammenführen" in out["result"]["box"]


@needs_node
def test_neueres_pdf_ohne_warnung_und_ohne_rueckfrage():
    out = run_page(_merge_scen(PREV, None), api={"/api/gvl/merge": MERGED})
    assert out["errors"] == []
    r = out["result"]
    assert r["warn"] is None and r["ask"] is None
    assert _merges(out) == [{"token": "tok1", "allow_older": False}]


@needs_node
def test_englisch_und_409_des_servers_uebersetzt():
    detail = ("Das PDF (Stand 2026-06-30) ist älter als der aktuelle Master (Stand 2026-09-15). "
              "Übernehmen nur nach ausdrücklicher Bestätigung — sonst gehen neuere Codes verloren.")
    out = run_page(_merge_scen(OLDER, "Apply older PDF"), lang="en",
                   api={"/api/gvl/merge": {"__status": 409, "__body": {"detail": detail}}})
    assert out["errors"] == []
    r = out["result"]
    assert "30/06/2026" in r["warn"] and "older" in r["warn"], r["warn"]
    assert r["ask"][0] == "Apply older PDF?" and r["ask"][2] == ["Apply older PDF", "Cancel"]
    assert "The PDF (as of 2026-06-30) is older than the current master" in r["box"]


# --- die Suche zeigte „30 Treffer“, obwohl es mehr gibt — ohne Hinweis und ohne Weiterblättern ------------
def _labels(n: int) -> list[dict]:
    return [{"labelcode": f"{10000 + i}", "label": f"Domino {i}", "hersteller": "", "plm": "", "rechterueckrufe": "", "source": "gvl"}
            for i in range(n)]


SEARCH_API = """__api['/api/search'] = u => { const lim = +(u.searchParams.get('limit') || 30);
  return {query: 'Domino', mode: 'fts', results: __all.slice(0, lim)}; };"""


@needs_node
def test_gekappte_suche_sagt_dass_es_mehr_gibt_und_laedt_weitere():
    scen = f"const __all = {json.dumps(_labels(78))};\n" + SEARCH_API + GVL + """
      $('#gq').value = 'Domino'; await runGvl('Domino'); await settle();
      const more = () => qa('#gresult button').find(b => b.textContent.includes('weitere') || b.textContent.includes('more'));
      const r = {head: txt('#gresult .meta-line'), rows: qa('#gresult .grow').length, hint: txt('#gresult > .hint'),
                 btn: more() && txt(more())};
      click(more()); await settle();
      r.head2 = txt('#gresult .meta-line'); r.rows2 = qa('#gresult .grow').length; r.btn2 = !!more(); return r;"""
    out = run_page(scen)
    assert out["errors"] == []
    r = out["result"]
    assert r["head"].startswith("30+ Treffer") and r["rows"] == 30, r
    assert "es gibt weitere" in r["hint"] and r["btn"] == "100 weitere laden", r
    assert r["head2"].startswith("78 Treffer") and r["rows2"] == 78 and r["btn2"] is False, r
    limits = [f["search"] for f in out["fetches"] if f["path"] == "/api/search"]
    assert limits == ["?q=Domino&limit=31", "?q=Domino&limit=131"], limits


@needs_node
def test_volle_liste_ohne_hinweis_und_englisch():
    scen = f"const __all = {json.dumps(_labels(5))};\n" + SEARCH_API + GVL + """
      $('#gq').value = 'Domino'; await runGvl('Domino'); await settle();
      return {head: txt('#gresult .meta-line'), buttons: qa('#gresult button').length};"""
    out = run_page(scen)
    assert out["errors"] == [] and out["result"] == {"head": "5 TrefferVolltext", "buttons": 0}
    en = run_page(scen.replace(json.dumps(_labels(5)), json.dumps(_labels(40))), lang="en")
    assert en["errors"] == []
    assert en["result"]["head"].startswith("30+ matches")


@needs_node
def test_neuer_suchbegriff_faengt_wieder_bei_30_an():
    scen = f"const __all = {json.dumps(_labels(78))};\n" + SEARCH_API + GVL + """
      await runGvl('Domino'); await settle();
      click(qa('#gresult button').find(b => b.textContent.includes('weitere'))); await settle();
      await runGvl('Domino Rec'); await settle(); return qa('#gresult .grow').length;"""
    out = run_page(scen)
    assert out["errors"] == []
    assert out["result"] == 30
