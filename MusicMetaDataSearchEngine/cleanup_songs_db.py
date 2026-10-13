#!/usr/bin/env python3
"""songs.db bereinigen — Seed und Live-DB.

  python cleanup_songs_db.py <songs.db>                      # PROBELAUF: Bericht, schreibt nichts
  python cleanup_songs_db.py <songs.db> --bericht bericht.txt
  python cleanup_songs_db.py <songs.db> --apply              # schreibt; vorher datierte Sicherung daneben

Regeln in dieser Reihenfolge, jede idempotent (ein zweiter Lauf ändert nichts):
  lc_format           Labelcode als 'LC' + Ziffern, in songs und library_files
  d1_lieferung        Lieferungszeile mit fremdem Code, dessen Name im Master unter genau einem anderen Code steht ->
                      GVL-Code, gelieferter Code als Notiz (librarytracks.heal_library_codes)
  d7_lieferung        Lieferungszeile ohne Code, Name im Master eindeutig -> GVL-Code mit Notiz (fill_codes_by_name)
  gvl_schreibweise    eigener Korpus: Labelname in GVL-Schreibweise zum Code
  labelname_ergaenzt  eigener Korpus: Code ohne Namen bekommt den GVL-Namen (nur bei genau einem)
  code_name_konflikt  eigener Korpus: Name passt nicht zum Code, steht im Master aber EINDEUTIG unter einem anderen
                      -> dieser Code, alter Code als Notiz ('Columbia' LC60459 -> COLUMBIA LC00162)
  review              eigener Korpus: Name mehrdeutig oder nicht im Master, Code nicht im Master, widersprüchliche
                      Dublette -> review_status 'review' (zur Prüfung), Code und Name bleiben
  wiki, pro_marker,   Wiki-Reste ('{{hlist'), PRO-Marker ('[IMRO]', '( NS)') in Interpret/Komponist/
  leerzeichen         Texter/Verlag, mehrfache Leerzeichen in den Textfeldern
  dubletten           Varianten desselben Songs (Doppel-Leerzeichen, Groß/klein) zu EINER Zeile, nichts geht
                      verloren: leere Felder werden aufgefüllt, abweichende Listenwerte zusammengeführt; widersprechen
                      sich Einzelwerte (ISRC, Code …), bleiben beide Zeilen und gehen zur Prüfung
  cue_dauer           Cue-Dauern alter EDL-Importe raus (songsdb.clear_cue_durations)

„Eigener Korpus" = alles ohne quelle library:* — Lieferungen sind verifiziert und laufen nur durch die beiden Lieferungsregeln (Cezame
LC10347 bleibt). Von Hand geprüfte Zeilen (review_status 'verified') fasst keine Labelregel an. Ein Name, den die
Liste der Library selbst unter dem Code führt (data/library_prefixes.csv), ist kein Konflikt ('Cézame' LC10347).

Probelauf und Anwendung rechnen auf einer Kopie im Speicher; --apply schreibt das Ergebnis in einem Schritt zurück,
nachdem die Sicherung '<name>.sicherung-<JJJJ-MM-TT_hhmmss>.db' daneben liegt (Backup-API). Ist die Datei von einem
anderen Prozess offen (laufende App, per lsof — unter Windows nicht prüfbar), bricht --apply ab.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
PROJECT = ENGINE.parent
sys.path.insert(0, str(ENGINE))
from core import librarytracks, songsdb                          # noqa: E402
from core.catalog import _PRO_MARKER, clean_writer_names          # noqa: E402
from core.labelcheck import GvlMaster, delivered_note, names_match   # noqa: E402
from core.labelmatch import norm                                  # noqa: E402
from core.librarytracks import flip_person_list                   # noqa: E402
from core.normalize import clean_text, normalize_labelcode, single_labelcode   # noqa: E402

DEFAULT_MASTER = PROJECT / "data" / "GVL_Labelcodes_synced.csv"
DEFAULT_PREFIXES = PROJECT / "data" / "library_prefixes.csv"

RULES = ("lc_format", "d1_lieferung", "d7_lieferung", "gvl_schreibweise", "labelname_ergaenzt", "code_name_konflikt",
         "review", "wiki", "pro_marker", "leerzeichen", "dubletten", "cue_dauer")
TEXT_FIELDS = ("interpret", "titel", "komponist", "texter", "verlag", "labelname", "album")
PRO_FIELDS = ("interpret", "komponist", "texter", "verlag")
_LIB = "COALESCE(quelle,'') LIKE 'library:%'"

# Gründe im Bericht
R_SPELLING = "GVL-Schreibweise zum Code"
R_FILLED = "Labelname fehlte, GVL-Name zum Code"
R_LIBLIST = "Name laut Liste der Library unter diesem Code, GVL-Schreibweise"
R_CONFLICT = "Name passt nicht zum Code, im Master eindeutig unter einem anderen"
R_SUBLABEL = "Name ist die Dachmarke des Codes (gleicher Hersteller), Code bleibt, GVL-Schreibweise"
R_GVL_WINS = "Name nur in der Zusatzliste, der Code steht im GVL-Master (GVL gewinnt), GVL-Schreibweise"
R_UNKNOWN_FIXED = "Code nicht im Master, Name dort eindeutig"
R_D1 = "Lieferung: Code nicht im Master, Name dort eindeutig"
R_D7 = "Lieferung ohne Code, Name im Master eindeutig"
R_UNKNOWN = "Code nicht im GVL-Master"
R_UNREADABLE = "Labelcode nicht als genau ein Code lesbar"


def load_master(path: str | Path = DEFAULT_MASTER) -> GvlMaster:
    """GVL-Master + Zusatzliste (wie labels.db source 'gvl', ohne manuelle Labels)."""
    from reconcile.csv_io import read_master_with_supplemental
    return GvlMaster([(r.labelcode, r.label) for r in read_master_with_supplemental(path)])


def load_hersteller(path: str | Path = DEFAULT_MASTER) -> dict[str, str]:
    """Code -> Hersteller, nur GVL-Master (ohne Zusatzliste). Für die Konfliktregel: gleicher Hersteller heißt
    Dachmarke und Sub-Label (KPM Music über KPM Shorts), und ein Code, der hier fehlt, stammt aus der Zusatzliste."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = normalize_labelcode(r.get("labelcode") or "")
            if code:
                out.setdefault(code, (r.get("hersteller") or "").strip())
    return out


