"""Oberfläche in node ausführen: web/index.html mit kleinem DOM-Ersatz (tests/webui_dom.js) laden,
Inline-Skript laufen lassen, dann ein Szenario (Body einer async-Funktion im Seitenkontext).

    out = run_page("click('.nav button[data-tool=home]'); await settle(); return texts('#hometiles .tile');",
                   lang="en", storage={"cuecore_recent_folders": "[...]"}, api={"/api/health": {...}})
    out["result"], out["errors"], out["fetches"]

Kein jsdom, keine Pakete. Im Szenario verfügbar: alle Top-Level-Namen der Seite ($, t, LANG, setLang …)
plus settle(), sleep(ms), qa(sel), txt(sel), texts(sel), click(sel), fire(sel, typ, init),
visibleText(), attrTexts() und __api (Antwort-Tabelle, im Szenario änderbar)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
INDEX = HERE.parent / "web" / "index.html"
DOM_JS = HERE / "webui_dom.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")


# Antworten, die die Seite beim Start immer abfragt (Export-Vorlagen, Projektliste); ein Szenario überschreibt sie.
DEFAULT_API = {
    "/api/export-templates": {"templates": [{"id": "standard", "name": "Standard", "required": ["labelcode", "komponist", "gema_werknummer"],
                                             "columns": ["Interpret", "Titel", "Komponist", "Texter", "Verlag", "Label", "LC", "Rechterückruf",
                                                         "ISRC", "ISWC", "GEMA-Werknr.", "Länge Sek."]}],
                              "active": "standard", "always": "Länge Sek.",
                              "columns": ["Interpret", "Titel", "Komponist", "Texter", "Verlag", "Label", "LC", "Rechterückruf",
                                          "ISRC", "ISWC", "GEMA-Werknr.", "Länge Sek."],
                              "required": ["labelcode", "komponist", "gema_werknummer", "texter", "verlag", "isrc", "iswc"]},
    "/api/projects": {"projects": []},
    "POST /api/projects": {"id": "abcdef123456", "name": "Testprojekt", "updated": "2026-10-23T10:00:00+00:00", "summary": {}},
}


def run_page(scenario: str = "return null;", *, lang: str | None = None, storage: dict | None = None,
             search: str = "", api: dict | None = None, confirm: bool = True, width: int = 1400,
             timeout: int = 30) -> dict:
    cfg = {"index": str(INDEX), "scenario": scenario, "lang": lang, "storage": storage or {},
           "search": search, "api": {**DEFAULT_API, **(api or {})}, "confirm": confirm, "width": width}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(cfg, f)
    try:
        r = subprocess.run(["node", str(DOM_JS), f.name], capture_output=True, text=True, timeout=timeout)
    finally:
        Path(f.name).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def i18n_en_source() -> str:
    """Quelltext des Objektliterals I18N_EN aus index.html (für statische Prüfungen)."""
    src = INDEX.read_text(encoding="utf-8")
    m = re.search(r"^const I18N_EN = \{\n(.*?)^\};", src, re.S | re.M)
    assert m, "const I18N_EN = { … }; nicht gefunden"
    return m.group(1)
