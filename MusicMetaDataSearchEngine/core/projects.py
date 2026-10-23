"""Cue-Sheet-Projekte speichern und fortsetzen (Teil D12/D14).

Ein Projekt ist ein bearbeitetes Cue-Sheet: Name, Karten (mit Notizen, Rückfragen, Herkunft), Optionen und eine
Zusammenfassung für die Übersicht (Cues, fertig, offen, Library-Anteil). Jedes Projekt ist eine JSON-Datei in
`<Datenordner>/projects/`, atomar geschrieben. Projektnamen sind Kundendaten: sie bleiben im Datenordner und
kommen nie in die songs.db oder das Repo.
"""
from __future__ import annotations
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

DIR_NAME = "projects"
_ID = re.compile(r"^[a-z0-9]{8,32}$")
SUMMARY_KEYS = ("cues", "ok", "warn", "bad", "library_sec", "total_sec")


def project_dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / DIR_NAME


def _path(data_dir: str | Path, pid: str) -> Path:
    if not _ID.match(pid or ""):
        raise ValueError("ungültige Projekt-ID")
    return project_dir(data_dir) / f"{pid}.json"


def _summary(raw) -> dict:
    s = raw if isinstance(raw, dict) else {}
    out = {}
    for k in SUMMARY_KEYS:
        try:
            out[k] = max(0, int(s.get(k) or 0))
        except (TypeError, ValueError):
            out[k] = 0
    return out


def save(data_dir: str | Path, project: dict) -> dict:
    """Projekt anlegen (ohne id) oder ersetzen. Rückgabe: Metadaten ohne Karten."""
    now = datetime.now(timezone.utc).isoformat()
    pid = str(project.get("id") or "") or uuid.uuid4().hex[:12]
    path = _path(data_dir, pid)
    created = now
    if path.exists():
        try:
            created = json.loads(path.read_text(encoding="utf-8")).get("created") or now
        except ValueError:
            pass
    doc = {"id": pid, "name": " ".join(str(project.get("name") or "").split()) or "Cue-Sheet",
           "created": created, "updated": now, "source_file": str(project.get("source_file") or ""),
           "options": project.get("options") if isinstance(project.get("options"), dict) else {},
           "summary": _summary(project.get("summary")),
           "cards": project.get("cards") if isinstance(project.get("cards"), list) else []}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return _meta(doc)


def _meta(doc: dict) -> dict:
    return {k: doc.get(k) for k in ("id", "name", "created", "updated", "source_file", "summary")}


def load(data_dir: str | Path, pid: str) -> dict | None:
    path = _path(data_dir, pid)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_all(data_dir: str | Path) -> list[dict]:
    """Alle Projekte, zuletzt bearbeitetes zuerst. Eine kaputte Datei erscheint mit Fehler statt still zu fehlen."""
    out = []
    d = project_dir(data_dir)
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(_meta(json.loads(p.read_text(encoding="utf-8"))))
        except (ValueError, OSError) as e:
            out.append({"id": p.stem, "name": p.stem, "updated": "", "summary": _summary({}),
                        "error": f"Datei nicht lesbar ({e.__class__.__name__})"})
    return sorted(out, key=lambda m: m.get("updated") or "", reverse=True)


def delete(data_dir: str | Path, pid: str) -> bool:
    path = _path(data_dir, pid)
    if not path.exists():
        return False
    path.unlink()
    return True
