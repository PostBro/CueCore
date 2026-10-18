#!/usr/bin/env python3
"""Library-Lieferung (Track-Datenbank einer Production-Music-Library) einpflegen.

  python import_library_tracks.py extract --src <lieferung.xlsx> --library cezame
      -> data/library_tracks/cezame_<JJJJ-MM-TT>.csv (schlanker Extrakt, wie geliefert; git-versioniert).
         Eine vorhandene Datei wird nie überschrieben — sonst ersetzt eine zweite Lieferung desselben
         Tages still die erste, und bestehende Installationen bekommen sie nie (Name schon eingepflegt).
  python import_library_tracks.py import data/library_tracks/cezame_2026-09.csv
      -> songs.db (verifiziert, quelle library:cezame) + library_files (Dateiname -> Track);
         Labelname in GVL-Schreibweise aus labels.db; Codes, die der Master nicht kennt, werden wie
         geliefert eingetragen UND als manuelles Label (labels_manual.json + labels.db) angelegt,
         mit Notiz — beim nächsten GVL-PDF-Update prüfen. Ausnahme: steht der
         gelieferte Labelname im Master unter einem anderen Code, gilt der GVL-Code (der gelieferte bleibt
         als Notiz an der Zeile, s. core/labelcheck.py).

Die App macht den Import beim Start auch selbst, einmal je Datei (librarytracks.ensure_imported) —
das Script ist für den ersten Lauf mit Bericht und für Wiederholungen nach einem Neuaufbau.
"""
from __future__ import annotations
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import labelcheck, labeldb, librarytracks, normalize, songsdb, trackindex  # noqa: E402

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_DIR / "data" / "library_tracks"


def cmd_extract(src: Path, library: str, out: Path | None, stamp: str) -> int:
    # Lieferungen kommen als CSV oder xlsx — der Leser entscheidet, nicht die Endung.
    readers = {"cezame": librarytracks.read_cezame_xlsx, "upm": librarytracks.read_upm_csv,
               "ripcue": lambda p: librarytracks.read_cuecore_csv(p, "ripcue")}   # CueCore-Exportformat
    if library not in readers:
        print(f"kein Leser für Library {library!r} (bekannt: {', '.join(readers)})")
        return 1
    out = out or DEFAULT_OUT_DIR / f"{library}_{stamp}.csv"
    if out.exists():
        # Nie still ersetzen: die Tracks der alten Datei wären aus dem Repo-Extrakt weg, und
        # ensure_imported kennt den Namen schon und nähme den neuen Inhalt nie.
        print(f"{out} gibt es schon — nicht überschrieben. Anderen Namen mit --out wählen.")
        return 1
    rows = readers[library](src)
    n = librarytracks.write_csv(rows, out)
    subs = {}
    for r in rows:
        e = subs.setdefault((r["sublibrary"], r["labelcode"]), 0)
        subs[(r["sublibrary"], r["labelcode"])] = e + 1
    print(f"{src.name}: {n:,} Zeilen -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    for (sub, code), k in sorted(subs.items(), key=lambda x: -x[1]):
        print(f"  {k:>7,}  {sub or '—':<32} {('LC' + normalize.normalize_labelcode(code)) if code else '—'}")
    return 0


def cmd_import(csv_path: Path, songs_path: Path, labels_path: Path, stamp: str) -> int:
    library = librarytracks.library_name(csv_path)
    rows = librarytracks.read_csv(csv_path)
    lcon = labeldb.connect(labels_path) if labels_path.exists() else None
    if lcon is None:
        print(f"labels.db fehlt unter {labels_path} — Labelnamen bleiben wie geliefert")
    # Nur GVL-Master + Zusatzliste, keine manuellen Labels: sonst gälte ein früher angelegtes LC21110 als bekannt
    master = labelcheck.GvlMaster.from_db(lcon) if lcon is not None else labelcheck.GvlMaster([])
    song_rows, rep = librarytracks.to_song_rows(rows, master)
    scon = songsdb.open(songs_path)
    before = songsdb.count(scon)
    res = songsdb.import_library_tracks(scon, song_rows, library=library, date_verified=stamp)
    after = songsdb.count(scon)
    print(f"{csv_path.name}: {rep['rows']:,} Zeilen -> songs.db {songs_path}: {res['inserted']:,} neu, "
          f"{res['updated']:,} schon vorhanden (Felder nur aufgefüllt), {res['files']:,} Dateinamen in library_files; "
          f"songs {before:,} -> {after:,}")
    for sub, gvl in sorted(rep["renamed"].items()):
        print(f"  Labelname in GVL-Schreibweise: {sub!r} -> {gvl!r}")
    for name, info in sorted(rep["by_name"].items()):
        print(f"  {name!r} ohne Labelcode: GVL führt den Namen eindeutig unter LC{info['gvl_code']} -> LC{info['gvl_code']} "
              f"({info['n']} Tracks, Notiz an der Zeile)")
    for name, info in sorted(rep["name_ambiguous"].items()):
        print(f"  {name!r} ohne Labelcode: Name im Master unter {', '.join('LC' + c for c in info['gvl_codes'])} — "
              f"offen gelassen ({info['n']} Tracks)")
    if rep["no_code"]:
        print(f"  {rep['no_code']} Zeilen ohne Labelcode")
    for code, info in sorted(rep["corrected"].items()):
        print(f"  LC{code} {info['label']!r}: GVL führt den Namen unter LC{info['gvl_code']} -> LC{info['gvl_code']} "
              f"({info['n']} Tracks, gelieferter Code als Notiz an der Zeile)")
    # Codes, die der Master nicht kennt: als manuelles Label anlegen, damit Suche/Resolver den Namen zum Code kennen
    for code, info in sorted(rep["unknown_codes"].items()):
        print(f"  LC{code} {info['label']!r}: nicht im GVL-Master ({info['n']} Tracks) -> manuelles Label angelegt")
    if lcon is not None and rep["unknown_codes"]:
        labelcheck.add_manual_labels(lcon, labels_path.parent / "labels_manual.json",
                                     labelcheck.manual_label_entries(rep["unknown_codes"], library=library,
                                                                     source=csv_path.name, stamp=stamp))
    if lcon is not None:
        lcon.close()
    scon.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    data_dir = trackindex.default_data_dir()
    ap.add_argument("--songs", default=str(data_dir / "songs.db"), help="aktive songs.db (Default: Datenordner der App)")
    ap.add_argument("--labels", default=str(data_dir / "labels.db"), help="labels.db (GVL-Namen zum Code)")
    ap.add_argument("--date", default=date.today().isoformat(), help="date_verified / Datei-Stempel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("extract", help="Lieferung (xlsx oder CSV) -> schlanker CSV-Extrakt")
    x.add_argument("--src", "--xlsx", dest="src", required=True, help="Lieferdatei (xlsx oder CSV; das Format bestimmt der Leser aus --library)")
    x.add_argument("--library", required=True, help="Leser: cezame, upm oder ripcue")
    x.add_argument("--out", default=None, help="Ziel-CSV (Default: data/library_tracks/<library>_<JJJJ-MM-TT>.csv)")
    i = sub.add_parser("import", help="CSV-Extrakt -> songs.db + library_files")
    i.add_argument("csv")
    a = ap.parse_args(argv)
    if a.cmd == "extract":
        return cmd_extract(Path(a.src).expanduser(), a.library.lower(), Path(a.out) if a.out else None, a.date)
    return cmd_import(Path(a.csv), Path(a.songs).expanduser(), Path(a.labels).expanduser(), a.date)


if __name__ == "__main__":
    sys.exit(main())
