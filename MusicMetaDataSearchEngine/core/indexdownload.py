"""Track-Index aus der App herunterladen und einrichten, ohne dass Dateien von Hand verschoben werden müssen.

Lädt das Archiv aus dem Release (tar.xz mit genau einer Datei track_index.db, Discogs- und MusicBrainz-Daten, CC0),
prüft die SHA-256, entpackt nur diese eine Datei neben die übrigen Datenbanken und tauscht sie erst am Ende atomar
ein. songs.db (eigene Tracks) wird nie angefasst; der Index ist eine eigene Datei und wird bei der Suche erst nach
der Track-Datenbank gefragt. Bei Fehler oder Abbruch bleibt ein vorhandener Index stehen, Teildateien werden
gelöscht. tar.xz, weil Python (tarfile/lzma), das macOS-Archivprogramm und Windows 11 es ohne Zusatz entpacken.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tarfile
from pathlib import Path
from typing import Callable, Iterable

URL = "https://github.com/PostBro/CueCore/releases/download/v0.9.1-beta/CueCore_Track-Index_2026-09.tar.xz"
SHA256 = "4724dd373ab619da3f054056eb4ae00d836c83348070387b99113e9cef9f9d84"
SIZE = 1_258_964_296          # Archiv
UNPACKED = 6_496_002_048      # track_index.db entpackt
STAND = "2026-09"
MEMBER = "track_index.db"
CHUNK = 1 << 20
RESERVE = 300_000_000         # Luft über Archiv + entpackte Datei hinaus
USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"


class DownloadError(Exception):
    """Für die Oberfläche formulierter Grund (deutsch)."""


class Cancelled(Exception):
    pass


LEFTOVERS = ("track_index.download.tar.xz", "track_index.db.part")


def cleanup(data_dir: str | Path) -> list[str]:
    """Reste eines abgebrochenen Downloads löschen (App mitten im Laden oder Entpacken beendet: der Thread stirbt mit
    dem Prozess, sein finally läuft nicht; es blieben bis zu 3,3 GB liegen). Nur aufrufen, wenn kein Download läuft."""
    removed = []
    for name in LEFTOVERS:
        try:
            (Path(data_dir) / name).unlink()
            removed.append(name)
        except FileNotFoundError:
            pass
    return removed


def _http_chunks(url: str) -> Iterable[bytes]:
    import httpx
    with httpx.stream("GET", url, follow_redirects=True, headers={"User-Agent": USER_AGENT},
                      timeout=httpx.Timeout(30.0, read=120.0)) as r:
        r.raise_for_status()
        yield from r.iter_bytes(CHUNK)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f}".replace(".", ",")


def install(data_dir: str | Path, *, chunks: Callable[[str], Iterable[bytes]] = _http_chunks, url: str = URL,
            sha256: str = SHA256, size: int = SIZE, unpacked: int = UNPACKED,
            progress: Callable[[str, int, int], None] = lambda phase, done, total: None,
            cancel=None, before_replace: Callable[[], None] = lambda: None) -> Path:
    """Herunterladen ("download"), entpacken ("extract"), eintauschen. Gibt den Pfad des Index zurück."""
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    target, archive, part = d / MEMBER, d / LEFTOVERS[0], d / LEFTOVERS[1]
    free = shutil.disk_usage(d).free
    need = size + unpacked + RESERVE
    if free < need:
        raise DownloadError(f"Zu wenig freier Platz: {_gb(free)} GB frei, rund {_gb(need)} GB nötig.")

    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise Cancelled()

    try:
        h, done = hashlib.sha256(), 0
        progress("download", 0, size)
        with open(archive, "wb") as f:
            for chunk in chunks(url):
                check_cancel()
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress("download", done, size)
        if h.hexdigest() != sha256:
            raise DownloadError("Prüfsumme stimmt nicht, der Download ist unvollständig oder beschädigt. "
                                "Bitte erneut versuchen.")
        # Stream-Modus, ein Durchgang: tf.getmember() entpackte die 6,5 GB erst komplett, um den Eintrag zu finden,
        # und danach ein zweites Mal zum Schreiben; die Anzeige stand so lange auf „fertig geladen“.
        progress("extract", 0, unpacked)
        with tarfile.open(archive, mode="r|xz") as tf:
            m = src = None
            for m in tf:
                if m.name == MEMBER and m.isfile():
                    src = tf.extractfile(m)
                    break
            if src is None:
                raise DownloadError("Im Archiv fehlt track_index.db.")
            done = 0
            with open(part, "wb") as out:
                while True:
                    check_cancel()
                    b = src.read(CHUNK)
                    if not b:
                        break
                    out.write(b)
                    done += len(b)
                    progress("extract", done, m.size)
        con = sqlite3.connect(f"{part.as_uri()}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1 FROM tracks LIMIT 1").fetchall()
        except sqlite3.DatabaseError as e:
            raise DownloadError(f"Entpackte Datei ist kein Track-Index ({e}).") from None
        finally:
            con.close()
        before_replace()
        os.replace(part, target)
        return target
    finally:
        for p in (archive, part):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
