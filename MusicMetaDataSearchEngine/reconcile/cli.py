"""GVL-Reconcile-CLI. Idempotent, re-runnable.

    python -m reconcile.cli --pdf ../GVL-Labelcodes.pdf

Liest das offizielle GVL-PDF, gleicht gegen data/GVL_Labelcodes_synced.csv ab
(aktueller Master), schreibt Report (docs/),
Diff-CSV + synced-Master (data/). Re-Run gegen synced = Verify (Diff ~0).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .csv_io import read_csv, write_csv
from .diff import diff
from .merge import archive_previous_master, build_synced, write_diff_csv, write_meta, write_report
from .pdf_parser import parse_pdf

# Projektwurzel = eine Ebene über dem Code-Repo (data/ und docs/ liegen dort).
REPO = Path(__file__).resolve().parents[1]
PROJECT = REPO.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GVL-PDF ⇄ CSV Abgleich")
    ap.add_argument("--pdf", default=str(PROJECT / "GVL-Labelcodes.pdf"), help="Pfad zum GVL-PDF")
    ap.add_argument("--csv", default=str(PROJECT / "data" / "GVL_Labelcodes_synced.csv"), help="lokale CSV (Master)")
    ap.add_argument("--data-dir", default=str(PROJECT / "data"))
    ap.add_argument("--docs-dir", default=str(PROJECT / "docs"))
    args = ap.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"PDF nicht gefunden: {pdf_path}", file=sys.stderr)
        return 2

    print(f"[1/4] Parse PDF: {pdf_path.name} …", flush=True)
    pdf_records, version = parse_pdf(pdf_path)
    print(f"      {len(pdf_records)} Datensätze, Quell-Version {version}")

    print(f"[2/4] Lese CSV: {Path(args.csv).name} …", flush=True)
    csv_records = read_csv(args.csv)
    print(f"      {len(csv_records)} Datensätze")

    print("[3/4] Diff …", flush=True)
    rep = diff(pdf_records, csv_records, version)
    print(
        f"      NEU={len(rep.neu)} GEÄNDERT={len(rep.geaendert)} "
        f"ENTFERNT={len(rep.entfernt)} DUPLIKATE={len(rep.duplikate)}"
    )

    print("[4/4] Schreibe Outputs …", flush=True)
    data_dir, docs_dir = Path(args.data_dir), Path(args.docs_dir)
    report_path = docs_dir / f"reconcile_report_{version}.md"
    diff_csv_path = data_dir / f"diff_{version}.csv"
    synced_path = data_dir / "GVL_Labelcodes_synced.csv"
    meta_path = data_dir / "GVL_Labelcodes_synced.meta.json"
    archive_dir = data_dir / "archive"

    synced = build_synced(pdf_records)
    archived = archive_previous_master(synced_path, meta_path, archive_dir)
    write_report(report_path, rep, pdf_records)
    write_diff_csv(diff_csv_path, rep)
    write_csv(synced_path, synced)
    write_meta(meta_path, version, len(synced))

    if archived:
        print(f"      Archiv:  {archived} (Vorgänger-Stand gesichert)")
    print(f"      Report:  {report_path}")
    print(f"      Diff:    {diff_csv_path}")
    print(f"      Master:  {synced_path} ({len(synced)} Zeilen)")
    print(f"      Meta:    {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
