"""operations_log — Sicherheitsnetz für destruktive Operationen.

Jede Tag-Schreibung und jede Umbenennung/Verschiebung wird protokolliert, damit
sie rückgängig gemacht werden kann, ohne manuell suchen zu müssen.

op-Typen:
  'tag'    — Tags geschrieben; details = JSON der ALTEN Tags (für Restore).
  'rename' — Datei umbenannt/verschoben; old_path -> new_path.
  'songs'  — Eintrag des Taggers in der songs.db; details = JSON aus songsdb.upsert_undo
             (sonst käme ein zurückgenommener LC als verifizierter Treffer wieder).
"""
from __future__ import annotations
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import songsdb
from . import tags as tagslib

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    op        TEXT NOT NULL,
    old_path  TEXT,
    new_path  TEXT,
    details   TEXT,
    undone    INTEGER NOT NULL DEFAULT 0
);
"""


def open_oplog(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)  # Watchfolder-Handler läuft im Observer-Thread
    con.executescript(_SCHEMA)
    return con


def log(con: sqlite3.Connection, op: str, old_path: str = "", new_path: str = "", details: str = "") -> int:
    cur = con.execute(
        "INSERT INTO operations (ts,op,old_path,new_path,details) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), op, old_path, new_path, details),
    )
    con.commit()
    return cur.lastrowid


def _invert(row: sqlite3.Row) -> None:
    op = row["op"]
    if op == "rename":
        new, old = Path(row["new_path"]), Path(row["old_path"])
        if new.exists() and not old.exists():
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(new), str(old))   # Cross-Drive-fest (wie tagging.apply)
    elif op == "tag":
        old_tags = json.loads(row["details"] or "{}")
        target = row["old_path"]
        if Path(target).exists():
            tagslib.restore_tags(target, old_tags)   # verwaltete Frames reset + alten Stand


def undo(con: sqlite3.Connection, op_ids: list[int], *, songs_con: sqlite3.Connection | None = None) -> int:
    """Macht die gegebenen Operationen in UMGEKEHRTER Reihenfolge rückgängig.

    Reihenfolge wichtig: erst Umbenennung zurück, dann Tags am Original-Pfad
    restaurieren (apply schreibt Tags VOR der Umbenennung). 'songs'-Einträge brauchen
    die songs.db-Verbindung; ohne sie bleiben sie offen (nicht als erledigt markiert).
    """
    con.row_factory = sqlite3.Row
    done = 0
    for op_id in reversed(op_ids):
        row = con.execute("SELECT * FROM operations WHERE id=? AND undone=0", (op_id,)).fetchone()
        if not row:
            continue
        if row["op"] == "songs":
            if songs_con is None:
                continue
            songsdb.revert_upsert(songs_con, json.loads(row["details"] or "{}"))
        else:
            _invert(row)
        con.execute("UPDATE operations SET undone=1 WHERE id=?", (op_id,))
        done += 1
    con.commit()
    return done
