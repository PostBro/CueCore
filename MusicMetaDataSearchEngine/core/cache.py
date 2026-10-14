"""Durabler API-Cache (SQLite). Bewusst SEPARAT von labels.db, weil deren Re-Build
(build_label_db) die Datei löscht und einen dort liegenden Cache mitnehmen würde.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_cache (
    source     TEXT NOT NULL,   -- 'musicbrainz' | 'discogs'
    query_key  TEXT NOT NULL,   -- normalisierter Lookup-Key
    payload    TEXT NOT NULL,   -- JSON-Response
    fetched_at TEXT NOT NULL,   -- ISO-8601
    PRIMARY KEY (source, query_key)
);
"""


def open_cache(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)  # Watchfolder-Handler läuft im Observer-Thread
    con.executescript(_SCHEMA)
    return con


def get_entry(con: sqlite3.Connection, source: str, query_key: str) -> tuple[str, str] | None:
    """(payload, fetched_at) oder None. `fetched_at` war bisher tot — der Aufrufer
    braucht es, um zu alte Einträge als Miss zu behandeln UND sie trotzdem als
    Notreserve zu behalten (s. netfetch: Stale-Fallback bei Rate-Limit)."""
    row = con.execute(
        "SELECT payload, fetched_at FROM api_cache WHERE source=? AND query_key=?", (source, query_key)
    ).fetchone()
    return (row[0], row[1]) if row else None


def age_seconds(fetched_at: str, *, now: datetime | None = None) -> float:
    """Alter eines Eintrags in Sekunden. Unparsbarer Zeitstempel -> `inf`
    (gilt als beliebig alt, damit ein kaputtes Feld nie 'frisch' vortäuscht)."""
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - ts).total_seconds()


def get(con: sqlite3.Connection, source: str, query_key: str,
        max_age: float | None = None) -> str | None:
    """Payload zum Key. Mit `max_age` (Sekunden) gilt ein älterer Eintrag als Miss."""
    entry = get_entry(con, source, query_key)
    if entry is None:
        return None
    payload, fetched_at = entry
    if max_age is not None and age_seconds(fetched_at) > max_age:
        return None
    return payload


def put(con: sqlite3.Connection, source: str, query_key: str, payload: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO api_cache (source,query_key,payload,fetched_at) VALUES (?,?,?,?)",
        (source, query_key, payload, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
