# Lizenzen

## CueCore

Copyright (C) 2026 POSTBRO

CueCore ist freie Software: Du kannst es unter den Bedingungen der GNU Affero General Public License, Version 3,
wie von der Free Software Foundation veröffentlicht, weitergeben und/oder verändern. Volltext: [LICENSE](LICENSE).
CueCore wird ohne jede Gewährleistung bereitgestellt, siehe dort.

## In den Apps enthaltene Komponenten

Die Apps für Mac und Windows bündeln Python und die folgenden Bibliotheken. Lizenzangaben laut Paket-Metadaten
der verwendeten Versionen (`MusicMetaDataSearchEngine/requirements.txt`); die Lizenztexte liegen den Paketen bei
und sind bei den Projekten abrufbar.

| Komponente | Version | Lizenz |
|---|---|---|
| Python | 3.12 | Python Software Foundation License |
| PyInstaller (Bootloader) | 6.22.3 | GPL-2.0-or-later mit Ausnahme für gebündelte Programme |
| PyMuPDF / MuPDF | 1.27.2.3 | GNU AGPL-3.0 (oder kommerzielle Lizenz von Artifex) |
| mutagen | 1.48.1 | GPL-2.0-or-later |
| pywebview | 6.2.1 | BSD-3-Clause |
| pyobjc (nur Mac) | 12.2.2 | MIT |
| pythonnet (nur Windows, über pywebview) | – | MIT |
| bottle, proxy_tools (über pywebview) | 0.13.4, 0.1.0 | MIT |
| FastAPI | 0.138.2 | MIT |
| Starlette | 1.6.0 | BSD-3-Clause |
| Uvicorn | 0.49.0 | BSD-3-Clause |
| pydantic, pydantic-core | 2.13.5, 2.46.5 | MIT |
| httpx, httpcore | 0.28.1, 1.0.9 | BSD-3-Clause |
| anyio, h11 | 4.15.1, 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| annotated-types, annotated-doc, typing-inspection | – | MIT |
| openpyxl, et_xmlfile | 3.1.5, 2.0.0 | MIT |
| xlrd | 2.0.1 | BSD |
| watchdog | 6.0.0 | Apache-2.0 |

**fpcalc (Chromaprint 1.6.1).** Offizielles Release von [acoustid/chromaprint](https://github.com/acoustid/chromaprint),
unverändert mitgeliefert (Mac) bzw. beim Windows-Build aus demselben Release geladen und per SHA-256 geprüft
(Herkunft und Prüfsummen: `MusicMetaDataSearchEngine/bin/README.md`). fpcalc enthält FFmpeg. Lizenzen und Quelltext:
Chromaprint-Repository und [ffmpeg.org](https://ffmpeg.org/legal.html).

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
  Lieferungen von Production-Music-Libraries, u.a. Cezame). Die Rechte an diesen Metadaten liegen bei den
  jeweiligen Libraries.
- **Offline-Track-Index (optionaler Download):** `CueCore_Track-Index_2026-09.tar.xz` beruht auf dem
  Discogs-Datendump vom 01.09.2026 (CC0) und den Kerndaten der MusicBrainz-Datenbank (CC0), gefiltert auf
  Veröffentlichungen mit einem Labelcode aus dem GVL-Master. Gebaut mit `build_track_index.py`.
- **Netzabfragen:** Ergebnisse von MusicBrainz, Discogs, Deezer, Wikidata, iTunes, Genius, AcoustID, AudD,
  ACRCloud und editingtools.io unterliegen den Bedingungen der jeweiligen Dienste.
