"""Song-Datenbank fürs Team (Teil D16): als CSV weitergeben und bei Kolleg:innen einlesen.

Die songs.db selbst gehört nie auf ein Netzlaufwerk (SQLite verträgt keine Netz-Dateisysteme, sqlite.org „Appropriate
Uses“), deshalb tauscht das Team eine Datei: die Datenfelder wie im normalen Export plus Prüfstatus, Quelle und
Prüfdatum. Regeln beim Einlesen:
  - verifiziert bleibt verifiziert, wenn der Labelcode im GVL-Master steht (oder keiner da ist); sonst „zu prüfen“
  - ein Song, den es schon gibt: die eigenen Werte gewinnen, nur leere Felder werden gefüllt; jede Abweichung landet
    in einer Liste zum Durchsehen
  - neue Songs bekommen quelle „team:<Dateiname>“ und das Prüfdatum der Datei
"""
from __future__ import annotations
import csv
from pathlib import Path

from . import songsdb
from .dbexport import EXPORT_COLUMNS, _val
from .normalize import normalize_labelcode

STATUS_HEAD, SOURCE_HEAD, DATE_HEAD = "Status", "Quelle", "Geprüft am"
VERIFIED, REVIEW = "verifiziert", "zu prüfen"
TEAM_HEADS = [h for _, h in EXPORT_COLUMNS] + [STATUS_HEAD, SOURCE_HEAD, DATE_HEAD]
FIELDS = [f for f, _ in EXPORT_COLUMNS if f not in ("interpret", "titel")]


def export_team_csv(rows: list[dict], path: str | Path) -> None:
    """CSV (UTF-8 mit BOM) mit Datenfeldern, Prüfstatus, Quelle und Prüfdatum."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(TEAM_HEADS)
        for r in rows:
            w.writerow([_val(r, field) for field, _ in EXPORT_COLUMNS]
                       + [REVIEW if r.get("review_status") == "review" else VERIFIED, r.get("quelle") or "",
                          str(r.get("date_verified") or "")[:10]])


def is_team_file(path: str | Path) -> bool:
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            head = next(csv.reader(f), [])
    except (OSError, UnicodeDecodeError):
        return False
    return all(h in head for h in ("Interpret", "Titel", STATUS_HEAD, DATE_HEAD))


def read_team(path: str | Path) -> list[dict]:
    """Zeilen als {feld: wert} plus status, quelle, date_verified. Zeilen ohne Interpret oder Titel fallen weg."""
    by_head = {h: f for f, h in EXPORT_COLUMNS}
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            row = {by_head[h]: (v or "").strip() for h, v in r.items() if h in by_head}
            if not row.get("interpret") or not row.get("titel"):
                continue
            row["status"] = (r.get(STATUS_HEAD) or "").strip()
            row["quelle"] = (r.get(SOURCE_HEAD) or "").strip()
            row["date_verified"] = (r.get(DATE_HEAD) or "").strip()
            out.append(row)
    return out


def plan(con, rows: list[dict], master_codes) -> dict:
    """Was ein Import täte, ohne zu schreiben: {new, filled, same, conflicts, review}. `master_codes`: Codes im
    GVL-Master (Ziffern, normalisiert)."""
    rep = {"new": [], "filled": [], "same": 0, "conflicts": [], "review": 0}
    for r in rows:
        code = normalize_labelcode(r.get("labelcode", ""))
        verified = r.get("status") != REVIEW and (not code or code in master_codes)
        own = songsdb.find_row(con, r["interpret"], r["titel"])
        if own is None:
            rep["new"].append({**r, "review_status": "verified" if verified else "review"})
            if not verified:
                rep["review"] += 1
            continue
        fills, diffs = {}, []
        for f in FIELDS:
            mine, theirs = str(own.get(f) or "").strip(), str(r.get(f) or "").strip()
            if not theirs:
                continue
            if f == "labelcode":
                mine, theirs = normalize_labelcode(mine), normalize_labelcode(theirs)
            if not mine:
                fills[f] = r.get(f)
            elif mine.casefold() != theirs.casefold():
                diffs.append({"field": f, "own": own.get(f), "team": r.get(f)})
        if fills:
            rep["filled"].append({"interpret": own["interpret"], "titel": own["titel"], "fields": fills})
        if diffs:
            rep["conflicts"].append({"interpret": own["interpret"], "titel": own["titel"], "diffs": diffs})
        if not fills and not diffs:
            rep["same"] += 1
    return rep


def apply(con, rep: dict, source: str) -> None:
    """Plan schreiben: neue Songs anlegen, bei vorhandenen nur leere Felder füllen (Konflikte bleiben, wie sie sind)."""
    quelle = "team:" + Path(source).name
    for r in rep["new"]:
        songsdb.upsert(con, interpret=r["interpret"], titel=r["titel"], quelle=quelle,
                       review_status=r["review_status"], date_verified=r.get("date_verified", ""),
                       **{f: r.get(f, "") for f in FIELDS})
    for r in rep["filled"]:
        songsdb.upsert(con, interpret=r["interpret"], titel=r["titel"], **r["fields"])
    con.commit()
