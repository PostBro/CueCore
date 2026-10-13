# CueCore aus dem Quellcode starten

Voraussetzung: Python 3.12.

```bash
cd MusicMetaDataSearchEngine
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_cuecore.py
```

Beim ersten Start baut CueCore die Label-Datenbank aus `data/GVL_Labelcodes_synced.csv`. Mit `CUECORE_WINDOW=0` öffnet sich statt des App-Fensters ein Browser-Tab. Tests: `.venv/bin/python -m pytest`. Die Apps für Mac und Windows entstehen mit PyInstaller aus `cuecore.spec`, Schritt für Schritt in [docs/PACKAGING.md](PACKAGING.md); Windows baut der Workflow in `.github/workflows/build-windows.yml`.

Für die normale Nutzung braucht es das alles nicht: App aus dem [Download](../README.md#download) entpacken und starten.
