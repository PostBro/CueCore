"""Library-Lieferungen im App-Weg: Upload, Ordner-Scan, Selbstheilung beim Start."""
import base64
import json

from fastapi.testclient import TestClient

import app as appmod
from app import app
from core import labeldb, librarytracks, songsdb
from core.labeldb import build_label_db
from core.models import LabelRecord

client = TestClient(app)

GVL = [
    LabelRecord("00121", "Atlantic", "", "", "Warner Music", "R6;R9"),
    LabelRecord("99126", "Nordwand Production Music", "", "Ja", "Testhersteller GmbH", ""),
    LabelRecord("99186", "Zzq Production Music", "", "Ja", "Testhersteller GmbH", ""),
    LabelRecord("99231", "Nordwand Music", "", "Ja", "Testhersteller GmbH", ""),
]
HEADER = ("AUDITION LINK,EDIT ID,WORK TITLE,ALBUM,ALBUM ID,TRACK ID,YEAR OF RELEASE,COMPOSERS,PUBLISHERS,"
          "LABEL NAME,GERMAN LABEL CODE,ISRC")


def _row(name, edit_id, title, label, code):
    return (f"https://x/{name}_{title.replace(' ', '_')}_{edit_id}.mp3,{edit_id},{title},Album,X001,X001-1,2026,"
            f"Erika Testerin,Testverlag,{label},{code},")


DELIVERY = "sep=,\n" + HEADER + "\n" + "\n".join([
    _row("ZZQ_40_1", "9000001", "Nordwand Eins", "Nordwand Music", "LC 99231"),
    _row("ZZQ_1_1", "9000002", "Nordwand Zwei", "Testlabel Nordwand", "LC 99022"),
    _row("ZZQPM_117_10", "9000003", "Nordwand Drei", "Nordwand Production Music", "LC 99110"),
]) + "\n"


def _setup(tmp_path):
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    appmod.configure(db)
    return db


def _upload(commit):
    return client.post("/api/ingest/upload", json={"filename": "music_metadata.csv", "commit": commit,
                                                   "content_b64": base64.b64encode(DELIVERY.encode()).decode()})


def test_upload_einer_lieferung_legt_fremde_codes_als_manuelles_label_an(tmp_path):
    """Der App-Weg legt fremde Codes als manuelles Label an (die Suche findet den Code, die Vorschau sagt
    dasselbe). Dazu: Nordwand LC99110 wird zum GVL-Code LC99126."""
    _setup(tmp_path)
    pv = _upload(False).json()
    assert pv["unknown_codes"] == {"99022": {"label": "Testlabel Nordwand", "n": 1}}
    assert pv["corrected"] == {"99110": {"label": "Nordwand Production Music", "gvl_code": "99126", "n": 1}}
    assert set(pv["labels"]) == {"LC99231", "LC99022", "LC99126"}
    assert client.get("/api/labels/manual").json()["labels"] == []                  # Vorschau schreibt nichts
    com = _upload(True).json()
    assert com["inserted"] == 3 and [m["labelcode"] for m in com["manual_labels"]] == ["99022"]
    manual = client.get("/api/labels/manual").json()["labels"]
    assert [(m["labelcode"], m["label"]) for m in manual] == [("99022", "Testlabel Nordwand")]
    assert "Aus Library-Lieferung upm (music_metadata.csv)" in manual[0]["note"]
    assert client.get("/api/search", params={"q": "99022"}).json()["results"][0]["label"] == "Testlabel Nordwand"
    assert [r["labelcode"] for r in client.get("/api/search", params={"q": "99110"}).json()["results"]] == []
    hit = songsdb.find_by_file(appmod.SONGS_CON, "ZZQPM_117_10_Nordwand_Drei_9000003")
    assert hit["labelcode"] == "LC99126" and hit["label_geliefert"] == "Nordwand Production Music · LC99110"
    assert _upload(True).json()["manual_labels"] == []                              # zweiter Lauf: nichts doppelt


