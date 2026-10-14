"""LOC() liefert das Locale für toLocaleString/toLocaleDateString in web/index.html.

Ein globales Ersetzen von 'de-DE' durch LOC() traf einmal auch die
Definition selbst — `LANG === 'en' ? 'en-GB' : LOC()` rief sich auf Deutsch (Default) endlos auf.
Jede Zahl warf „Maximum call stack size exceeded": Startseite „Backend nicht erreichbar.", in der
Rail nur „–" bei Labelcodes, Tracks, Track-Index. Auf Englisch fiel es nicht auf, deshalb hier
beide Sprachen."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"
LOC_DEF = re.compile(r"^const LOC\s*=\s*\(\)\s*=>\s*(.+);\s*$", re.MULTILINE)


def _loc_def() -> re.Match:
    m = LOC_DEF.search(INDEX.read_text(encoding="utf-8"))
    assert m, "const LOC = () => …; nicht gefunden — Regex an die Definition anpassen"
    return m


def test_loc_ruft_sich_nicht_selbst_auf():
    body = _loc_def().group(1)
    assert "LOC(" not in body, f"LOC() ruft sich selbst auf: {body}"
    assert "'de-DE'" in body and "'en-GB'" in body, body


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
@pytest.mark.parametrize("lang,locale,zahl", [
    ("de", "de-DE", "17.037.079"),
    ("en", "en-GB", "17,037,079"),
])
def test_loc_liefert_locale_je_sprache(tmp_path, lang, locale, zahl):
    f = tmp_path / "loc.js"
    f.write_text(f"let LANG = {json.dumps(lang)};\n{_loc_def().group(0)}\n"
                 "console.log(JSON.stringify([LOC(), (17037079).toLocaleString(LOC())]));\n", encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [locale, zahl]
