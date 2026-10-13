"""Library-Listen (Kürzel → Label → LC) — Regelwerk und Nutzer-Upload.

Zwei Wege führen in die Kürzel-Tabelle:
  1. die mitgelieferte Tabelle data/library_prefixes.csv (Generator build_library_prefixes.py aus
     den Listen der Libraries; im Repo/Bundle, read-only),
  2. Listen, die der Nutzer in der App hochlädt (CSV/XLSX mit Spalten Kürzel, Label, LC) — sie
     liegen unter <Datenordner>/library_lists/ neben labels.db und werden zu
     <Datenordner>/library_prefixes_user.csv gebaut. Die gelieferte Tabelle gewinnt bei gleichem
     Kürzel; ein Kürzel mit zwei Codes ist mehrdeutig und fliegt raus (kein Raten).

`reduce_rows` ist das gemeinsame Regelwerk beider Wege; der Ausschlussbericht ist genau die
Anzeige, die der Nutzer vor dem Übernehmen sieht (Vorschau, dann Übernahme).
"""
from __future__ import annotations
import csv
import json
import re
from datetime import date
from pathlib import Path

from . import ingest
from .catalog import normalize_prefix
from .normalize import normalize_labelcode

COLUMNS = ["prefix", "labelcode", "label_gvl", "library", "list_label", "source"]
# Listen-Zuschreibungen, die der GVL-Master widerlegt (GVL gewinnt) — nie ins Mapping.
#   84940 Extreme „Devil Cut“ / GVL Smalltown Kid Records · 94544 KPM „Go Discover“ / GVL Dv Music Records
#   89388 APL „Benztown Branding (BZ)“ / GVL Fake Palm Records — Benztown hat bei der GVL LC101537
#         (Benztown Production Music, Benztown Branding USA, LLC)
#   98314 KPM „Rhymez (RHZ)“ / GVL HYNY — bei KPM in der Gruppe „Z - Disabled Labels“
KNOWN_CONFLICTS = {"84940", "94544", "89388", "98314"}

USER_LISTS_DIR = "library_lists"
USER_PREFIXES = "library_prefixes_user.csv"
USER_META = "library_prefixes_user.meta.json"
USER_MANIFEST = "manifest.json"       # in library_lists/: {gespeicherter Name: {library, original, uploaded}}

# Spalten einer Nutzer-Liste (kleingeschrieben, Punkt am Ende weg — wie ingest.guess_mapping).
LIST_ALIASES = {
    "prefix": ["kürzel", "kuerzel", "prefix", "präfix", "praefix", "code", "album-code", "album code", "albumcode",
               "shortform", "short form", "abbreviation", "abk", "abk.", "katalog", "katalogkürzel", "album_no",
               "album no", "series code", "label code prefix", "kürzel/prefix"],
    "label": ["label", "labelname", "label name", "name", "sub-label", "sublabel", "sub label", "library", "series", "serie"],
    "labelcode": ["lc", "labelcode", "label code", "label_code", "gvl", "gvl lc", "gvl-lc", "lc code", "lc-code",
                  "lccode", "german label code", "gemacode", "gema code", "gvl labelcode"],
    "library": ["anbieter", "provider", "publisher", "verlag", "quelle", "source", "library name", "katalog/anbieter"],
}


def guess_list_mapping(header: list[str]) -> dict:
    by_lower = {(h or "").strip().lower().rstrip("."): h for h in header}
    mapping: dict[str, str] = {}
    for field, aliases in LIST_ALIASES.items():
        for a in aliases:
            if a in by_lower:
                mapping[field] = by_lower[a]
                break
    # „Label“ und „Library“ dürfen nicht dieselbe Spalte sein (Alias „library“ in beiden Listen)
    if mapping.get("library") == mapping.get("label"):
        mapping.pop("library", None)
    return mapping


