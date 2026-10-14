#!/usr/bin/env python3
"""CueCore Watchfolder — überwacht einen Ordner, verarbeitet neue Audiodateien.

  python watch_folder.py "<ordner>"                                   # Preview (schreibt NICHTS)
  python watch_folder.py "<ordner>" --apply --min-band review --library "<ziel>"

Default ist sicher: ohne --apply wird nur erkannt + vorgeschlagen, nichts geschrieben.
--apply taggt + benennt + verschiebt nur Treffer ab --min-band (Default auto = nur
sehr sichere). Jede Schreibung landet im operations_log (undo via tag_track.py).
"""
from __future__ import annotations
import argparse
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core import audd, discogs, musicbrainz, netfetch, operations, settings, watch
from core.labeldb import connect, lookup_by_code
from core.labelmatch import build_index_from_db
from core.resolver import resolve

DEFAULT_DB = Path.home() / "Library" / "Application Support" / "MusicMeta" / "labels.db"
OPLOG = DEFAULT_DB.parent / "operations.db"
_noop = lambda *a, **k: None


def make_resolver(idx, db):
    fetcher = netfetch.build_fetcher(db)
    def rf(artist, title, isrc=""):
        return resolve(artist, title, idx, isrc=isrc,
                       isrc_lookup=lambda i: musicbrainz.lookup_by_isrc(i, fetch=fetcher, sleep=_noop),
                       mb_lookup=lambda a, t: musicbrainz.lookup(a, t, fetch=fetcher, sleep=_noop),
                       discogs_label_codes=lambda rid: discogs.lookup_label_codes(rid, fetch=fetcher, sleep=_noop))
    return rf


def make_label_lookup(con):
    def ll(code):
        rows = lookup_by_code(con, code)
        return (rows[0]["label"], rows[0]["rechterueckrufe"]) if rows else ("", "")
    return ll


class Handler(FileSystemEventHandler):
    def __init__(self, args, resolve_fn, label_lookup, op_con, recognize=None):
        self.args, self.resolve_fn, self.label_lookup, self.op_con = args, resolve_fn, label_lookup, op_con
        self.recognize = recognize

    def _maybe(self, src_path: str, is_dir: bool):
        if is_dir:
            return
        p = Path(src_path)
        if p.suffix.lower() not in watch.AUDIO_EXT:
            return
        if not watch.is_stable(p):
            print(f"[warte]   {p.name} (noch in Bewegung)", flush=True)
            return
        r = watch.process_one(p, resolve_fn=self.resolve_fn, label_lookup=self.label_lookup,
                              op_con=self.op_con, apply=self.args.apply,
                              min_band=self.args.min_band, move_to=self.args.library,
                              recognize=self.recognize)
        if r.action == "applied":
            tail = f"-> LC{r.labelcode} {r.label}  ({Path(r.new_path).name})"
        elif r.identified:
            tail = f"[{r.band}] {('LC'+r.labelcode+' '+r.label) if r.labelcode else r.note}"
        else:
            tail = r.note
        if r.possible_cover:
            tail += "  [possible_cover: Fingerprint-Treffer weicht von bekanntem Artist/Titel ab]"
        print(f"[{r.action:7}] {p.name}  {tail}", flush=True)

    def on_created(self, event):
        self._maybe(event.src_path, event.is_directory)

    def on_moved(self, event):
        self._maybe(event.dest_path, event.is_directory)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CueCore Watchfolder")
    ap.add_argument("folder")
    ap.add_argument("--apply", action="store_true", help="taggen+umbenennen (sonst nur Preview)")
    ap.add_argument("--min-band", default="auto", choices=["auto", "review", "review_low"],
                    help="ab welcher Confidence geschrieben wird (Default auto)")
    ap.add_argument("--library", default=None, help="Zielordner für getaggte Files (sonst in-place)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args(argv)

    folder = Path(a.folder)
    if not folder.is_dir():
        print(f"Kein Ordner: {folder}"); return 1

    con = connect(a.db)
    idx = build_index_from_db(con)
    op = operations.open_oplog(OPLOG)
    audd_token = settings.get("audd_token")
    recognize = ((lambda p, ea="", et="": audd.recognize(p, audd_token, expected_artist=ea, expected_title=et))
                 if audd_token else None)
    handler = Handler(a, make_resolver(idx, Path(a.db)), make_label_lookup(con), op, recognize)

    mode = f"APPLY (min-band={a.min_band})" if a.apply else "PREVIEW (kein Schreiben)"
    print(f"CueCore Watchfolder · {folder} · Modus: {mode} · Strg+C beendet")
    obs = Observer()
    obs.schedule(handler, str(folder), recursive=False)
    obs.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    obs.join()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
