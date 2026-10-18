"""packaging/collect_licenses.py: Lizenztexte der Build-Umgebung für THIRD-PARTY-LICENSES.txt."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    spec = importlib.util.spec_from_file_location("collect_licenses", ROOT / "packaging" / "collect_licenses.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_collect_enthaelt_gebundelte_pakete_und_lizenztexte(tmp_path):
    m = _mod()
    out = tmp_path / "THIRD-PARTY-LICENSES.txt"
    assert m.main(["--out", str(out)]) == 0
    t = out.read_text(encoding="utf-8")
    assert "==== mutagen " in t and "==== fastapi " in t.lower()
    assert "GNU GENERAL PUBLIC LICENSE" in t          # mutagen bringt den GPL-Text mit
    assert "==== fpcalc (Chromaprint 1.6.1) ====" in t and "LGPL-2.1-or-later" in t
    assert "==== pip " not in t


def test_spec_legt_lizenzdateien_in_die_app():
    spec = (ROOT / "cuecore.spec").read_text(encoding="utf-8")
    assert "THIRD-PARTY-LICENSES.txt" in spec and '"LICENSE", "LIZENZEN.md"' in spec
