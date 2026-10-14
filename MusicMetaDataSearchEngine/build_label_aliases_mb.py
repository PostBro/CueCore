#!/usr/bin/env python3
"""MusicBrainz-Label-Namen und -Aliasse -> data/label_aliases_mb.csv — dritte lokale Quelle für
labelmatch.match_label (nach GVL-Master und ALIASES). Vorbefüllen Schritt 1.

Quelle: MusicBrainz Full Export mbdump.tar.bz2 (CC0, data.metabrainz.org/pub/musicbrainz/data/fullexport/),
daraus nur zwei Tabellen im PostgreSQL-COPY-Textformat (Tab-getrennt, \\N = NULL, Backslash-Escapes):
  mbdump/label        Spalten laut admin/sql/CreateTables.sql: id, gid, name, begin/end-Datum (6 Felder),
                      label_code, type, area, comment, edits_pending, last_updated, ended
  mbdump/label_alias  id, label, name, locale, edits_pending, last_updated, type, sort_name, …
Extrakt:  tar -xjf mbdump.tar.bz2 mbdump/label mbdump/label_alias mbdump/TIMESTAMP
          (bsdtar -q bricht ab, sobald alle drei gefunden sind; bz2 wird trotzdem sequenziell gelesen)
Lauf:     python build_label_aliases_mb.py --dump <ordner mit mbdump/> [--master <csv>] [--out <csv>]

Regeln (GVL gewinnt, nichts raten):
  - label_code ist eine Ganzzahl ohne führende Nullen -> normalize_labelcode; nur Codes aus dem GVL-Master.
  - Namen, die der GVL-Index selbst beantwortet (exakt oder Suffix-bereinigt, wie match_label prüft) oder in
    ALIASES stehen, bleiben draußen: entweder redundant oder vom GVL überstimmt — match_label fragt die
    MB-Tabelle erst, wenn der Master nichts hergibt.
  - Ein Name, der auf mehr als einen Code zeigt, fliegt. Namen unter 3 Zeichen oder nur aus Generika auch.
Ausgabe: name (MB-Schreibweise), labelcode, kind (name | alias), mb_label (MB-Label-ID), sortiert;
dazu label_aliases_mb.meta.json mit Dump-Stand und Zählern.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
import sys
from pathlib import Path

from core.labelmatch import ALIASES, build_index, norm, strip_generic
from core.normalize import normalize_labelcode
from reconcile.csv_io import read_csv

REPO = Path(__file__).resolve().parent
PROJECT = REPO.parent
DEFAULT_MASTER = PROJECT / "data" / "GVL_Labelcodes_synced.csv"
DEFAULT_OUT = PROJECT / "data" / "label_aliases_mb.csv"

# Spaltenindizes laut CreateTables.sql (master, geprüft 14.09.2026) — beim ersten Datensatz verifiziert.
LABEL_COLS = {"id": 0, "gid": 1, "name": 2, "label_code": 9}
ALIAS_COLS = {"id": 0, "label": 1, "name": 2, "type": 6}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "\\": "\\"}


def unescape(field: str) -> str:
    """PostgreSQL-COPY-Text: '\\\\' -> '\\', '\\t' -> Tab, '\\n' -> Zeilenumbruch …"""
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(1)), field)


def read_table(path: Path):
    """Zeilen einer COPY-Textdatei als Liste von Feldern (None = \\N)."""
    with open(path, encoding="utf-8", newline="") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            yield [None if c == "\\N" else unescape(c) for c in line.split("\t")]


def _check_label_row(row: list) -> None:
    ok = (len(row) >= 10 and row[LABEL_COLS["gid"]] and _UUID.match(row[LABEL_COLS["gid"]])
          and (row[LABEL_COLS["label_code"]] is None or row[LABEL_COLS["label_code"]].isdigit()))
    if not ok:
        raise SystemExit("mbdump/label: Spaltenreihenfolge weicht von CreateTables.sql ab "
                         "(gid nicht in Spalte 2 oder label_code nicht in Spalte 10) — nicht raten, Schema prüfen.")


def _check_alias_row(row: list) -> None:
    if not (len(row) >= 8 and row[ALIAS_COLS["label"]] and row[ALIAS_COLS["label"]].isdigit()
            and (row[ALIAS_COLS["type"]] is None or row[ALIAS_COLS["type"]].isdigit())):
        raise SystemExit("mbdump/label_alias: Spaltenreihenfolge weicht von CreateTables.sql ab — Schema prüfen.")


def build(dump_dir: str | Path, master_csv: str | Path = DEFAULT_MASTER,
          out_csv: str | Path = DEFAULT_OUT) -> dict:
    dump_dir, out_csv = Path(dump_dir), Path(out_csv)
    label_path, alias_path = dump_dir / "mbdump" / "label", dump_dir / "mbdump" / "label_alias"
    for p in (label_path, alias_path):
        if not p.exists():
            raise SystemExit(f"fehlt: {p} (tar -xjf mbdump.tar.bz2 mbdump/label mbdump/label_alias)")
    master = read_csv(master_csv)
    gvl_codes = {normalize_labelcode(r.labelcode) for r in master}
    idx = build_index((normalize_labelcode(r.labelcode), r.label, r.hersteller) for r in master)

    stats = {"labels_total": 0, "labels_with_lc": 0, "labels_lc_in_gvl": 0, "aliases_total": 0,
             "aliases_of_gvl_labels": 0, "dropped_generic": 0, "dropped_gvl_known": 0,
             "dropped_ambiguous": 0, "rows": 0}
    kept: dict[str, str] = {}                      # MB-Label-ID -> Code
    cands: list[tuple[str, str, str, str]] = []    # (name, code, kind, mb_label)
    first = True
    for row in read_table(label_path):
        if first:
            _check_label_row(row); first = False
        stats["labels_total"] += 1
        lc = row[LABEL_COLS["label_code"]]
        if not lc:
            continue
        stats["labels_with_lc"] += 1
        code = normalize_labelcode(lc)
        if code not in gvl_codes:
            continue
        stats["labels_lc_in_gvl"] += 1
        kept[row[LABEL_COLS["id"]]] = code
        cands.append((row[LABEL_COLS["name"]], code, "name", row[LABEL_COLS["id"]]))
    first = True
    for row in read_table(alias_path):
        if first:
            _check_alias_row(row); first = False
        stats["aliases_total"] += 1
        code = kept.get(row[ALIAS_COLS["label"]])
        if not code:
            continue
        stats["aliases_of_gvl_labels"] += 1
        cands.append((row[ALIAS_COLS["name"]], code, "alias", row[ALIAS_COLS["label"]]))

    by_key: dict[str, set[str]] = {}
    chosen: dict[str, tuple[str, str, str, str]] = {}
    for name, code, kind, mid in cands:            # Label-Namen zuerst, dann Aliasse -> Name gewinnt bei gleichem Schlüssel
        n = norm(name or "")
        sn = strip_generic(n)
        if len(n) < 3 or not sn:
            stats["dropped_generic"] += 1
            continue
        if (n in ALIASES or sn in ALIASES or n in idx.full or sn in idx.full
                or n in idx.stripped or sn in idx.stripped):
            stats["dropped_gvl_known"] += 1
            continue
        by_key.setdefault(n, set()).add(code)
        chosen.setdefault(n, (name, code, kind, mid))
    rows = [chosen[k] for k, codes in by_key.items() if len(codes) == 1]
    stats["dropped_ambiguous"] = sum(1 for codes in by_key.values() if len(codes) > 1)
    rows.sort(key=lambda r: (r[0].casefold(), r[1]))
    stats["rows"] = len(rows)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["name", "labelcode", "kind", "mb_label"])
        w.writerows(rows)
    ts = dump_dir / "mbdump" / "TIMESTAMP"
    meta = {"source": "MusicBrainz Full Export mbdump.tar.bz2 (CC0), Tabellen label + label_alias",
            "dump_timestamp": ts.read_text(encoding="utf-8").strip() if ts.exists() else "",
            "master": str(Path(master_csv).name), **stats}
    out_csv.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                                 encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True, help="Ordner, der mbdump/label und mbdump/label_alias enthält")
    ap.add_argument("--master", default=str(DEFAULT_MASTER), help="GVL-Master-CSV (nur diese Codes zählen)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Ziel-CSV")
    a = ap.parse_args(argv)
    meta = build(a.dump, a.master, a.out)
    print(f"label_aliases_mb.csv: {meta['rows']} Namen -> {a.out}")
    print(f"  MB-Labels: {meta['labels_total']} | mit LC: {meta['labels_with_lc']} | LC im GVL-Master: "
          f"{meta['labels_lc_in_gvl']} | Aliasse dazu: {meta['aliases_of_gvl_labels']}")
    print(f"  raus: GVL kennt den Namen {meta['dropped_gvl_known']} | mehrdeutig {meta['dropped_ambiguous']} "
          f"| generisch/kurz {meta['dropped_generic']} | Dump {meta['dump_timestamp'] or '?'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
