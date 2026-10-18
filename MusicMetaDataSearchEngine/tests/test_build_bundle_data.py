"""build_bundle_data: die eingebackenen DBs für den App-Build (labels_prebuilt.db + songs.db mit Seed und
allen Library-Lieferungen). Läuft gegen die Repo-Daten (Cezame-CSV, ~3 s)."""
import sqlite3

import build_bundle_data


def test_build_writes_labels_and_full_songs_db(tmp_path):
    # alte labels_manual.json aus einem früheren Build (mit den gelieferten Codes LC21110/LC26766) darf nicht mitgehen
    (tmp_path / "bd").mkdir()
    (tmp_path / "bd" / "labels_manual.json").write_text('[{"labelcode": "21110", "label": "BBC Production Music"}]',
                                                         encoding="utf-8")
    rep = build_bundle_data.build(tmp_path / "bd", stamp="2026-09-21", quiet=True)
    labels = sqlite3.connect(tmp_path / "bd" / "labels_prebuilt.db")
    assert labels.execute("SELECT COUNT(*) FROM labels").fetchone()[0] > 85000
    songs = sqlite3.connect(tmp_path / "bd" / "songs.db")
    n = songs.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    assert n > 60000 and rep["songs"] == n                                   # Seed + Cezame (60.938)
    assert songs.execute("SELECT COUNT(*) FROM library_files WHERE library='cezame'").fetchone()[0] > 60000
    assert songs.execute("SELECT COUNT(*) FROM songs WHERE quelle='library:cezame'").fetchone()[0] > 59000
    assert not (tmp_path / "bd" / "track_index.db").exists()               # der Index gehört nie ins Bundle
    # die UPM-Lieferung bringt BBC Production Music mit LC21110 — im Bundle steht der GVL-Code LC27926,
    # der gelieferte als Notiz; ein manuelles Label 21110 entsteht nicht mehr
    assert songs.execute("SELECT DISTINCT labelcode, label_geliefert FROM library_files "
                         "WHERE labelname='BBC Production Music'").fetchall() == [("LC27926", "BBC Production Music · LC21110")]
    assert songs.execute("SELECT COUNT(*) FROM library_files WHERE labelcode IN ('LC21110','LC26766')").fetchone()[0] == 0
    manual = tmp_path / "bd" / "labels_manual.json"
    assert not manual.exists() or "21110" not in manual.read_text(encoding="utf-8")


def test_build_is_idempotent(tmp_path):
    build_bundle_data.build(tmp_path / "bd", stamp="2026-09-21", quiet=True)
    rep2 = build_bundle_data.build(tmp_path / "bd", stamp="2026-09-21", quiet=True)
    songs = sqlite3.connect(tmp_path / "bd" / "songs.db")
    assert songs.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == rep2["songs"]


def test_build_stops_when_library_deliveries_are_missing(tmp_path, monkeypatch):
    """Ist data/library_tracks leer (etwa versehentlich gelöscht), darf der Build nicht mit einer unvollständigen
    songs.db durchlaufen."""
    import pytest
    monkeypatch.setattr(build_bundle_data, "PROJECT", tmp_path)          # tmp_path/data/library_tracks fehlt
    with pytest.raises(SystemExit, match="Keine Library-Lieferung"):
        build_bundle_data.build(tmp_path / "bd", quiet=True)


