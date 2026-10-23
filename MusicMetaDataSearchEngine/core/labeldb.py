"""App-Label-DB: GVL-Master (synced.csv) -> lokale SQLite (labels + FTS5 + meta).

Idempotent: Neuaufbau bei jedem Lauf, gleiche Quelle -> gleiches Ergebnis. labelcode bleibt TEXT (führende Nullen).
1:n LC->Label (ein Code, mehrere Label durch Fusionen) bleibt erhalten.

Geteilt von App-Backend (Lookup/FTS) und Build-CLI (build_label_db.py).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .models import LabelRecord
from .normalize import normalize_labelcode, normalize_plm, normalize_record

_SCHEMA = """
CREATE TABLE labels (
    id              INTEGER PRIMARY KEY,
    labelcode       TEXT    NOT NULL,           -- '00121', '101515' — zero-padded, NIE Integer
    label           TEXT    NOT NULL,
    kurzname        TEXT,
    plm             INTEGER NOT NULL DEFAULT 0, -- 1 = 'Ja' (Production Library Music)
    hersteller      TEXT,
    rechterueckrufe TEXT,                       -- normalisiert: 'R6;R9;R12;R13' oder ''
    source          TEXT    NOT NULL DEFAULT 'gvl'  -- 'gvl' (Master/Supplemental) | 'manuell' (im Tool angelegt)
);
CREATE INDEX idx_labels_labelcode ON labels(labelcode);
CREATE VIRTUAL TABLE labels_fts USING fts5(
    label, hersteller, content='labels', content_rowid='id'
);
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_FTS_TERM = re.compile(r"\w+", re.UNICODE)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """SQLite-Verbindung mit Row-Factory (Spaltenzugriff per Name)."""
    con = sqlite3.connect(db_path, check_same_thread=False)  # seriell von App-/Observer-Thread nutzbar
    con.row_factory = sqlite3.Row
    return con


def build_label_db(
    records: list[LabelRecord],
    db_path: str | Path,
    source_version: str,
) -> int:
    """Baut die Label-SQLite neu aus records. Gibt die Zeilenzahl zurück.

    records werden normalisiert (idempotent); plm 'Ja'->1, sonst 0. Eine bestehende
    DB an db_path wird ersetzt (Re-Run = sauberer Neuaufbau, keine Doppel-Inserts).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    norm = [normalize_record(r) for r in records]
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.executemany(
            "INSERT INTO labels (labelcode,label,kurzname,plm,hersteller,rechterueckrufe) "
            "VALUES (?,?,?,?,?,?)",
            [
                (
                    r.labelcode, r.label, r.kurzname,
                    1 if normalize_plm(r.plm) == "Ja" else 0,
                    r.hersteller, r.rechterueckrufe,
                )
                for r in norm
            ],
        )
        # External-content FTS5 aus der labels-Tabelle aufbauen.
        con.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
        con.executemany(
            "INSERT INTO meta (key,value) VALUES (?,?)",
            [("source_version", source_version), ("row_count", str(len(norm)))],
        )
        con.commit()
        return len(norm)
    finally:
        con.close()


# --- Händisch angelegte Labels -------------------------------------------------
# labels.db wird bei jedem GVL-Update komplett neu gebaut. Eigene Labels (von einer Library
# gelieferte, Sub-Label, das die GVL noch nicht listet) leben deshalb in labels_manual.json neben der DB und
# werden beim Laden in die Tabelle nachgetragen (source='manuell'); GVL-Zeilen tragen 'gvl'.

_COLS = "labelcode,label,kurzname,plm,hersteller,rechterueckrufe,source"


def ensure_source_column(con: sqlite3.Connection) -> None:
    """Spalte `source` nachrüsten (ältere labels.db ohne sie): 'gvl' als Default."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(labels)")}
    if "source" not in cols:
        con.execute("ALTER TABLE labels ADD COLUMN source TEXT NOT NULL DEFAULT 'gvl'")
        con.commit()


def add_manual_labels(con: sqlite3.Connection, rows: list[dict], *, before_write=None) -> int:
    """Manuelle Labels in die Tabelle nachtragen — nur (Code, Label)-Paare, die es noch nicht
    gibt (idempotent, egal ob GVL oder früher manuell). Rückgabe: Anzahl neuer Zeilen.
    FTS wird nur bei Änderung neu gebaut. before_write() läuft vor dem ersten Schreiben (Sicherung)."""
    ensure_source_column(con)
    n = 0
    for r in rows:
        code = normalize_labelcode(str(r.get("labelcode", "")))
        label = (r.get("label") or "").strip()
        if not code or not label:
            continue
        dup = con.execute("SELECT 1 FROM labels WHERE labelcode=? AND lower(label)=lower(?)",
                          (code, label)).fetchone()
        if dup:
            continue
        if before_write and not n:
            before_write()
        con.execute("INSERT INTO labels (labelcode,label,kurzname,plm,hersteller,rechterueckrufe,source) "
                    "VALUES (?,?,?,?,?,?,'manuell')",
                    (code, label, (r.get("kurzname") or "").strip(), 0, (r.get("hersteller") or "").strip(), ""))
        n += 1
    if n:
        con.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
        con.commit()
    return n