def load_library_names(path: str | Path = DEFAULT_PREFIXES) -> dict[str, set[str]]:
    """Code -> normierte Namen, unter denen die Listen der Libraries selbst ihn führen (Library + Listen-Label)."""
    out: dict[str, set[str]] = {}
    if Path(path).is_file():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                code = normalize_labelcode(r.get("labelcode") or "")
                names = {norm(r.get(k) or "") for k in ("library", "list_label")} - {""}
                if code and names:
                    out.setdefault(code, set()).update(names)
    return out


# --- Textregeln -------------------------------------------------------------------------------------------------

_BARE_SHARE = re.compile(r"\s*\d{1,3}(?:[.,]\d+)?\s*%")
_WIKI_LIST = re.compile(r"\{\{\s*(?:hlist|flatlist|plainlist|ubl)\s*\|([^{}]*)\}\}", re.IGNORECASE)
_WIKI_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_WIKI_LINK = re.compile(r"\[\[(?:[^\[\]|]*\|)?([^\[\]|]*)\]\]")
_WIKI_REST = re.compile(r"\{\{[^{}|]*\|?|\}\}|\[\[|\]\]")


def clean_wiki(value: str) -> str:
    """Reste aus Wiki-Quelltext: '{{hlist|A|B}}' -> 'A; B', '[[Ziel|Text]]' -> 'Text', angebrochene Vorlage
    ('{{hlist') -> weg. Ohne geschweifte/eckige Doppelklammer unverändert."""
    if not any(t in value for t in ("{{", "}}", "[[", "]]")):
        return value
    v = _WIKI_LIST.sub(lambda m: "; ".join(p.strip() for p in m.group(1).split("|") if p.strip()), value)
    v = _WIKI_LINK.sub(r"\1", _WIKI_TEMPLATE.sub("", v))
    return clean_text(_WIKI_REST.sub("", v)).strip(" ;,|")


