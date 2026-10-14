"""Track-Index aus der App herunterladen (Nutzer:innen sollen nur die App starten, ohne Dateien zu verschieben)."""
import hashlib
import io
import shutil
import sqlite3
import tarfile
import threading

import pytest

from core import indexdownload


def _archive(tmp_path, rows=3, name="track_index.db"):
    """Kleines tar.xz wie im Release: eine Datei track_index.db mit Tabelle tracks."""
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, interpret TEXT)")
    con.executemany("INSERT INTO tracks (interpret) VALUES (?)", [(f"A{i}",) for i in range(rows)])
    con.commit(); con.close()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        tf.add(db, arcname=name)
    data = buf.getvalue()
    return data, hashlib.sha256(data).hexdigest(), db.stat().st_size


def _chunks(data, size=1000):
    return lambda url: (data[i:i + size] for i in range(0, len(data), size))


def test_install_laedt_prueft_entpackt_und_laesst_songs_db_in_ruhe(tmp_path):
    data, sha, unpacked = _archive(tmp_path)
    d = tmp_path / "daten"; d.mkdir()
    (d / "songs.db").write_bytes(b"eigene Tracks")
    seen = []
    out = indexdownload.install(d, chunks=_chunks(data), sha256=sha, size=len(data), unpacked=unpacked,
                                progress=lambda phase, done, total: seen.append(phase))
    assert out == d / "track_index.db"
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3
    assert (d / "songs.db").read_bytes() == b"eigene Tracks"               # eigene Tracks unberührt
    assert sorted(p.name for p in d.iterdir()) == ["songs.db", "track_index.db"]   # keine Reste
    assert "download" in seen and "extract" in seen


def test_falsche_pruefsumme_laesst_vorhandenen_index_stehen(tmp_path):
    data, _sha, unpacked = _archive(tmp_path)
    d = tmp_path / "daten"; d.mkdir()
    (d / "track_index.db").write_bytes(b"alter Index")
    with pytest.raises(indexdownload.DownloadError, match="Prüfsumme"):
        indexdownload.install(d, chunks=_chunks(data), sha256="0" * 64, size=len(data), unpacked=unpacked)
    assert (d / "track_index.db").read_bytes() == b"alter Index"
    assert sorted(p.name for p in d.iterdir()) == ["track_index.db"]


def test_abbrechen_raeumt_auf(tmp_path):
    data, sha, unpacked = _archive(tmp_path)
    d = tmp_path / "daten"; d.mkdir()
    cancel = threading.Event()

    def chunks(url):
        yield data[:10]
        cancel.set()
        yield data[10:]
    with pytest.raises(indexdownload.Cancelled):
        indexdownload.install(d, chunks=chunks, sha256=sha, size=len(data), unpacked=unpacked, cancel=cancel)
    assert list(d.iterdir()) == []


def test_zu_wenig_platz_bricht_vor_dem_download_ab(tmp_path, monkeypatch):
    d = tmp_path / "daten"; d.mkdir()
    monkeypatch.setattr(indexdownload.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(0, 0, 1_000_000_000))
    called = []
    with pytest.raises(indexdownload.DownloadError, match="Platz"):
        indexdownload.install(d, chunks=lambda url: called.append(url) or iter(()), size=1_300_000_000,
                              unpacked=6_500_000_000)
    assert called == []


def test_archiv_ohne_track_index_wird_abgelehnt(tmp_path):
    data, sha, unpacked = _archive(tmp_path, name="etwas_anderes.db")
    d = tmp_path / "daten"; d.mkdir()
    with pytest.raises(indexdownload.DownloadError):
        indexdownload.install(d, chunks=_chunks(data), sha256=sha, size=len(data), unpacked=unpacked)
    assert list(d.iterdir()) == []


def test_before_replace_laeuft_vor_dem_tausch(tmp_path):
    """Die App schließt dort ihre offene Index-Verbindung (Windows sperrt sonst die Datei)."""
    data, sha, unpacked = _archive(tmp_path)
    d = tmp_path / "daten"; d.mkdir()
    order = []
    indexdownload.install(d, chunks=_chunks(data), sha256=sha, size=len(data), unpacked=unpacked,
                          before_replace=lambda: order.append((d / "track_index.db").exists()))
    assert order == [False]



def test_entpackt_in_einem_durchgang_und_meldet_das_sofort(tmp_path, monkeypatch):
    """tarfile.getmember würde die 6,5 GB erst komplett entpacken, um den Eintrag zu finden, und dann ein zweites
    Mal zum Schreiben; die Anzeige bliebe minutenlang stehen. Deshalb Stream-Modus (ein Durchgang), und
    „extract“ wird gemeldet, bevor das Archiv geöffnet wird."""
    data, sha, unpacked = _archive(tmp_path)
    d = tmp_path / "daten"; d.mkdir()
    modes, phases, real_open = [], [], tarfile.open
    def spy_open(*a, **kw):
        modes.append(kw.get("mode", a[1] if len(a) > 1 else ""))
        phases.append(("open", None))
        return real_open(*a, **kw)
    monkeypatch.setattr(indexdownload.tarfile, "open", spy_open)
    indexdownload.install(d, chunks=_chunks(data), sha256=sha, size=len(data), unpacked=unpacked,
                          progress=lambda ph, done, total: phases.append((ph, done)))
    assert modes == ["r|xz"]
    assert phases.index(("extract", 0)) < phases.index(("open", None))


def test_cleanup_entfernt_reste_eines_abgebrochenen_downloads(tmp_path):
    """Wird die App mitten im Entpacken beendet, bleiben Reste (Archiv + .part) liegen; cleanup räumt sie weg."""
    (tmp_path / "track_index.download.tar.xz").write_bytes(b"x")
    (tmp_path / "track_index.db.part").write_bytes(b"y")
    (tmp_path / "track_index.db").write_bytes(b"bleibt")
    (tmp_path / "songs.db").write_bytes(b"bleibt")
    assert sorted(indexdownload.cleanup(tmp_path)) == ["track_index.db.part", "track_index.download.tar.xz"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["songs.db", "track_index.db"]
    assert indexdownload.cleanup(tmp_path) == []
