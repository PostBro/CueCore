# Lizenzen

## CueCore

Copyright (C) 2026 POSTBRO

CueCore ist freie Software: Du kannst es unter den Bedingungen der GNU Affero General Public License, Version 3,
wie von der Free Software Foundation veröffentlicht, weitergeben und/oder verändern. Volltext: [LICENSE](LICENSE).
CueCore wird ohne jede Gewährleistung bereitgestellt, siehe dort.

## In den Apps enthaltene Komponenten

Die Apps für Mac und Windows werden bei GitHub aus genau diesem Quellcode gebaut (`.github/workflows/`) und bündeln
Python und die folgenden Bibliotheken. Jede App enthält außerdem `LICENSE` (AGPL-3.0), diese Datei und
`THIRD-PARTY-LICENSES.txt` mit den Lizenztexten aller gebündelten Pakete, beim Build aus der Build-Umgebung gesammelt
(`packaging/collect_licenses.py`). Versionen wie im Build von Beta 0.9.7.

| Komponente | Version | Lizenz |
|---|---|---|
| Python (mit OpenSSL, SQLite, libffi, xz, zlib, bzip2, expat, mpdecimal) | 3.12 | Python Software Foundation License (Bestandteile: Apache-2.0, Public Domain, MIT, 0BSD, zlib, bzip2, MIT, BSD-2-Clause) |
| PyInstaller (Bootloader) | 6.22.3 | GPL-2.0-or-later mit Ausnahme für gebündelte Programme |
| PyMuPDF / MuPDF (mit FreeType, HarfBuzz, jbig2dec, lcms2, libjpeg, OpenJPEG, Gumbo, Brotli, Tesseract, Leptonica) | 1.27.2.3 | GNU AGPL-3.0 (Bestandteile unter ihren eigenen Lizenzen, siehe THIRD-PARTY-LICENSES.txt) |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| pywebview | 6.2.1 | BSD-3-Clause |
| pyobjc (nur Mac) | 12.2.2 | MIT |
| pythonnet, clr_loader (nur Windows) | 3.2.1, 0.3.1 | MIT |
| cffi, pycparser (nur Windows) | 2.1.1, 3.11 | MIT, BSD-3-Clause |
| Microsoft Edge WebView2 SDK, Visual C++ Runtime/UCRT (nur Windows) | – | Lizenzen von Microsoft zur Weitergabe |
| bottle, proxy_tools (über pywebview) | 0.13.4, 0.1.0 | MIT |
| FastAPI | 0.138.2 | MIT |
| Starlette | 1.8.0 | BSD-3-Clause |
| Uvicorn | 0.49.0 | BSD-3-Clause |
| pydantic, pydantic-core | 2.14.1, 2.50.1 | MIT |
| httpx, httpcore | 0.28.1, 1.0.9 | BSD-3-Clause |
| anyio, h11 | 4.15.1, 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| annotated-types, annotated-doc, typing-inspection | 0.8.0, 0.0.5, 0.4.4 | MIT |
| openpyxl, et_xmlfile | 3.1.5, 2.0.0 | MIT |
| xlrd | 2.0.1 | BSD-3-Clause |
| pygments, packaging, setuptools (Teile, über Abhängigkeiten) | 2.21.0, 26.3, 84.0.0 | BSD-2-Clause, Apache-2.0 oder BSD-2-Clause, MIT |

**fpcalc (Chromaprint 1.6.1).** Offizielles Release von [acoustid/chromaprint](https://github.com/acoustid/chromaprint).
Chromaprint steht unter MIT, das enthaltene FFmpeg unter LGPL-2.1-or-later; Quelltext beider Projekte:
[Chromaprint](https://github.com/acoustid/chromaprint), [ffmpeg.org](https://ffmpeg.org/download.html). In der Mac-App
liegt nur der Apple-Silicon-Teil des Universal-Binarys, beim Bauen ad-hoc neu signiert; die Prüfsumme in
`MusicMetaDataSearchEngine/bin/README.md` gilt für die Originaldatei. Windows lädt fpcalc beim Build aus demselben
Release und prüft es per SHA-256.

## Icons

Einige Symbole der Oberfläche folgen den Pfaden von [Feather Icons](https://github.com/feathericons/feather) (MIT,
Copyright Cole Bemis) und [Lucide](https://github.com/lucide-icons/lucide) (ISC, Lucide Icons and Contributors).
Lizenztexte: `MusicMetaDataSearchEngine/web/ICONS-LICENSE.txt`, in den Apps unter `web/`.

## Schriften

Saira Condensed (Omnibus-Type) und Inter (Rasmus Andersson), beide unter der SIL Open Font License 1.1, lokal
gebündelt in `MusicMetaDataSearchEngine/web/fonts/` (siehe `LICENSE.txt` dort).

## Daten

- **Labelcodes:** `data/GVL_Labelcodes_synced.csv` beruht auf der Labelcode-Liste der GVL (Gesellschaft zur
  Verwertung von Leistungsschutzrechten), PDF-Export der GVL-Labelrecherche, Stand 13.10.2026. Verbindlich ist
  immer die Labelrecherche der GVL.
- **Zusatz-Labelcodes und Schreibvarianten:** `data/labelcodes_supplemental.csv`, `data/library_prefixes.csv`,
  `data/label_aliases_mb.csv` (Label-Aliasse aus MusicBrainz, CC0), jeweils nur Codes, die der GVL-Master kennt
  oder deren Herkunft in der Datei vermerkt ist.
- **Tracks:** `MusicMetaDataSearchEngine/data/songs.db` (eigener Korpus) und `data/library_tracks/` (Metadaten aus
  Lieferungen von Production-Music-Libraries, u.a. APL Music, Cezame und RipCue Music). Die Rechte an diesen Metadaten liegen bei den
  jeweiligen Libraries.
- **Offline-Track-Index (optionaler Download):** `CueCore_Track-Index_2026-09-r2.tar.xz` beruht auf dem
  Discogs-Datendump vom 01.09.2026 (CC0) und den Kerndaten der MusicBrainz-Datenbank (CC0), gefiltert auf
  Veröffentlichungen mit einem Labelcode aus dem GVL-Master. Gebaut mit `build_track_index.py`.
- **Netzabfragen:** Ergebnisse von MusicBrainz, Discogs, Deezer, Wikidata, iTunes, Genius, AcoustID, AudD,
  ACRCloud und editingtools.io unterliegen den Bedingungen der jeweiligen Dienste.
