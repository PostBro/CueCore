"""Laufzeittest der Oberfläche: Dev-Server mit Temp-Datenordner, Chrome headless per CDP.

Vom Inline-Script wurde sonst nur LOC() ausgeführt, der Rest nur auf
Syntax und Textstellen geprüft. Laufzeitfehler landen in catch-Blöcken und erscheinen als „Backend nicht
erreichbar." oder „–" in der Rail (so eine LOC()-Rekursion, die erst in der App auffiel). Hier wird
jeder Tab in DE und EN geladen, je Sprache einmal über den Knopf in der Rail gewechselt und danach jeder Tab
angeklickt (Treiber: webui_runtime_cdp.mjs). Rot bei jeder Exception, auch einer verschluckten (der Debugger
pausiert auf allen), bei jedem console.error und jeder Fehlerzeile im Log; außerdem müssen Startseite und
Rail die Zahlen aus /api/health im Format der Sprache zeigen. Die Zahlenprüfung ist nötig: einen Stack-Überlauf
wie bei LOC() meldet V8 dem Debugger nicht, rot wird der Test dann über „—" in der Rail.

Der Datenordner entsteht aus den getrackten Quellen wie in build_label_db.py (GVL-Master -> labels.db), die
songs.db seedet die App beim Start selbst aus data/songs.db; build_data/ wird nicht gebraucht. Ohne Library-
Import (CUECORE_SKIP_LIBRARY_IMPORT wie in conftest), ohne Track-Index, ohne Netz. Ohne Chrome oder node: skip."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

import pytest

from core.labeldb import build_label_db
from reconcile.csv_io import read_master_with_supplemental

APP_DIR = Path(__file__).resolve().parent.parent
MASTER = APP_DIR.parent / "data" / "GVL_Labelcodes_synced.csv"
DRIVER = Path(__file__).resolve().parent / "webui_runtime_cdp.mjs"
TOOLS = ("home", "resolve", "tagger", "edl", "db", "gvl", "settings")


def _chrome() -> str | None:
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    return None


CHROME = _chrome()
pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert"),
    pytest.mark.skipif(CHROME is None, reason="Chrome/Chromium nicht gefunden"),
]
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 nie über einen System-Proxy


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stop(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(10)


@pytest.fixture(scope="module")
def ui_run(tmp_path_factory):
    """Startet Server und Chrome, fährt den Treiber einmal, liefert (Health, Treiber-Ergebnis)."""
    tmp = tmp_path_factory.mktemp("webui-runtime")
    data = tmp / "data"
    meta = json.loads(MASTER.with_suffix(".meta.json").read_text(encoding="utf-8"))
    build_label_db(read_master_with_supplemental(MASTER), data / "labels.db", meta["source_version"])

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, CUECORE_DB=str(data / "labels.db"), CUECORE_SETTINGS=str(data / "settings.json"),
               CUECORE_SKIP_LIBRARY_IMPORT="1")
    log = (tmp / "server.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-c", "import uvicorn,sys; sys.path.insert(0,'.'); "
                               f"uvicorn.run('app:app', host='127.0.0.1', port={port}, log_level='warning')"],
        cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    chrome = None
    try:
        health = None
        for _ in range(300):
            if server.poll() is not None:
                pytest.fail(f"Server beendet: {(tmp / 'server.log').read_text(encoding='utf-8')}")
            try:
                health = json.loads(_http.open(f"{base}/api/health", timeout=1).read())
                break
            except OSError:
                time.sleep(0.1)
        assert health and health["ready"], f"Server nicht bereit: {health}"

        profile = tmp / "chrome"
        chrome = subprocess.Popen(
            [CHROME, "--headless=new", f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run",
             "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
             "--disable-sync", "--disable-component-update", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = profile / "DevToolsActivePort"
        for _ in range(300):
            if active.exists() and active.read_text().strip():
                break
            time.sleep(0.1)
        devport = active.read_text().split()[0]

        r = subprocess.run(["node", str(DRIVER), base, devport], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"Treiber abgebrochen:\n{r.stderr}"
        yield health, json.loads(r.stdout)
    finally:
        if chrome is not None:
            _stop(chrome)
        _stop(server)
        log.close()


def _fmt(n: int, lang: str) -> str:
    s = f"{n:,}"
    return s.replace(",", ".") if lang == "de" else s


def test_jeder_tab_in_beiden_sprachen_ohne_exception_und_console_error(ui_run):
    _, res = ui_run
    # Dieselbe Exception kommt bei jedem Seitenaufruf wieder: zusammenfassen, mit Zahl und erstem Ort.
    grouped = Counter((e["kind"], e["text"].split("\n")[0]) for e in res["errors"])
    first = {}
    for e in res["errors"]:
        first.setdefault((e["kind"], e["text"].split("\n")[0]), e)
    assert not grouped, "\n".join(f"{n}x {k}: {t}\n   zuerst bei {first[(k, t)]['where']}:\n   {first[(k, t)]['text']}"
                                  for (k, t), n in grouped.items())


def test_rail_und_startseite_zeigen_zahlen_je_sprache(ui_run):
    health, res = ui_run
    steps = res["steps"]
    assert {(s["lang"], s["tool"], s["via"]) for s in steps} >= {(lang, tool, "load") for lang in ("de", "en")
                                                                 for tool in TOOLS}
    assert {(s["lang"], s["via"]) for s in steps if s["via"] != "load"} == {("en", "switch"), ("en", "click"),
                                                                            ("de", "switch"), ("de", "click")}
    problems = []
    for s in steps:
        lang, where = s["lang"], s["where"]
        rows, songs = _fmt(health["rows"], lang), _fmt(health["songs"], lang)
        y, m, d = health["source_version"].split("-")                # GVL-Stand je Sprache als Datum
        ver = f"{d}.{m}.{y}" if lang == "de" else f"{d}/{m}/{y}"
        if s["shown"] != [f"tool-{s['tool']}"]:
            problems.append(f"{where}: sichtbar {s['shown']}")
        if s["langbtn"] != ("EN" if lang == "de" else "DE"):
            problems.append(f"{where}: Sprachknopf zeigt {s['langbtn']!r}, Sprache {lang} nicht aktiv")
        rail = s["rail"]
        if (rail["ver"], rail["rows"], rail["songs"]) != (ver, rows, songs):
            problems.append(f"{where}: Rail {rail}, erwartet {ver} / {rows} / {songs}")
        if s["tool"] == "home" and not (any(rows in r for r in s["home"]) and any(songs in r for r in s["home"])):
            problems.append(f"{where}: Startseite ohne {rows} / {songs}: {s['home']}")
    assert not problems, "\n".join(problems)


def test_elemente_mit_hidden_bleiben_unsichtbar(ui_run):
    """Unter der Aktionsleiste des Track-Datenbank-Editors stand ein leerer Kasten: .autobox{display:flex} schlug
    das hidden-Attribut (Regeln der Seite gehen den Regeln des Browsers vor, egal wie spezifisch). Geprüft wird
    jeder Schritt des Treibers und der geöffnete Editor: kein Element mit hidden darf gerendert werden."""
    _, res = ui_run
    assert res["editor"]["opened"], "Editor der Track-Datenbank nicht geöffnet (Doppelklick auf die erste Zeile)"
    shown = {s["where"]: s["hiddenShown"] for s in res["steps"] if s["hiddenShown"]}
    if res["editor"]["hiddenShown"]:
        shown["Editor der Track-Datenbank"] = res["editor"]["hiddenShown"]
    assert not shown, "\n".join(f"{w}: {els}" for w, els in shown.items())