def test_ordner_scan_zeigt_und_legt_fremde_codes_an(tmp_path):
    """Ordner-Scan: unknown_codes stehen in der Gesamtvorschau, die Übernahme legt das Label an."""
    _setup(tmp_path)
    d = tmp_path / "Downloads" / "Download 2026-10-01"
    d.mkdir(parents=True)
    (d / "music_metadata.csv").write_text(DELIVERY, encoding="utf-8")
    pv = client.post("/api/ingest/scan-folder", json={"folder": str(tmp_path / "Downloads")}).json()
    assert pv["found"] == 1 and pv["unknown_codes"] == {"99022": {"label": "Testlabel Nordwand", "n": 1}}
    assert pv["corrected"]["99110"]["gvl_code"] == "99126"
    com = client.post("/api/ingest/scan-folder", json={"folder": str(tmp_path / "Downloads"), "commit": True}).json()
    assert com["files"] == 1 and [m["labelcode"] for m in com["manual_labels"]] == ["99022"]
    assert client.get("/api/search", params={"q": "99022"}).json()["results"][0]["source"] == "manuell"


def test_start_heilt_bestand_und_legt_fehlende_labels_an(tmp_path):
    """Eine Installation, die die Lieferungen vor der GVL-Code-Korrektur eingepflegt hat (LC99110 an den
    Tracks eines Labels, ein automatisch angelegtes Label 99110, kein Label für den fremden Code), bekommt beim Start
    die Korrektur und das fehlende manuelle Label. Von Hand angelegte Labels bleiben."""
    con = songsdb.open(tmp_path / "songs.db")
    rows = librarytracks.read_upm_csv(_write(tmp_path / "md.csv", DELIVERY))
    old, _ = librarytracks.to_song_rows(rows, {"99231": "Nordwand Music"}.get)         # Stand vor der Korrektur
    songsdb.import_library_tracks(con, old, library="upm", date_verified="2026-10-08")
    con.close()
    (tmp_path / "labels_manual.json").write_text(json.dumps([
        {"labelcode": "99110", "label": "Nordwand Production Music", "note": "Aus Library-Lieferung upm "
         "(zzq_2026-01-01.csv), 16 Tracks; nicht im GVL-Master beim Import am 2026-01-01"},
        {"labelcode": "99001", "label": "Testlabel Manuell", "note": "Testnotiz"}]), encoding="utf-8")
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    lcon = labeldb.connect(db)          # wie labels_prebuilt.db des alten Bundles: Nova steht nur in der DB
    labeldb.add_manual_labels(lcon, [{"labelcode": "99166", "label": "Zzq Production Music"}])
    lcon.close()
    appmod.configure(db)
    hit = songsdb.find_by_file(appmod.SONGS_CON, "ZZQPM_117_10_Nordwand_Drei_9000003")
    assert hit["labelcode"] == "LC99126" and hit["label_geliefert"] == "Nordwand Production Music · LC99110"
    manual = {m["labelcode"]: m for m in client.get("/api/labels/manual").json()["labels"]}
    assert sorted(manual) == ["99001", "99022"]
    assert [r["labelcode"] for r in client.get("/api/search", params={"q": "Nordwand Production Music"}).json()["results"]] == ["99126"]
    assert [r["labelcode"] for r in client.get("/api/search", params={"q": "Zzq Production Music"}).json()["results"]] == ["99186"]
    appmod.configure(tmp_path / "labels.db")                                         # zweiter Start: stabil
    assert sorted(m["labelcode"] for m in client.get("/api/labels/manual").json()["labels"]) == ["99001", "99022"]


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return p


