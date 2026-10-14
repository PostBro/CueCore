#!/usr/bin/env python3
"""CueCore Metadaten-Eingang: Eingangsordner nach Library-Dumps (CSV) scannen
und in die wachsende songs.db einspeisen.

  python ingest_folder.py "<eingangsordner>"

Verarbeitete Dateien wandern nach <ordner>/_done/. Aktive songs.db liegt unter
~/Library/Application Support/MusicMeta/songs.db (außerhalb von Cloud-synchronisierten Ordnern), einmalig aus
dem Repo geseedet. Idempotent: gleicher Track -> Merge, keine Dublette.
"""
from __future__ import annotations
import argparse
import shutil
from pathlib import Path

from core import ingest, songsdb

REPO = Path(__file__).resolve().parent
SEED = REPO / "data" / "songs.db"
ACTIVE = Path.home() / "Library" / "Application Support" / "MusicMeta" / "songs.db"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CueCore Metadaten-Eingang")
    ap.add_argument("folder")
    ap.add_argument("--db", default=str(ACTIVE))
    args = ap.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Kein Ordner: {folder}"); return 1

    songsdb.seed(args.db, SEED)
    con = songsdb.open(args.db)
    before = songsdb.count(con)

    done = folder / "_done"
    done.mkdir(exist_ok=True)
    files = sorted(p for p in folder.glob("*.csv"))
    if not files:
        print("Keine CSV-Dateien im Eingangsordner."); return 0

    for f in files:
        res = ingest.import_csv(con, f, quelle=f.stem)
        if res.get("error"):
            print(f"[skip] {f.name}: {res['error']} (Spalten: {res.get('fieldnames')})")
            continue
        print(f"[ok]   {f.name}: {res['inserted']} neu, {res['updated']} ergänzt, "
              f"{res.get('skipped', 0)} übersprungen  | Mapping: {res['mapping']}")
        shutil.move(str(f), str(done / f.name))

    after = songsdb.count(con)
    con.close()
    print(f"\nDB: {args.db}  |  {before} -> {after} Songs (+{after - before})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