def add_supplemental_labels(con: sqlite3.Connection, records: list[LabelRecord], *, before_write=None) -> int:
    """Zusatz-Codes (labelcodes_supplemental.csv, s. reconcile.csv_io.read_supplemental) in eine
    BESTEHENDE labels.db nachtragen — nur Codes, die die Tabelle noch gar nicht kennt: GVL-Master
    und manuelle Zeilen gewinnen. Selbstheilung beim Start (app._load): Installationen, deren DB per
    PDF-Update ohne die Library-Codes neu gebaut wurde, bekommen sie
    beim nächsten Start zurück; Zusatz-Codes eines neueren Builds kommen ohne Neuaufbau an. Kein
    Neuaufbau, damit ein neuerer PDF-Stand in der DB nicht durch die gebündelte CSV überschrieben wird.
    Idempotent (zweiter Lauf: 0). Rückgabe: Anzahl neuer Zeilen; FTS nur bei Änderung neu gebaut.
    before_write() läuft vor dem Schreiben, nur wenn etwas fehlt (Sicherung der Selbstheilung)."""
    ensure_source_column(con)
    known = {str(r[0]) for r in con.execute("SELECT DISTINCT labelcode FROM labels")}
    rows: list[tuple] = []
    for r in records:
        nr = normalize_record(r)
        if not nr.labelcode or not nr.label or nr.labelcode in known:
            continue
        known.add(nr.labelcode)
        rows.append((nr.labelcode, nr.label, nr.kurzname, 1 if normalize_plm(nr.plm) == "Ja" else 0,
                     nr.hersteller, nr.rechterueckrufe))
    if rows:
        if before_write:
            before_write()
        con.executemany("INSERT INTO labels (labelcode,label,kurzname,plm,hersteller,rechterueckrufe,source) "
                        "VALUES (?,?,?,?,?,?,'gvl')", rows)
        con.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
        con.commit()
    return len(rows)


def apply_name_fixes(con: sqlite3.Connection, fixes: dict[str, tuple[str, str]], *, before_write=None) -> int:
    """Namenskorrekturen (reconcile.csv_io.read_name_fixes) in einer BESTEHENDEN labels.db nachziehen: nur GVL-Zeilen,
    deren Name noch genau dem Fehler der Quelle entspricht. Idempotent. before_write() nur, wenn etwas zu ändern ist."""
    hits = [(new, code, old) for code, (old, new) in fixes.items()
            if con.execute("SELECT 1 FROM labels WHERE labelcode=? AND label=? AND source='gvl'", (code, old)).fetchone()]
    if not hits:
        return 0
    if before_write:
        before_write()
    con.executemany("UPDATE labels SET label=? WHERE labelcode=? AND label=? AND source='gvl'", hits)
    con.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
    con.commit()
    return len(hits)


def remove_manual_label(con: sqlite3.Connection, labelcode: str, label: str, *, before_write=None) -> int:
    """Eine manuell angelegte Zeile entfernen. GVL-Zeilen bleiben unantastbar (0). before_write() vor dem Löschen."""
    from .labelmatch import norm
    ensure_source_column(con)
    code = normalize_labelcode(labelcode)
    target = norm(label or "")
    # Vergleich in Python (Unicode-casefold), nicht per SQL lower() — das ist nur ASCII.
    ids = [r[0] for r in con.execute("SELECT id, label FROM labels WHERE labelcode=? AND source='manuell'",
                                     (code,)).fetchall() if norm(r[1]) == target]
    if ids:
        if before_write:
            before_write()
        con.executemany("DELETE FROM labels WHERE id=?", [(i,) for i in ids])
        con.execute("INSERT INTO labels_fts(labels_fts) VALUES('rebuild')")
        con.commit()
    return len(ids)


def lookup_by_code(con: sqlite3.Connection, labelcode: str) -> list[sqlite3.Row]:
    """Alle Label-Zeilen zu einem Labelcode (eingehend normalisiert). 1:n möglich. GVL-Zeilen vor manuellen:
    wer die erste Zeile als offizielle Schreibweise nimmt (Tagger, Cue-Karte, Rechterückruf), bekommt den
    GVL-Namen, nicht ein von Hand angelegtes zweites Label."""
    ensure_source_column(con)
    code = normalize_labelcode(labelcode)
    return con.execute(
        f"SELECT {_COLS} FROM labels WHERE labelcode=? ORDER BY (source='manuell'), label",
        (code,),
    ).fetchall()


def search_labels(con: sqlite3.Connection, query: str, limit: int = 25) -> list[sqlite3.Row]:
    """Fuzzy-Suche über label+hersteller via FTS5 (Präfix-Match), sortiert nach rank — der exakte
    Labelname (ohne Groß/klein, auch bei Umlauten) steht vorn: 'Atlantic' landete sonst auf Platz 20
    hinter AFRO-ATLANTIC und fiel bei mehr Präfix-Treffern als `limit` ganz heraus."""
    terms = _FTS_TERM.findall(query)
    if not terms:
        return []
    match = " ".join(f'"{t}"*' for t in terms)
    ensure_source_column(con)
    con.create_function("casefold", 1, lambda s: (s or "").casefold(), deterministic=True)
    return con.execute(
        "SELECT l.labelcode,l.label,l.kurzname,l.plm,l.hersteller,l.rechterueckrufe,l.source "
        "FROM labels l JOIN labels_fts f ON l.id=f.rowid "
        "WHERE labels_fts MATCH ? ORDER BY casefold(l.label) = ? DESC, rank LIMIT ?",
        (match, " ".join(query.split()).casefold(), limit),
    ).fetchall()
