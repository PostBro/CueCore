"""Lieferungs-Vorschau in web/index.html: fremde und ersetzte Codes müssen in BEIDEN Vorschauen
stehen — der Ordner-Scan zeigte sie gar nicht, nur 'Zzq Fantasy Label · LC88888' wie ein gültiger Chip."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


def _src() -> str:
    return INDEX.read_text(encoding="utf-8")


def _function(name: str) -> str:
    m = re.search(rf"^function {name}\(.*?^}}\n", _src(), re.MULTILINE | re.DOTALL)
    assert m, f"function {name} nicht gefunden"
    return m.group(0)


def test_beide_vorschauen_zeigen_die_code_hinweise():
    assert "deliveryCodeNotes(j)" in _function("renderScanPreview")
    assert "deliveryCodeNotes(j)" in _function("renderDeliveryPreview")


def test_hinweise_sind_uebersetzt():
    src = _src()
    for de in ("GVL-Code statt des gelieferten: {list} — der gelieferte Code bleibt als Notiz am Track.",
               "geliefert: {v}"):
        assert f'"{de}":' in src, de


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
def test_delivery_code_notes_in_node(tmp_path):
    lcnum = re.search(r"^const lcNum = .*$", _src(), re.MULTILINE).group(0)
    f = tmp_path / "notes.js"
    f.write_text(
        "const t=(s,v)=>s.replace(/\\{(\\w+)\\}/g,(_,k)=>v[k]);\n" + lcnum + "\n" + _function("deliveryCodeNotes")
        + "const txt=ns=>ns.map(x=>({cls:x.cls, text:typeof x.text==='function'?x.text():x.text}));\n"
        + "console.log(JSON.stringify([txt(deliveryCodeNotes({unknown_codes:{'88888':{label:'Zzq Fantasy Label',n:1}},"
          "corrected:{'21110':{label:'BBC Production Music',gvl_code:'27926',n:16}}})), txt(deliveryCodeNotes({labels:{}}))]));\n",
        encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    notes, empty = json.loads(r.stdout)
    assert empty == []
    assert notes[0]["cls"] == "ierr" and "LC88888" in notes[0]["text"]
    assert notes[1]["cls"] == "hint" and "LC21110 BBC Production Music → LC27926" in notes[1]["text"]


def test_ungepruefte_zeilen_sind_nicht_gruen():
    """Cue-Karten auf zur Prüfung markierte Zeilen (Status db_review) und die Zeilen selbst in der
    Track-Datenbank zeigen 'ungeprüft' bzw. 'Prüfen' statt 'Verifiziert'; die Import-Vorschau warnt."""
    src = _src()
    assert re.search(r"db_review:\s*\{t:'In DB, ungeprüft',\s*c:'warn'\}", src)
    assert "r.review_status==='review'" in _function("dbStatusCell")
    assert "j.label_review" in _function("renderImportPreview")
    for de in ("In DB, ungeprüft", "Prüfen", "Labelcode nicht im GVL-Master bestätigt", "{n} zur Prüfung markiert."):
        assert f'"{de}":' in src, de


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
def test_edl_ampel_fuer_db_review_ist_gelb(tmp_path):
    f = tmp_path / "ampel.js"
    f.write_text(_function("edlAmpel") + "console.log(JSON.stringify([edlAmpel({labelcode:'LC99095',status:'db_review'}),"
                 "edlAmpel({labelcode:'LC00121',status:'found_in_db'})]));\n", encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["warn", "ok"]


# --- Code über den eindeutigen Labelnamen — die Notiz am LC heißt so, nicht „geliefert: …“, und folgt der Sprache ---
from tests.webui_harness import needs_node, run_page                                    # noqa: E402

NOTE_BY_NAME = "LC aus dem GVL-Master über den Labelnamen"
_SONG = {"interpret": "Zonk Band", "titel": "Moonlight Groove", "labelname": "ACME", "komponist": "Hans Zimm", "texter": "",
         "verlag": "", "album": "", "track_number": "", "isrc": "", "iswc": "", "gema_werknummer": "", "verified": True,
         "sources": [], "date_verified": "2026-10-11"}
_DBSONGS = {"total": 2, "total_all": 2, "index_rows": [], "index_total": 0, "index_present": False, "missing_counts": {},
            "rows": [dict(_SONG, id=7, labelcode="LC48731", label_geliefert=NOTE_BY_NAME),
                     dict(_SONG, id=8, titel="Sunset", labelcode="LC27926", label_geliefert="Zonk Records · LC21110")]}


@needs_node
def test_notiz_am_lc_in_der_track_datenbank_folgt_der_sprache():
    out = run_page("""click('.nav button[data-tool=db]'); await settle();
      const r = [qa('#dbresult .lcnote').map(e => e.textContent)]; click('#langbtn'); await settle();
      r.push(qa('#dbresult .lcnote').map(e => e.textContent)); return r;""",
                   api={"/api/db/songs": _DBSONGS}, storage={"cuecore_onboarded": "1"})
    assert out["errors"] == []
    assert out["result"] == [[NOTE_BY_NAME, "geliefert: Zonk Records · LC21110"],
                             ["LC from the GVL master via the label name", "delivered: Zonk Records · LC21110"]]


@needs_node
def test_lieferungs_vorschau_nennt_codes_ueber_den_namen():
    j = {"rows": 2, "delivery": True, "library": "upm", "labels": {"48731": {"label": "Volta Music", "n": 1}, "—": {"label": "", "n": 1}},
         "unknown_codes": {}, "corrected": {}, "by_name": {"Volta Music": {"gvl_code": "48731", "n": 1}}, "no_code": 1, "sample": []}
    out = run_page(f"""click('.nav button[data-tool=db]'); await settle(); renderImportPreview('md.csv', {json.dumps(j)});
      const hint = () => qa('#dbimportbox .hint').map(e => e.textContent).filter(x => x.includes('LC48731'));
      const r = [hint()]; click('#langbtn'); await settle(); r.push(hint()); return r;""",
                   storage={"cuecore_onboarded": "1"})
    assert out["errors"] == []
    de, en = out["result"]
    assert de == ["Ohne Labelcode geliefert, LC über den Labelnamen aus dem GVL-Master: Volta Music → LC48731 — die Notiz steht am Track."]
    assert en == ["Delivered without a label code, LC taken from the GVL master via the label name: Volta Music → LC48731 — the note stays on the track."]


@needs_node
def test_zeilen_ohne_code_zeigen_den_labelnamen_statt_lc_strich():
    """Zeilen ohne Code erschienen in beiden Vorschauen als Chip „— · LC— · 1 Tracks“. Jetzt
    je gelieferten Labelnamen ein Chip „Name · kein Labelcode · n Tracks“; ohne Namen „ohne Labelname“."""
    labels = {"48731": {"label": "Volta Music", "n": 2},
              "—": {"label": "", "n": 3, "names": {"Testlabel Ohne Code": 2, "": 1}}}
    j = {"rows": 5, "delivery": True, "library": "upm", "labels": labels, "unknown_codes": {}, "corrected": {},
         "by_name": {}, "no_code": 3, "sample": []}
    scan = {"found": 1, "rows": 5, "labels": labels, "items": [{"parent": "dl", "name": "music_metadata.csv", "rows": 5}]}
    out = run_page(f"""click('.nav button[data-tool=db]'); await settle();
      const chips = () => qa('#dbimportbox .imap .ichip').map(e => e.textContent);
      renderImportPreview('md.csv', {json.dumps(j)}); const r = [chips()];
      renderScanPreview('/dl', {json.dumps(scan)}); r.push(chips()); click('#langbtn'); await settle(); r.push(chips());
      return r;""", storage={"cuecore_onboarded": "1"})
    assert out["errors"] == []
    upload, scan_de, scan_en = out["result"]
    assert upload == scan_de == ["Volta Music · LC48731 · 2 Tracks", "Testlabel Ohne Code · kein Labelcode · 2 Tracks",
                                 "ohne Labelname · kein Labelcode · 1 Track"]
    assert scan_en == ["Volta Music · LC48731 · 2 tracks", "Testlabel Ohne Code · no label code · 2 tracks",
                       "no label name · no label code · 1 track"]


def test_notiztext_gleich_im_backend_und_in_der_oberflaeche():
    """Die Oberfläche erkennt die Notizen am Wortlaut — er muss mit labelcheck.LC_BY_*_NOTE übereinstimmen."""
    from core.labelcheck import LC_BY_NAME_NOTE, LC_BY_PUBLISHER_NOTE
    assert LC_BY_NAME_NOTE == NOTE_BY_NAME
    for note in (LC_BY_NAME_NOTE, LC_BY_PUBLISHER_NOTE):
        assert f"'{note}'" in _src().split("const _LC_NOTES=")[1].split("\n")[0]
        assert f'"{note}":' in _src()
