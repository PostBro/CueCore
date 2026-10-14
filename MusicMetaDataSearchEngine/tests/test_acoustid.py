"""AcoustID-Fingerprinting.

Kein Netz, kein fpcalc: Fingerprinter und HTTP werden eingespeist. Die Antwortform stammt aus der
offiziellen Doku (acoustid.org/webservice): status/results[]/score/id/recordings[]/artists[].
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from core import acoustid

# Antwort wie in der Doku: ein Ergebnis mit Score, darin die Aufnahme mit Interpret und Titel.
OK = {"status": "ok", "results": [
    {"id": "9ff43b6a-4f16-427c-93c2-92307ca505e0", "score": 0.98, "recordings": [
        {"id": "b2b3b2a1-0000-4000-8000-000000000001", "title": "Dancing in the Smoke",
         "artists": [{"id": "a1", "name": "GIVEON"}],
         "releases": [{"id": "r1", "title": "Beloved"}],
         "isrcs": ["USUM72400001"]}]},
    {"id": "schwach", "score": 0.11, "recordings": [
        {"id": "x", "title": "Falsch", "artists": [{"name": "Niemand"}]}]}]}


def _runner(stdout: str, code: int = 0):
    return lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr="")


def test_fingerprint_liest_fpcalc_json(tmp_path, monkeypatch):
    exe = tmp_path / "fpcalc"
    exe.write_text("#!/bin/sh\n"); exe.chmod(0o755)
    monkeypatch.setenv("CUECORE_FPCALC", str(exe))
    out = json.dumps({"duration": 213.48, "fingerprint": "AQADtEmi..."})
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=_runner(out)) == (213, "AQADtEmi...")



def test_fingerprint_trotz_lesefehler_am_dateiende(tmp_path, monkeypatch):
    """fpcalc 1.6.1 rechnet den Fingerabdruck fertig, meldet aber Exit 3
    („Error reading from the audio source"), wenn ein Frame kaputt ist. Eine Option
    zum Ignorieren hat fpcalc 1.6.1 nicht; liegt gültiges JSON vor, wird es genommen."""
    exe = tmp_path / "fpcalc"; exe.write_text("x"); exe.chmod(0o755)
    monkeypatch.setenv("CUECORE_FPCALC", str(exe))
    out = json.dumps({"duration": 349.36, "fingerprint": "AQADlomS"})
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=_runner(out, 3)) == (349, "AQADlomS")


def test_lookup_trennt_meta_mit_leerzeichen():
    """Per POST (Formular) kommt „recordings+releases+isrcs" als ein Wert mit buchstäblichen
    Pluszeichen an; AcoustID erkennt den Track dann, liefert aber keine Aufnahme.
    Mit Leerzeichen (im Formular als + kodiert) kommen die Aufnahmen zurück."""
    gesendet = {}
    acoustid.lookup("FP", 210, "KEY", poster=lambda p: gesendet.update(p) or {"status": "ok"})
    assert gesendet["meta"].split() == ["recordings", "releases", "isrcs"] and "+" not in gesendet["meta"]

def test_fingerprint_ohne_fpcalc_oder_bei_fehler(tmp_path, monkeypatch):
    # „kein fpcalc": weder gesetzt, noch im Repo, noch im PATH
    monkeypatch.setenv("CUECORE_FPCALC", str(tmp_path / "gibtsnicht"))
    monkeypatch.setattr(acoustid, "fpcalc_path", lambda: None)
    assert acoustid.fingerprint(tmp_path / "a.mp3") is None            # uebersprungen, kein Fehler
    monkeypatch.undo()

    exe = tmp_path / "fpcalc"; exe.write_text("x"); exe.chmod(0o755)
    monkeypatch.setenv("CUECORE_FPCALC", str(exe))
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=_runner("", 1)) is None       # Exit != 0
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=_runner("kein json")) is None
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=_runner('{"duration":0,"fingerprint":""}')) is None

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("fpcalc", 1)
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=boom) is None


def test_fingerprint_ohne_konsolenfenster_unter_windows(tmp_path, monkeypatch):
    """Im fensterlosen Windows-Build blitzt sonst je Datei eine Konsole auf (wie folderpick)."""
    import sys
    exe = tmp_path / "fpcalc.exe"; exe.write_text("x")
    monkeypatch.setenv("CUECORE_FPCALC", str(exe))
    gesehen = {}

    def runner(cmd, **kw):
        gesehen.update(kw)
        return SimpleNamespace(returncode=0, stdout='{"duration": 5, "fingerprint": "FP"}', stderr="")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert acoustid.fingerprint(tmp_path / "a.mp3", runner=runner) == (5, "FP")
    assert gesehen.get("creationflags") == subprocess.CREATE_NO_WINDOW
    gesehen.clear()
    monkeypatch.setattr(sys, "platform", "darwin")
    acoustid.fingerprint(tmp_path / "a.mp3", runner=runner)
    assert "creationflags" not in gesehen


def test_lookup_schickt_die_pflichtfelder():
    gesehen = {}

    def poster(params):
        gesehen.update(params)
        return OK
    assert acoustid.lookup("FP", 213, "KEY", poster=poster) is OK
    assert gesehen == {"client": "KEY", "duration": 213, "fingerprint": "FP",
                       "meta": acoustid.META, "format": "json"}
    assert acoustid.lookup("FP", 213, "", poster=poster) is None       # ohne Schluessel kein Netz
    assert acoustid.lookup("", 213, "KEY", poster=poster) is None


def test_parse_nimmt_den_besten_treffer():
    r = acoustid.parse_result(OK)
    assert r["artist"] == "GIVEON" and r["title"] == "Dancing in the Smoke"
    assert r["album"] == "Beloved" and r["isrc"] == "USUM72400001"
    assert r["score"] == 0.98 and r["recording_mbid"].startswith("b2b3b2a1")
    assert r["label"] == ""                                            # AcoustID kennt kein Label


def test_parse_verwirft_schwache_und_leere_treffer():
    assert acoustid.parse_result(None) is None
    assert acoustid.parse_result({"status": "error"}) is None
    assert acoustid.parse_result({"status": "ok", "results": []}) is None
    nur_schwach = {"status": "ok", "results": [OK["results"][1]]}
    assert acoustid.parse_result(nur_schwach) is None                  # 0.11 unter der Schwelle
    assert acoustid.parse_result(nur_schwach, min_score=0.1)["title"] == "Falsch"
    # Aufnahme ohne Interpret wird uebersprungen, die naechste zaehlt
    gemischt = {"status": "ok", "results": [{"id": "a", "score": 0.9, "recordings": [
        {"id": "1", "title": "Ohne Interpret"},
        {"id": "2", "title": "Mit", "artists": [{"name": "Wer"}]}]}]}
    assert acoustid.parse_result(gemischt)["title"] == "Mit"


def test_parse_verbindet_interpreten_mit_der_joinphrase():
    """Mehrere Interpreten in der Schreibweise des Credits („Queen & David Bowie“), nicht „Queen; David
    Bowie“. Die AcoustID-API liefert `joinphrase` an einem Interpreten, wenn MusicBrainz eine führt;
    fehlt sie, bleibt „; “ der Trenner."""
    def antwort(artists):
        return {"status": "ok", "results": [{"id": "a", "score": 0.9, "recordings": [
            {"id": "1", "title": "Under Pressure", "artists": artists}]}]}
    r = acoustid.parse_result(antwort([{"name": "Queen", "joinphrase": " & "}, {"name": "David Bowie"}]))
    assert r["artist"] == "Queen & David Bowie"
    r = acoustid.parse_result(antwort([{"name": "A", "joinphrase": " feat. "}, {"name": "B", "joinphrase": " & "},
                                       {"name": "C"}]))
    assert r["artist"] == "A feat. B & C"
    assert acoustid.parse_result(antwort([{"name": "A"}, {"name": "B"}]))["artist"] == "A; B"
    assert acoustid.parse_result(antwort([{"name": "GIVEON"}]))["artist"] == "GIVEON"


def test_recognize_liefert_dasselbe_format_wie_audd():
    r = acoustid.recognize("egal.mp3", "KEY", expected_artist="GIVEON", expected_title="Dancing in the Smoke",
                           fingerprinter=lambda p: (213, "FP"), poster=lambda params: OK)
    assert set(("artist", "title", "album", "label", "isrc", "date", "possible_cover")) <= set(r)
    assert r["possible_cover"] is False                                # erwartet == gefunden
    assert r["duration_sec"] == 213
    cover = acoustid.recognize("egal.mp3", "KEY", expected_artist="Jemand Anders", expected_title="Anderer Titel",
                               fingerprinter=lambda p: (213, "FP"), poster=lambda params: OK)
    assert cover["possible_cover"] is True
    assert acoustid.recognize("egal.mp3", "", fingerprinter=lambda p: (213, "FP")) is None
    assert acoustid.recognize("egal.mp3", "KEY", fingerprinter=lambda p: None) is None


def test_kette_fragt_erst_acoustid_dann_audd(monkeypatch):
    """Reihenfolge nach Kosten: AcoustID ist frei, AudD kostet pro Anfrage."""
    import app as appmod
    gerufen = []
    monkeypatch.setattr(appmod.settings, "get", lambda k, *a, **kw: {"acoustid_api_key": "K", "audd_token": "T"}.get(k, ""))
    monkeypatch.setattr(appmod.acoustid, "recognize",
                        lambda *a, **k: gerufen.append("acoustid") or {"artist": "A", "title": "B"})
    monkeypatch.setattr(appmod.audd, "recognize", lambda *a, **k: gerufen.append("audd") or {"artist": "C", "title": "D"})
    hit = appmod._fingerprint_recognize("x.mp3", "", "")
    assert gerufen == ["acoustid"] and hit["fingerprint_source"] == "acoustid"

    gerufen.clear()
    monkeypatch.setattr(appmod.acoustid, "recognize", lambda *a, **k: gerufen.append("acoustid") or None)
    hit = appmod._fingerprint_recognize("x.mp3", "", "")
    assert gerufen == ["acoustid", "audd"] and hit["fingerprint_source"] == "audd"

    gerufen.clear()
    monkeypatch.setattr(appmod.settings, "get", lambda k, *a, **kw: {"audd_token": "T"}.get(k, ""))
    appmod._fingerprint_recognize("x.mp3", "", "")
    assert gerufen == ["audd"]                                         # ohne AcoustID-Key gar kein Versuch


def test_mitgeliefertes_fpcalc_rechnet_einen_fingerabdruck(tmp_path, monkeypatch):
    """Das mitgelieferte Binary muss ohne systemweit installiertes ffmpeg laufen.
    Quelle und Pruefsumme: bin/README.md."""
    import shutil as _sh
    import subprocess as _sp
    from pathlib import Path
    import sys
    monkeypatch.delenv("CUECORE_FPCALC", raising=False)
    # Binary der laufenden Plattform: im Windows-Checkout liegt das Mac-fpcalc auch, gesucht wird dort aber
    # fpcalc.exe (sonst läuft der Test gegen das falsche Binary).
    name = "fpcalc.exe" if sys.platform.startswith("win") else "fpcalc"
    exe = Path(acoustid.__file__).resolve().parent.parent / "bin" / name
    if not exe.is_file():
        import pytest
        pytest.skip(f"bin/{name} nicht im Repo")
    assert acoustid.fpcalc_path() == str(exe)
    assert _sp.run([str(exe), "-version"], capture_output=True, text=True).stdout.startswith("fpcalc version")
    if not _sh.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg fehlt — keine Testdatei erzeugbar")
    ton = tmp_path / "ton.mp3"
    _sp.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
             "-ac", "2", "-ar", "44100", "-y", str(ton)], check=True)
    got = acoustid.fingerprint(ton)
    assert got and got[0] == 12 and len(got[1]) > 20


def test_settings_view_nennt_den_fpcalc_pfad(tmp_path, monkeypatch):
    """Ohne fpcalc findet AcoustID nichts — die Oberflaeche soll den Grund zeigen, nicht schweigen."""
    from fastapi.testclient import TestClient
    import app as appmod
    from pathlib import Path
    d = TestClient(appmod.app).get("/api/settings").json()
    assert "fpcalc" in d
    assert d["fpcalc"] == "" or Path(d["fpcalc"]).name in ("fpcalc", "fpcalc.exe")
    exe = tmp_path / "fpcalc.exe"; exe.write_text("x")            # so heisst es unter Windows
    monkeypatch.setenv("CUECORE_FPCALC", str(exe))
    d = TestClient(appmod.app).get("/api/settings").json()
    assert d["fpcalc"] == str(exe)
