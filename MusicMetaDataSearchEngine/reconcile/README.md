# GVL Reconcile

Gleicht die lokale GVL-CSV gegen das offizielle GVL-PDF ab und baut einen
PDF-basierten Master. Idempotent, wiederholbar (wöchentlicher Re-Download trivial).

## Lauf

```bash
.venv/bin/python gvl_sync.py                            # dünner CLI-Einstieg, nutzt ../GVL-Labelcodes.pdf + ../data/GVL_Labelcodes_synced.csv
.venv/bin/python gvl_sync.py --pdf /pfad/zum/neuen.pdf  # anderes/neueres PDF
.venv/bin/python build_label_db.py                      # danach: labels.db aus dem neuen Master neu bauen
```

`gvl_sync.py` ist nur ein dünner Wrapper um `python -m reconcile.cli` (gleiche Argumente).

## Outputs (im Projekt-Root, eine Ebene über dem Code-Repo)
- `data/GVL_Labelcodes_synced.csv` — PDF-basierter Master (PDF gewinnt).
- `data/GVL_Labelcodes_synced.meta.json` — source_version (PDF-Kopf-Datum) + row_count.
- `data/archive/GVL_Labelcodes_synced_<alte-version>.csv` — Vorgänger-Stand, VOR dem Überschreiben automatisch gesichert (idempotent: existiert die Archiv-Kopie schon, wird sie nicht erneut geschrieben). Ohne das wäre jeder Lauf ein stiller, irreversibler Overwrite des Masters.
- `data/diff_<version>.csv` — maschinenlesbarer Diff (NEU/ENTFERNT/GEAENDERT).
- `docs/reconcile_report_<version>.md` — menschenlesbarer Report mit GEÄNDERT-Klassifizierung.

## Parsing-Strategie
pymupdf, Spaltenzuordnung über X-Anker (linkester Anker ≤ x0). Datensatz beginnt
am Labelcode-Token und läuft offen weiter — auch über Seitengrenzen. Letter-Spacing
(`G L I S S A N D O`) wird bei Runs von ≥3 Einzelzeichen zusammengezogen; 2-Zeichen-
und Symbol-Fälle bleiben unangetastet (zu mehrdeutig) und landen im Report unter
`spacing_only` zur manuellen Prüfung.

## Tests
```bash
.venv/bin/python -m pytest -q
```
Parser-Tests laufen gegen ein generiertes Fixture-PDF mit echtem Spaltenlayout
(mehrzeiliger Umbruch, seitenübergreifender Datensatz, PLM, R-im-Firmennamen).

## Wichtig
- `labelcode` ist IMMER String (führende Nullen). Nie als Integer parsen.
- ENTFERNT-Codes nicht blind löschen — der Master ist PDF-basiert, sie fehlen dort schlicht; Report listet sie zur Prüfung.