def clean_pro(field: str, value: str) -> str:
    """PRO-Marker, IPI und Anteile raus wie beim Import (catalog.clean_writer_names, Personen vorher gedreht) — nur,
    wenn wirklich ein Marker drinsteht: ein nackter Anteil allein ('100% HER') ist kein Marker."""
    if not any(not _BARE_SHARE.fullmatch(m.group(0)) for m in _PRO_MARKER.finditer(value)):
        return value
    return clean_writer_names(value if field == "verlag" else flip_person_list(value))


def _text_changes(row: dict) -> tuple[dict, list]:
    """Neue Werte der Textfelder einer Zeile + Liste (regel, feld, alt, neu) je Schritt."""
    new, steps = {}, []
    for f in TEXT_FIELDS:
        cur = row.get(f)
        if not cur:
            continue
        for rule, fn in (("wiki", clean_wiki), ("pro_marker", lambda v, f=f: clean_pro(f, v) if f in PRO_FIELDS else v),
                         ("leerzeichen", clean_text)):
            nv = fn(cur)
            if nv != cur:
                steps.append((rule, f, cur, nv))
                cur = nv
        if cur != row[f]:
            new[f] = cur
    if not new.get("interpret", row["interpret"]) or not new.get("titel", row["titel"]):
        # Schlüssel darf nicht leer werden (songs.interpret/titel NOT NULL): dann bleibt er wie er ist
        for f in ("interpret", "titel"):
            new.pop(f, None)
        steps = [s for s in steps if s[1] not in ("interpret", "titel")]
    return new, steps


# --- Dubletten --------------------------------------------------------------------------------------------------

_JOIN_FIELDS = ("komponist", "texter", "verlag", "quelle", "album", "track_number", "sublibraries")
_MIN_FIELDS = ("date_added",)
_MAX_FIELDS = ("date_verified", "confidence_score", "possible_cover", "gema_lookup_pending")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _merge(group: list[dict], cols: list[str]) -> tuple[dict, list[str]]:
    """Zeilen desselben Songs zu einer (die erste ist die bleibende). Rückgabe (Werte, widersprüchliche Felder)."""
    out, conflicts = dict(group[0]), []
    for c in cols:
        if c in ("id", "nkey", "interpret", "titel"):
            continue
        vals = [r[c] for r in group if not _blank(r[c])]
        if not vals:
            continue
        if c in _JOIN_FIELDS:
            parts = []
            for v in vals:
                parts += [p for p in (x.strip() for x in str(v).split(";")) if p and p not in parts]
            out[c] = "; ".join(parts)
        elif c in _MIN_FIELDS:
            out[c] = min(vals)
        elif c in _MAX_FIELDS:
            out[c] = max(vals)
        elif c == "review_status":
            out[c] = "review" if "review" in vals else vals[0]
        elif len(set(vals)) > 1:
            conflicts.append(c)
        else:
            out[c] = vals[0]
    return out, conflicts


def _filled(r: dict) -> int:
    return sum(not _blank(v) for k, v in r.items() if k not in ("id", "nkey"))


