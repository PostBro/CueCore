"""Packaging: Windows-Workflow, cuecore.spec und PACKAGING.md passen zusammen (fpcalc für AcoustID,
macOS-Mindestversion). Reine Quelltext-Prüfung — nichts wird gebaut oder heruntergeladen."""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows.yml"
SPEC = ROOT / "cuecore.spec"
FPCALC_ZIP = ("https://github.com/acoustid/chromaprint/releases/download/v1.6.1/"
              "chromaprint-fpcalc-1.6.1-windows-x86_64.zip")


def _steps() -> list[dict]:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build-windows"]["steps"]


def _step_with(steps: list[dict], needle: str) -> int:
    hits = [i for i, s in enumerate(steps) if needle in s.get("run", "").replace("\\", "/")]
    assert hits, f"kein Workflow-Schritt mit {needle!r}"
    return hits[0]


def _spec_fpcalc_path(*, win: bool) -> str:
    """Pfad, unter dem cuecore.spec fpcalc erwartet — aus der Spec ausgewertet, nicht abgeschrieben."""
    ns = {"Path": Path, "WIN": win}
    for node in ast.parse(SPEC.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("ENGINE", "_fpcalc"):
            exec(compile(ast.Module([node], []), str(SPEC), "exec"), ns)
    return ns["_fpcalc"].as_posix()


def test_windows_workflow_parses_and_starts_only_by_hand():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))                     # YAML 1.1 liest den Schlüssel on als True
    assert set(on) == {"workflow_dispatch"}             # WINDOWS_BUILD.md: kein Build bei jedem Push


def test_windows_workflow_fetches_fpcalc_with_pinned_sha256_before_the_build():
    """Die Spec sucht fpcalc.exe unter bin/, also muss der Workflow es vor dem Build laden (sonst fehlt AcoustID unter Windows)."""
    steps = _steps()
    fetch, build = _step_with(steps, FPCALC_ZIP), _step_with(steps, "pyinstaller --noconfirm")
    assert fetch < build
    script = steps[fetch]["run"]
    sha = re.search(r"\b[0-9a-f]{64}\b", script)
    assert sha and "Get-FileHash" in script
    assert _spec_fpcalc_path(win=True) in script.replace("\\", "/")
    readme = (ROOT / "MusicMetaDataSearchEngine" / "bin" / "README.md").read_text(encoding="utf-8")
    assert sha.group(0) in readme                        # dieselbe Prüfsumme wie im README


def test_windows_workflow_stops_when_fpcalc_is_missing_from_the_build():
    """Die Spec warnt nur (lokale Builds ohne fpcalc) — im Workflow muss ein fehlendes fpcalc.exe abbrechen."""
    steps = _steps()
    build = _step_with(steps, "pyinstaller --noconfirm")
    assert _step_with(steps, "dist/CueCore/_internal/bin/fpcalc.exe") > build


def _doc_section(text: str, heading: str) -> str:
    """Abschnitt ab '## <heading>' bis zur nächsten '## '-Überschrift."""
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def test_windows_docs_name_the_release_the_workflow_publishes():
    """Hauptweg ist das Pre-Release, das Artefakt nur der zweite Weg: der Windows-Abschnitt in PACKAGING.md muss
    das Release nennen. Der Tag wird aus dem Workflow gelesen, nicht abgeschrieben."""
    steps = _steps()
    tag = re.search(r"gh release upload (\S+)", steps[_step_with(steps, "gh release upload")]["run"]).group(1)
    packaging = _doc_section((ROOT / "docs" / "PACKAGING.md").read_text(encoding="utf-8"), "Windows")
    assert f"`{tag}`" in packaging
    assert tag in (ROOT / "WINDOWS_BUILD.md").read_text(encoding="utf-8")


# Steht im Code, wird aber nie angesprochen: nur Kontaktangabe im User-Agent der AcoustID-Anfrage.
NOT_CONTACTED = {"postbro.de", "github.com"}   # Projektseite im User-Agent, kein Abruf


def _code_hosts() -> set[str]:
    """Jeder Host, der in app.py oder core/*.py als http(s)-Adresse steht (Abrufe UND Links)."""
    engine = ROOT / "MusicMetaDataSearchEngine"
    hosts: set[str] = set()
    for p in [engine / "app.py", *sorted((engine / "core").glob("*.py"))]:
        hosts |= set(re.findall(r"https?://([a-z0-9.-]+\.[a-z]{2,})", p.read_text(encoding="utf-8")))
    return hosts - NOT_CONTACTED


def test_firewall_hint_names_every_host_in_the_code():
    """Jeder Host aus dem Code steht im Firewall-Hinweis, angesprochen oder als Link, der im Standardbrowser aufgeht."""
    doc = (ROOT / "docs" / "PACKAGING.md").read_text(encoding="utf-8")
    hint = doc[doc.index("**Firewall-Hinweis"):]
    hint = hint[:hint.index("\n## ")]
    missing = sorted(h for h in _code_hosts() if f"`{h}`" not in hint)
    assert not missing, f"im Firewall-Hinweis fehlen: {missing}"


def test_macos_minimum_in_info_plist_matches_packaging_doc():
    """Die Mindestversion in der Info.plist (cuecore.spec) und die Angabe in PACKAGING.md nennen dieselbe macOS-Version."""
    plist = re.search(r'"LSMinimumSystemVersion":\s*"(\d+)\.', SPEC.read_text(encoding="utf-8")).group(1)
    doc = re.search(r"macOS (\d+) oder neuer", (ROOT / "docs" / "PACKAGING.md").read_text(encoding="utf-8"))
    assert plist == doc.group(1)