def test_generische_liste_mit_fremden_codes_wird_zur_pruefung_markiert(tmp_path):
    """Eine Liste mit LC99095 (nicht im Master) und 'Nordwand Production Music' + LC00121 (laut Master Atlantic)
    wird zur Prüfung markiert und nicht als verifiziert übernommen; das Cue-Sheet zeigt beide nicht grün."""
    _setup(tmp_path)
    text = ("Work Title;Composer;Publisher;Label Name;LC Code\n"
            "Nordwand Generisch;Erika Testerin;Testverlag;Testlabel Generisch;LC99095\n"
            "Nordwand Falschcode;Erika Testerin;Testverlag;Nordwand Production Music;LC00121\n"
            "Nordwand Passt;Erika Testerin;Testverlag;ATLANTIC;LC00121\n")
    body = {"filename": "generic.csv", "content_b64": base64.b64encode(text.encode()).decode()}
    pv = client.post("/api/ingest/upload", json={**body, "commit": False}).json()
    assert pv["label_review"] == 2 and pv["label_review_codes"] == ["LC00121", "LC99095"]
    com = client.post("/api/ingest/upload", json={**body, "commit": True}).json()
    assert com["inserted"] == 3 and com["label_review"] == 2
    edl = "".join(f"00{i}  AX  A  C  00:00:00:00 00:00:10:00 01:00:0{i}:00 01:00:0{i}:10\n"
                  f"* FROM CLIP NAME: Erika Testerin - Nordwand {t}.wav\n"
                  for i, t in enumerate(("Generisch", "Falschcode", "Passt"), 1))
    cards = client.post("/api/edl/import", json={"content": "TITLE: T\nFCM: NON-DROP FRAME\n\n" + edl}).json()["cards"]
    assert [(c["status"], c["labelcode"], c["labelname"]) for c in cards] == [
        ("db_review", "LC99095", "Testlabel Generisch"), ("db_review", "LC00121", "Nordwand Production Music"),
        ("found_in_db", "LC00121", "Atlantic")]                                        # GVL-Schreibweise
    rows = {r["titel"]: r for r in client.get("/api/db/songs", params={"q": "Nordwand"}).json()["rows"]}
    assert rows["Nordwand Generisch"]["review_status"] == "review" and not rows["Nordwand Passt"]["review_status"]


def _install_mit_automatischen_labels(tmp_path):
    """Installation wie in test_start_heilt_bestand_und_legt_fehlende_labels_an: automatisch angelegtes LC99110 in der JSON, Nova
    LC99166 nur in labels.db, von Hand angelegtes LC99001 — der Start räumt die beiden automatischen weg."""
    manual = [{"labelcode": "99110", "label": "Nordwand Production Music", "note": "Aus Library-Lieferung upm "
               "(zzq_2026-01-01.csv), 16 Tracks; nicht im GVL-Master beim Import am 2026-01-01"},
              {"labelcode": "99001", "label": "Testlabel Manuell", "note": "Testnotiz"}]
    (tmp_path / "labels_manual.json").write_text(json.dumps(manual), encoding="utf-8")
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    lcon = labeldb.connect(db)
    labeldb.add_manual_labels(lcon, [{"labelcode": "99166", "label": "Zzq Production Music"}])
    lcon.close()
    return db, (tmp_path / "labels_manual.json").read_bytes()


def _backups(tmp_path):
    return (sorted(p.name for p in tmp_path.glob("labels_manual.sicherung-*.json")),
            sorted(p.name for p in tmp_path.glob("labels.sicherung-*.db")))


def test_selbstheilung_sichert_labels_vorher_datiert(tmp_path, monkeypatch):
    """Die Selbstheilung beim Start schreibt labels_manual.json und labels.db um (überholte
    automatische Labels raus, fehlende rein). Vorher liegt jetzt eine datierte Sicherung daneben — mit dem Stand
    VOR der Änderung —, einmal je Lauf."""
    monkeypatch.setattr(appmod, "_LABEL_BACKUPS", set(), raising=False)
    db, before = _install_mit_automatischen_labels(tmp_path)
    appmod.configure(db)
    jsons, dbs = _backups(tmp_path)
    assert len(jsons) == 1 and len(dbs) == 1, (jsons, dbs)
    assert (tmp_path / jsons[0]).read_bytes() == before                            # Stand vor dem Umschreiben
    assert "99110" not in (tmp_path / "labels_manual.json").read_text(encoding="utf-8")
    old = labeldb.connect(tmp_path / dbs[0])
    assert old.execute("SELECT COUNT(*) FROM labels WHERE labelcode='99166' AND source='manuell'").fetchone()[0] == 1
    old.close()
    live = labeldb.connect(db)
    assert live.execute("SELECT COUNT(*) FROM labels WHERE labelcode='99166'").fetchone()[0] == 0
    live.close()
    # derselbe Lauf schreibt noch einmal um (neues überholtes Label) — keine zweite Sicherung
    manual = json.loads((tmp_path / "labels_manual.json").read_text(encoding="utf-8"))
    manual.append({"labelcode": "99166", "label": "Zzq Production Music", "note": "Aus Library-Lieferung upm (x)"})
    (tmp_path / "labels_manual.json").write_text(json.dumps(manual), encoding="utf-8")
    appmod.configure(db)
    assert "99166" not in (tmp_path / "labels_manual.json").read_text(encoding="utf-8")
    assert _backups(tmp_path) == (jsons, dbs)


