"""Eigene Änderungen an der Label-Datenbank: Kürzel und LC-Korrekturen (label_edits.json im Datenordner).

Eine eigene Schicht über GVL-Master und Kürzel-Tabellen, wie labels_manual.json und gvl_name_fixes.csv: der Master
bleibt unverändert, die Datei überlebt jedes GVL-Update und wird bei jedem Laden neu angewendet.

  prefixes: [{prefix, labelcode, note, date}] — Kürzel -> Code. labelcode '' = Kürzel entfernt (eine gelieferte
            Zuordnung gilt nicht mehr). Gewinnt vor allen Kürzel-Tabellen: der Mensch hat es in der Liste entschieden.
  names:    [{name, from, labelcode, note, date}] — Labelname -> anderer Code als der Master (Korrektur, Notiz Pflicht).
            Gewinnt in match_label vor dem Master. `from` ist der Code der Zeile, an der korrigiert wurde (Anzeige).

Ein Eintrag, der nur wiederholt, was ohne ihn gilt, wird nicht gespeichert (so ist Rückgängig = alten Wert setzen).
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import normalize_prefix
from .labelmatch import LabelIndex, norm

FILE = "label_edits.json"


def path_for(base: str | Path) -> Path:
    return Path(base) / FILE


def read(base: str | Path) -> dict:
    """Datei lesen; fehlt sie oder ist sie kaputt (hand-editierbar) -> leere Schicht, nur saubere Einträge."""
    p = path_for(base)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    clean = lambda rows, keys: [{k: str(r.get(k) or "").strip() for k in keys}       # noqa: E731
                                for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
    return {"prefixes": [r for r in clean(data.get("prefixes"), ("prefix", "labelcode", "note", "date"))
                         if normalize_prefix(r["prefix"])],
            "names": [r for r in clean(data.get("names"), ("name", "from", "labelcode", "note", "date"))
                      if norm(r["name"]) and r["labelcode"]]}


def write(base: str | Path, data: dict) -> None:
    p = path_for(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    out = {"prefixes": sorted(data.get("prefixes", []), key=lambda r: normalize_prefix(r["prefix"])),
           "names": sorted(data.get("names", []), key=lambda r: norm(r["name"]))}
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def set_prefix(data: dict, prefix: str, code: str, *, base_code: str, note: str, date: str) -> dict | None:
    """Kürzel auf `code` setzen ('' = entfernen). `base_code` = was ohne eigene Änderung gilt. Rückgabe: der neue
    Eintrag oder None (nichts mehr zu merken, weil gleich der Grundlage). Ändert `data` an Ort und Stelle."""
    key = normalize_prefix(prefix)
    data["prefixes"] = [r for r in data.get("prefixes", []) if normalize_prefix(r["prefix"]) != key]
    if code == base_code:
        return None
    entry = {"prefix": prefix.strip().upper(), "labelcode": code, "note": note.strip(), "date": date}
    data["prefixes"].append(entry)
    return entry


def set_name(data: dict, name: str, code: str, *, from_code: str, note: str, date: str) -> dict | None:
    """Labelname auf `code` korrigieren; code '' oder gleich `from_code` hebt die Korrektur auf (None)."""
    key = norm(name)
    data["names"] = [r for r in data.get("names", []) if norm(r["name"]) != key]
    if not code or code == from_code:
        return None
    entry = {"name": " ".join(name.split()), "from": from_code, "labelcode": code, "note": note.strip(), "date": date}
    data["names"].append(entry)
    return entry


def apply(idx: LabelIndex, data: dict) -> int:
    """Schicht in den Index hängen (nach allen Kürzel-Tabellen). Nur Codes, die der Index kennt (Master oder manuelles
    Label) — ein Code, der nach einem GVL-Update fehlt, greift nicht, bleibt aber in der Datei. Rückgabe: Anzahl."""
    n = 0
    for r in data.get("prefixes", []):
        key = normalize_prefix(r["prefix"])
        if not r["labelcode"]:
            n += idx.prefix2code.pop(key, None) is not None
        elif r["labelcode"] in idx.code2name:
            idx.prefix2code[key] = {"labelcode": r["labelcode"], "prefix": r["prefix"], "edit": True,
                                    "library": "eigene Zuordnung", "note": r["note"]}
            n += 1
    idx.name_fix = {norm(r["name"]): r["labelcode"] for r in data.get("names", []) if r["labelcode"] in idx.code2name}
    return n + len(idx.name_fix)
