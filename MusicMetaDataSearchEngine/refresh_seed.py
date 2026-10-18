#!/usr/bin/env python3
"""Repo-Seed `data/songs.db` aus der laufenden Datenbank auffrischen.

  python refresh_seed.py              # Live-DB des Nutzers -> data/songs.db + data/songs.csv
  python refresh_seed.py --from <db> --dry-run

Warum: `build_songs_db.py` baut den Seed aus kuratierten MP3s (Ordner mit verifizierten Dateien) — die liegen nicht überall,
deshalb blieb der Seed klein, während die Live-DB stark gewachsen ist. Der Seed soll den
gesamten eigenen Korpus mitliefern, damit schon beim ersten Start eine große Datenbank vorhanden ist.

Was NICHT in den Seed geht: die Zeilen der Library-Lieferungen (`quelle LIKE 'library:%'`). Die kommen
beim Build aus `data/library_tracks/*.csv` dazu (`build_bundle_data.py`) — sonst läge jede
Lieferung doppelt im Repo, einmal als große Binärdatei. Im fertigen Bundle ist trotzdem alles drin.

Mitgenommen wird jede Spalte der Quelltabelle (Tabellenschema, nicht songsdb._ALL_FIELDS — sonst fehlten quelle
und sublibraries). songs.csv bildet songs.db vollständig ab: alle Spalten außer id und dem aus
Interpret+Titel abgeleiteten nkey. Der neue Seed läuft durch dieselbe Bereinigung wie die Live-DB
(cleanup_songs_db.py) — sonst brächte jedes Auffrischen PRO-Marker, nackte Codes und Dubletten zurück.
Herkunftswerte, die auf eine konkrete Produktion zurückführen (Cue-Sheet-Dateinamen als quelle, source_type
`edl_import`), werden neutralisiert.
"""
from __future__ import annotations
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
import cleanup_songs_db                        # noqa: E402
from core import songsdb, trackindex          # noqa: E402

SEED_DB = ENGINE / "data" / "songs.db"
SEED_CSV = ENGINE / "data" / "songs.csv"
# Nicht in songs.csv: die laufende Nummer und der abgeleitete Schlüssel (songsdb rechnet nkey beim Öffnen)
CSV_SKIP = ("id", "nkey")
# Eigener Korpus = alles, was nicht aus einer Library-Lieferung stammt.
WHERE = "COALESCE(quelle,'') NOT LIKE 'library:%'"


# Herkunft im Seed ohne Dateinamen: ein importiertes Cue-Sheet trägt sonst den Namen seiner Datei als quelle.
# Bekannte Marker bleiben; Herkunftswerte, die keine Library sind, werden zu cue_sheet neutralisiert.
KEEP_QUELLE = {"", "MASTER", "TRACKINFO", "tagger", "manuell", "index", "edl_import"}


def neutral_quelle(q: str | None) -> str:
    parts: list[str] = []
    for p in (q or "").split(";"):
        p = p.strip()
        p = p if (p in KEEP_QUELLE or p.startswith("library:")) else "cue_sheet"
        if p and p not in parts:
            parts.append(p)
    return "; ".join(parts)


def neutralize_quelle(con: sqlite3.Connection) -> int:
    n = 0
    for (q,) in con.execute("SELECT DISTINCT quelle FROM songs").fetchall():
        new = neutral_quelle(q)
        if new != (q or ""):
            n += con.execute("UPDATE songs SET quelle=? WHERE quelle IS ?", (new, q)).rowcount
    con.commit()
    return n


def neutralize_source_type(con: sqlite3.Connection) -> int:
    """Zeilen aus einem EDL-Import (source_type 'edl_import') führen auf eine konkrete Produktion zurück. Im Seed
    tragen sie den Wert der übrigen Cue-Sheet-Zeilen (leer). Zuerst wird die Cue-Dauer geleert (sie gehört zum
    Einsatz im Projekt, nicht zum Song; die Regel hängt an source_type), dann der Wert umgeschrieben."""
    songsdb.clear_cue_durations(con)
    n = con.execute("UPDATE songs SET source_type='' WHERE source_type='edl_import'").rowcount
    con.commit()
    return n


