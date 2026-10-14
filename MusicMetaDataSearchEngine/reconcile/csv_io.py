"""CSV lesen/schreiben. labelcode bleibt String (führende Nullen)."""
from __future__ import annotations

import csv
from pathlib import Path

from core.models import COLUMNS, LabelRecord
from core.normalize import normalize_labelcode


def read_supplemental(path: str | Path, existing_codes: set[str]) -> list[LabelRecord]:
    """Zusatz-Labelcodes aus externen Quellen (CSV: labelcode,label,source) als LabelRecords.

    Diese Codes sind NICHT GVL-doppelt-verifiziert — der GVL-Master GEWINNT immer: Codes,
    die schon in `existing_codes` (Master) stehen, werden übersprungen. Die Herkunft landet
    als Provenance im `hersteller`-Feld (‚Zusatzquelle: …'), sichtbar in der Label-Suche, damit
    solche Einträge von den GVL-verifizierten unterscheidbar bleiben. Fehlt die Datei -> []."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[LabelRecord] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = normalize_labelcode(row.get("labelcode", ""))
            label = (row.get("label", "") or "").strip()
            src = (row.get("source", "") or "").strip()
            if not code or not label or code in existing_codes or code in seen:
                continue
            seen.add(code)
            out.append(LabelRecord(labelcode=code, label=label, kurzname="", plm="",
                                   hersteller=f"Zusatzquelle: {src}" if src else "Zusatzquelle",
                                   rechterueckrufe=""))
    return out


def read_master_with_supplemental(master_csv: str | Path) -> list[LabelRecord]:
    """GVL-Master + optionale `labelcodes_supplemental.csv` (Sibling) — GVL gewinnt.
    Einheitlicher Einstieg für App-DB-Bau (build_label_db.py) und run_cuecore-Fallback."""
    records = read_csv(master_csv)
    existing = {normalize_labelcode(r.labelcode) for r in records}
    supp = Path(master_csv).parent / "labelcodes_supplemental.csv"
    return records + read_supplemental(supp, existing)


def read_csv(path: str | Path) -> list[LabelRecord]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            LabelRecord(
                labelcode=row.get("labelcode", ""),
                label=row.get("label", "") or "",
                kurzname=row.get("kurzname", "") or "",
                plm=row.get("plm", "") or "",
                hersteller=row.get("hersteller", "") or "",
                rechterueckrufe=row.get("rechterueckrufe", "") or "",
            )
            for row in reader
        ]


def write_csv(path: str | Path, records: list[LabelRecord]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for rec in records:
            writer.writerow(rec.as_row())
