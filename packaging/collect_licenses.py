#!/usr/bin/env python3
"""Lizenztexte aller installierten Python-Pakete der Build-Umgebung in eine Datei sammeln (THIRD-PARTY-LICENSES.txt).

Läuft im Build (CI) mit demselben Python, das PyInstaller nutzt; die Spec legt die Datei in jede App.

    python packaging/collect_licenses.py --out build_data/THIRD-PARTY-LICENSES.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from importlib import metadata
from pathlib import Path

LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", re.I)
SKIP = {"pip", "wheel"}                       # Werkzeuge der Umgebung, nicht in der App

EXTRA = """\
==== Python (Laufzeit) ====
Python Software Foundation License Version 2. Volltext: https://docs.python.org/3/license.html
Die Laufzeit bringt mit: OpenSSL (Apache-2.0), SQLite (Public Domain), libffi (MIT), xz/liblzma (0BSD),
zlib (zlib-Lizenz), bzip2 (bzip2-Lizenz), expat (MIT), mpdecimal (BSD-2-Clause).

==== MuPDF (in PyMuPDF) ====
GNU AGPL-3.0, Artifex Software. Enthält u.a. FreeType (FreeType-Lizenz), HarfBuzz (MIT), jbig2dec (AGPL-3.0),
lcms2 (MIT), libjpeg (IJG), OpenJPEG (BSD-2-Clause), zlib, Gumbo (Apache-2.0), Brotli (MIT),
Tesseract (Apache-2.0), Leptonica (BSD-2-Clause). Quelltext: https://mupdf.com/releases

==== fpcalc (Chromaprint 1.6.1) ====
Chromaprint: MIT. Enthält FFmpeg: LGPL-2.1-or-later. Quelltext: https://github.com/acoustid/chromaprint und
https://ffmpeg.org/download.html

==== Nur Windows ====
Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core.dll, über pywebview): Microsoft-Lizenz des WebView2 SDK.
Microsoft Visual C++ Runtime / Universal CRT: von Microsoft zur Weitergabe freigegebene Laufzeitbibliotheken.
"""


def _license_name(md) -> str:
    lic = md.get("License-Expression") or ""
    if not lic:
        lic = (md.get("License") or "").strip().splitlines()[0] if md.get("License") else ""
    if not lic or len(lic) > 80:
        cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = "; ".join(cls) or lic[:80]
    return lic or "siehe Lizenztext"


def collect() -> str:
    parts = ["Drittlizenzen der in CueCore gebündelten Komponenten", "=" * 53, ""]
    dists = sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower())
    seen = set()
    for d in dists:
        name = d.metadata["Name"] or ""
        if not name or name.lower() in SKIP or name.lower() in seen:
            continue
        seen.add(name.lower())
        parts.append(f"==== {name} {d.version} ({_license_name(d.metadata)}) ====")
        texts = []
        for f in d.files or []:
            if LICENSE_FILE.search(str(f)):
                try:
                    texts.append(Path(d.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip())
                except OSError:
                    pass
        parts.append("\n\n".join(texts) if texts else "(kein Lizenztext im Paket; Lizenz siehe Kopfzeile)")
        parts.append("")
    parts.append(EXTRA)
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(collect(), encoding="utf-8")
    print(f"{out}: {out.stat().st_size // 1024} KB, Python {sys.version.split()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
