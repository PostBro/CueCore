"""Die Oberfläche ist ein einziger <script>-Block in web/index.html — ein Tippfehler in einem String
killt die ganze App stumm (ein gerades Anführungszeichen in einem deutschen Satz beendete einmal
den String mitten im Satz, die Seite lud, aber kein Knopf tat noch etwas). Deshalb hier ein
Syntax-Check des Blocks."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"
SCRIPTS = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def test_index_html_hat_genau_einen_inline_script_block():
    blocks = SCRIPTS.findall(INDEX.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"{len(blocks)} Inline-Skripte — der Syntax-Check prüft alle, der Hinweis hier nur Info"


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
def test_inline_javascript_parst(tmp_path):
    blocks = SCRIPTS.findall(INDEX.read_text(encoding="utf-8"))
    assert blocks, "kein Inline-Skript in index.html gefunden — Regex stimmt nicht mehr"
    for i, code in enumerate(blocks):
        f = tmp_path / f"block{i}.js"
        f.write_text(code, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"Block {i} parst nicht:\n{r.stderr}"