def test_selbstheilung_ohne_aenderung_legt_keine_sicherung_an(tmp_path, monkeypatch):
    """Nur wenn wirklich etwas umgeschrieben wird: ein neuer Lauf auf einem schon geheilten Stand sichert nichts."""
    monkeypatch.setattr(appmod, "_LABEL_BACKUPS", set(), raising=False)
    db, _ = _install_mit_automatischen_labels(tmp_path)
    appmod.configure(db)
    first = _backups(tmp_path)
    monkeypatch.setattr(appmod, "_LABEL_BACKUPS", set(), raising=False)                          # nächster Programmstart
    appmod.configure(db)
    assert _backups(tmp_path) == first


OHNE_CODE_DELIVERY = "sep=,\n" + HEADER + "\n" + "\n".join([
    _row("ZZQ_40_2", "9000011", "Nordwand Elf", "Nordwand Music", ""),
    _row("ZZQ_2_1", "9000012", "Nordwand Zwoelf", "Testlabel Ohne Code", ""),
]) + "\n"


def test_upload_traegt_lc_ueber_den_eindeutigen_namen_ein(tmp_path):
    """Im App-Weg: eine Lieferungszeile ohne Code, deren Labelname im Master eindeutig ist, bekommt den GVL-Code
    mit Notiz; die Vorschau nennt das (by_name), der Rest bleibt 'ohne Labelcode'."""
    from core.labelcheck import LC_BY_NAME_NOTE
    _setup(tmp_path)
    body = {"filename": "music_metadata.csv", "content_b64": base64.b64encode(OHNE_CODE_DELIVERY.encode()).decode()}
    pv = client.post("/api/ingest/upload", json={**body, "commit": False}).json()
    assert pv["by_name"] == {"Nordwand Music": {"gvl_code": "99231", "n": 1}} and pv["no_code"] == 1
    assert set(pv["labels"]) == {"LC99231", "—"}
    com = client.post("/api/ingest/upload", json={**body, "commit": True}).json()
    assert com["by_name"] == {"Nordwand Music": {"gvl_code": "99231", "n": 1}} and com["manual_labels"] == []
    hit = songsdb.find_by_file(appmod.SONGS_CON, "ZZQ_40_2_Nordwand_Elf_9000011")
    assert (hit["labelcode"], hit["labelname"], hit["label_geliefert"]) == ("LC99231", "Nordwand Music", LC_BY_NAME_NOTE)
    assert songsdb.find_by_file(appmod.SONGS_CON, "ZZQ_2_1_Nordwand_Zwoelf_9000012")["labelcode"] == ""


def test_vorschau_nennt_die_labelnamen_der_zeilen_ohne_code(tmp_path):
    """Zeilen ohne Code erscheinen in der Lieferungs-Vorschau unter dem Eintrag „—“. Er trägt die gelieferten
    Labelnamen mit Zeilenzahl, im Upload und im Ordner-Scan (zusammengezählt über mehrere Lieferungen)."""
    _setup(tmp_path)
    body = {"filename": "music_metadata.csv", "content_b64": base64.b64encode(OHNE_CODE_DELIVERY.encode()).decode()}
    pv = client.post("/api/ingest/upload", json={**body, "commit": False}).json()
    assert pv["labels"]["—"] == {"label": "", "n": 1, "names": {"Testlabel Ohne Code": 1}}
    for sub in ("Download 1", "Download 2"):
        d = tmp_path / "Downloads" / sub
        d.mkdir(parents=True)
        (d / "music_metadata.csv").write_text(OHNE_CODE_DELIVERY, encoding="utf-8")
    scan = client.post("/api/ingest/scan-folder", json={"folder": str(tmp_path / "Downloads")}).json()
    assert scan["labels"]["—"] == {"label": "", "n": 2, "names": {"Testlabel Ohne Code": 2}}
    assert scan["labels"]["LC99231"] == {"label": "Nordwand Music", "n": 2}


