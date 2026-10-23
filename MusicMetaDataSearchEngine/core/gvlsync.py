"""GVL-PDF-Reconcile für die App: Upload -> Diff-Vorschau -> Merge -> labels.db.

Kapselt die getestete reconcile-Pipeline (parse/diff/build_synced/archive/write_*)
in zwei Schritte für die UI:

  preview(pdf, csv) -> parst + difft gegen den aktuellen Master, schreibt NICHTS,
                       gibt Zähler + Stichproben + ein Token zurück.
  merge(token, ...)  -> schreibt Master/Report/Diff/Meta (Vorgänger wird archiviert)
                       und baut labels.db neu. Verwendet die schon geparsten Records
                       aus dem preview (das 16-MB-PDF wird nur EINMAL geparst).

Zwischen den Schritten liegt das Parse-Ergebnis in-memory unter dem Token (nur der
letzte Preview wird gehalten). Diff VOR Merge — nichts wird ohne Bestätigung berührt.
"""
from __future__ import annotations
import json
import re
import uuid
from pathlib import Path

from core.labeldb import build_label_db
from core.models import LabelRecord
from reconcile.csv_io import apply_name_fixes, read_csv, read_name_fixes, read_supplemental, write_csv
from reconcile.diff import diff
from reconcile.merge import (archive_previous_master, build_synced, write_diff_csv,
                             write_meta, write_report)
from reconcile.pdf_parser import parse_pdf

_STAGE: dict[str, dict] = {}   # token -> {records, version, rep}