def write_csv(con: sqlite3.Connection, path: Path) -> None:
    """songs.csv als vollständiger Spiegel der songs-Tabelle (alle Spalten außer CSV_SKIP), nach Interpret/Titel."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(songs)") if r[1] not in CSV_SKIP]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in con.execute(f"SELECT {','.join(cols)} FROM songs ORDER BY interpret, titel, id"):
            w.writerow(["" if v is None else v for v in r])


def refresh(src: Path, *, out_db: Path = SEED_DB, out_csv: Path = SEED_CSV, dry_run: bool = False,
            master=None, lib_names: dict | None = None) -> dict:
    """master/lib_names: wie cleanup_songs_db.clean (Standard: GVL-Master und Library-Listen aus dem Repo)."""
    if not src.is_file():
        raise SystemExit(f"Keine Datenbank unter {src}")
    con = sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True)     # Live-DB nur lesen
    con.row_factory = sqlite3.Row
    try:
        # Spalten aus dem Schema der Quelle; was die Quelle (noch) nicht hat, legt songsdb.open leer an — eine Live-DB,
        # die die neue App-Version noch nicht geöffnet hat, kennt eine neu eingeführte Spalte (label_geliefert) nicht.
        schema = [(r[1], r[2]) for r in con.execute("PRAGMA table_info(songs)") if r[1] not in CSV_SKIP]
        cols = [c for c, _ in schema]
        rows = con.execute(f"SELECT {','.join(cols)} FROM songs WHERE {WHERE} ORDER BY interpret, titel").fetchall()
        total = con.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    finally:
        con.close()
    rep = {"source": str(src), "source_rows": total, "seed_rows": len(rows),
           "skipped_library": total - len(rows)}
    tmp = out_db.with_suffix(".db.new")
    if not dry_run:
        tmp.unlink(missing_ok=True)
    dst = songsdb.open(":memory:" if dry_run else tmp)
    try:
        have = {r[1] for r in dst.execute("PRAGMA table_info(songs)")}
        for c, typ in schema:
            if c not in have:                    # Spalte einer neueren Version: mitnehmen statt verlieren
                dst.execute(f'ALTER TABLE songs ADD COLUMN "{c}" {typ}')
        dst.executemany(f"INSERT INTO songs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r) for r in rows])
        dst.commit()
        if master is None:                       # echter Lauf: GVL-Master und Hersteller (Dachmarke, GVL gewinnt)
            master, hersteller = cleanup_songs_db.load_master(), cleanup_songs_db.load_hersteller()
        else:
            hersteller = None
        rep["bereinigung"] = cleanup_songs_db.clean(dst, master, lib_names=lib_names, hersteller=hersteller)
        rep["quelle_neutral"] = neutralize_quelle(dst)
        rep["source_type_neutral"] = neutralize_source_type(dst)
        if dry_run:
            return rep
        dst.execute("VACUUM")
        rep["written"] = songsdb.count(dst)
        write_csv(dst, out_csv)
    finally:
        dst.close()
    tmp.replace(out_db)
    rep["size_mb"] = round(out_db.stat().st_size / 1e6, 1)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default=str(trackindex.default_data_dir() / "songs.db"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rep = refresh(Path(a.src).expanduser(), dry_run=a.dry_run)
    print(f"{rep['source']}: {rep['source_rows']:,} Zeilen, davon {rep['skipped_library']:,} aus "
          f"Library-Lieferungen (kommen beim Build aus data/library_tracks/)")
    rules = rep["bereinigung"]["rules"]
    print("Bereinigung: " + (", ".join(f"{k} {n:,}" for k, n in rules.items() if n) or "nichts zu tun")
          + " (Bericht: python cleanup_songs_db.py <songs.db>)")
    if a.dry_run:
        print(f"Trockenlauf — würde {rep['bereinigung']['rows_after']:,} Zeilen in den Seed schreiben")
    else:
        print(f"data/songs.db: {rep['written']:,} Zeilen, {rep['size_mb']} MB — data/songs.csv daneben")
    return 0


if __name__ == "__main__":
    sys.exit(main())
