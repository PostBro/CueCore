"""Merge: PDF gewinnt. Erzeugt den PDF-basierten Master + Diff-CSV + Report."""
from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

from core.models import LabelRecord
from core.normalize import normalize_record
from .diff import DiffReport

# Codes mit auffälligen Zeilen, die der Abgleich im Bericht einzeln ausweist.
EDGE_CASES = ["05205", "15507", "33780", "44566", "70306", "71848", "71851", "94248"]


def build_synced(pdf_records: list[LabelRecord]) -> list[LabelRecord]:
    """PDF-basierter Master, normalisiert, stabil nach (labelcode, label) sortiert."""
    recs = [normalize_record(r) for r in pdf_records]
    recs.sort(key=lambda r: (r.labelcode, r.label))
    return recs


def archive_previous_master(csv_path: str | Path, meta_path: str | Path,
                            archive_dir: str | Path) -> str | None:
    """Vor dem Überschreiben: bisherigen Master unter SEINER EIGENEN Quell-Version
    archivieren (data/archive/GVL_Labelcodes_synced_<version>.csv). Ohne das wäre
    jeder reconcile-Lauf ein stiller, irreversibler Overwrite des Masters.

    Idempotent: schon archivierte Versionen werden übersprungen (gleicher Stand
    kann beliebig oft re-synct werden, ohne die Archiv-Kopie zu duplizieren).
    Gibt den archivierten Pfad zurück, oder None (erster Lauf / kein Vorgänger).
    """
    csv_path, meta_path, archive_dir = Path(csv_path), Path(meta_path), Path(archive_dir)
    if not csv_path.exists() or not meta_path.exists():
        return None
    with open(meta_path, encoding="utf-8") as f:
        old_version = json.load(f).get("source_version", "unknown")
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"GVL_Labelcodes_synced_{old_version}.csv"
    if not dest.exists():
        shutil.copy(csv_path, dest)
    return str(dest)


def write_meta(path: str | Path, source_version: str, row_count: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"source_version": source_version, "row_count": row_count, "source": "GVL all-labelcodes.pdf"},
            f,
            ensure_ascii=False,
            indent=2,
        )


def write_diff_csv(path: str | Path, rep: DiffReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["status", "labelcode", "label", "kurzname", "plm", "hersteller", "rechterueckrufe"])
        for r in rep.neu:
            w.writerow(["NEU", r.labelcode, r.label, r.kurzname, r.plm, r.hersteller, r.rechterueckrufe])
        for r in rep.entfernt:
            w.writerow(["ENTFERNT", r.labelcode, r.label, r.kurzname, r.plm, r.hersteller, r.rechterueckrufe])
        for lc, csv_recs, pdf_recs in rep.geaendert:
            for r in csv_recs:
                w.writerow(["GEAENDERT_ALT", r.labelcode, r.label, r.kurzname, r.plm, r.hersteller, r.rechterueckrufe])
            for r in pdf_recs:
                w.writerow(["GEAENDERT_NEU", r.labelcode, r.label, r.kurzname, r.plm, r.hersteller, r.rechterueckrufe])


def _fmt(r: LabelRecord) -> str:
    return f"`{r.labelcode}` · {r.label} · kurz={r.kurzname or '–'} · plm={r.plm or '–'} · {r.hersteller or '–'} · rr={r.rechterueckrufe or '–'}"


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


def classify_change(csv_recs: list[LabelRecord], pdf_recs: list[LabelRecord]) -> str:
    """GEÄNDERT-Eintrag einordnen: plm_only | spacing_only | substantive | multi."""
    if len(csv_recs) != 1 or len(pdf_recs) != 1:
        return "multi"
    a, n = csv_recs[0], pdf_recs[0]
    if a.label == n.label and a.kurzname == n.kurzname and a.hersteller == n.hersteller and a.rechterueckrufe == n.rechterueckrufe and a.plm != n.plm:
        return "plm_only"
    if (
        _nospace(a.label) == _nospace(n.label)
        and _nospace(a.kurzname) == _nospace(n.kurzname)
        and _nospace(a.hersteller) == _nospace(n.hersteller)
        and a.plm == n.plm
        and a.rechterueckrufe == n.rechterueckrufe
    ):
        return "spacing_only"
    return "substantive"


