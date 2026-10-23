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


# --- Label-Datenbank als Liste: alle Labels seitenweise, Kürzel und LC direkt in der Tabelle bearbeiten -----------
def _lrow(i: int, **kw) -> dict:
    return {"labelcode": f"{91000 + i}", "label": f"Zzq Label {i}", "kurzname": "", "plm": 0, "hersteller": "Zzq Verlag",
            "rechterueckrufe": "", "source": "gvl", "herkunft": "gvl", "kuerzel": [], **kw}


LIST_API = """__api['/api/labels/list'] = u => { const off = +u.searchParams.get('offset'), lim = +u.searchParams.get('limit');
  return {total: __all.length, rows: __all.slice(off, off + lim)}; };"""
CELL = """const dbl = (i, f) => qa('#gresult tr.etrow')[i].querySelector('td[data-field="' + f + '"]').dispatchEvent(new Event('dblclick', {bubbles: true}));
const type = v => { const inp = $('#gresult input.cellinp'); inp.value = v;
  inp.dispatchEvent(new Event('keydown', {bubbles: true, cancelable: true, init: {key: 'Enter'}})); };
const __posts = [], __fetch = window.fetch;
window.fetch = (u, o) => { if (o && o.method === 'POST') __posts.push([String(u), JSON.parse(o.body)]); return __fetch(u, o); };
const posts = () => __posts.slice();"""


def _run(rows, scen, api=None, **kw):
    return run_page(f"const __all = {json.dumps(rows)};\n" + LIST_API + CELL + GVL + scen, api=api or {}, **kw)


@needs_node
def test_liste_laedt_beim_oeffnen_und_blaettert():
    rows = [_lrow(i) for i in range(120)]
    out = _run(rows, """const r = {head: txt('#gresult .meta-line'), n: qa('#gresult tr.etrow').length, page: txt('#gpage'), prev: $('#gprev').disabled};
      click('#gnext'); await settle(); r.first = txt(qa('#gresult tr.etrow')[0].querySelector('td'));
      click(qa('#gfbar .gfilt')[0]); await settle(); click(qa('#gfbar .seg button')[2]); await settle(); return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert r == {"head": "120 Labels", "n": 50, "page": "1–50 von 120", "prev": True, "first": "91050"}, r
    lists = [f["search"] for f in out["fetches"] if f["path"] == "/api/labels/list"]
    assert lists[-1] == "?q=&offset=0&limit=50&plm=true&herkunft=eigen", lists


@needs_node
def test_kuerzel_inline_aendern_und_rueckgaengig():
    rows = [_lrow(1, kuerzel=[{"prefix": "ZZA", "key": "ZZA", "library": "Zzq", "note": "", "origin": "liste"}],
                  kurzname="Zq")]
    out = _run(rows, """const r = {chips: texts('#gresult td[data-field=kuerzel] .chip')};
      dbl(0, 'kuerzel'); r.val = $('#gresult input.cellinp').value; type('zzb, ZZC'); await settle();
      r.posts = posts(); r.undo = txt('#gundo');
      click('#gundo'); await settle(); r.after = posts().slice(r.posts.length); return r;""",
               api={"POST /api/labels/edits/prefix": {"before": "", "before_note": "", "after": ""}})
    assert out["errors"] == []
    r = out["result"]
    assert r["chips"] == ["ZZA", "Zq"] and r["val"] == "ZZA"   # GVL-Kurzname steht dabei, ist aber nicht im Feld
    assert [b for _, b in r["posts"]] == [{"prefix": "ZZA", "labelcode": ""}, {"prefix": "ZZB", "labelcode": "91001"},
                                          {"prefix": "ZZC", "labelcode": "91001"}]
    assert r["undo"] == "Rückgängig: Kürzel bei LC91001"
    assert [b["prefix"] for _, b in r["after"]] == ["ZZC", "ZZB", "ZZA"]


@needs_node
def test_umhaengen_fragt_nach_notiz():
    calls = """let __n = 0; __api['POST /api/labels/edits/prefix'] = (u, o) => JSON.parse(o.body).note
      ? {before: '91009', before_note: '', after: '91001'}
      : {__status: 409, __body: {detail: 'ZZX gehört laut Kürzel-Tabelle zu LC91009 Zzq Andere. Umhängen ist eine Korrektur und braucht eine Notiz.'}};\n"""
    out = _run([_lrow(1)], calls + """dbl(0, 'kuerzel'); type('ZZX'); await settle();
      const r = {ask: txt('.askbox p'), ok: qa('.askbox button')[0].disabled};
      const inp = $('.askbox input'); inp.value = 'Mail des Anbieters'; inp.dispatchEvent(new Event('input', {bubbles: true}));
      click(qa('.askbox button')[0]); await settle(); r.posts = posts().map(p => p[1]); r.msg = txt('#gmsg'); return r;""")
    assert out["errors"] == []
    r = out["result"]
    assert r["ask"].startswith("ZZX gehört laut Kürzel-Tabelle zu LC91009") and r["ok"] is True
    assert r["posts"][-1] == {"prefix": "ZZX", "labelcode": "91001", "note": "Mail des Anbieters"}
    assert r["msg"] == "Gespeichert — gilt sofort im Cue-Sheet."


@needs_node
def test_lc_korrektur_mit_notiz_und_markierung():
    rows = [_lrow(3, herkunft="korrektur", korrektur={"labelcode": "91004", "label": "Zzq Label 4", "note": "alt", "valid": True})]
    out = _run(rows, """const r = {lc: txt('#gresult td[data-field=lc]'), hk: txt(qa('#gresult tr.etrow td')[6])};
      dbl(0, 'lc'); r.val = $('#gresult input.cellinp').value; type('LC 91005'); await settle();
      r.ask = txt('.askbox h3'); const inp = $('.askbox input'); inp.value = 'Pressung';
      inp.dispatchEvent(new Event('input', {bubbles: true})); click(qa('.askbox button')[0]); await settle();
      r.posts = posts(); return r;""",
               api={"POST /api/labels/edits/name": {"entry": {}, "before": {"labelcode": "91004", "note": "alt"}}})
    assert out["errors"] == []
    r = out["result"]
    assert r["lc"] == "91003→ 91004" and r["hk"] == "Korrektur" and r["val"] == "91004" and r["ask"] == "LC korrigieren"
    assert r["posts"] == [["/api/labels/edits/name", {"name": "Zzq Label 3", "from_code": "91003", "labelcode": "91005",
                                                      "note": "Pressung"}]]


