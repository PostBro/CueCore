"""Library-Lieferungen (komplette Track-Datenbanken einer Production-Music-Library) in die songs.db.

Metadaten, die eine Library selbst liefert, gelten als verifiziert — sie sind die
Quelle, gegen die alles andere geprüft würde. Deshalb landen sie als `review_status=verified`,
`quelle=library:<name>` in der songs.db, plus eine Zeile je Dateiname in `library_files`, damit der
Cue-Sheet-Tab einen Clip „CEZ4030_1“ sofort findet (songsdb.find_by_file).

Ablauf: `extract` liest die Lieferung (xlsx) und schreibt einen schlanken CSV-Extrakt nach
data/library_tracks/<name>_<datum>.csv — so wie geliefert, nur die genutzten Spalten (git-versioniert,
wiederholbar). `import` liest den CSV und bildet die Zeilen auf die songs.db ab: Interpret
„Nachname, Vorname“ -> „Vorname Nachname“, Labelname in GVL-Schreibweise zum
Code (Codes, die der Master nicht kennt, bleiben wie geliefert und werden gemeldet — außer der gelieferte
Labelname steht im Master unter einem anderen Code: dann gilt der GVL-Code, s. core/labelcheck.py).

Beispiel einer Lieferung: Cezame, 60.938 Tracks, 20 Sub-Libraries mit eigenem LC in der Spalte
„SubLibrary Code“.
"""
from __future__ import annotations
import csv
import re
from pathlib import Path

from .normalize import normalize_labelcode

# Spalten des schlanken Extrakts — Werte wie geliefert, keine Umformung.
CSV_COLUMNS = ("file", "library", "sublibrary", "labelcode", "catno", "album", "track_number", "title", "version",
               "duration_sec", "artist", "composers", "arranger", "publisher", "isrc", "iswc", "gema", "release_date")


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _person(first: str, last: str) -> str:
    return " ".join(x for x in (first.strip(), last.strip()) if x)


def flip_name(name: str) -> str:
    """„Athané, Pierre-André“ -> „Pierre-André Athané“ (genau ein Komma, beide Teile gefüllt); alles
    andere unverändert („Pedro Resende“, Bandnamen ohne Komma, „A, B & C“ mit mehreren Kommas)."""
    m = re.fullmatch(r"\s*([^,]+?)\s*,\s*([^,]+?)\s*", name or "")
    return f"{m.group(2)} {m.group(1)}" if m else (name or "").strip()


def flip_person_list(value: str) -> str:
    """Personenliste mit Einträgen „Nachname, Vorname“ -> „Vorname Nachname“, je ';'-Teil. Gedreht wird nur,
    wenn beide Seiten des einen Kommas ein einzelnes Wort sind („Testerin, Erika“); „Anna Bell, Tom Roe“ sind
    zwei Personen und bleiben stehen. Ohne das teilte clean_writer_names am Komma, und aus einer Person wurden
    zwei (Interpret und Komponist „Testerin; Erika“). PRO-Marker vorher raus, sonst zählt
    „Erika [GEMA]“ als zwei Wörter."""
    from .catalog import _PRO_MARKER
    out = []
    for part in _PRO_MARKER.sub("", value or "").split(";"):
        m = re.fullmatch(r"\s*([^\s,]+)\s*,\s*([^\s,]+)\s*", part)
        out.append(f"{m.group(2)} {m.group(1)}" if m else part.strip())
    return "; ".join(x for x in out if x)