def read_user_list(path: str | Path, *, library: str = "") -> dict:
    """CSV/XLSX/XLS -> {rows, mapping, fieldnames, warnings} oder {error}. rows in der Form des
    Generators (prefix, labelcode, list_label, library, source)."""
    p = Path(path)
    try:
        header, raw = ingest.read_table(p)
    except ValueError as e:
        return {"error": str(e)}
    mapping = guess_list_mapping(header)
    if "prefix" not in mapping or "labelcode" not in mapping:
        # Track-Lieferung im falschen Upload: statt der Spalten-Meldung der richtige Weg.
        if ingest.library_delivery_kind(p):
            return {"error": "Das ist eine Track-Lieferung (Titel, Komponisten, Labelcodes), keine "
                             "Kürzel-Liste. Sie gehört in die Track-Datenbank: dort „Import“ — die Tracks "
                             "werden als verifiziert übernommen und das Cue-Sheet findet sie über den Dateinamen. "
                             "Hier wird nur die Zuordnung Katalog-Kürzel → Labelcode gepflegt."}
        return {"error": "Spalten für Kürzel und LC nicht erkannt — gefunden: "
                         + (", ".join(h for h in header if h) or "(keine Kopfzeile)")
                         + ". Erwartet z.B. „Kürzel“, „Label“, „LC“."}
    get = lambda row, f: (row.get(mapping[f], "") or "").strip() if f in mapping else ""
    lib_default = (library or "").strip() or re.sub(r"[_\-]+", " ", re.sub(r"^\d{4}-\d{2}-\d{2}_", "", p.stem)).strip()
    rows: list[dict] = []
    warnings: list[str] = []
    for i, row in enumerate(raw, 2):
        prefix, code = get(row, "prefix"), get(row, "labelcode")
        if not prefix and not code:
            continue
        digits = re.sub(r"\D", "", code)
        norm = normalize_labelcode(digits) if digits else ""
        if not norm and code:
            warnings.append(f"Zeile {i}: LC „{code}“ nicht lesbar.")
        rows.append({"prefix": prefix, "labelcode": norm, "list_label": get(row, "label"),
                     "library": get(row, "library") or lib_default, "source": p.name})
    return {"rows": rows, "mapping": mapping, "fieldnames": header, "warnings": warnings}


