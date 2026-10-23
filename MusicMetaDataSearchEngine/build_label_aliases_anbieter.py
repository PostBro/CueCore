#!/usr/bin/env python3
"""Labelnamen laut Anbieter -> GVL-Code: data/label_aliases_anbieter.csv (Recherche 23.10.2026).

Quelle: data/library_lists/Label_Codes_Weltweit_2026-10-23.csv, Spalte „Weitere_Labelnamen_laut_Anbieter“ (z. B.
SONOTON CLASSICAL für LC00358), dazu die Recherche data/library_lists/Labels_Labelcode_Recherche_2026-10-23.csv
(nur Zeilen „BELEGT_LABEL“: Labelname wie in der Datenbank, Code laut Labelcode-Liste des Anbieters). Aufgenommen wird ein Name nur, wenn er genau EINEM Code zugeordnet ist, der Code im
GVL-Master steht und kein GVL-Labelname schon auf einen anderen Code zeigt (dann gilt die GVL). Die Namensregel
(core/labelcheck.GvlMaster.codes_for_name) nutzt die Datei zusätzlich zu den GVL-Namen.

    python build_label_aliases_anbieter.py
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

from core.labelcheck import GvlMaster
from core.labelmatch import norm
from core.normalize import normalize_labelcode

DATA = Path(__file__).resolve().parent.parent / "data"
SRC = DATA / "library_lists" / "Label_Codes_Weltweit_2026-10-23.csv"
RESEARCH = DATA / "library_lists" / "Labels_Labelcode_Recherche_2026-10-23.csv"
OUT = DATA / "label_aliases_anbieter.csv"


def research_pairs(path: Path) -> list[tuple[str, str]]:
    """(Code, Name) aus der Recherche-Tabelle: nur ganze Labels mit belegtem Code, keine Einzeltracks."""
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [(normalize_labelcode(r["Labelcode_belegt"]), r["Label (wie in der Datenbank)"].strip())
                for r in csv.DictReader(f)
                if r.get("Pruefstatus") == "BELEGT_LABEL" and r.get("Labelcode_belegt") and
                r.get("Label (wie in der Datenbank)", "").strip()]


def build(src: Path, master: GvlMaster, extra: list[tuple[str, str]] = ()) -> tuple[list[dict], list[str]]:
    by: dict[str, set[tuple[str, str]]] = {}
    for code, name in extra:
        by.setdefault(norm(name), set()).add((code, name))
    with open(src, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            code = normalize_labelcode(r.get("Labelcode") or "")
            for name in (r.get("Weitere_Labelnamen_laut_Anbieter") or "").split("|"):
                name = " ".join(name.split())
                if name and code:
                    by.setdefault(norm(name), set()).add((code, name))
    rows, skipped = [], []
    for key, vals in sorted(by.items()):
        codes = {c for c, _ in vals}
        name = sorted(vals)[0][1]
        gvl = master.codes_for_name(name)
        if len(codes) != 1:
            skipped.append(f"{name}: mehrere Codes {sorted(codes)}")
        elif not master.names(next(iter(codes))):
            skipped.append(f"{name}: LC{next(iter(codes))} nicht im Master")
        elif gvl and next(iter(codes)) not in gvl:
            skipped.append(f"{name}: GVL-Name zeigt auf {gvl}, Anbieter auf {sorted(codes)}")
        elif not gvl:
            rows.append({"alias": name, "labelcode": next(iter(codes)), "source": "Anbieterangaben 2026-10-23"})
    return rows, skipped


def main() -> int:
    mr = list(csv.DictReader(open(DATA / "GVL_Labelcodes_synced.csv", encoding="utf-8")))
    rows, skipped = build(SRC, GvlMaster([(r["labelcode"], r["label"]) for r in mr]), research_pairs(RESEARCH))
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["alias", "labelcode", "source"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"{OUT.name}: {len(rows)} Aliasse, {len(skipped)} übersprungen")
    for s in skipped:
        print("  -", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