def read_cezame_xlsx(path: str | Path) -> list[dict]:
    """Cezame-Lieferung (ein Blatt, Kopfzeile TrackFilepath … ATT:AlbumDiscNumber) -> Zeilen in CSV_COLUMNS.
    Komponisten aus COM:1/COM:2 (NamesBeforeKeyName + KeyName), Arrangeur ARR:1, Verlag PUB:1.
    Die Datei trägt keine Dimensionsangabe -> reset_dimensions, sonst liefert openpyxl 0 Zeilen."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        ws.reset_dimensions()
        it = ws.iter_rows(values_only=True)
        header = [_s(c) for c in next(it, [])]
        idx = {h: i for i, h in enumerate(header)}
        need = ("TrackFilepath", "Library", "SubLibrary", "SubLibrary Code", "CatNo", "CDTitle", "TrackNo",
                "TrackTitle", "Version", "Length", "ATT:Artistname", "COM:1:KeyName", "PUB:1:KeyName", "CODE:ISRC")
        missing = [h for h in need if h not in idx]
        if missing:
            raise ValueError(f"Cezame-Lieferung: Spalten fehlen: {', '.join(missing)}")

        def g(r, h):
            i = idx.get(h)
            return _s(r[i]) if i is not None and i < len(r) else ""
        out = []
        for r in it:
            if not any(c not in (None, "") for c in r):
                continue
            title = g(r, "TrackTitle")
            if not title:
                continue
            composers = [_person(g(r, f"COM:{k}:NamesBeforeKeyName"), g(r, f"COM:{k}:KeyName")) for k in (1, 2)]
            out.append({
                "file": g(r, "TrackFilepath"), "library": g(r, "Library"), "sublibrary": g(r, "SubLibrary"),
                "labelcode": g(r, "SubLibrary Code"), "catno": g(r, "CatNo"), "album": g(r, "CDTitle"),
                "track_number": g(r, "TrackNo"), "title": title, "version": g(r, "Version"),
                "duration_sec": g(r, "Length"), "artist": g(r, "ATT:Artistname"),
                "composers": "; ".join(c for c in composers if c),
                "arranger": _person(g(r, "ARR:1:NamesBeforeKeyName"), g(r, "ARR:1:KeyName")),
                "publisher": g(r, "PUB:1:KeyName"), "isrc": g(r, "CODE:ISRC"), "iswc": g(r, "CODE:ISWC"),
                "gema": g(r, "CODE:GEMA"), "release_date": g(r, "ReleaseDate")})
        return out
    finally:
        wb.close()


# --- Universal Production Music: „music_metadata.csv“, die bei einem Download mitgeliefert wird -----------
# Spalten: AUDITION LINK, EDIT ID, WORK TITLE, ALBUM, ALBUM ID,
# TRACK ID, YEAR OF RELEASE, COMPOSERS, PUBLISHERS, LABEL NAME, GERMAN LABEL CODE, ISRC.
# Zwei Eigenheiten: Zeile 1 ist Excels „sep=,", und der Dateiname steckt nur im AUDITION LINK
# (…/VM_40_11_Little_White_Lies_Anderson_Moenks_1018801.mp3?response-content-type=…).
# Die EDIT ID steht am Ende dieses Dateinamens — und dieselbe Nummer trägt der Clip-Name in den EDLs
# („UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631"). Sie ist damit der verlässlichste
# Schlüssel, weil Katalog-Schreibweisen auseinandergehen (CSV „VM_40_11", EDL „UPM_VM40_11").
UPM_REQUIRED = ("AUDITION LINK", "EDIT ID", "WORK TITLE", "GERMAN LABEL CODE")


def looks_like_upm_csv(fieldnames) -> bool:
    have = {(f or "").strip().upper() for f in (fieldnames or [])}
    return all(c in have for c in UPM_REQUIRED)


def _upm_filename(audition_link: str) -> str:
    """Dateiname aus dem AUDITION LINK (ohne Query). '' wenn keiner drin steht."""
    return (audition_link or "").split("?")[0].rsplit("/", 1)[-1].strip()


def read_upm_csv(path: str | Path) -> list[dict]:
    """UPM-Export -> CSV_COLUMNS-Zeilen. Interpret bleibt hier leer — die Lieferung führt keinen; beim
    Import wird daraus der Komponist (bei Production Libraries ohne Interpret ist
    der Komponist der Interpret, so läuft die GEMA-Meldung). Die Cue-Zuordnung läuft ohnehin über den
    Dateinamen bzw. die Edit-ID, nicht über Interpret+Titel."""
    from .catalog import clean_writer_names
    with open(path, newline="", encoding="utf-8-sig") as f:
        first = f.readline()
        if not first.lstrip().lower().startswith("sep="):
            f.seek(0)
        rd = csv.DictReader(f)
        if not looks_like_upm_csv(rd.fieldnames):
            raise ValueError(f"{path}: keine UPM-Metadaten-CSV (erwartet: {', '.join(UPM_REQUIRED)})")
        src = [dict(r) for r in rd]
    out = []
    for r in src:
        g = lambda k: _s(r.get(k)).strip()
        fn = _upm_filename(g("AUDITION LINK"))
        album_id, track_id = g("ALBUM ID"), g("TRACK ID")
        # 'VM040-11' -> Track 11; fehlt der Bindestrich, bleibt die Nummer leer statt geraten.
        track_no = track_id.rsplit("-", 1)[-1] if "-" in track_id else ""
        out.append({
            "file": fn, "library": "upm", "sublibrary": g("LABEL NAME"),
            "labelcode": g("GERMAN LABEL CODE"),        # 'LC 48731' -> normalize_labelcode kürzt das 'LC '
            "catno": album_id or track_id, "album": g("ALBUM"), "track_number": track_no,
            "title": g("WORK TITLE"), "version": "", "duration_sec": "",
            "artist": "", "composers": clean_writer_names(flip_person_list(g("COMPOSERS"))), "arranger": "",
            "publisher": clean_writer_names(g("PUBLISHERS")), "isrc": g("ISRC"), "iswc": "", "gema": "",
            "release_date": g("YEAR OF RELEASE"), "edit_id": g("EDIT ID")})
    return out


def write_csv(rows: list[dict], path: str | Path) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CSV_COLUMNS})
    return len(rows)


def read_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        missing = [c for c in CSV_COLUMNS if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: Spalten fehlen: {', '.join(missing)}")
        return [dict(r) for r in rd]


def library_name(csv_path: str | Path) -> str:
    """data/library_tracks/cezame_2026-09.csv -> 'cezame' (Name vor dem ersten Unterstrich)."""
    return Path(csv_path).stem.split("_", 1)[0].lower()


def to_song_rows(rows: list[dict], code2name) -> tuple[list[dict], dict]:
    """CSV-Zeilen -> songsdb.import_library_tracks-Zeilen. code2name(code) -> GVL-Name oder None.
    Kennt code2name zusätzlich codes_for_name (labelcheck.GvlMaster), gilt: ein Code, den der Master nicht kennt, dessen Labelname dort aber unter genau einem anderen Code steht, wird
    durch den GVL-Code ersetzt; der gelieferte bleibt als label_geliefert an der Zeile.
    Ohne gelieferten Code gilt: steht der gelieferte Labelname (sublibrary, nicht der
    Library-Name) im Master unter genau EINEM Code, bekommt die Zeile diesen Code mit der Notiz LC_BY_NAME_NOTE;
    mehrdeutige Namen bleiben offen.
    Report: unknown_codes {code: {label, n[, gvl_codes]}} (nicht im Master — Code bleibt wie geliefert),
    corrected {geliefert: {label, gvl_code, n}}, by_name {name: {gvl_code, n}},
    name_ambiguous {name: {gvl_codes, n}} (offen), renamed {sublibrary: gvl_name} (Name weicht ab), rows,
    no_code (Zeilen, die ohne Code bleiben)."""
    from .catalog import clean_writer_names
    from .labelcheck import LC_BY_NAME_NOTE, delivered_note
    name2codes = getattr(code2name, "codes_for_name", None)
    out, unknown, corrected, renamed, no_code = [], {}, {}, {}, 0
    by_name, name_ambiguous = {}, {}
    for r in rows:
        # Der Extrakt trägt die Namen wie geliefert — PRO-Marker ('[IMRO]', '( NS)') hier raus, sonst landen sie
        # in Komponist, Verlag und bei UPM auch im Interpret. Idempotent für schon geputzte Werte.
        composers = clean_writer_names(flip_person_list(r.get("composers", "")))
        publisher = clean_writer_names(r.get("publisher", ""))
        digits = normalize_labelcode(r.get("labelcode", "")) if r.get("labelcode") else ""
        sub = r.get("sublibrary", "") or r.get("library", "")
        note = ""
        if digits:
            gvl = code2name(digits)
            alt = name2codes(sub) if (not gvl and name2codes and sub) else []
            if gvl:
                labelname = gvl
                if sub and gvl.casefold() != sub.casefold():
                    renamed[sub] = gvl
            elif len(alt) == 1:
                # UPM liefert 'BBC Production Music' mit LC21110, der Master führt es unter LC27926 —
                # sonst gäbe die App für denselben Track je nach Weg zwei verschiedene LCs aus.
                note = delivered_note(sub, digits)
                e = corrected.setdefault(digits, {"label": sub, "gvl_code": alt[0], "n": 0})
                e["n"] += 1
                digits, labelname = alt[0], code2name(alt[0]) or sub
            else:
                labelname = sub
                e = unknown.setdefault(digits, {"label": sub, "n": 0})
                if alt:
                    e["gvl_codes"] = alt          # Name unter mehreren Codes: nicht raten, Notiz am manuellen Label
                e["n"] += 1
        else:
            labelname = ""
            given = (r.get("sublibrary") or "").strip()
            alt = name2codes(given) if (name2codes and given) else []
            if len(alt) == 1:
                # UPM liefert z.B. 'Hooked' ohne German Label Code, der Master führt den Namen unter genau
                # einem Code — der gilt, die Notiz macht sichtbar, woher er kommt
                digits, labelname, note = alt[0], code2name(alt[0]) or given, LC_BY_NAME_NOTE
                e = by_name.setdefault(given, {"gvl_code": alt[0], "n": 0})
                e["n"] += 1
            else:
                no_code += 1
                if alt:                           # Name unter mehreren Codes: nicht raten, offen lassen
                    e = name_ambiguous.setdefault(given, {"gvl_codes": alt, "n": 0})
                    e["n"] += 1
        # Lieferungen ohne Interpret (UPM): **der Komponist IST der Interpret** (bei Production Libraries
        # läuft die GEMA-Meldung so). Er bleibt zusätzlich im
        # Komponist-Feld stehen. Nur wenn auch der fehlt, tritt das Label ein (songs braucht Interpret+Titel
        # als Schlüssel).
        interpret = flip_name(r["artist"]) if r.get("artist") else (composers or labelname or sub)
        out.append({
            "file": r["file"], "interpret": interpret, "titel": r["title"],
            "komponist": composers, "texter": "", "verlag": publisher,
            "labelname": labelname, "labelcode": ("LC" + digits) if digits else "",
            "album": r.get("album", "").strip(), "track_number": r.get("track_number", ""),
            "isrc": r.get("isrc", ""), "iswc": r.get("iswc", ""), "gema_werknummer": r.get("gema", ""),
            "duration_sec": r.get("duration_sec", ""), "catno": r.get("catno", ""), "version": r.get("version", ""),
            "sublibrary": r.get("sublibrary", ""), "edit_id": r.get("edit_id", ""), "label_geliefert": note})
    return out, {"rows": len(out), "unknown_codes": unknown, "corrected": corrected, "renamed": renamed,
                 "no_code": no_code, "by_name": by_name, "name_ambiguous": name_ambiguous}


def heal_library_codes(songs_con, master) -> dict:
    """Selbstheilung beim Start, wiederverwendbar für die Bereinigung der Live-DB: Lieferungs-Zeilen, die
    vor der Code-Korrektur eingepflegt wurden (LC21110 an den BBC-Tracks), bekommen den GVL-Code — Datei- und songs-Zeile, der
    gelieferte Code als label_geliefert. Dazu die verbliebenen fremden Codes (Master kennt weder Code noch Namen),
    damit der Aufrufer für sie das manuelle Label anlegt. Nur Lieferungs-Zeilen (library_files, songs mit quelle
    library:*), nur solche, die Code UND Namen noch wie geliefert tragen. Idempotent.
    Rückgabe {corrected: [{from, to, label, files, songs}], unknown_codes: {code: {label, n, library[, gvl_codes]}}}."""
    from .labelcheck import check_label, delivered_note
    pairs: dict[tuple[str, str], tuple[int, str]] = {}
    for code, name, lib, n in songs_con.execute(
            "SELECT labelcode, COALESCE(labelname,''), MIN(library), COUNT(*) FROM library_files "
            "WHERE COALESCE(labelcode,'')<>'' GROUP BY labelcode, labelname"):
        pairs[(code, name)] = (n, lib)
    for code, name, quelle, n in songs_con.execute(
            "SELECT labelcode, COALESCE(labelname,''), MIN(quelle), COUNT(*) FROM songs "
            "WHERE quelle LIKE 'library:%' AND COALESCE(labelcode,'')<>'' GROUP BY labelcode, labelname"):
        pairs.setdefault((code, name), (n, quelle.split(":", 1)[1]))
    corrected, unknown = [], {}
    for (code, name), (n, lib) in sorted(pairs.items()):
        if master.names(code):
            continue                                       # bekannter Code: nichts zu tun (der Name ersetzt ihn nie)
        chk = check_label(code, name, master)
        digits = normalize_labelcode(code)
        if chk["status"] == "corrected":
            new = ("LC" + chk["code"], chk["label"], delivered_note(name, digits))
            sql = ("labelcode=?, labelname=?, label_geliefert=CASE WHEN COALESCE(label_geliefert,'')='' THEN ? "
                   "ELSE label_geliefert END")
            files = songs_con.execute(f"UPDATE library_files SET {sql} WHERE labelcode=? AND COALESCE(labelname,'')=?",
                                      (*new, code, name)).rowcount
            songs = songs_con.execute(f"UPDATE songs SET {sql} WHERE quelle LIKE 'library:%' AND labelcode=? "
                                      "AND COALESCE(labelname,'')=?", (*new, code, name)).rowcount
            corrected.append({"from": digits, "to": chk["code"], "label": chk["label"], "files": files, "songs": songs})
        elif chk["status"] != "unreadable":          # unlesbarer Code: kein manuelles Label unter einem Teil davon
            e = unknown.setdefault(digits, {"label": name, "n": 0, "library": lib})
            e["n"] += n
            if chk["gvl_codes"]:
                e["gvl_codes"] = chk["gvl_codes"]
    if corrected:
        songs_con.commit()
    return {"corrected": corrected, "unknown_codes": unknown}


def fill_codes_by_name(songs_con, master) -> dict:
    """Code über den Labelnamen für schon eingepflegte Lieferungen (Migration beim Start, wiederverwendbar für die
    Bereinigung der Live-DB): Lieferungs-Zeilen ohne Labelcode, deren gelieferter Labelname (library_files.sublibrary) im Master
    unter genau EINEM Code steht, bekommen Code, GVL-Namen und die Notiz LC_BY_NAME_NOTE — Datei-Zeile und, wenn
    dort noch kein Code steht, die songs-Zeile (quelle library:*). Mehrdeutige Namen bleiben offen. Idempotent.
    Rückgabe {filled: [{label, code, files, songs}], ambiguous: {name: {gvl_codes, n}}}."""
    from .labelcheck import LC_BY_NAME_NOTE
    filled, ambiguous = [], {}
    for name, n in songs_con.execute(
            "SELECT sublibrary, COUNT(*) FROM library_files WHERE COALESCE(labelcode,'')='' "
            "AND COALESCE(sublibrary,'')<>'' GROUP BY sublibrary ORDER BY sublibrary").fetchall():
        codes = master.codes_for_name(name)
        if len(codes) != 1:
            if codes:
                ambiguous[name] = {"gvl_codes": codes, "n": n}
            continue
        lc, label = "LC" + codes[0], master(codes[0]) or name
        songs = songs_con.execute(
            "UPDATE songs SET labelcode=?, labelname=?, label_geliefert=CASE WHEN COALESCE(label_geliefert,'')='' "
            "THEN ? ELSE label_geliefert END WHERE quelle LIKE 'library:%' AND COALESCE(labelcode,'')='' AND id IN "
            "(SELECT song_id FROM library_files WHERE COALESCE(labelcode,'')='' AND sublibrary=?)",
            (lc, label, LC_BY_NAME_NOTE, name)).rowcount
        files = songs_con.execute(
            "UPDATE library_files SET labelcode=?, labelname=?, label_geliefert=CASE WHEN COALESCE(label_geliefert,'')='' "
            "THEN ? ELSE label_geliefert END WHERE COALESCE(labelcode,'')='' AND sublibrary=?",
            (lc, label, LC_BY_NAME_NOTE, name)).rowcount
        filled.append({"label": name, "code": codes[0], "files": files, "songs": songs})
    if filled:
        songs_con.commit()
    return {"filled": filled, "ambiguous": ambiguous}


def ensure_imported(songs_con, csv_dir: str | Path, code2name, *, date_verified: str) -> list[dict]:
    """Beim App-Start: jede Lieferungs-CSV unter csv_dir, die noch nicht in library_deliveries steht, einmal
    einpflegen (Selbstheilung wie bei den Zusatz-Labels — andere Installationen bekommen die Daten so
    ohne Handarbeit). Gemerkt wird je Datei: UPM liefert bei jedem Download eine neue CSV, „einmal je
    Library" hätte ab der zweiten nichts mehr übernommen. Rückgabe: Reports je importierter Datei."""
    from . import songsdb
    done = []
    files = sorted(Path(csv_dir).glob("*.csv")) if Path(csv_dir).is_dir() else []
    known = songsdb.imported_deliveries(songs_con)
    if not known:
        # Erstlauf nach dem Umbau auf Datei-Ebene: was die alte Regel („einmal je Library") schon eingepflegt
        # hat, gilt als erledigt — sonst liefe Cezame auf jeder bestehenden Installation ein zweites Mal durch.
        for p in files:
            if songsdb.library_files_count(songs_con, library_name(p)):
                songsdb.mark_delivery(songs_con, p.name, library_name(p), rows=None, date=date_verified)
        known = songsdb.imported_deliveries(songs_con)
    for p in files:
        if p.name in known:
            continue
        name = library_name(p)
        rows, rep = to_song_rows(read_csv(p), code2name)
        res = songsdb.import_library_tracks(songs_con, rows, library=name, date_verified=date_verified)
        songsdb.mark_delivery(songs_con, p.name, name, rows=rep["rows"], date=date_verified)
        done.append({"file": p.name, "library": name, **rep, **res})
    return done
