#!/usr/bin/env python3
"""Baut die App-Label-DB (labels + FTS5 + meta) aus dem GVL-Master.

  python build_label_db.py                          # Default-Pfade
  python build_label_db.py --csv <pfad> --out <pfad>

Quelle: data/GVL_Labelcodes_synced.csv (+ .meta.json für source_version).
Ziel-Default: ~/Library/Application Support/MusicMeta/labels.db — bewusst AUSSERHALB
von Cloud-synchronisierten Ordnern: SQLite + Cloud-Sync = Korruptionsrisiko.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

from core.labeldb import build_label_db, connect
from reconcile.csv_io import read_master_with_supplemental

REPO = Path(__file__).resolve().parent
PROJECT = REPO.parent
DEFAULT_CSV = PROJECT / "data" / "GVL_Labelcodes_synced.csv"
DEFAULT_META = PROJECT / "data" / "GVL_Labelcodes_synced.meta.json"
DEFAULT_OUT = Path.home() / "Library" / "Application Support" / "MusicMeta" / "labels.db"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GVL-Master -> App-Label-SQLite")
    ap.add_argument("--csv", default=str(DEFAULT_CSV), help="GVL-Master-CSV")
    ap.add_argument("--meta", default=str(DEFAULT_META), help="Meta-JSON (source_version)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Ziel-SQLite")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV nicht gefunden: {csv_path}", file=sys.stderr)
        return 2

    source_version = "unknown"
    meta_path = Path(args.meta)
    if meta_path.exists():
        source_version = json.loads(meta_path.read_text(encoding="utf-8")).get("source_version", "unknown")

    records = read_master_with_supplemental(csv_path)   # GVL-Master + labelcodes_supplemental.csv (GVL gewinnt)
    n = build_label_db(records, args.out, source_version)

    con = connect(args.out)
    try:
        codes = con.execute("SELECT COUNT(DISTINCT labelcode) FROM labels").fetchone()[0]
        plm = con.execute("SELECT COUNT(*) FROM labels WHERE plm=1").fetchone()[0]
    finally:
        con.close()
    print(f"Label-DB: {args.out}")
    print(f"  Zeilen: {n}  | eindeutige Labelcodes: {codes}  | PLM-Labels: {plm}  | Quell-Version: {source_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