def test_start_traegt_lc_fuer_bestehende_installationen_nach(tmp_path):
    """Migration beim Start (gleiche Stelle wie die Selbstheilung): früher eingepflegte Zeilen ohne Code
    bekommen den eindeutigen GVL-Code mit Notiz; ein zweiter Start ändert nichts mehr."""
    from core.labelcheck import LC_BY_NAME_NOTE
    con = songsdb.open(tmp_path / "songs.db")
    rows = librarytracks.read_upm_csv(_write(tmp_path / "md.csv", OHNE_CODE_DELIVERY))
    old, _ = librarytracks.to_song_rows(rows, {}.get)
    songsdb.import_library_tracks(con, old, library="upm", date_verified="2026-10-08")
    con.close()
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    appmod.configure(db)
    hit = songsdb.find_by_file(appmod.SONGS_CON, "ZZQ_40_2_Nordwand_Elf_9000011")
    assert (hit["labelcode"], hit["label_geliefert"]) == ("LC99231", LC_BY_NAME_NOTE)
    assert songsdb.find(appmod.SONGS_CON, "Erika Testerin", "Nordwand Elf")["labelcode"] == "LC99231"
    assert songsdb.find_by_file(appmod.SONGS_CON, "ZZQ_2_1_Nordwand_Zwoelf_9000012")["labelcode"] == ""
    assert librarytracks.fill_codes_by_name(appmod.SONGS_CON, appmod.GVL_MASTER)["filled"] == []


def test_selbstheilung_sichert_vorhandene_songs_db_einmal_vor_dem_umschreiben(tmp_path, monkeypatch):
    """Der erste Start einer neuen Version schreibt songs.db um (Schlüssel, GVL-Codes, Codes über den Labelnamen).
    Eine VORHANDENE songs.db mit älterem Stand (PRAGMA user_version) wird vorher einmal datiert gesichert, mit dem
    Stand davor; der nächste Programmstart sichert nichts mehr."""
    import sqlite3
    from core import songsdb
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    con = songsdb.open(tmp_path / "songs.db")
    songsdb.upsert(con, interpret="Zzq Alt", titel="Bestand", labelcode="LC00121")
    con.commit()
    con.close()
    appmod.configure(db)
    backups = sorted(tmp_path.glob("songs.sicherung-*.db"))
    assert len(backups) == 1, backups
    old = sqlite3.connect(backups[0])
    assert old.execute("SELECT COUNT(*) FROM songs WHERE titel='Bestand'").fetchone()[0] == 1
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0                      # Stand vor dem Umschreiben
    old.close()
    assert appmod.SONGS_CON.execute("PRAGMA user_version").fetchone()[0] == songsdb.HEAL_VERSION
    monkeypatch.setattr(appmod, "_SONGS_PATH", None)                                  # nächster Programmstart
    appmod.configure(db)
    assert sorted(tmp_path.glob("songs.sicherung-*.db")) == backups


def test_frisch_angelegte_songs_db_wird_nicht_gesichert(tmp_path):
    """Eine songs.db, die dieser Start erst aus dem mitgelieferten Seed anlegt, braucht keine Sicherung."""
    db = tmp_path / "labels.db"
    build_label_db(GVL, db, "2026-09-15")
    appmod.configure(db)
    assert (tmp_path / "songs.db").exists() and not list(tmp_path.glob("songs.sicherung-*.db"))


def test_import_mit_zwei_codes_bleibt_wie_geliefert_und_zur_pruefung(tmp_path):
    """'LC00121 / LC00392' darf beim generischen Import nicht still zu LC00121 (verifiziert) werden."""
    _setup(tmp_path)
    text = ("Work Title;Composer;Label Name;LC Code\n"
            "Nordwand Zwei Codes;Erika Testerin;ATLANTIC;LC00121 / LC00392\n"
            "Nordwand Tippfehler;Erika Testerin;ATLANTIC;LC 001-21\n")
    body = {"filename": "generic.csv", "content_b64": base64.b64encode(text.encode()).decode(), "commit": True}
    com = client.post("/api/ingest/upload", json=body).json()
    assert com["inserted"] == 2 and com["label_review"] == 2, com
    rows = {r["titel"]: r for r in client.get("/api/db/songs", params={"q": "Nordwand"}).json()["rows"]}
    assert rows["Nordwand Zwei Codes"]["labelcode"] == "LC00121 / LC00392"
    assert rows["Nordwand Tippfehler"]["labelcode"] == "LC 001-21"
    assert all(r["review_status"] == "review" for r in rows.values())
