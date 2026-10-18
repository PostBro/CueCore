#!/usr/bin/env python3
"""Eingebackene Datenbanken für den App-Build (Mac-.app und Windows-Ordner).

  python build_bundle_data.py                 # -> ../build_data/
  python build_bundle_data.py --out <ordner>

Legt in <out> ab (die Spec bündelt den Ordner nach data/ im Bundle):
  labels_prebuilt.db  GVL-Master + Zusatz-Codes (build_label_db), Erststart kopiert sie nur noch
  songs.db            Repo-Seed (data/songs.db, verifizierte Tracks) + alle Library-Lieferungen aus
                      data/library_tracks/*.csv (Cezame: 60.938 Tracks): die gelieferten Libraries sind
                      ab dem ersten Start drin, kein Import beim Start nötig.
Nicht im Bundle: track_index.db (6,5 GB, Dump-Index) — bleibt im Datenordner des Nutzers.
Der Ordner ist ein Build-Artefakt (gitignored), reproduzierbar aus dem Repo.
"""
from __future__ import annotations
import argparse
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
PROJECT = ENGINE.parent
DEFAULT_OUT = PROJECT / "build_data"


def missing_deliveries(project: Path) -> list[str]:
    """Im Git geführte Lieferungs-CSVs (data/library_tracks/*.csv), die auf der Platte fehlen. Ohne git oder
    außerhalb eines Repos [] — dann bleibt nur die Prüfung „mindestens eine CSV". Seit mehr als eine Lieferung
    im Ordner liegt, reicht die allein nicht: fehlt eine davon, liefe der Build durch, und das Bundle
    trüge deutlich weniger Tracks."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(project), "ls-files", "--", "data/library_tracks/*.csv"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [f for f in out.splitlines() if f.strip() and not (project / f).exists()]


def write_builtin_keys(out: Path) -> bool:
    """Eingebaute Anwendungs-Schlüssel (core/settings.BUILTIN_ENV) aus der Umgebung verschleiert nach
    out/builtin_keys.bin — nie aus dem Repo. Ohne gesetzte Variable keine Datei (eine alte wird entfernt)."""
    import os
    from core import settings
    f = out / "builtin_keys.bin"
    keys = {k: os.environ[env].strip() for k, env in settings.BUILTIN_ENV.items() if os.environ.get(env, "").strip()}
    if "trackmeta_profile" in keys:
        from core import trackmeta
        if trackmeta.parse_profile(keys["trackmeta_profile"]) is None:   # kaputtes Profil nicht still ausliefern
            raise SystemExit(f"{settings.BUILTIN_ENV['trackmeta_profile']}: Profil unvollständig oder kein JSON")
    if f.exists():
        f.unlink()
    if not keys:
        return False
    out.mkdir(parents=True, exist_ok=True)
    f.write_bytes(settings.encode_builtin(keys))
    return True


def build(out: Path, *, stamp: str | None = None, quiet: bool = False) -> dict:
    import build_label_db
    import import_library_tracks
    from core import songsdb

    # Erst prüfen, dann bauen: die Lieferungen sind git-versioniert, ein leerer Ordner heißt „aus Versehen
    # gelöscht": ohne Stopp trüge das Bundle eine songs.db nur mit dem Seed, ohne dass es auffällt.
    csv_dir = PROJECT / "data" / "library_tracks"
    csvs = sorted(csv_dir.glob("*.csv")) if csv_dir.is_dir() else []
    if not csvs:
        raise SystemExit(f"Keine Library-Lieferung in {csv_dir} — erwartet mindestens eine CSV "
                         f"(git restore data/library_tracks/). Ohne sie trägt das Bundle nur den Repo-Seed.")
    missing = missing_deliveries(PROJECT)
    if missing:
        raise SystemExit(f"Library-Lieferung fehlt auf der Platte, im Git aber vorhanden: {', '.join(missing)} "
                         f"(git restore data/library_tracks/). Ohne sie fehlen ihre Tracks im Bundle.")

    out.mkdir(parents=True, exist_ok=True)
    labels = out / "labels_prebuilt.db"
    songs = out / "songs.db"
    # labels_manual.json mit: cmd_import legt sie neu an, wenn eine Lieferung fremde Codes trägt. Eine alte bliebe
    # sonst liegen und ginge mit ins Bundle (frühere Stände trugen dort LC21110/LC26766, vom GVL-Code überholt).
    for p in (labels, songs, out / "labels_manual.json"):
        if p.exists():
            p.unlink()
    rc = build_label_db.main(["--out", str(labels)])
    if rc != 0:
        raise SystemExit(rc)
    shutil.copy(ENGINE / "data" / "songs.db", songs)
    for csv in csvs:
        import_library_tracks.cmd_import(csv, songs, labels, stamp or date.today().isoformat())
    con = songsdb.open(songs)
    try:
        n = songsdb.count(con)
        files = con.execute("SELECT COUNT(*) FROM library_files").fetchone()[0]
        con.commit()
        con.execute("VACUUM")          # keine gelöschten Reste in der ausgelieferten Datei
    finally:
        con.close()
    lcon = sqlite3.connect(labels)
    try:
        lcon.execute("VACUUM")         # auch hier: keine gelöschten Reste (freie Seiten) in der ausgelieferten Datei
    finally:
        lcon.close()
    builtin_keys = write_builtin_keys(out)
    rep = {"out": str(out), "songs": n, "library_files": files, "builtin_keys": builtin_keys,
           "size_mb": round((labels.stat().st_size + songs.stat().st_size) / 1e6, 1)}
    if not quiet:
        print(f"build_data: {out} — songs.db {n:,} Tracks, {files:,} Library-Dateinamen, {rep['size_mb']} MB gesamt")
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    build(Path(a.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main())
