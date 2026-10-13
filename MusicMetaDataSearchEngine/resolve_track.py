#!/usr/bin/env python3
"""Track auflösen: artist+title -> GVL-LC-Kandidaten (MusicBrainz + Discogs, keyless).

  python resolve_track.py "Alanis Morissette" "Thank You" [pfad/zur/labels.db]

Live-Lookup (Netz). Ohne API-Key. Reine Demo/CLI für den Resolver aus core/.
"""
from __future__ import annotations
import sys
from pathlib import Path

from core import deezer, discogs, musicbrainz, netfetch, songsdb
from core.labeldb import connect
from core.labelmatch import build_index_from_db
from core.resolver import local_result, resolve

_noop = lambda *a, **k: None
SONGS_SEED = Path(__file__).resolve().parent / "data" / "songs.db"
ACTIVE_SONGS = Path.home() / "Library" / "Application Support" / "MusicMeta" / "songs.db"

DEFAULT_DB = Path.home() / "Library" / "Application Support" / "MusicMeta" / "labels.db"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        print('Aufruf: python resolve_track.py "Artist" "Title" [labels.db]')
        return 2
    artist, title = argv[0], argv[1]
    db = Path(argv[2]) if len(argv) > 2 else DEFAULT_DB
    if not db.exists():
        print(f"labels.db nicht gefunden: {db}\n(zuerst build_label_db.py laufen lassen)")
        return 2

    con = connect(db)
    idx = build_index_from_db(con)
    con.close()

    # Stufe 1: verifizierte lokale songs.db (sofort, kostenlos, indiziert)
    songsdb.seed(ACTIVE_SONGS, SONGS_SEED)
    hit = songsdb.find(songsdb.open(ACTIVE_SONGS), artist, title)
    if hit:
        fetcher = netfetch.Fetcher()   # nur für Stats-Ausgabe (0 Calls)
        res = local_result(artist, title, hit)
        print("  [lokale verifizierte DB]")
    else:
        fetcher = netfetch.build_fetcher(db)   # Cache + zentraler Rate-Limit-Scheduler
        res = resolve(artist, title, idx,
                      mb_lookup=lambda a, t: musicbrainz.lookup(a, t, fetch=fetcher, sleep=_noop),
                      discogs_label_codes=lambda rid: discogs.lookup_label_codes(rid, fetch=fetcher, sleep=_noop),
                      deezer_lookup=lambda a, t: deezer.lookup(a, t, fetch=fetcher, sleep=_noop))

    print(f"\n{res.artist} — {res.title}")
    print(f"  MB-Match: {res.matched}  Score: {res.mb_score}  Band: {res.band}"
          + ("  [UNVOLLSTÄNDIG]" if (res.incomplete or fetcher.failures) else ""))
    print(f"  Album: {res.album or '–'} ({(res.date or '')[:4] or '–'})  ISRC: {(res.isrcs[0] if res.isrcs else '–')}")
    print(f"  Komponist: {res.composer or '–'}")
    print(f"  Texter:    {res.lyricist or '–'}")
    best = res.best
    if not best:
        print("  -> kein GVL-Label zuordenbar")
    else:
        flag = "DUAL-SOURCE" if best.dual_source else "single"
        print(f"  -> LC{best.labelcode}  {best.label}  (conf={best.confidence}, q={best.quality}, {flag})")
        for s in best.source_urls:
            print(f"     Quelle {s['name']}: {s['url']}")
        if len(res.candidates) > 1:
            print(f"     ({len(res.candidates)-1} weitere Kandidaten verworfen)")
    for n in res.notes:
        print(f"  · {n}")
    print(f"  (Netz: {fetcher.calls} Calls, {fetcher.cache_hits} Cache-Hits, {fetcher.failures} Fehler)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
