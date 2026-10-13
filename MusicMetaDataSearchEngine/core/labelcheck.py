"""Labelcode und Labelname aus einer fremden Quelle gegen den GVL-Master prüfen.

Zwei Regeln:
  * Library-Lieferungen: kennt der Master den gelieferten Code nicht, steht der gelieferte Labelname dort
    aber unter genau EINEM anderen Code, gilt der GVL-Code. Der gelieferte bleibt als Notiz an der Zeile
    (Spalte `label_geliefert`, nie still verwerfen). So liefert UPM BBC Production Music mit LC21110 (GVL:
    LC27926) und Nova Production Music mit LC26766 (GVL: LC86328). Kennt der Master weder Code noch Namen, bleibt
    der Code wie geliefert und bekommt ein manuelles Label mit Notiz. Einen Code, den der Master
    kennt, ersetzt der Name nie: 'Cézame' steht im Master unter LC04952 CEZAME, Cezame selbst nennt aber LC10347
    (s. core/catalog.py).
  * Listen aus fremden Quellen (generischer Import): passen Code und Name nicht zusammen oder kennt
    der Master den Code nicht, wird die Zeile zur Prüfung markiert (review_status 'review'), statt im Cue-Sheet
    als verifiziert zu gelten.

„Master" heißt hier GVL-Master plus Zusatzliste (labels.db, source 'gvl'), OHNE manuelle Labels — sonst gälte
ein früher automatisch angelegtes LC21110 als bekannt, und die Korrektur griffe nie.

  * Lieferungszeilen ohne Labelcode: steht der gelieferte Labelname im Master unter genau EINEM Code, gilt
    dieser, mit sichtbarer Notiz LC_BY_NAME_NOTE. Mehrdeutige Namen bleiben offen.

Wiederverwendbar für die spätere Bereinigung von Seed und Live-DB: check_label, GvlMaster,
librarytracks.heal_library_codes, librarytracks.fill_codes_by_name.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

from .labelmatch import ALIASES, norm, strip_generic
from .normalize import normalize_labelcode, strict_labelcode

# Notizen automatisch angelegter Labels beginnen so.
AUTO_NOTE_PREFIX = "Aus Library-Lieferung"
# Sichtbare Notiz (Spalte label_geliefert) an einer Lieferungszeile OHNE Labelcode, deren Labelname im Master unter
# genau EINEM Code steht und die deshalb diesen Code bekommen hat. Fester
# Text, die Oberfläche übersetzt ihn; s. librarytracks.to_song_rows und fill_codes_by_name.
LC_BY_NAME_NOTE = "LC aus dem GVL-Master über den Labelnamen"


class GvlMaster:
    """Code -> GVL-Namen und Name -> Codes, nur GVL-Master + Zusatzliste. Aufrufbar wie code2name
    (erster GVL-Name oder None), damit die bestehenden Lieferungs-Funktionen es direkt nehmen."""

    def __init__(self, rows):
        self.code2names: dict[str, list[str]] = {}
        for code, label in rows:
            code, label = normalize_labelcode(str(code)), (label or "").strip()
            if code and label:
                self.code2names.setdefault(code, []).append(label)
        self._name2codes: dict[str, set[str]] | None = None     # erst bei Bedarf (norm über 85.000 Namen: 0,25 s)

    @classmethod
    def from_db(cls, con) -> "GvlMaster":
        from .labeldb import ensure_source_column
        ensure_source_column(con)
        return cls(con.execute("SELECT labelcode, label FROM labels WHERE source='gvl'").fetchall())

    def __call__(self, code: str) -> str | None:
        names = self.names(code)
        return names[0] if names else None

    def names(self, code: str) -> list[str]:
        return self.code2names.get(normalize_labelcode(code or ""), [])

    def codes_for_name(self, name: str) -> list[str]:
        """GVL-Codes, deren Name dem gegebenen entspricht (norm-gleich: Groß/klein, Akzente, &/and, Satzzeichen)."""
        if self._name2codes is None:
            self._name2codes = {}
            for code, names in self.code2names.items():
                for n in names:
                    self._name2codes.setdefault(norm(n), set()).add(code)
        return sorted(self._name2codes.get(norm(name or ""), ()))


def names_match(name: str, gvl_names: list[str], code: str = "") -> bool:
    """Bezeichnet `name` dasselbe Label wie einer der GVL-Namen des Codes? Großzügig bei Schreibweise und
    Zusätzen ('Bruton' = 'BRUTON MUSIC', 'Focus Music' = 'FOCUS MUSIC LIBRARY', 'MasterSource' = 'Master
    Source'), streng beim Inhalt ('Atlantic' ist nicht 'MOM & POP', 'MYMA' nicht 'Stereoscopic')."""
    n = norm(name)
    if not n:
        return True
    if code and ALIASES.get(n) == code:
        return True
    for g in gvl_names:
        gn = norm(g)
        if n == gn or n.replace(" ", "") == gn.replace(" ", ""):
            return True
        if strip_generic(n) and strip_generic(n) == strip_generic(gn):
            return True
        short, long_ = sorted((n, gn), key=len)
        if len(short) >= 3 and long_.startswith(short + " "):
            return True
    return False


def check_label(code: str, name: str, master: GvlMaster) -> dict:
    """Ein gelieferter Code + Name gegen den Master. Rückgabe {status, code, label, gvl_codes}:
      ok         Code im Master, Name leer oder passend   -> label in GVL-Schreibweise
      renamed    Code im Master, Name passt nicht          -> label = GVL-Name des Codes
      corrected  Code nicht im Master, Name dort unter genau einem Code -> code + label aus dem Master
      ambiguous  Code nicht im Master, Name dort unter mehreren Codes   -> wie geliefert, gvl_codes zur Prüfung
      unknown    Code nicht im Master, Name auch nicht     -> wie geliefert
      no_code    kein Code
      unreadable nicht genau ein sauber geschriebener Code ('LC00121 / LC00392', 'LC 24-584') -> wie geliefert,
                 zur Prüfung (sonst gälte still der erste Ziffernblock)
    `code` ohne 'LC', mit führenden Nullen. Was daraus folgt (übernehmen, korrigieren, prüfen lassen), entscheidet
    der Aufrufer: Lieferungen ersetzen den Code, Listen markieren die Zeile zur Prüfung."""
    digits = strict_labelcode(code or "")
    name = (name or "").strip()
    if digits is None:
        return {"status": "unreadable", "code": "", "label": name, "gvl_codes": []}
    if not digits:
        return {"status": "no_code", "code": "", "label": name, "gvl_codes": []}
    gvl = master.names(digits)
    if gvl:
        hit = next((g for g in gvl if norm(g) == norm(name)), None) or (gvl[0] if not name else None)
        if hit or names_match(name, gvl, digits):
            return {"status": "ok", "code": digits, "label": hit or gvl[0], "gvl_codes": [digits]}
        return {"status": "renamed", "code": digits, "label": gvl[0], "gvl_codes": [digits]}
    alt = master.codes_for_name(name) if name else []
    if len(alt) == 1:
        return {"status": "corrected", "code": alt[0], "label": master(alt[0]) or name, "gvl_codes": alt}
    return {"status": "ambiguous" if alt else "unknown", "code": digits, "label": name, "gvl_codes": alt}


def delivered_note(name: str, code: str) -> str:
    """Wert für `label_geliefert`: was die Quelle geliefert hat, sprachneutral ('BBC Production Music · LC21110')."""
    return " · ".join(x for x in ((name or "").strip(), "LC" + normalize_labelcode(code)) if x)


def manual_label_entries(unknown: dict, *, library: str, source: str, stamp: str) -> list[dict]:
    """unknown_codes eines Lieferungs-Imports -> Einträge für labels_manual.json. Fremde Codes
    werden als manuelles Label mit Notiz angelegt, nie still übernommen, nie weggelassen."""
    out = []
    for code, info in sorted(unknown.items()):
        note = (f"{AUTO_NOTE_PREFIX} {library} ({source}), {info['n']} Tracks; nicht im GVL-Master beim Import am "
                f"{stamp} — beim nächsten GVL-PDF-Update prüfen")
        if info.get("gvl_codes"):
            note += "; der Name steht im Master unter " + ", ".join("LC" + c for c in info["gvl_codes"])
        out.append({"labelcode": code, "label": info["label"], "hersteller": "", "note": note, "created": stamp})
    return out


def _read_manual(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _same(a: dict, b: dict) -> bool:
    return (normalize_labelcode(re.sub(r"\D", "", str(a.get("labelcode", "")))) ==
            normalize_labelcode(re.sub(r"\D", "", str(b.get("labelcode", ""))))
            and norm(a.get("label") or "") == norm(b.get("label") or ""))


def _write_manual(path: Path, rows: list[dict]) -> None:
    # Format wie app._save_manual_labels: nach Code und Name sortiert
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(rows, key=lambda r: (str(r.get("labelcode", "")), str(r.get("label", "")).lower())),
                               ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def add_manual_labels(lcon, manual_path: str | Path, entries: list[dict], *, before_write=None) -> list[dict]:
    """Einträge in labels_manual.json (ohne Dubletten Code + Name) UND in labels.db (source 'manuell').
    Idempotent. Rückgabe: die neu in die JSON geschriebenen Einträge. before_write() läuft vor dem ersten
    Schreiben in eine der beiden Dateien, nur wenn wirklich etwas dazukommt (Sicherung, backup_label_files)."""
    from . import labeldb
    path = Path(manual_path)
    rows = _read_manual(path)
    added = [e for e in entries if e.get("label") and not any(_same(e, r) for r in rows)]
    if added:
        if before_write:
            before_write()
        _write_manual(path, rows + added)
    if lcon is not None and entries:
        labeldb.add_manual_labels(lcon, entries, before_write=before_write)
    return added


def obsolete_delivery_labels(entries: list[dict], master: GvlMaster) -> list[dict]:
    """Automatisch angelegte Labels (Notiz „Aus Library-Lieferung …“), die überholt sind: der Master
    kennt den Code nicht, den Namen aber unter genau einem anderen Code. Früher legte der Import für
    BBC Production Music LC21110 und Nova Production Music LC26766 so ein Label an; es stand in der Label-Suche
    vor dem GVL-Code. Von Hand angelegte Labels (andere Notiz) bleiben immer."""
    out = []
    for e in entries:
        if not str(e.get("note") or "").startswith(AUTO_NOTE_PREFIX):
            continue
        if check_label(str(e.get("labelcode", "")), str(e.get("label") or ""), master)["status"] == "corrected":
            out.append(e)
    return out


def db_only_manual_labels(lcon, entries: list[dict]) -> list[dict]:
    """Manuelle Zeilen in labels.db ohne Eintrag in labels_manual.json. Die App legt jedes Label in beiden an —
    so etwas kommt nur aus der mitgelieferten labels_prebuilt.db (Build-Import) und gilt deshalb als automatisch
    angelegt (Notiz AUTO_NOTE_PREFIX), damit obsolete_delivery_labels es mitprüft."""
    from .labeldb import ensure_source_column
    ensure_source_column(lcon)
    out = []
    for code, label in lcon.execute("SELECT labelcode, label FROM labels WHERE source='manuell'").fetchall():
        e = {"labelcode": code, "label": label, "note": f"{AUTO_NOTE_PREFIX} (nur in labels.db)"}
        if not any(_same(e, r) for r in entries):
            out.append(e)
    return out


def remove_manual_labels(lcon, manual_path: str | Path, entries: list[dict], *, before_write=None) -> int:
    """Gegenstück zu add_manual_labels: aus labels_manual.json und labels.db entfernen. Rückgabe: Anzahl.
    before_write() wie dort: vor dem ersten Schreiben, nur wenn etwas wegfällt."""
    from . import labeldb
    if not entries:
        return 0
    path = Path(manual_path)
    rows = _read_manual(path)
    keep = [r for r in rows if not any(_same(r, e) for e in entries)]
    if len(keep) != len(rows):
        if before_write:
            before_write()
        _write_manual(path, keep)
    if lcon is not None:
        for e in entries:
            labeldb.remove_manual_label(lcon, str(e.get("labelcode", "")), str(e.get("label") or ""),
                                        before_write=before_write)
    return len(rows) - len(keep)


def backup_label_files(lcon, db_path: str | Path, manual_path: str | Path, stamp: str) -> list[Path]:
    """Datierte Sicherungskopie von labels.db und labels_manual.json daneben, bevor die Selbstheilung beim Start sie
    umschreibt: 'labels.sicherung-<stamp>.db', 'labels_manual.sicherung-<stamp>.json'. labels.db
    über die Backup-API der offenen Verbindung (konsistent, auch mit offener Verbindung). Eine schon vorhandene
    Sicherung gleichen Namens wird nie überschrieben. Rückgabe: die angelegten Dateien."""
    out = []
    manual = Path(manual_path)
    if manual.exists():
        dst = manual.with_name(f"{manual.stem}.sicherung-{stamp}{manual.suffix}")
        if not dst.exists():
            shutil.copy2(manual, dst)
            out.append(dst)
    db = Path(db_path)
    dst = db.with_name(f"{db.stem}.sicherung-{stamp}{db.suffix}")
    if lcon is not None and not dst.exists():
        bcon = sqlite3.connect(dst)
        try:
            lcon.backup(bcon)
        finally:
            bcon.close()
        out.append(dst)
    return out
