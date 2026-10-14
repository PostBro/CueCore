"""Tagger-Karten der Oberfläche (web/index.html) im DOM-Ersatz: Fingerprint-Quelle, Vorschau und Apply am selben
Stand, Rückgängig. Testdaten frei erfunden."""
from __future__ import annotations

import json

from tests.webui_harness import needs_node, run_page

FILE = {"path": "/in/zonk.mp3", "name": "zonk.mp3", "artist": "Zonk Band", "title": "Moonlight Groove", "album": ""}
BEST = {"labelcode": "01485", "label": "MAVERICK", "sources": ["musicbrainz"], "source_urls": [], "confidence": 0.9,
        "dual_source": False, "rechterueckrufe": ""}
INTAKE = {"artist": "Zonk Band", "title": "Moonlight Groove", "best": BEST, "band": "review", "matched": True, "isrcs": [],
          "stats": {"calls": 1, "cache_hits": 0, "failures": 0},
          "plan": {"proposed_name": "Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3", "warnings": [], "can_apply": True}}
PLAN_121 = {"label": "Atlantic", "rechterueckrufe": "",
            "plan": {"proposed_name": "Zonk Band - Moonlight Groove - [Atlantic_LC00121].mp3", "warnings": [], "can_apply": True}}

# Karte wie im Tagger: tagCard(f) in den Body, dann auflösen. btn(text) sucht einen Knopf der Karte nach Beschriftung.
CARD = f"""const f = {json.dumps(FILE)};
  const ctrl = tagCard(f); document.body.appendChild(ctrl.el); await ctrl.resolve(); await settle();
  const card = ctrl.el, btn = s => [...card.querySelectorAll('button')].find(b => b.textContent === s);
  const apply = () => btn(t('Taggen & verschieben')), lc = () => card.querySelector('.trow input');
  const newname = () => {{ const n = card.querySelector('.newname'); return n ? txt(n) : null; }};
"""


@needs_node
def test_fingerprint_hinweis_nennt_die_erkennende_quelle():
    scen = """const out = {};
      for (const src of ['acoustid', 'audd', '']) {
        const prop = document.createElement('div'); document.body.appendChild(prop);
        renderProposal(prop, {path: '/in/x.mp3', name: 'x.mp3'}, {artist: 'Zonk', title: 'Moon', fingerprinted: true,
          fingerprint_source: src, best: null, isrcs: [], matched: true});
        out[src || 'none'] = txt(prop.querySelector('.notes'));
      }
      return out;"""
    de = run_page(scen)
    assert de["errors"] == []
    r = de["result"]
    assert "AcoustID-Fingerprint" in r["acoustid"] and "AudD" not in r["acoustid"], r
    assert "AudD-Fingerprint" in r["audd"], r
    assert "AudD" not in r["none"] and "AcoustID" not in r["none"] and "Fingerprint erkannt" in r["none"], r
    en = run_page(scen, lang="en")["result"]
    assert "AcoustID fingerprint" in en["acoustid"] and "AudD" not in en["acoustid"], en
    assert "AudD fingerprint" in en["audd"] and "via fingerprint" in en["none"], en


@needs_node
def test_geaenderter_labelcode_sperrt_taggen_bis_die_vorschau_neu_ist():
    out = run_page(CARD + """const r = {start: [newname(), apply().disabled]};
      click(btn(t('Bearbeiten'))); lc().value = '00121'; fire(lc(), 'input'); await settle();
      r.stale = [newname(), apply().disabled, txt(card.querySelector('.lab'))];
      click(apply()); await settle();
      click(btn(t('Vorschau aktualisieren'))); await settle();
      r.fresh = [newname(), apply().disabled, txt(card.querySelector('.lab'))];
      click(apply()); await settle();
      return r;""", api={"/api/intake/resolve": INTAKE, "/api/intake/plan": PLAN_121,
                         "/api/intake/apply": {"name": "Zonk Band - Moonlight Groove - [Atlantic_LC00121].mp3", "op_ids": [1]}})
    assert out["errors"] == []
    r = out["result"]
    assert r["start"] == ["Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3", False]
    # neuer Code eingetippt: der alte Zielname und das alte Label stehen nicht mehr da, Taggen ist gesperrt
    assert r["stale"][0] != "Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3" and r["stale"][1] is True, r
    assert r["stale"][2] != "MAVERICK", r
    assert r["fresh"] == ["Zonk Band - Moonlight Groove - [Atlantic_LC00121].mp3", False, "Atlantic"]
    # genau ein Apply, erst nach der neuen Vorschau, mit dem neuen Code
    paths = [f["path"] for f in out["fetches"]]
    applied = [json.loads(f["body"])["labelcode"] for f in out["fetches"] if f["path"] == "/api/intake/apply"]
    assert applied == ["00121"] and paths.index("/api/intake/plan") < paths.index("/api/intake/apply"), paths


@needs_node
def test_zurueck_zum_alten_code_gibt_die_alte_vorschau_frei():
    out = run_page(CARD + """click(btn(t('Bearbeiten'))); lc().value = '00121'; fire(lc(), 'input');
      lc().value = '01485'; fire(lc(), 'input'); await settle(); return [newname(), apply().disabled];""",
                   api={"/api/intake/resolve": INTAKE})
    assert out["errors"] == []
    assert out["result"] == ["Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3", False]


@needs_node
def test_rueckgaengig_bringt_die_karte_in_den_pruefzustand_zurueck():
    scen = CARD + """click(apply()); await settle();
      const r = {done: [card.classList.contains('applied'), txt(card.querySelector('.newname'))]};
      click(btn(t('Rückgängig'))); await settle();
      r.undone = [card.classList.contains('applied'), newname(), !!apply() && apply().disabled,
                  !!btn(t('Verwerfen')) && btn(t('Verwerfen')).disabled, txt(card.querySelector('.tprop .tactions .hint'))];
      r.fname = txt(card.querySelector('.fname'));
      click('#langbtn'); await settle(); r.en = txt(card.querySelector('.tprop .tactions .hint'));
      return r;"""
    out = run_page(scen, api={"/api/intake/resolve": INTAKE,
                              "/api/intake/apply": {"name": "Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3", "op_ids": [7]},
                              "/api/intake/undo": {"undone": True}})
    assert out["errors"] == []
    r = out["result"]
    assert r["done"] == [True, "✓ Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3"]
    # wieder die prüfbare Karte: Vorschau, Taggen und Verwerfen bedienbar, kein „✓ Zielname“ mehr
    assert r["undone"] == [False, "Zonk Band - Moonlight Groove - [MAVERICK_LC01485].mp3", False, False, "Rückgängig gemacht."]
    assert r["fname"] == "zonk.mp3"
    assert r["en"] == "Undone."
