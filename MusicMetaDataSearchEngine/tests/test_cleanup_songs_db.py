"""cleanup_songs_db.py: Bereinigung von Seed und Live-DB.

Je Regel eine kleine, frei erfundene songs.db — Codes und Namen sind Testwerte, keine verbindlichen Zuordnungen.
Standard ist der Probelauf (schreibt nichts), --apply legt vorher eine datierte Sicherung daneben.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

import cleanup_songs_db as cl
from core import songsdb
from core.labelcheck import GvlMaster, LC_BY_NAME_NOTE

MASTER = GvlMaster([
    ("00162", "COLUMBIA"), ("60459", "Colombia (COL)"), ("02061", "KPM MUSIC"), ("52738", "KPM Shorts"),
    ("30242", "Juice Music"), ("12345", "Doppel Records"), ("12346", "Doppel Records"), ("07573", "SONOTON"),
    ("10347", "CMG"), ("04952", "CEZAME"), ("99126", "Nordwand Production Music"), ("99011", "ZONK SOUNDS"),
    ("23133", "KTV"), ("00383", "Verve"), ("35188", "TRILOGY"), ("77001", "Erstname"), ("77001", "Zweitname"),
])
# Listen der Libraries selbst (data/library_prefixes.csv): Code -> normierte Library- und Listen-Namen
LIBS = {"10347": {"cezame", "cezame music agency"}, "23133": {"koka for tv"}}


def _db(tmp_path, rows: list[dict], files: list[dict] = ()) -> tuple:
    path = tmp_path / "songs.db"
    con = songsdb.open(path)
    for r in rows:
        r = {"quelle": "MASTER", **r}
        r["nkey"] = songsdb._nkey(r["interpret"], r["titel"])
        con.execute(f"INSERT INTO songs ({','.join(r)}) VALUES ({','.join('?' * len(r))})", list(r.values()))
    for f in files:
        sid = con.execute("SELECT id FROM songs WHERE interpret=? AND titel=?", (f.pop("interpret"), f.pop("titel"))).fetchone()[0]
        f = {"file_name": f["file"], "library": "upm", "song_id": sid, **f}
        con.execute(f"INSERT INTO library_files ({','.join(f)}) VALUES ({','.join('?' * len(f))})", list(f.values()))
    con.commit()
    con.close()
    return path


def _clean(path, **kw):
    con = songsdb.open(path)
    try:
        return cl.clean(con, MASTER, lib_names=LIBS, **kw), {(r["interpret"], r["titel"]): dict(r)
                                                             for r in con.execute("SELECT * FROM songs")}
    finally:
        con.close()


def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def test_lc_format(tmp_path):
    """Alt-Zeilen tragen den Code als nackte Zahl ('00383') — in songs und library_files wird 'LC' + Ziffern."""
    path = _db(tmp_path, [{"interpret": "Arooj Test", "titel": "Whisky", "labelname": "Verve", "labelcode": "00383"},
                          {"interpret": "Lib Test", "titel": "Spur", "labelname": "TRILOGY", "labelcode": "LC 35 188",
                           "quelle": "library:upm"}],
                   [{"file": "spur_1", "interpret": "Lib Test", "titel": "Spur", "labelcode": "35188"}])
    rep, rows = _clean(path)
    assert rows[("Arooj Test", "Whisky")]["labelcode"] == "LC00383"
    assert rows[("Lib Test", "Spur")]["labelcode"] == "LC35188"
    assert rep["rules"]["lc_format"] == 3                                     # 2 songs + 1 library_files
    assert ("00383", "LC00383", 1) in rep["lc_changes"]
    con = sqlite3.connect(path)
    assert con.execute("SELECT labelcode FROM library_files").fetchone()[0] == "LC35188"


def test_pro_marker_in_interpret_komponist_verlag(tmp_path):
    """'[IMRO]', '[SADAIC]', '( NS)' raus aus Interpret, Komponist und Verlag. Ein Album '100% HER' bleibt."""
    path = _db(tmp_path, [
        {"interpret": "Carl Test [IMRO]; David Probe", "titel": "Flucht", "komponist": "Carl Test [IMRO]; David Probe",
         "verlag": "Music 2 Test ( NS)", "album": "100% HER Pop Songs"},
        {"interpret": "Zhi Test [MUST]", "titel": "Weg", "komponist": "Zhi Test [MUST]", "quelle": "library:upm"},
    ])
    rep, rows = _clean(path)
    r = rows[("Carl Test; David Probe", "Flucht")]
    assert (r["komponist"], r["verlag"], r["album"]) == ("Carl Test; David Probe", "Music 2 Test", "100% HER Pop Songs")
    assert r["nkey"] == songsdb._nkey("Carl Test; David Probe", "Flucht")      # Schlüssel folgt dem Interpret
    assert rows[("Zhi Test", "Weg")]["komponist"] == "Zhi Test"                # Lieferungszeilen genauso
    assert rep["rules"]["pro_marker"] == 5
    assert ("pro_marker", "verlag", "Music 2 Test ( NS)", "Music 2 Test", 1) in rep["text_changes"]
    con = songsdb.open(path)                                                   # Suche (songs_fts) folgt
    assert songsdb.list_songs(con, query="Zhi Test")["total"] == 1
    assert songsdb.list_songs(con, query="IMRO")["total"] == 0
    con.close()


def test_wiki_reste(tmp_path):
    """'{{hlist' als Komponist (Wiki-Vorlagenrest) wird leer; eine vollständige Liste wird zu Namen."""
    path = _db(tmp_path, [{"interpret": "Ashe Test", "titel": "Moral", "komponist": "{{hlist"},
                          {"interpret": "Wiki Test", "titel": "Liste", "komponist": "{{hlist|Anna Eins|Bert Zwei}}"},
                          {"interpret": "Link Test", "titel": "Verweis", "texter": "[[Carla Drei|Carla]]"}])
    rep, rows = _clean(path)
    assert rows[("Ashe Test", "Moral")]["komponist"] == ""
    assert rows[("Wiki Test", "Liste")]["komponist"] == "Anna Eins; Bert Zwei"
    assert rows[("Link Test", "Verweis")]["texter"] == "Carla"
    assert rep["rules"]["wiki"] == 3


def test_leerzeichen(tmp_path):
    """Doppel-Leerzeichen in Interpret, Titel, Komponist, Album zu einem."""
    path = _db(tmp_path, [{"interpret": "Will  Slater", "titel": "Lights  On", "komponist": "Will  Slater",
                           "album": "Deserts  #1", "quelle": "library:cezame"}])
    rep, rows = _clean(path)
    r = rows[("Will Slater", "Lights On")]
    assert (r["komponist"], r["album"]) == ("Will Slater", "Deserts #1")
    assert rep["rules"]["leerzeichen"] == 4


def test_dubletten_zusammengefuehrt_nichts_verloren(tmp_path):
    """'Jake  Ray' und 'Jake Ray' mit demselben Titel sind EIN Song. Zusammengeführt; abweichende
    Track-Nummer und Herkunft bleiben beide stehen, leere Felder werden aufgefüllt, Dateinamen zeigen auf die
    verbleibende Zeile. Auch eine Variante nur in Groß/klein ('ANOTHER LIFE') ist dieselbe."""
    path = _db(tmp_path, [
        {"interpret": "Jake  Test", "titel": "Clear (a)", "komponist": "Jake  Test", "labelname": "TRILOGY",
         "labelcode": "LC35188", "isrc": "GBTST1800001", "track_number": "7", "quelle": "cue_sheet (7)",
         "date_added": "2026-10-08T10:12:53"},
        {"interpret": "Jake Test", "titel": "Clear (a)", "komponist": "Jake Test", "labelname": "TRILOGY",
         "labelcode": "LC35188", "isrc": "GBTST1800001", "track_number": "082", "gema_werknummer": "211337",
         "date_added": "2026-10-08T10:12:52"},
        {"interpret": "Kat  Test", "titel": "Another Life", "isrc": "GBTST2400001"},
        {"interpret": "Kat Test", "titel": "ANOTHER LIFE", "isrc": "GBTST2400001", "verlag": "TRILOGY"},
    ], [{"file": "clear_a", "interpret": "Jake  Test", "titel": "Clear (a)"}])
    rep, rows = _clean(path)
    assert len(rows) == 2
    (jake,) = [r for k, r in rows.items() if k[0] == "Jake Test"]
    assert (jake["isrc"], jake["gema_werknummer"]) == ("GBTST1800001", "211337")
    assert set(jake["track_number"].split("; ")) == {"7", "082"}
    assert set(jake["quelle"].split("; ")) == {"cue_sheet (7)", "MASTER"}
    assert jake["date_added"] == "2026-10-08T10:12:52"                         # erstes Anlegen bleibt
    con = sqlite3.connect(path)
    assert con.execute("SELECT song_id FROM library_files").fetchone()[0] == jake["id"]
    assert con.execute("SELECT COUNT(*) FROM songs GROUP BY nkey HAVING COUNT(*) > 1").fetchall() == []
    (kat,) = [r for k, r in rows.items() if k[0] == "Kat Test"]
    assert kat["verlag"] == "TRILOGY"
    assert rep["rules"]["dubletten"] == 2 and len(rep["merges"]) == 2


def test_dublette_mit_widerspruch_bleibt_zur_pruefung(tmp_path):
    """Zwei Varianten mit verschiedener ISRC: zusammenführen hieße eine verlieren — beide bleiben, zur Prüfung."""
    path = _db(tmp_path, [{"interpret": "Ray  Test", "titel": "Say Yes", "isrc": "GBTST1400001"},
                          {"interpret": "Ray Test", "titel": "Say Yes", "isrc": "GBTST1400002"}])
    rep, rows = _clean(path)
    assert len(rows) == 2 and {r["review_status"] for r in rows.values()} == {"review"}
    assert ("Ray  Test", "Say Yes") in rows                                    # Schlüssel bleibt, sonst UNIQUE-Kollision
    assert rep["rules"]["dubletten"] == 0 and rep["merge_conflicts"][0]["fields"] == ["isrc"]


def test_gvl_schreibweise_und_leerer_labelname(tmp_path):
    """Labelname in GVL-Schreibweise zum Code; Code ohne Namen bekommt den GVL-Namen. Bei einem Code mit
    mehreren GVL-Namen wird nicht geraten."""
    path = _db(tmp_path, [{"interpret": "Anna Probe", "titel": "Lied", "labelname": "KPM Music", "labelcode": "LC02061"},
                          {"interpret": "Leer Name", "titel": "X", "labelname": "", "labelcode": "LC30242"},
                          {"interpret": "Zwei Namen", "titel": "Y", "labelcode": "LC77001"}])
    rep, rows = _clean(path)
    assert rows[("Anna Probe", "Lied")]["labelname"] == "KPM MUSIC"
    assert rows[("Leer Name", "X")]["labelname"] == "Juice Music"
    assert not rows[("Zwei Namen", "Y")]["labelname"]
    assert rep["rules"]["gvl_schreibweise"] == 1 and rep["rules"]["labelname_ergaenzt"] == 1
    assert ("LC02061", "KPM Music", "LC02061", "KPM MUSIC", cl.R_SPELLING, 1) in rep["code_changes"]


def test_code_name_konflikt_name_eindeutig_gvl_code(tmp_path):
    """'Columbia' mit LC60459 (im Master 'Colombia (COL)'): der Name steht im Master eindeutig unter
    LC00162 -> GVL-Code, GVL-Schreibweise, der alte Code als Notiz an der Zeile."""
    path = _db(tmp_path, [{"interpret": "Jessie Test", "titel": "Ding", "labelname": "Columbia", "labelcode": "LC60459"}])
    rep, rows = _clean(path)
    r = rows[("Jessie Test", "Ding")]
    assert (r["labelcode"], r["labelname"], r["label_geliefert"]) == ("LC00162", "COLUMBIA", "Columbia · LC60459")
    assert rep["rules"]["code_name_konflikt"] == 1
    assert ("LC60459", "Columbia", "LC00162", "COLUMBIA", cl.R_CONFLICT, 1) in rep["code_changes"]
    assert r["review_status"] in (None, "")


def test_review_mehrdeutig_unbekannt_und_fremder_code(tmp_path):
    """Name unter mehreren Codes oder gar nicht im Master -> zur Prüfung, Code und Name bleiben. Ein Code
    außerhalb des Masters ebenso."""
    path = _db(tmp_path, [
        {"interpret": "Mehr Deutig", "titel": "Y", "labelname": "Doppel Records", "labelcode": "LC07573"},
        {"interpret": "Ohne Treffer", "titel": "W", "labelname": "SCDV", "labelcode": "LC07573"},
        {"interpret": "Fremd Code", "titel": "Z", "labelname": "Irgendwas", "labelcode": "LC88888"},
    ])
    rep, rows = _clean(path)
    for k, code, name in ((("Mehr Deutig", "Y"), "LC07573", "Doppel Records"), (("Ohne Treffer", "W"), "LC07573", "SCDV"),
                          (("Fremd Code", "Z"), "LC88888", "Irgendwas")):
        assert (rows[k]["labelcode"], rows[k]["labelname"], rows[k]["review_status"]) == (code, name, "review")
    assert rep["rules"]["review"] == 3
    reasons = {(x["interpret"], x["reason"]) for x in rep["review_rows"]}
    assert ("Fremd Code", cl.R_UNKNOWN) in reasons
    assert any(i == "Mehr Deutig" and "LC12345, LC12346" in why for i, why in reasons)


def test_cezame_lc10347_bleibt_und_library_liste_bestaetigt(tmp_path):
    """'Cézame' steht im Master norm-gleich unter LC04952, Cezame selbst nennt LC10347 — der bleibt (die
    Liste der Library bestätigt das Paar), nur der Name kommt in GVL-Schreibweise. Ebenso 'Koka for TV' LC23133."""
    path = _db(tmp_path, [{"interpret": "Cez Hand", "titel": "V", "labelname": "Cézame", "labelcode": "LC10347"},
                          {"interpret": "Koka Hand", "titel": "U", "labelname": "Koka for TV", "labelcode": "LC23133"},
                          {"interpret": "Cez Lib", "titel": "T", "labelname": "CMG", "labelcode": "LC10347",
                           "quelle": "library:cezame", "review_status": "verified"}])
    rep, rows = _clean(path)
    assert (rows[("Cez Hand", "V")]["labelcode"], rows[("Cez Hand", "V")]["labelname"]) == ("LC10347", "CMG")
    assert (rows[("Koka Hand", "U")]["labelcode"], rows[("Koka Hand", "U")]["labelname"]) == ("LC23133", "KTV")
    assert rows[("Cez Lib", "T")]["labelcode"] == "LC10347"
    assert rep["rules"]["review"] == 0 and rep["rules"]["code_name_konflikt"] == 0


def test_verifizierte_zeile_bleibt(tmp_path):
    """Von Hand geprüft ('verified', z.B. nach 'Prüfen' im Editor gespeichert): die Bereinigung ändert Code und
    Namen nicht und markiert sie nicht erneut."""
    path = _db(tmp_path, [{"interpret": "Hand Geprueft", "titel": "Q", "labelname": "Columbia", "labelcode": "LC60459",
                           "review_status": "verified"}])
    rep, rows = _clean(path)
    r = rows[("Hand Geprueft", "Q")]
    assert (r["labelcode"], r["labelname"], r["review_status"]) == ("LC60459", "Columbia", "verified")
    assert rep["skipped_verified"] == 1


def test_lieferungen_gvl_code_und_code_ueber_den_namen(tmp_path):
    """Lieferungszeilen: fremder Code mit GVL-Namen -> GVL-Code mit Notiz, ohne Code mit eindeutigem Namen ->
    GVL-Code mit Notiz. Ein fremder Code, dessen Name der Master nicht kennt, bleibt wie geliefert und wird
    NICHT zur Prüfung markiert (Lieferungen sind verifiziert)."""
    lib = {"quelle": "library:upm", "review_status": "verified", "source_type": "library"}
    path = _db(tmp_path, [
        {"interpret": "Nord Test", "titel": "Herbst", "labelname": "Nordwand Production Music", "labelcode": "LC99110", **lib},
        {"interpret": "Zonk Test", "titel": "Nordlicht", **lib},
        {"interpret": "Neu Test", "titel": "Jung", "labelname": "Ganz Neu Music", "labelcode": "LC66666", **lib},
    ], [{"file": "nord_1", "interpret": "Nord Test", "titel": "Herbst", "labelcode": "LC99110",
         "labelname": "Nordwand Production Music", "sublibrary": "Nordwand Production Music"},
        {"file": "zonk_1", "interpret": "Zonk Test", "titel": "Nordlicht", "sublibrary": "Zonk Sounds"}])
    rep, rows = _clean(path)
    bbc = rows[("Nord Test", "Herbst")]
    assert (bbc["labelcode"], bbc["label_geliefert"]) == ("LC99126", "Nordwand Production Music · LC99110")
    zonk = rows[("Zonk Test", "Nordlicht")]
    assert (zonk["labelcode"], zonk["labelname"], zonk["label_geliefert"]) == ("LC99011", "ZONK SOUNDS", LC_BY_NAME_NOTE)
    neu = rows[("Neu Test", "Jung")]
    assert (neu["labelcode"], neu["review_status"]) == ("LC66666", "verified")
    assert rep["rules"]["fremder_code_lieferung"] == 2 and rep["rules"]["ohne_code_lieferung"] == 2     # je Datei- + songs-Zeile
    assert "66666" in rep["library_unknown_codes"]


def test_cue_dauer_geleert(tmp_path):
    """edl_import-Zeilen tragen die Dauer eines Cues als Songdauer — geleert; Lieferungen behalten sie."""
    path = _db(tmp_path, [{"interpret": "Cue Test", "titel": "Kurz", "source_type": "edl_import", "duration_sec": 4.0},
                          {"interpret": "Lib Test", "titel": "Lang", "source_type": "edl_import", "duration_sec": 125.0,
                           "quelle": "library:cezame"}])
    rep, rows = _clean(path)
    assert rows[("Cue Test", "Kurz")]["duration_sec"] is None
    assert rows[("Lib Test", "Lang")]["duration_sec"] == 125.0
    assert rep["rules"]["cue_dauer"] == 1


def _alles(tmp_path):
    return _db(tmp_path, [
        {"interpret": "Jessie Test", "titel": "Ding", "labelname": "Columbia", "labelcode": "60459"},
        {"interpret": "Carl Test [IMRO]", "titel": "Flucht", "komponist": "Carl Test [IMRO]"},
        {"interpret": "Jake  Test", "titel": "Clear (a)", "track_number": "7"},
        {"interpret": "Jake Test", "titel": "Clear (a)", "track_number": "082"},
        {"interpret": "Ohne Treffer", "titel": "W", "labelname": "SCDV", "labelcode": "LC07573"},
        {"interpret": "Ashe Test", "titel": "Moral", "komponist": "{{hlist"},
        {"interpret": "Cue Test", "titel": "Kurz", "source_type": "edl_import", "duration_sec": 4.0},
    ])


def test_probelauf_schreibt_nichts(tmp_path):
    path = _alles(tmp_path)
    before = _md5(path)
    rep = cl.run(path, master=MASTER, lib_names=LIBS)
    assert _md5(path) == before and list(tmp_path.iterdir()) == [path]          # keine Sicherung, nichts geschrieben
    assert not rep["apply"] and rep["changed"]
    assert rep["rules"]["code_name_konflikt"] == 1 and rep["rules"]["dubletten"] == 1
    text = cl.format_report(rep)
    assert "Probelauf" in text and "LC60459 Columbia -> LC00162 COLUMBIA" in text
    assert "Ohne Treffer - W" in text                                           # review-Zeile mit Interpret und Titel


def test_apply_mit_sicherung_und_idempotent(tmp_path):
    path = _alles(tmp_path)
    dump = lambda p: list(sqlite3.connect(p).iterdump())                        # noqa: E731
    before = dump(path)
    rep = cl.run(path, apply=True, master=MASTER, lib_names=LIBS, stamp="2026-10-12_120000")
    backup = tmp_path / "songs.sicherung-2026-10-12_120000.db"
    assert rep["backup"] == str(backup) and dump(backup) == before               # Sicherung = Stand vorher
    assert dump(path) != before
    con = sqlite3.connect(path)
    assert con.execute("SELECT labelcode FROM songs WHERE interpret='Jessie Test'").fetchone()[0] == "LC00162"
    assert con.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 6
    con.close()
    after = _md5(path)
    rep2 = cl.run(path, apply=True, master=MASTER, lib_names=LIBS, stamp="2026-10-12_120001")
    assert not rep2["changed"] and sum(rep2["rules"].values()) == 0
    assert _md5(path) == after and "backup" not in rep2                          # zweiter Lauf: nichts, keine Sicherung
    assert not (tmp_path / "songs.sicherung-2026-10-12_120001.db").exists()


def test_apply_bricht_ab_wenn_datei_offen(tmp_path, monkeypatch):
    path = _alles(tmp_path)
    before = _md5(path)
    monkeypatch.setattr(cl, "open_by_others", lambda p: [4711])
    with pytest.raises(SystemExit, match="4711"):
        cl.run(path, apply=True, master=MASTER, lib_names=LIBS)
    assert _md5(path) == before and list(tmp_path.iterdir()) == [path]


def test_open_by_others_erkennt_fremden_prozess(tmp_path):
    """Prüfung per lsof: ein zweiter Prozess hält die Datei offen (wie die laufende App)."""
    import shutil
    import subprocess
    import sys
    import time
    if cl.open_by_others(tmp_path / "fehlt.db") is None or not shutil.which("lsof"):
        pytest.skip("lsof fehlt — hier nicht prüfbar")
    path = _alles(tmp_path)
    assert cl.open_by_others(path) == []
    p = subprocess.Popen([sys.executable, "-c", "import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1]); "
                          "c.execute('SELECT 1 FROM songs'); print('ok', flush=True); time.sleep(30)", str(path)],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "ok"
        deadline = time.time() + 10
        while p.pid not in (cl.open_by_others(path) or []) and time.time() < deadline:
            time.sleep(0.2)
        assert p.pid in cl.open_by_others(path)
    finally:
        p.kill()
        p.wait()


def test_cli_probelauf_und_bericht(tmp_path, capsys):
    path = _alles(tmp_path)
    out = tmp_path / "bericht.txt"
    master = tmp_path / "master.csv"
    master.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n00162,COLUMBIA,,,,\n"
                      "60459,Colombia (COL),,,,\n07573,SONOTON,,,,\n", encoding="utf-8")
    assert cl.main([str(path), "--master", str(master), "--bericht", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "LC60459 Columbia -> LC00162 COLUMBIA" in text and "Probelauf" in capsys.readouterr().out
    assert sqlite3.connect(path).execute("SELECT labelcode FROM songs WHERE interpret='Jessie Test'").fetchone()[0] == "60459"


# Hersteller laut GVL-Master (ohne Zusatzliste) — fiktive Werte, nur die Gleichheit zählt
HERSTELLER = {"02061": "EMI Testmusik GmbH", "52738": "EMI Testmusik GmbH", "00162": "Sony Test GmbH",
              "60459": "Colombia Test"}


def test_dachmarke_mit_genauerem_sublabel_code_behaelt_den_code(tmp_path):
    """'KPM Music' mit LC52738 (KPM Shorts) darf nach der Konfliktregel nicht auf KPM MUSIC
    LC02061 springen. Gleicher Hersteller heißt: der Name ist die Dachmarke, der Code das genauere Sub-Label —
    Code bleibt, Labelname in GVL-Schreibweise des Codes. Colombia/Columbia (anderer Hersteller) bleibt Konflikt."""
    path = _db(tmp_path, [
        {"interpret": "Kpm Test", "titel": "Kurz", "labelname": "KPM Music", "labelcode": "LC52738"},
        {"interpret": "Jessie Test", "titel": "Ding", "labelname": "Columbia", "labelcode": "LC60459"},
    ])
    rep, rows = _clean(path, hersteller=HERSTELLER)
    r = rows[("Kpm Test", "Kurz")]
    assert (r["labelcode"], r["labelname"], r["review_status"] or "") == ("LC52738", "KPM Shorts", "")
    assert ("LC52738", "KPM Music", "LC52738", "KPM Shorts", cl.R_SUBLABEL, 1) in rep["code_changes"]
    assert rows[("Jessie Test", "Ding")]["labelcode"] == "LC00162"
    assert rep["rules"]["code_name_konflikt"] == 1


def test_gvl_code_gewinnt_gegen_namen_aus_der_zusatzliste(tmp_path):
    """'Charly Records' mit LC08477 (GVL: Charly (englisch)): der Name steht nur in der Zusatzliste unter LC04647.
    Die Zusatzliste ist nachrangig (GVL gewinnt) — Code bleibt, Labelname in GVL-Schreibweise."""
    master = GvlMaster([("08477", "Charly (englisch)"), ("04647", "Charly Records")])   # 04647 nur Zusatzliste
    path = _db(tmp_path, [{"interpret": "Charly Test", "titel": "Alt", "labelname": "Charly Records",
                           "labelcode": "LC08477"}])
    con = songsdb.open(path)
    try:
        rep = cl.clean(con, master, lib_names={}, hersteller={"08477": "Charly Testlizenz"})
        r = con.execute("SELECT labelcode, labelname FROM songs").fetchone()
    finally:
        con.close()
    assert tuple(r) == ("LC08477", "Charly (englisch)")
    assert rep["rules"]["code_name_konflikt"] == 0
    assert ("LC08477", "Charly Records", "LC08477", "Charly (englisch)", cl.R_GVL_WINS, 1) in rep["code_changes"]


def test_cli_nutzt_die_hersteller_verfeinerung(tmp_path, capsys):
    """Die Kommandozeile lädt neben dem Master auch die Hersteller (sonst würde der Probelauf
    'KPM Music' LC52738 noch auf KPM MUSIC LC02061 umschreiben, obwohl run() es schon richtig macht)."""
    master = tmp_path / "master.csv"
    master.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n"
                      "02061,KPM MUSIC,KPM,Ja,EMI Testmusik GmbH,\n52738,KPM Shorts,KPMS,Ja,EMI Testmusik GmbH,\n",
                      encoding="utf-8")
    path = _db(tmp_path, [{"interpret": "Kpm Test", "titel": "Kurz", "labelname": "KPM Music", "labelcode": "LC52738"}])
    assert cl.main([str(path), "--master", str(master), "--apply"]) == 0
    out = capsys.readouterr().out
    assert cl.R_SUBLABEL in out and cl.R_CONFLICT not in out
    con = songsdb.open(path)
    assert tuple(con.execute("SELECT labelcode, labelname FROM songs").fetchone()) == ("LC52738", "KPM Shorts")
    con.close()
