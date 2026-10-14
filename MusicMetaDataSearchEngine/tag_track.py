#!/usr/bin/env python3
"""CueCore Tagger-CLI: Datei taggen + nach Schema umbenennen — geloggt, undo-fähig.

  python tag_track.py "<file>" "<Artist>" "<Title>" --lc 01485          # Vorschau (Plan)
  python tag_track.py "<file>" "<Artist>" "<Title>" --lc 01485 --apply  # schreibt + benennt um
  python tag_track.py "<file>" "<Artist>" "<Title>" --resolve [--apply] # LC via Resolver (Netz)
  python tag_track.py --undo 12 13                                      # Operationen rückgängig

Hartstopp: kein Schreiben ohne --apply, kein Rename ohne bestätigten LC.
"""
from __future__ import annotations
import argparse
from pathlib import Path

from core import discogs, musicbrainz, netfetch, operations, tagging
from core.labeldb import connect, lookup_by_code
from core.labelmatch import build_index_from_db
from core.normalize import normalize_labelcode
from core.resolver import resolve

DEFAULT_DB = Path.home() / "Library" / "Application Support" / "MusicMeta" / "labels.db"
OPLOG = DEFAULT_DB.parent / "operations.db"
_noop = lambda *a, **k: None


def _label_and_rr(con, code: str):
    rows = lookup_by_code(con, code)
    if not rows:
        return None, ""
    return rows[0]["label"], rows[0]["rechterueckrufe"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CueCore Tagger")
    ap.add_argument("file", nargs="?")
    ap.add_argument("artist", nargs="?")
    ap.add_argument("title", nargs="?")
    ap.add_argument("--lc")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", nargs="+", type=int)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args(argv)

    op = operations.open_oplog(OPLOG)
    if a.undo:
        print(f"{operations.undo(op, a.undo)} Operation(en) rückgängig gemacht.")
        return 0
    if not (a.file and a.artist and a.title):
        ap.error("file, artist und title sind nötig (oder --undo).")

    db = Path(a.db)
    con = connect(db)
    code, isrc = a.lc, ""

    if a.resolve and not code:
        idx = build_index_from_db(con)
        fetcher = netfetch.build_fetcher(db)
        res = resolve(a.artist, a.title, idx,
                      mb_lookup=lambda ar, t: musicbrainz.lookup(ar, t, fetch=fetcher, sleep=_noop),
                      discogs_label_codes=lambda rid: discogs.lookup_label_codes(rid, fetch=fetcher, sleep=_noop))
        if not res.candidates:
            con.close(); print("Resolver: kein Kandidat gefunden."); return 1
        top = res.candidates[0]
        code, isrc = top.labelcode, (res.isrcs[0] if res.isrcs else "")
        flag = "DUAL-SOURCE" if top.dual_source else "single-source"
        print(f"Resolver-Top: LC{code} ({top.label}) · conf={top.confidence} · band={res.band} · {flag}")
        if res.incomplete:
            print("  ! Abfrage unvollständig (Rate-Limit) — Ergebnis prüfen.")

    if not code:
        con.close(); print("Kein Labelcode — gib --lc <code> oder --resolve."); return 1
    code = normalize_labelcode(code)
    label, rr = _label_and_rr(con, code)
    con.close()
    if label is None:
        print(f"LC{code} ist nicht im GVL-Master — kein Tagging."); return 1

    pl = tagging.plan(a.file, a.artist, a.title, code, label, isrc=isrc, rechterueckrufe=rr)
    print(f"\nDatei:        {Path(a.file).name}")
    print(f"  aktuell:    {pl.current_tags or '— (keine verwalteten Tags)'}")
    print(f"  Vorschlag:  {pl.proposed_tags}")
    print(f"  neuer Name: {pl.proposed_name or '— (kein LC, kein Rename)'}")
    for w in pl.warnings:
        print(f"  ! {w}")

    if not a.apply:
        print("\n(Vorschau — mit --apply wird geschrieben und umbenannt.)")
        return 0

    out = tagging.apply(pl, op_con=op, confirm=True)
    print(f"\nGeschrieben + umbenannt -> {Path(out['new_path']).name}")
    print(f"Undo: python tag_track.py --undo {' '.join(map(str, out['op_ids']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