def test_build_stops_when_one_tracked_delivery_is_missing(tmp_path, monkeypatch):
    """Der Stopp gilt nicht nur für einen leeren Ordner: fehlt eine einzelne im Git geführte Lieferung
    (hier cezame_2026-09.csv), bricht der Build ab."""
    import shutil
    import subprocess
    import pytest
    if not shutil.which("git"):
        pytest.skip("git fehlt")
    lt = tmp_path / "data" / "library_tracks"
    lt.mkdir(parents=True)
    for name in ("cezame_2026-09.csv", "upm_2026-10.csv", "upm_2026-10-archiv.csv"):
        (lt / name).write_text("file\n", encoding="utf-8")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    run("init", "-q"); run("add", "data"); run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    assert build_bundle_data.missing_deliveries(tmp_path) == []
    (lt / "cezame_2026-09.csv").unlink()                                   # Lieferung fehlt im Arbeitsordner
    assert build_bundle_data.missing_deliveries(tmp_path) == ["data/library_tracks/cezame_2026-09.csv"]
    monkeypatch.setattr(build_bundle_data, "PROJECT", tmp_path)
    with pytest.raises(SystemExit, match="cezame_2026-09.csv"):
        build_bundle_data.build(tmp_path / "bd", quiet=True)
    assert not (tmp_path / "bd").exists()                                  # geprüft, bevor irgendetwas gebaut wird


def test_spec_prueft_dieselben_lieferungen():
    """cuecore.spec prüft dieselben Lieferungen wie build_bundle_data, nicht nur „mindestens eine CSV"."""
    spec = (build_bundle_data.PROJECT / "cuecore.spec").read_text(encoding="utf-8")
    assert "missing_deliveries" in spec


def test_eingebauter_key_nur_aus_der_umgebung_und_verschleiert(tmp_path, monkeypatch):
    """Der AcoustID-Key steht nicht im Repo: build_bundle_data schreibt ihn nur, wenn CUECORE_ACOUSTID_APP_KEY gesetzt
    ist, verschleiert nach builtin_keys.bin; ohne Variable bleibt keine alte Datei liegen."""
    from core import settings
    out = tmp_path / "bd"
    monkeypatch.delenv("CUECORE_TRACKMETA_PROFILE", raising=False)
    monkeypatch.setenv("CUECORE_ACOUSTID_APP_KEY", "zzqBuildKey")
    build_bundle_data.write_builtin_keys(out)
    blob = (out / "builtin_keys.bin").read_bytes()
    assert b"zzqBuildKey" not in blob and settings.decode_builtin(blob) == {"acoustid_api_key": "zzqBuildKey"}
    monkeypatch.delenv("CUECORE_ACOUSTID_APP_KEY")
    build_bundle_data.write_builtin_keys(out)
    assert not (out / "builtin_keys.bin").exists()


def test_trackmeta_profil_nur_aus_der_umgebung_verschleiert_und_geprueft(tmp_path, monkeypatch):
    """Das Profil der Track-Metadata-API (conftest.py: erfundene Werte) landet verschleiert in builtin_keys.bin;
    ein kaputtes Profil bricht den Build ab, statt eine App ohne funktionierende Abfrage auszuliefern."""
    import json
    import os
    import pytest
    from core import settings
    out = tmp_path / "bd"
    monkeypatch.delenv("CUECORE_ACOUSTID_APP_KEY", raising=False)
    raw = os.environ["CUECORE_TRACKMETA_PROFILE"]
    build_bundle_data.write_builtin_keys(out)
    blob = (out / "builtin_keys.bin").read_bytes()
    assert b"/zzq/" not in blob and settings.decode_builtin(blob) == {"trackmeta_profile": raw}
    monkeypatch.setenv("CUECORE_TRACKMETA_PROFILE", json.dumps({"base": "/zzq"}))
    with pytest.raises(SystemExit):
        build_bundle_data.write_builtin_keys(out)


def test_ausgelieferte_dbs_ohne_freie_seiten(tmp_path):
    """Gelöschte Reste stehen in freien Seiten und wären im Bundle lesbar: beide DBs nach VACUUM ohne freelist."""
    build_bundle_data.build(tmp_path / "bd", stamp="2026-01-01", quiet=True)
    for name in ("labels_prebuilt.db", "songs.db"):
        con = sqlite3.connect(tmp_path / "bd" / name)
        assert con.execute("PRAGMA freelist_count").fetchone()[0] == 0, name
        con.close()