def _sample(rec: LabelRecord) -> dict:
    return {"labelcode": rec.labelcode, "label": rec.label,
            "hersteller": rec.hersteller, "rechterueckrufe": rec.rechterueckrufe}


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def master_version(csv_path: str | Path) -> str:
    """Stand des aktuellen Masters aus der meta.json daneben ('' ohne Meta)."""
    meta = Path(csv_path).with_name("GVL_Labelcodes_synced.meta.json")
    try:
        return str(json.loads(meta.read_text(encoding="utf-8")).get("source_version") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def is_older(pdf_version: str, master: str) -> bool:
    """PDF-Stand vor dem Master-Stand? Nur bei zwei ISO-Daten ('unknown' o.ä. entscheidet nichts)."""
    return bool(_ISO_DATE.fullmatch(pdf_version or "") and _ISO_DATE.fullmatch(master or "")) and pdf_version < master


def db_version(labels_db: str | Path) -> str:
    """Stand, aus dem labels.db gebaut wurde ('' ohne DB oder Meta)."""
    if not Path(labels_db).exists():
        return ""
    import sqlite3
    con = sqlite3.connect(labels_db)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='source_version'").fetchone()
        return str(row[0]) if row else ""
    except sqlite3.Error:
        return ""
    finally:
        con.close()


def needs_rebuild(labels_db: str | Path, master_csv: str | Path) -> bool:
    """labels.db älter als der mitgelieferte Master? Eine Installation behält sonst den GVL-Stand ihres ersten
    Starts (neue Codes fehlen, geänderte PLM-Kennzeichen bleiben alt). Ein selbst eingespieltes, neueres PDF
    bleibt (dann ist der Master nicht neuer)."""
    return Path(labels_db).exists() and is_older(db_version(labels_db), master_version(master_csv))


def rebuild_from_master(master_csv: str | Path, labels_db: str | Path,
                        supplemental_path: str | Path) -> tuple[str, int]:
    """labels.db aus dem Master neu bauen, mit den Zusatz-Codes wie beim PDF-Update. Manuelle Labels, Namens-
    korrekturen und eigene Änderungen trägt das Laden danach wieder ein. Rückgabe (Stand, Zeilen)."""
    from core.normalize import normalize_labelcode
    records = read_csv(master_csv)
    existing = {normalize_labelcode(r.labelcode) for r in records}
    version = master_version(master_csv)
    return version, build_label_db(records + read_supplemental(Path(supplemental_path), existing), labels_db, version)


def preview(pdf_path: str | Path, csv_path: str | Path, *, limit: int = 25,
            name_fixes_path: str | Path | None = None) -> dict:
    """PDF parsen + gegen den aktuellen Master diffen. Schreibt nichts. Meldet, ob das PDF älter ist als
    der Master (sonst ersetzte ein älteres PDF ohne Hinweis einen neueren Master, und Codes gingen verloren)."""
    records, version = parse_pdf(pdf_path)
    # Bekannte Fehler der Quelle vor dem Diff korrigieren, sonst stünde jede Korrektur bei jedem Update als „geändert“ da
    apply_name_fixes(records, read_name_fixes(name_fixes_path or Path(csv_path).with_name("gvl_name_fixes.csv")))
    csv_records = read_csv(csv_path) if Path(csv_path).exists() else []
    rep = diff(records, csv_records, version)
    token = uuid.uuid4().hex
    _STAGE.clear()
    _STAGE[token] = {"records": records, "version": version, "rep": rep}
    master = master_version(csv_path)
    return {
        "token": token,
        "source_version": version,
        "pdf_version": version,
        "master_version": master,
        "older_than_master": is_older(version, master),
        "pdf_count": rep.pdf_count,
        "csv_count": rep.csv_count,
        "counts": {"neu": len(rep.neu), "geaendert": len(rep.geaendert),
                   "entfernt": len(rep.entfernt), "duplikate": len(rep.duplikate)},
        "sample_neu": [_sample(r) for r in rep.neu[:limit]],
        "sample_entfernt": [_sample(r) for r in rep.entfernt[:limit]],
        "sample_geaendert": [
            {"labelcode": lc, "csv": [_sample(r) for r in cs], "pdf": [_sample(r) for r in ps]}
            for lc, cs, ps in rep.geaendert[:limit]
        ],
    }


def merge(token: str, *, csv_path: str | Path, data_dir: str | Path,
          docs_dir: str | Path, labels_db: str | Path, allow_older: bool = False,
          supplemental_path: str | Path | None = None) -> dict:
    """Den zuvor angezeigten Diff festschreiben: Master/Report/Diff/Meta schreiben
    (Vorgänger archivieren) + labels.db neu bauen. Nutzt die gecachten Records.

    Ein PDF, das älter ist als der Master, wird nur mit allow_older=True übernommen; sonst
    {"error", "older_than_master": True}, nichts geschrieben, der Token bleibt für die Bestätigung gültig.
    supplemental_path: wo labelcodes_supplemental.csv liegt (gebündelte App: im Bundle, nicht im gvl/-Ordner);
    Default wie bisher neben dem Master."""
    st = _STAGE.get(token)
    if st is None:
        return {"error": "Vorschau abgelaufen — bitte das PDF erneut hochladen"}
    records, version, rep = st["records"], st["version"], st["rep"]
    master = master_version(csv_path)
    if is_older(version, master) and not allow_older:
        return {"error": f"Das PDF (Stand {version}) ist älter als der aktuelle Master (Stand {master}). "
                         "Übernehmen nur nach ausdrücklicher Bestätigung — sonst gehen neuere Codes verloren.",
                "older_than_master": True, "pdf_version": version, "master_version": master}
    data_dir, docs_dir = Path(data_dir), Path(docs_dir)
    synced_path = Path(csv_path)
    meta_path = data_dir / "GVL_Labelcodes_synced.meta.json"

    synced = build_synced(records)
    archived = archive_previous_master(synced_path, meta_path, data_dir / "archive")
    write_report(docs_dir / f"reconcile_report_{version}.md", rep, records)
    write_diff_csv(data_dir / f"diff_{version}.csv", rep)
    write_csv(synced_path, synced)
    write_meta(meta_path, version, len(synced))
    # labels.db neu bauen — MIT den Zusatz-Codes (labelcodes_supplemental.csv, GVL gewinnt), sonst
    # verschwinden nach jedem PDF-Update die Library-Codes aus der Datenbank.
    from core.normalize import normalize_labelcode
    existing = {normalize_labelcode(r.labelcode) for r in synced}
    supp = read_supplemental(Path(supplemental_path) if supplemental_path else data_dir / "labelcodes_supplemental.csv",
                             existing)
    rows = build_label_db(synced + supp, labels_db, version)

    _STAGE.clear()
    return {"source_version": version, "rows": rows,
            "archived": str(archived) if archived else "", "labels_db": str(labels_db)}