def _text_and_dupes(con: sqlite3.Connection, rep: dict) -> None:
    cols = [r[1] for r in con.execute("PRAGMA table_info(songs)")]
    rows = [dict(r) for r in con.execute("SELECT * FROM songs ORDER BY id")]
    planned = {}
    for r in rows:
        new, steps = _text_changes(r)
        planned[r["id"]] = (new, steps)
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        new = planned[r["id"]][0]
        i, t = new.get("interpret", r["interpret"]), new.get("titel", r["titel"])
        groups.setdefault((songsdb._nkey(i, t), songsdb._skey(i), songsdb._skey(t)), []).append({**r, **new})
    deletes, updates = [], {}
    for g in groups.values():
        if len(g) == 1:
            continue
        g.sort(key=lambda r: (-_filled(r), r["id"]))
        merged, conflicts = _merge(g, cols)
        if conflicts:
            # Einzelwerte widersprechen sich — zusammenführen hieße einen verlieren: beide bleiben, zur Prüfung.
            # Der Schlüssel bleibt wie er war, sonst kollidierten die Zeilen in UNIQUE(interpret, titel).
            rep["merge_conflicts"].append({"rows": [(r["id"], r["interpret"], r["titel"]) for r in g],
                                           "fields": conflicts,
                                           "values": {c: [r[c] for r in g] for c in conflicts}})
            for r in g:
                new, steps = planned[r["id"]]
                for f in ("interpret", "titel"):
                    new.pop(f, None)
                planned[r["id"]] = (new, [s for s in steps if s[1] not in ("interpret", "titel")])
                if r["review_status"] != "verified" and r["review_status"] != "review":
                    updates.setdefault(r["id"], {})["review_status"] = "review"
                    _review(rep, r, "Dublette mit widersprüchlichen Werten: " + ", ".join(conflicts))
            continue
        keep = g[0]
        diff = {c: merged[c] for c in cols if c not in ("id", "nkey") and merged[c] != keep[c]}
        updates.setdefault(keep["id"], {}).update(diff)
        drop = [r["id"] for r in g[1:]]
        deletes += drop
        rep["merges"].append({"kept": (keep["id"], keep["interpret"], keep["titel"]),
                              "removed": [(r["id"], r["interpret"], r["titel"]) for r in g[1:]],
                              "joined": {c: merged[c] for c in _JOIN_FIELDS if c in diff}})
        for sid in drop:
            con.execute("UPDATE library_files SET song_id=? WHERE song_id=?", (keep["id"], sid))
    for sid in deletes:
        con.execute("DELETE FROM songs WHERE id=?", (sid,))
    rep["rules"]["dubletten"] += len(deletes)
    gone = set(deletes)
    for r in rows:
        if r["id"] in gone:
            continue
        new, steps = planned[r["id"]]
        for rule, f, old, nv in steps:
            rep["rules"][rule] += 1
            rep["_text"][(rule, f, old, nv)] += 1
        upd = {**new, **updates.get(r["id"], {})}
        if not upd:
            continue
        if "interpret" in upd or "titel" in upd:
            upd["nkey"] = songsdb._nkey(upd.get("interpret", r["interpret"]), upd.get("titel", r["titel"]))
        con.execute(f"UPDATE songs SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), r["id"]))


# --- Labelregeln ------------------------------------------------------------------------------------------------

def _review(rep: dict, r: dict, reason: str) -> None:
    rep["rules"]["review"] += 1
    rep["review_rows"].append({"id": r["id"], "interpret": clean_text(r["interpret"]), "titel": clean_text(r["titel"]),
                               "labelcode": r["labelcode"] or "", "labelname": r["labelname"] or "", "reason": reason})


def _decide(r, master: GvlMaster, lib_names: dict, hersteller: dict | None = None) -> tuple[str, str, str, str] | None:
    """Was die Labelregeln aus einer Zeile des eigenen Korpus machen: (regel, neuer Code, neuer Name, Grund) oder None.
    regel 'review': Code/Name bleiben, Grund im Bericht."""
    code, name = r["labelcode"] or "", clean_text(r["labelname"])
    digits = single_labelcode(code)
    if not digits:
        return ("review", code, name, R_UNREADABLE)
    gvl = master.names(digits)
    if not gvl:
        alt = master.codes_for_name(name) if name else []
        if len(alt) == 1:
            return ("code_name_konflikt", alt[0], _gvl_name(master, alt[0], name), R_UNKNOWN_FIXED)
        return ("review", code, name, R_UNKNOWN)
    if not name:
        return ("labelname_ergaenzt", digits, gvl[0], R_FILLED) if len(set(gvl)) == 1 else None
    hit = (next((g for g in gvl if norm(g) == norm(name)), None)
           or next((g for g in gvl if names_match(name, [g], digits)), None))
    if hit:
        return ("gvl_schreibweise", digits, hit, R_SPELLING) if hit != r["labelname"] else None
    if norm(name) in lib_names.get(digits, ()):
        return ("gvl_schreibweise", digits, gvl[0], R_LIBLIST) if gvl[0] != r["labelname"] else None
    alt = master.codes_for_name(name)
    if len(alt) == 1:
        if hersteller is not None:
            # Der Name allein ist nicht immer das Genauere
            if digits in hersteller and alt[0] not in hersteller:      # Name nur in der Zusatzliste: GVL gewinnt
                return ("gvl_schreibweise", digits, gvl[0], R_GVL_WINS) if gvl[0] != r["labelname"] else None
            h = hersteller.get(digits, "").casefold()
            if h and h == hersteller.get(alt[0], "").casefold():          # Dachmarke über Sub-Label: Code bleibt
                return ("gvl_schreibweise", digits, gvl[0], R_SUBLABEL) if gvl[0] != r["labelname"] else None
        return ("code_name_konflikt", alt[0], _gvl_name(master, alt[0], name), R_CONFLICT)
    where = ("im Master unter mehreren Codes: " + ", ".join("LC" + c for c in alt)) if alt else "steht nicht im Master"
    return ("review", code, name, f"Name passt nicht zum Code ({' / '.join(gvl)}), {where}")


def _gvl_name(master: GvlMaster, code: str, name: str) -> str:
    names = master.names(code)
    return next((n for n in names if norm(n) == norm(name)), names[0] if names else name)


def _label_rules(con: sqlite3.Connection, master: GvlMaster, lib_names: dict, rep: dict,
                 hersteller: dict | None = None) -> None:
    rows = con.execute(f"SELECT id, interpret, titel, labelname, labelcode, review_status FROM songs "
                       f"WHERE NOT {_LIB} AND COALESCE(labelcode,'')<>'' ORDER BY id").fetchall()
    for r in rows:
        d = _decide(r, master, lib_names, hersteller)
        if d is None:
            continue
        rule, code, name, reason = d
        if r["review_status"] == "verified":
            # von Hand geprüft: nichts anfassen, nur im Bericht nennen
            rep["skipped_verified"] += 1
            rep["verified_rows"].append({"interpret": r["interpret"], "titel": r["titel"], "labelcode": r["labelcode"],
                                         "labelname": r["labelname"] or "", "reason": reason})
            continue
        if rule == "review":
            if r["review_status"] != "review":
                con.execute("UPDATE songs SET review_status='review' WHERE id=?", (r["id"],))
                _review(rep, r, reason)
            continue
        new_code = "LC" + code
        if rule == "code_name_konflikt":
            con.execute("UPDATE songs SET labelcode=?, labelname=?, label_geliefert=CASE WHEN COALESCE(label_geliefert,'')='' "
                        "THEN ? ELSE label_geliefert END WHERE id=?",
                        (new_code, name, delivered_note(r["labelname"] or "", r["labelcode"]), r["id"]))
        else:
            con.execute("UPDATE songs SET labelname=? WHERE id=?", (name, r["id"]))
        rep["rules"][rule] += 1
        rep["_codes"][(r["labelcode"], r["labelname"] or "", new_code, name, reason)] += 1


def _library_rules(con: sqlite3.Connection, master: GvlMaster, rep: dict) -> None:
    heal = librarytracks.heal_library_codes(con, master)
    for c in heal["corrected"]:
        rep["rules"]["d1_lieferung"] += c["files"] + c["songs"]
        rep["_codes"][("LC" + c["from"], c["label"], "LC" + c["to"], c["label"], R_D1)] += c["files"] + c["songs"]
    rep["library_unknown_codes"] = heal["unknown_codes"]
    fill = librarytracks.fill_codes_by_name(con, master)
    for c in fill["filled"]:
        rep["rules"]["d7_lieferung"] += c["files"] + c["songs"]
        rep["_codes"][("", c["label"], "LC" + c["code"], master(c["code"]) or c["label"], R_D7)] += c["files"] + c["songs"]
    rep["d7_ambiguous"] = fill["ambiguous"]


def _lc_format(con: sqlite3.Connection, rep: dict) -> None:
    seen = Counter()
    for table in ("songs", "library_files"):
        for (v,) in con.execute(f"SELECT labelcode FROM {table} WHERE COALESCE(labelcode,'')<>''"):
            if songsdb._lc_store(v) != v:
                seen[(v, songsdb._lc_store(v))] += 1
    n = songsdb.normalize_labelcodes(con)
    rep["rules"]["lc_format"] += sum(n.values())
    rep["lc_changes"] = sorted((a, b, k) for (a, b), k in seen.items())


# --- Ablauf -----------------------------------------------------------------------------------------------------

def clean(con: sqlite3.Connection, master: GvlMaster, *, lib_names: dict | None = None,
          hersteller: dict | None = None) -> dict:
    """Alle Regeln auf eine offene songs.db anwenden (committet). Rückgabe: Bericht (rules = Zählung je Regel).
    hersteller (load_hersteller) schaltet die Verfeinerung der Konfliktregel ein: Dachmarke und GVL gewinnt."""
    con.row_factory = sqlite3.Row
    songsdb.ensure_schema(con)
    lib_names = load_library_names() if lib_names is None else lib_names
    rep = {"rules": dict.fromkeys(RULES, 0), "rows_before": songsdb.count(con), "review_rows": [], "merges": [],
           "merge_conflicts": [], "verified_rows": [], "skipped_verified": 0, "lc_changes": [],
           "_codes": Counter(), "_text": Counter()}
    _lc_format(con, rep)
    _library_rules(con, master, rep)
    _label_rules(con, master, lib_names, rep, hersteller)
    _text_and_dupes(con, rep)
    con.commit()
    rep["rules"]["cue_dauer"] = songsdb.clear_cue_durations(con)
    rep["rows_after"] = songsdb.count(con)
    rep["code_changes"] = sorted((*k, n) for k, n in rep.pop("_codes").items())
    rep["text_changes"] = sorted((*k, n) for k, n in rep.pop("_text").items())
    rep["changed"] = sum(rep["rules"].values()) > 0
    if rep["changed"] and songsdb.has_fts(con):
        # Tausende Einzel-Updates zerstückeln den Trigram-Index der Suche (Seed: 1,3 -> 3,6 MB) — zusammenlegen
        con.execute("INSERT INTO songs_fts(songs_fts) VALUES('optimize')")
        con.commit()
    return rep


def open_by_others(path: str | Path) -> list[int] | None:
    """PIDs anderer Prozesse, die die Datei (oder ihr -wal/-journal) offen haben — per lsof (macOS, Linux).
    None: hier nicht prüfbar (kein lsof, Windows)."""
    lsof = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if Path(p).exists()), None)
    if not lsof:
        return None
    files = [str(p) for p in (Path(path), Path(f"{path}-wal"), Path(f"{path}-journal")) if p.exists()]
    if not files:
        return []
    res = subprocess.run([lsof, "-t", "--", *files], capture_output=True, text=True)
    return sorted({int(x) for x in res.stdout.split() if x.isdigit()} - {os.getpid()})


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def backup(path: Path, stamp: str) -> Path:
    """Datierte Sicherung daneben ('songs.sicherung-<stamp>.db') über die Backup-API. Nie überschreiben."""
    dst = path.with_name(f"{path.stem}.sicherung-{stamp}{path.suffix}")
    if dst.exists():
        raise SystemExit(f"Sicherung {dst} gibt es schon — nichts geschrieben")
    src, out = _ro(path), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def run(path: str | Path, *, apply: bool = False, master: GvlMaster | None = None, lib_names: dict | None = None,
        stamp: str | None = None, hersteller: dict | None = None) -> dict:
    """Probelauf (Standard) oder Anwendung. Gerechnet wird auf einer Kopie im Speicher; mit apply und nur, wenn eine
    Regel etwas ändert: Sicherung daneben, dann das Ergebnis in einem Schritt zurück in die Datei."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"Keine Datenbank unter {path}")
    checked = None
    if apply:
        others = open_by_others(path)
        checked = others is not None
        if others:
            raise SystemExit(f"{path} ist von einem anderen Prozess offen (PID {', '.join(map(str, others))}) — "
                             "CueCore beenden, dann erneut starten. Nichts geschrieben.")
    mem = sqlite3.connect(":memory:")
    try:
        src = _ro(path)
        try:
            src.backup(mem)
        finally:
            src.close()
        if master is None:                       # echter Lauf: Master und Hersteller aus der GVL-CSV
            master, hersteller = load_master(), load_hersteller() if hersteller is None else hersteller
        rep = clean(mem, master, lib_names=lib_names, hersteller=hersteller)
        rep.update(path=str(path), apply=apply, lsof=checked)
        if apply and rep["changed"]:
            rep["backup"] = str(backup(path, stamp or datetime.now().strftime("%Y-%m-%d_%H%M%S")))
            dst = sqlite3.connect(path)
            try:
                mem.backup(dst)
            finally:
                dst.close()
    finally:
        mem.close()
    return rep


_RULE_TEXT = {
    "lc_format": "Labelcode als LC + Ziffern, Track- und Dateizeilen",
    "d1_lieferung": "Lieferung: fremder Code -> GVL-Code, Track- und Dateizeilen",
    "d7_lieferung": "Lieferung ohne Code -> GVL-Code über den Namen, Track- und Dateizeilen",
    "gvl_schreibweise": "Labelname in GVL-Schreibweise", "labelname_ergaenzt": "Labelname zum Code ergänzt",
    "code_name_konflikt": "Code-Name-Konflikt -> GVL-Code zum Namen", "review": "zur Prüfung markiert",
    "wiki": "Wiki-Reste entfernt", "pro_marker": "PRO-Marker entfernt", "leerzeichen": "Leerzeichen bereinigt",
    "dubletten": "Dubletten zusammengeführt (entfernte Zeilen)", "cue_dauer": "Cue-Dauer geleert (edl_import)"}


def _n(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def format_report(rep: dict) -> str:
    def lc(v):
        return v or "ohne LC"
    mode = (f"angewendet, Sicherung: {rep['backup']}" if rep.get("backup") else
            "angewendet, nichts zu tun" if rep.get("apply") else "Probelauf, schreibt nichts")
    out = [f"Bereinigung {rep.get('path', '')} ({mode})",
           f"Zeilen: vorher {_n(rep['rows_before'])}, nachher {_n(rep['rows_after'])}", "", "Je Regel:"]
    out += [f"  {rep['rules'][k]:>6}  {k:<19} {_RULE_TEXT[k]}" for k in RULES]
    if rep.get("apply") and rep.get("lsof") is False:
        out.append("Hinweis: ob die Datei offen ist, ließ sich hier nicht prüfen (kein lsof) — CueCore vorher beenden.")
    if rep["lc_changes"]:
        out += ["", "Labelcode-Schreibweise (alt -> neu, Zeilen):"]
        out += [f"  {a} -> {b} ({n})" for a, b, n in rep["lc_changes"]]
    if rep["code_changes"]:
        out += ["", "Code-/Namensänderungen (alt -> neu: Grund, Zeilen):"]
        out += [f"  {lc(oc)} {on or '(leer)'} -> {nc} {nn}: {why} ({n})" for oc, on, nc, nn, why, n in rep["code_changes"]]
    if rep["review_rows"]:
        out += ["", f"Zur Prüfung markiert (review_status 'review'): {len(rep['review_rows'])} Zeilen"]
        out += [f"  {r['interpret']} - {r['titel']} | {lc(r['labelcode'])} {r['labelname']} | {r['reason']}"
                for r in rep["review_rows"]]
    if rep["verified_rows"]:
        out += ["", f"Von Hand verifiziert, nicht angefasst, obwohl eine Regel greifen würde: {len(rep['verified_rows'])}"]
        out += [f"  {r['interpret']} - {r['titel']} | {lc(r['labelcode'])} {r['labelname']} | {r['reason']}"
                for r in rep["verified_rows"]]
    if rep["merges"]:
        out += ["", f"Dubletten zusammengeführt: {len(rep['merges'])}"]
        for m in rep["merges"]:
            joined = "; ".join(f"{k}={v}" for k, v in m["joined"].items())
            out.append(f"  bleibt #{m['kept'][0]} {m['kept'][1]} - {m['kept'][2]}, entfernt "
                       + ", ".join(f"#{i} {a} - {t}" for i, a, t in m["removed"]) + (f" (zusammengeführt: {joined})" if joined else ""))
    if rep["merge_conflicts"]:
        out += ["", f"Dubletten mit Widerspruch, nicht zusammengeführt (zur Prüfung): {len(rep['merge_conflicts'])}"]
        out += [f"  " + ", ".join(f"#{i} {a} - {t}" for i, a, t in m["rows"]) + " | "
                + "; ".join(f"{c}: {' / '.join(map(str, v))}" for c, v in m["values"].items()) for m in rep["merge_conflicts"]]
    marked = [c for c in rep["text_changes"] if c[0] != "leerzeichen"]
    if marked:
        out += ["", "Textänderungen Wiki-Reste und PRO-Marker (Regel, Feld: alt -> neu, Zeilen):"]
        out += [f"  {rule}, {f}: {old} -> {new or '(leer)'} ({n})" for rule, f, old, new, n in marked]
    spaces = [c for c in rep["text_changes"] if c[0] == "leerzeichen"]
    if spaces:
        out += ["", f"Leerzeichen: {sum(c[4] for c in spaces)} Felder, Beispiele:"]
        out += [f"  {f}: {old!r} -> {new!r}" for _, f, old, new, _n in spaces[:10]]
    if rep.get("library_unknown_codes"):
        out += ["", "Lieferungen: Codes, die der Master weder als Code noch über den Namen kennt (bleiben wie geliefert, "
                    "das manuelle Label legt die App beim Start an):"]
        out += [f"  LC{c} {i['label']} ({i['n']}, {i.get('library', '')})" for c, i in sorted(rep["library_unknown_codes"].items())]
    if rep.get("d7_ambiguous"):
        out += ["", "Lieferungen ohne Code, Name im Master mehrdeutig (offen gelassen):"]
        out += [f"  {k}: {', '.join('LC' + c for c in v['gvl_codes'])} ({v['n']})" for k, v in sorted(rep["d7_ambiguous"].items())]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", help="songs.db (Seed oder Live-DB)")
    ap.add_argument("--apply", action="store_true", help="schreiben (vorher datierte Sicherung daneben)")
    ap.add_argument("--master", default=str(DEFAULT_MASTER), help="GVL-Master-CSV (Zusatzliste daneben wird mitgelesen)")
    ap.add_argument("--bericht", default=None, help="Bericht zusätzlich in diese Datei schreiben")
    a = ap.parse_args(argv)
    rep = run(Path(a.db).expanduser(), apply=a.apply, master=load_master(a.master), hersteller=load_hersteller(a.master))
    text = format_report(rep)
    print(text, end="")
    if a.bericht:
        Path(a.bericht).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
