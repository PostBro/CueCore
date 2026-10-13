"""Diff PDF (= Wahrheit) gegen lokale CSV. Beide Seiten vorher normalisiert.

Gruppierung pro labelcode (1:n LC->Label ist gültig, kein Konflikt). Verglichen
werden die content_key-Mengen pro Code.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from core.models import LabelRecord
from core.normalize import normalize_record


def _group(records: list[LabelRecord]) -> dict[str, list[LabelRecord]]:
    g: dict[str, list[LabelRecord]] = defaultdict(list)
    for rec in (normalize_record(r) for r in records):
        g[rec.labelcode].append(rec)
    return g


@dataclass
class DiffReport:
    source_version: str
    pdf_count: int
    csv_count: int
    neu: list[LabelRecord] = field(default_factory=list)          # im PDF, nicht in CSV
    entfernt: list[LabelRecord] = field(default_factory=list)     # in CSV, nicht (mehr) im PDF
    geaendert: list[tuple[str, list[LabelRecord], list[LabelRecord]]] = field(default_factory=list)  # (lc, csv_recs, pdf_recs)
    duplikate: dict[str, int] = field(default_factory=dict)       # lc -> Anzahl Zeilen (>1)


def diff(pdf_records: list[LabelRecord], csv_records: list[LabelRecord], source_version: str) -> DiffReport:
    pdf = _group(pdf_records)
    csv = _group(csv_records)

    rep = DiffReport(
        source_version=source_version,
        pdf_count=len(pdf_records),
        csv_count=len(csv_records),
    )

    for lc, recs in pdf.items():
        if len(recs) > 1:
            rep.duplikate[lc] = len(recs)

    pdf_codes, csv_codes = set(pdf), set(csv)

    for lc in sorted(pdf_codes - csv_codes):
        rep.neu.extend(pdf[lc])

    for lc in sorted(csv_codes - pdf_codes):
        rep.entfernt.extend(csv[lc])

    for lc in sorted(pdf_codes & csv_codes):
        pdf_keys = sorted(r.content_key() for r in pdf[lc])
        csv_keys = sorted(r.content_key() for r in csv[lc])
        if pdf_keys != csv_keys:
            rep.geaendert.append((lc, csv[lc], pdf[lc]))

    return rep