def write_report(path: str | Path, rep: DiffReport, pdf_records: list[LabelRecord]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    by_lc: dict[str, list[LabelRecord]] = {}
    for r in (normalize_record(x) for x in pdf_records):
        by_lc.setdefault(r.labelcode, []).append(r)

    lines: list[str] = []
    lines.append(f"# GVL Reconcile-Report — {rep.source_version}")
    lines.append("")
    lines.append("PDF (offiziell, maßgeblich) abgeglichen gegen den bisherigen Master (`data/GVL_Labelcodes_synced.csv`).")
    lines.append("")
    lines.append("## Zusammenfassung")
    lines.append(f"- Quell-Version (PDF-Kopf): **{rep.source_version}**")
    lines.append(f"- Datensätze PDF: **{rep.pdf_count}**")
    lines.append(f"- Datensätze CSV: **{rep.csv_count}**")
    lines.append(f"- NEU (im PDF, nicht in CSV): **{len(rep.neu)}**")
    lines.append(f"- GEÄNDERT (Labelcodes mit Feldabweichung): **{len(rep.geaendert)}**")
    lines.append(f"- ENTFERNT (in CSV, nicht mehr im PDF): **{len(rep.entfernt)}**")
    lines.append(f"- DUPLIKATE (echte 1:n LC→Label): **{len(rep.duplikate)}** Codes")
    lines.append("")

    lines.append("## Auffällige Codes (im PDF aufgelöst)")
    lines.append("")
    lines.append("| Labelcode | im PDF aufgelöst als |")
    lines.append("|---|---|")
    for lc in EDGE_CASES:
        recs = by_lc.get(lc)
        if recs:
            lines.append(f"| {lc} | " + "<br>".join(_fmt(r) for r in recs) + " |")
        else:
            lines.append(f"| {lc} | **nicht im PDF gefunden** |")
    lines.append("")

    lines.append("## DUPLIKATE (1:n LC→Label, gültig)")
    if rep.duplikate:
        lines.append("")
        lines.append("| Labelcode | Zeilen |")
        lines.append("|---|---|")
        for lc, n in sorted(rep.duplikate.items()):
            lines.append(f"| {lc} | {n} |")
    else:
        lines.append("\nKeine.")
    lines.append("")

    lines.append("## ENTFERNT (vorsichtig behandeln — nicht blind löschen)")
    if rep.entfernt:
        lines.append("")
        for r in rep.entfernt[:500]:
            lines.append(f"- {_fmt(r)}")
        if len(rep.entfernt) > 500:
            lines.append(f"- … und {len(rep.entfernt) - 500} weitere (s. diff-CSV)")
    else:
        lines.append("\nKeine.")
    lines.append("")

    # Klassifizierung der GEÄNDERT-Einträge
    buckets: dict[str, list] = {"plm_only": [], "spacing_only": [], "substantive": [], "multi": []}
    for lc, csv_recs, pdf_recs in rep.geaendert:
        buckets[classify_change(csv_recs, pdf_recs)].append((lc, csv_recs, pdf_recs))

    lines.append("## GEÄNDERT — Klassifizierung (PDF gewinnt)")
    lines.append("")
    lines.append(f"- **plm_only** (nur PLM-Flag ergänzt, echte Korrektur): {len(buckets['plm_only'])}")
    lines.append(f"- **substantive** (echte Inhaltsänderung): {len(buckets['substantive'])}")
    lines.append(f"- **spacing_only** (nur Whitespace — geklärt, s.u.): {len(buckets['spacing_only'])}")
    lines.append(f"- **multi** (1:n LC→Label, z.B. Ex-Duplikate): {len(buckets['multi'])}")
    lines.append("")

    lines.append("### spacing_only — geklärt (kein offener Fehler)")
    lines.append("")
    lines.append("Reine Letter-Spacing-Artefakte (`W a Z`→`WaZ`, ganze Zelle nur Einzelzeichen) "
                 "werden im Parser automatisch korrigiert. Die hier verbleibenden Einträge sind "
                 "**Symbol-Spacing** (`R & G`, `A + P`) = echte GVL-Schreibweise: belegt durch den "
                 "XLSX-Datenexport (Mehrwort-Labels tragen die Spaces) und gegengeprüft auf "
                 "musik-sammler.de (LC95009 = `R & G`). PDF-gewinnt ist korrekt; nichts zu tun.")
    lines.append("")
    for lc, csv_recs, pdf_recs in buckets["spacing_only"]:
        lines.append(f"- `{lc}`: alt `{csv_recs[0].label}|{csv_recs[0].kurzname}` → neu `{pdf_recs[0].label}|{pdf_recs[0].kurzname}`")
    lines.append("")

    lines.append("### substantive — echte Inhaltsänderungen (Auszug)")
    lines.append("")
    for lc, csv_recs, pdf_recs in buckets["substantive"][:300]:
        lines.append(f"#### `{lc}`")
        for r in csv_recs:
            lines.append(f"- alt: {_fmt(r)}")
        for r in pdf_recs:
            lines.append(f"- neu: {_fmt(r)}")
    if len(buckets["substantive"]) > 300:
        lines.append(f"\n… und {len(buckets['substantive']) - 300} weitere (s. diff-CSV)")
    lines.append("")

    lines.append("## NEU (Auszug, vollständig in diff-CSV)")
    lines.append("")
    for r in rep.neu[:200]:
        lines.append(f"- {_fmt(r)}")
    if len(rep.neu) > 200:
        lines.append(f"- … und {len(rep.neu) - 200} weitere")
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