def reduce_rows(rows: list[dict], names: dict[str, str], *, known: dict[str, dict] | None = None) -> dict:
    """Das Regelwerk (gemeinsam für Generator und Nutzer-Upload):
      - leer (kein Kürzel oder kein Code) -> raus
      - Code in KNOWN_CONFLICTS -> raus („GVL widerspricht der Liste“)
      - Code weder im Master noch im Supplemental (`names`) -> raus
      - ein Kürzel mit zwei Codes -> alle raus („mehrdeutig“)
      - `known` (schon bekannte Kürzel, z.B. die gelieferte Tabelle): gleicher Code -> „schon bekannt“
        (info, nicht doppelt aufnehmen), anderer Code -> raus („Kürzel schon vergeben“)
      - Listenname ≠ GVL-Name -> info (der GVL-Name kommt in die Datei)
    Rückgabe {kept, excluded, infos}; kept-Zeilen tragen label_gvl und key."""
    known = known or {}
    excluded: list[dict] = []
    infos: list[dict] = []
    by: dict[str, list[dict]] = {}
    for r in rows:
        r = dict(r)
        r["key"] = normalize_prefix(r.get("prefix", ""))
        if not r["key"] or not r.get("labelcode"):
            excluded.append({**r, "why": "leer"}); continue
        if r["labelcode"] in KNOWN_CONFLICTS:
            excluded.append({**r, "why": "GVL widerspricht der Liste"}); continue
        name = names.get(r["labelcode"])
        if not name:
            excluded.append({**r, "why": "Code weder im Master noch im Supplemental"}); continue
        r["label_gvl"] = name
        by.setdefault(r["key"], []).append(r)
    kept: list[dict] = []
    for key, group in by.items():
        codes = {g["labelcode"] for g in group}
        if len(codes) > 1:
            for g in group:
                excluded.append({**g, "why": "Kürzel mehrdeutig: " + ", ".join(sorted(codes))})
            continue
        first = dict(group[0])
        first["library"] = "; ".join(sorted({g["library"] for g in group}))
        first["source"] = "; ".join(sorted({g["source"] for g in group}))
        k = known.get(key)
        if k:
            if k["labelcode"] == first["labelcode"]:
                infos.append({**first, "why": f"schon bekannt ({k.get('library') or 'gelieferte Tabelle'})"})
            else:
                excluded.append({**first, "why": f"Kürzel schon vergeben: LC{k['labelcode']} {names.get(k['labelcode'], '')} "
                                                 f"({k.get('library') or 'gelieferte Tabelle'})"})
            continue
        if first.get("list_label") and _n(first["list_label"]) != _n(first["label_gvl"]):
            infos.append({**first, "why": f"Name in der Liste „{first['list_label']}“, GVL „{first['label_gvl']}“ — GVL-Name wird übernommen"})
        kept.append(first)
    kept.sort(key=lambda r: (r["library"], r["key"]))
    return {"kept": kept, "excluded": excluded, "infos": infos}


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def write_prefixes(kept: list[dict], out: Path, meta: Path, *, sources: list[str], excluded: list[dict],
                   decided: list[str] | None = None) -> None:
    """library_prefixes(.csv|_user.csv) + .meta.json schreiben — Format ist für beide Wege gleich."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in kept:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    meta.write_text(json.dumps({
        "built": date.today().isoformat(), "rows": len(kept),
        "libraries": sorted({r["library"] for r in kept}),
        "sources": sorted(sources),
        "decided_by_gvl_name": decided or [],
        "excluded": [{"prefix": e.get("prefix", ""), "labelcode": e.get("labelcode", ""), "library": e.get("library", ""), "why": e["why"]}
                     for e in excluded],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# --- Nutzer-Upload: Dateien unter <base>/library_lists/, Ergebnis <base>/library_prefixes_user.csv ---

def user_lists_dir(base: str | Path) -> Path:
    return Path(base) / USER_LISTS_DIR


def read_manifest(base: str | Path) -> dict:
    p = user_lists_dir(base) / USER_MANIFEST
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def write_manifest(base: str | Path, data: dict) -> None:
    d = user_lists_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    (d / USER_MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def list_user_files(base: str | Path) -> list[dict]:
    """Hochgeladene Listen (Dateien im Ordner, Library-Name/Original/Datum aus dem Manifest)."""
    d = user_lists_dir(base)
    if not d.exists():
        return []
    man = read_manifest(base)
    out = []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() in ingest.SUPPORTED_EXTENSIONS and not p.name.startswith("."):
            m = man.get(p.name) or {}
            out.append({"name": p.name, "size": p.stat().st_size, "library": m.get("library", ""),
                        "original": m.get("original", p.name), "uploaded": m.get("uploaded", "")})
    return out


def safe_name(filename: str) -> str:
    base = re.sub(r"[^\w.\- ]+", "_", Path(filename).name).strip() or "liste.csv"
    return base


def rebuild_user_prefixes(base: str | Path, names: dict[str, str], known: dict[str, dict]) -> dict:
    """Alle Nutzer-Listen lesen, Regelwerk anwenden, <base>/library_prefixes_user.csv schreiben.
    Rückgabe: Bericht {kept, excluded, infos, files, warnings}. Ohne Listen: leere Datei."""
    d = user_lists_dir(base)
    rows: list[dict] = []
    warnings: list[str] = []
    files = list_user_files(base)
    for f in files:
        res = read_user_list(d / f["name"], library=f.get("library", ""))
        if "error" in res:
            warnings.append(f"{f['name']}: {res['error']}")
            continue
        rows.extend(res["rows"])
        warnings.extend(f"{f['name']}: {w}" for w in res["warnings"])
    rep = reduce_rows(rows, names, known=known)
    write_prefixes(rep["kept"], Path(base) / USER_PREFIXES, Path(base) / USER_META,
                   sources=[f["name"] for f in files], excluded=rep["excluded"])
    return {**rep, "files": files, "warnings": warnings}


def attach_user_prefixes(idx, base: str | Path) -> int:
    """<base>/library_prefixes_user.csv in den Index hängen — nur Kürzel, die die gelieferte Tabelle
    nicht schon kennt (sie gewinnt), und deren Code der Index kennt. Rückgabe: Anzahl."""
    from .catalog import load_library_prefixes
    n = 0
    for key, v in load_library_prefixes(Path(base) / USER_PREFIXES).items():
        if key in idx.prefix2code or v["labelcode"] not in idx.code2name:
            continue
        idx.prefix2code[key] = {**v, "user": True}
        n += 1
    return n
