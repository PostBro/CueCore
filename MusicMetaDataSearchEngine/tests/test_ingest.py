from pathlib import Path

from core import ingest, songsdb


def test_guess_mapping_de_en():
    m = ingest.guess_mapping(["Künstler", "Track", "Composer", "LC", "Egal"])
    assert m["interpret"] == "Künstler" and m["titel"] == "Track"
    assert m["komponist"] == "Composer" and m["labelcode"] == "LC"


def test_import_csv(tmp_path):
    csvp = tmp_path / "lib.csv"
    csvp.write_text("Artist,Title,Label,LC\nDua Lipa,Levitating,Warner,LC00392\nFoo,Bar,,\n", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_csv(con, csvp, quelle="lib")
    assert res["inserted"] == 2 and res.get("error") is None
    hit = songsdb.find(con, "Dua Lipa", "Levitating")
    assert hit["labelcode"] == "LC00392" and hit["labelname"] == "Warner"
    con.close()


def test_import_csv_idempotent_merge(tmp_path):
    csvp = tmp_path / "lib.csv"
    csvp.write_text("Artist,Title,LC\nDua Lipa,Levitating,LC00392\n", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    ingest.import_csv(con, csvp)
    res2 = ingest.import_csv(con, csvp)        # zweiter Lauf -> Merge, keine Dublette
    assert res2["inserted"] == 0 and res2["updated"] == 1
    assert songsdb.count(con) == 1
    con.close()


def test_import_csv_missing_required(tmp_path):
    csvp = tmp_path / "bad.csv"
    csvp.write_text("ColA,ColB\n1,2\n", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_csv(con, csvp)
    assert "error" in res
    con.close()


# --- Erweiterte Felder + XLSX/XLS + Delimiter-Sniff + Preview ---

def test_guess_mapping_extended_fields():
    m = ingest.guess_mapping(["Artist", "Title", "Album", "Track-Nr.", "ISRC", "ISWC", "GEMA-Werknummer"])
    assert m["album"] == "Album" and m["track_number"] == "Track-Nr."
    assert m["isrc"] == "ISRC" and m["iswc"] == "ISWC" and m["gema_werknummer"] == "GEMA-Werknummer"


def test_import_csv_extended_fields(tmp_path):
    csvp = tmp_path / "lib.csv"
    csvp.write_text("Artist,Title,Album,Track-Nr.,ISRC,ISWC,GEMA-Werknummer\n"
                    "Dua Lipa,Levitating,Future Nostalgia,7,GBAHT2000406,T-311.223.407-6,12345678-001\n",
                    encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_file(con, csvp)
    assert res["inserted"] == 1
    hit = songsdb.find(con, "Dua Lipa", "Levitating")
    assert hit["album"] == "Future Nostalgia" and hit["track_number"] == "7"
    assert hit["isrc"] == "GBAHT2000406" and hit["iswc"] == "T-311.223.407-6"
    assert hit["gema_werknummer"] == "12345678-001"
    con.close()


def test_import_csv_semicolon_delimiter(tmp_path):
    # Deutsche Excel-CSVs nutzen oft ';' — der Sniffer muss das erkennen.
    csvp = tmp_path / "de.csv"
    csvp.write_text("Interpret;Titel;LC\nAURORA;Runaway;LC00171\n", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_file(con, csvp)
    assert res["inserted"] == 1
    assert songsdb.find(con, "AURORA", "Runaway")["labelcode"] == "LC00171"
    con.close()


def _write_xlsx(path, header, rows):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_import_xlsx_with_numeric_track_number(tmp_path):
    xp = tmp_path / "lib.xlsx"
    # track_number als numerische Zelle (2.0) muss als "2" landen, nicht "2.0"
    _write_xlsx(xp, ["Artist", "Title", "Album", "Track number", "LC"],
                [["Bon Iver", "Holocene", "Bon Iver", 5, "LC00382"]])
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_file(con, xp)
    assert res["inserted"] == 1
    hit = songsdb.find(con, "Bon Iver", "Holocene")
    assert hit["track_number"] == "5" and hit["album"] == "Bon Iver" and hit["labelcode"] == "LC00382"
    con.close()


def test_import_xls_legacy(tmp_path):
    # Committete Binär-Fixture (BIFF/.xls), gebaut mit xlwt — kein Laufzeit-Dep, nur xlrd zum Lesen.
    xp = Path(__file__).parent / "fixtures" / "lib_legacy.xls"
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_file(con, xp)
    assert res["inserted"] == 1
    hit = songsdb.find(con, "Alanis Morissette", "Thank You")
    assert hit["labelcode"] == "LC01485" and hit["track_number"] == "7"
    assert hit["album"] == "Jagged Little Pill"
    con.close()


def test_preview_file_reports_mapping_without_writing(tmp_path):
    csvp = tmp_path / "lib.csv"
    csvp.write_text("Artist,Title,Album\nA,B,C\nD,E,F\n", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    prev = ingest.preview_file(csvp)
    assert prev["required_ok"] and prev["rows"] == 2
    assert prev["mapping"]["album"] == "Album"
    assert prev["sample"][0]["interpret"] == "A"
    assert songsdb.count(con) == 0   # Trockenlauf hat nichts geschrieben
    con.close()


def test_read_table_rejects_unknown_extension(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("nope", encoding="utf-8")
    con = songsdb.open(tmp_path / "songs.db")
    assert "error" in ingest.import_file(con, p)
    con.close()


def test_guess_mapping_verlag_de_en():
    m = ingest.guess_mapping(["Artist", "Title", "Publisher"])
    assert m["verlag"] == "Publisher"
    assert ingest.guess_mapping(["Interpret", "Titel", "Musikverlag"])["verlag"] == "Musikverlag"


# --- Genereller Import: fremde Listen ohne eigenen Leser ------------------
# CSV und XLS mit automatischer Erkennung der Felder. Die fünf Formate hier entsprechen typischen
# Kopfzeilen von Katalog- und Cue-Sheet-Exporten.

def _w(p, text, encoding="utf-8"):
    p.write_bytes(text.encode(encoding))
    return p


def test_liest_utf16_mit_tabs(tmp_path):
    """Cue-Sheets mancher Libraries sind UTF-16 mit Tabs; der Parser darf daraus nicht eine Spalte Nullbytes lesen."""
    f = _w(tmp_path / "cue.csv",
           "Label\tSub Label\tAlbum title\tTrack number\tTrack title\tOriginal publisher\tComposers\t"
           "ISRC code\tLC code\tGema work code\n"
           "Nordwand\tNordwand\tZzq Album\t1\tStille Suche\tNORDWAND (NS: 100000001, 100%)\t"
           "Erika Testerin (PRS: 100000002, 100%)\tGB-X-1\tLC 35188\t1234567-001\n", "utf-16")
    pv = ingest.preview_file(f)
    assert pv["rows"] == 1
    assert pv["mapping"]["titel"] == "Track title" and pv["mapping"]["komponist"] == "Composers"
    assert pv["mapping"]["labelcode"] == "LC code" and pv["mapping"]["gema_werknummer"] == "Gema work code"
    s = pv["sample"][0]
    assert s["labelcode"] == "LC35188"                       # „LC 35188" -> Repo-Schreibweise
    assert s["komponist"] == "Erika Testerin"            # PRO, IPI und Anteil raus
    assert s["verlag"] == "NORDWAND"
    assert pv["artist_from_composer"] == 1 and pv["required_ok"]


def test_trennzeichen_wird_gezaehlt_nicht_geraten(tmp_path):
    """csv.Sniffer riet bei TRACKLISTE.csv auf Komma (die Komponisten-Spalte trägt eins) und warf
    die ganze Zeile in eine Spalte."""
    f = _w(tmp_path / "TRACKLISTE.csv",
           "File;Label;CDNumber;TrackNumber;TrackTitle;Composers;ean;isrc;AlbumTitle;LC-Code;\n"
           "A.mp3;ZZQ;1176;1.0;NORDWAND A;Erika Testerin, Max Beispiel;402;DE-B63-23-06564;"
           "ZZQ BEATS;LC07573\n")
    pv = ingest.preview_file(f)
    assert pv["mapping"]["titel"] == "TrackTitle" and pv["mapping"]["labelcode"] == "LC-Code"
    assert pv["sample"][0]["track_number"] == "1"            # „1.0" aus dem Tabellen-Export
    assert pv["sample"][0]["komponist"] == "Erika Testerin; Max Beispiel"


def test_kopfzeile_unter_einem_titelblock(tmp_path):
    """GEMA-Cue-Sheets haben Briefkopf und Leerzeilen über der Kopfzeile."""
    f = _w(tmp_path / "cue.csv",
           ";;;Music Cue Sheet;;;\n"
           "Original Title of Production:;Testserie;;;;;\n"
           ";;;;;;\n"
           "Work/ Cue Title or Number;Composer/Author IPI#;Share       %;Society;Publisher;Time code In;Duration\n"
           "It's a Good Day;Adam Howard;100;GEMA;FTMP MUSIC;10:00:00:00;0:12\n")
    pv = ingest.preview_file(f)
    assert pv["mapping"]["titel"] == "Work/ Cue Title or Number"
    assert pv["mapping"]["komponist"] == "Composer/Author IPI#" and pv["mapping"]["verlag"] == "Publisher"
    assert pv["rows"] == 1 and pv["sample"][0]["titel"] == "It's a Good Day"


def test_zusammengesetzte_kopfzeilen(tmp_path):
    """„CODE: GEMA WORK NUMBER" und „GERMAN LABEL CODE" treffen erst im zweiten Durchgang."""
    f = _w(tmp_path / "m.csv",
           "sep=,\nWORK TITLE,COMPOSERS,LABEL NAME,GERMAN LABEL CODE,CODE: GEMA WORK NUMBER,TRACK: Number\n"
           "Stille Suche,Erika Testerin [PRS] 50%,BLOCK,LC 49229,9876543-001,7\n")
    pv = ingest.preview_file(f)
    assert pv["mapping"] == {"titel": "WORK TITLE", "komponist": "COMPOSERS", "labelname": "LABEL NAME",
                             "labelcode": "GERMAN LABEL CODE", "gema_werknummer": "CODE: GEMA WORK NUMBER",
                             "track_number": "TRACK: Number"}
    assert pv["sample"][0]["komponist"] == "Erika Testerin" and pv["sample"][0]["labelcode"] == "LC49229"


def test_komponist_wird_interpret_wenn_keiner_da_ist(tmp_path):
    """Bei Production Music ohne Interpret ist der Komponist der Interpret."""
    f = _w(tmp_path / "m.csv", "Track title,Composers,LC code,Gema work code\n"
                               "Stille Suche,Erika Testerin,LC 35188,1234567-001\n"
                               "Zweiter Titel,,LC 35188,\n")
    con = songsdb.open(tmp_path / "songs.db")
    res = ingest.import_file(con, f, quelle="test")
    assert res["inserted"] == 1 and res["skipped"] == 1 and res["artist_from_composer"] == 1
    row = songsdb.find(con, "Erika Testerin", "Stille Suche")
    assert row["interpret"] == "Erika Testerin" and row["komponist"] == "Erika Testerin"
    assert row["gema_werknummer"] == "1234567-001" and row["labelcode"] == "LC35188"
    # Werknummer ist suchbar
    assert [r["titel"] for r in songsdb.list_songs(con, query="1234567-001")["rows"]] == ["Stille Suche"]
    con.close()


def test_mapping_laesst_sich_korrigieren(tmp_path):
    """Automatisch erkannt ist nicht immer richtig — die Oberfläche schickt eine korrigierte Zuordnung."""
    f = _w(tmp_path / "m.csv", "Wer,Was,Notiz\nAphex Twin,Xtal,egal\n")
    con = songsdb.open(tmp_path / "songs.db")
    assert "error" in ingest.import_file(con, f)                      # nichts erkennbar
    res = ingest.import_file(con, f, mapping={"interpret": "Wer", "titel": "Was", "komponist": "fehlt"})
    assert res["inserted"] == 1 and res["mapping"] == {"interpret": "Wer", "titel": "Was"}
    con.close()


def test_kopfzeile_gewinnt_gegen_breitere_datenzeile(tmp_path):
    """Die letzte Spalte heißt nicht — die Datenzeile hat damit eine gefüllte Zelle mehr als der Kopf.
    Trotzdem muss der Kopf der Kopf bleiben."""
    f = _w(tmp_path / "x.csv", "Interpret,Titel,\nAphex Twin,Xtal,Zusatz\nAutechre,Eggshell,Zusatz\n")
    pv = ingest.preview_file(f)
    assert pv["mapping"]["interpret"] == "Interpret" and pv["rows"] == 2


def test_kurze_aliasse_treffen_nicht_als_teilstring(tmp_path):
    """„track" steckt in „Soundtrack", „gvl" in nichts Sinnvollem — im zweiten Durchgang zählen nur
    aussagekräftige Aliasse ab sechs Zeichen."""
    f = _w(tmp_path / "x.csv", "Soundtrack Album,Calculated Value,Song Title\nx,y,Der Titel\n")
    mp = ingest.guess_mapping(["Soundtrack Album", "Calculated Value", "Song Title"])
    assert mp.get("titel") == "Song Title"          # exakter Treffer, nicht „Soundtrack Album"
    assert "labelcode" not in mp                    # „lc" aus „Calculated" zaehlt nicht
    assert ingest.preview_file(f)["sample"][0]["titel"] == "Der Titel"


def test_vorspann_verdraengt_die_kopfzeile_schmaler_listen_nicht(tmp_path):
    """Bei 3–4 Spalten ist „Maximum − 1" genau die Breite typischer Vorspann-Zeilen ('Projekt;Name').
    Die Toleranz darf den Vorspann nicht zur Kopfzeile machen — die Kopfzeile ist die mit den erkannten Spalten."""
    f = _w(tmp_path / "x.csv", "Projekt;Testprojekt 1234\nDatum;09.10.2026\nInterpret;Titel;LC\n"
                               "Aphex Twin;Xtal;LC01234\nAutechre;Eggshell;LC05678\n")
    pv = ingest.preview_file(f)
    assert pv["mapping"] == {"interpret": "Interpret", "titel": "Titel", "labelcode": "LC"} and pv["rows"] == 2
    f4 = _w(tmp_path / "y.csv", "Projekt;Testprojekt;Teil 7\nInterpret;Titel;Komponist;LC\nA;B;C;LC01234\n")
    assert ingest.preview_file(f4)["mapping"]["komponist"] == "Komponist"


def test_kurze_aliasse_mit_zusatz_werden_wieder_erkannt():
    """Auch kurze Aliasse (isrc, iswc, titel, title, gema) werden im zweiten Durchgang erkannt, wenn ein Zusatz folgt."""
    g = ingest.guess_mapping
    m = g(["Interpret", "Titel des Werkes", "Komponist", "ISRC-Nummer", "GEMA-Nr."])
    assert m["titel"] == "Titel des Werkes" and m["isrc"] == "ISRC-Nummer" and m["gema_werknummer"] == "GEMA-Nr."
    m = g(["Artist", "Original Title", "Composer", "Track ISRC", "Work ISWC"])
    assert m["titel"] == "Original Title" and m["isrc"] == "Track ISRC" and m["iswc"] == "Work ISWC"
    m = g(["Künstler", "Titel (Werk)", "Label", "LC-Nr.", "ISRC (Recording)"])
    assert m["titel"] == "Titel (Werk)" and m["isrc"] == "ISRC (Recording)"
    assert g(["Performer", "Song", "Publisher", "Label Code", "ISWC-Nr."])["iswc"] == "ISWC-Nr."
    # die Fälle, für die die Grenze eingeführt wurde, bleiben draußen
    m = g(["Soundtrack Album", "Calculated Value", "Music Library"])
    assert "titel" not in m and "labelcode" not in m and "komponist" not in m


GENERIC = ("Work Title;Composer;Label Name;LC Code\n"
           "Nordwand Generisch;Erika Testerin;Testlabel Generisch;LC99095\n"
           "Nordwand Falschcode;Erika Testerin;Atlantic;LC24584\n"
           "Nordwand Richtig;Erika Testerin;atlantic;LC 121\n"
           "Nordwand Ohne Name;Erika Testerin;;LC00121\n")


def _master():
    from core.labelcheck import GvlMaster
    return GvlMaster([("24584", "MOM & POP"), ("00121", "Atlantic")])


def test_generischer_import_prueft_code_und_name_gegen_den_master(tmp_path):
    """Listen aus fremden Quellen kommen nicht ungeprüft als verifiziert in die songs.db (LC99095 steht nicht im
    Master, 'Atlantic' + LC24584 ist laut Master MOM & POP). Passt Code und Name nicht zusammen, wird
    die Zeile zur Prüfung markiert; passt es, steht der Labelname in GVL-Schreibweise da."""
    f = _w(tmp_path / "generic.csv", GENERIC)
    pv = ingest.preview_file(f, master=_master())
    assert pv["label_review"] == 2 and pv["label_review_codes"] == ["LC24584", "LC99095"]
    con = songsdb.open(tmp_path / "songs.db")
    # eine schon belegte, verifizierte Zeile wird nicht herabgestuft
    songsdb.upsert(con, interpret="Erika Testerin", titel="Nordwand Falschcode", labelcode="LC24584",
                   labelname="MOM & POP", review_status="verified")
    res = ingest.import_file(con, f, master=_master())
    assert res["inserted"] == 3 and res["label_review"] == 1
    g = songsdb.find(con, "Erika Testerin", "Nordwand Generisch")
    assert g["review_status"] == "review" and g["labelcode"] == "LC99095" and g["labelname"] == "Testlabel Generisch"
    assert songsdb.find(con, "Erika Testerin", "Nordwand Falschcode")["review_status"] == "verified"
    ok = songsdb.find(con, "Erika Testerin", "Nordwand Richtig")
    assert not ok["review_status"] and ok["labelcode"] == "LC00121" and ok["labelname"] == "Atlantic"
    assert songsdb.find(con, "Erika Testerin", "Nordwand Ohne Name")["labelname"] == "Atlantic"
    # neu angelegt mit falschem Paar: zur Prüfung
    con2 = songsdb.open(tmp_path / "songs2.db")
    assert ingest.import_file(con2, f, master=_master())["label_review"] == 2
    assert songsdb.find(con2, "Erika Testerin", "Nordwand Falschcode")["review_status"] == "review"
    # gespeichert im Editor = vom Menschen geprüft
    row = songsdb.find_row(con2, "Erika Testerin", "Nordwand Falschcode")
    assert songsdb.update_song(con2, row["id"], {"labelname": "MOM & POP"})["ok"]
    assert songsdb.find(con2, "Erika Testerin", "Nordwand Falschcode")["review_status"] == "verified"
    con.close(); con2.close()


def test_cue_auf_ungepruefte_zeile_ist_ein_vorschlag(tmp_path):
    """Cue-Sheet: ein Treffer auf eine zur Prüfung markierte Zeile ist kein 'found_in_db' (grün), und der
    Tagger schlägt ihn nicht als doppelt verifiziert vor."""
    from core import edl_import
    from core.edl import EDLEvent
    from core.resolver import local_result
    con = songsdb.open(tmp_path / "songs.db")
    ingest.import_file(con, _w(tmp_path / "generic.csv", GENERIC), master=_master())
    ev = lambda n, clip: EDLEvent(n, "AX", "A", "C", "00:00:00:00", "00:00:10:00", "01:00:00:00", "01:00:10:00",
                                  from_clip_name=clip)
    cards = edl_import.cross_check([ev("001", "Erika Testerin - Nordwand Generisch.wav"),
                                    ev("002", "Erika Testerin - Nordwand Richtig.wav")], con)
    assert [(c["status"], c["labelcode"]) for c in cards] == [("db_review", "LC99095"), ("found_in_db", "LC00121")]
    res = local_result("Erika Testerin", "Nordwand Generisch", songsdb.find(con, "Erika Testerin", "Nordwand Generisch"))
    assert res.best.dual_source is False and res.band == "review"
    assert local_result("Erika Testerin", "Nordwand Richtig",
                        songsdb.find(con, "Erika Testerin", "Nordwand Richtig")).band == "auto"
    con.close()


def test_gema_work_no_ist_alias_der_werknummer():
    """'GEMA work no.' (so heißt das Feld in der englischen Oberfläche) ist ein eigener Alias, nicht nur ein
    Zufallstreffer über den Teilstring 'gema': steht davor eine andere GEMA-Spalte, nahm der Teilstring-Durchgang
    die erste Spalte mit 'gema' im Namen."""
    for head in ("GEMA work no.", "GEMA work no", "gema work no"):
        m = ingest.guess_mapping(["Artist", "Title", "GEMA member", head])
        assert m["gema_werknummer"] == head, (head, m)
    assert ingest.guess_mapping(["Artist", "Title", "GEMA work no."])["gema_werknummer"] == "GEMA work no."
