"""Tagging-Orchestrierung: Plan (ungefährlich) + Apply (geloggt, hinter confirm).

plan() berechnet nur, was passieren WÜRDE (aktuelle vs. vorgeschlagene Tags +
Zielname + Warnungen). apply() schreibt Tags und benennt um — nur mit confirm=True
und nur mit bestätigtem Labelcode (Default-Stopp), jeder Schritt ins operations_log.
"""
from __future__ import annotations
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import naming, operations, tags
from .normalize import normalize_labelcode, to_filename_lc


@dataclass
class TagPlan:
    path: str
    artist: str
    title: str
    label: str
    labelcode: str          # normalisierte Ziffern ('' = kein bestätigter LC)
    lc: str                 # 'LCXXXXX'
    current_tags: dict = field(default_factory=dict)
    proposed_tags: dict = field(default_factory=dict)
    current_name: str = ""
    proposed_name: str = ""
    rechterueckrufe: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def can_apply(self) -> bool:
        # Ohne Labelname (Code nicht im Master) gibt es keinen Zielnamen nach Schema
        return bool(self.labelcode and self.label)


def plan(path: str | Path, artist: str, title: str, labelcode: str, label: str,
         *, isrc: str = "", rechterueckrufe: str = "",
         album: str = "", composer: str = "", lyricist: str = "") -> TagPlan:
    path = Path(path)
    code = normalize_labelcode(labelcode)
    lc = to_filename_lc(code) if code else ""
    current = tags.read_tags(path)

    proposed: dict[str, str] = {"artist": artist, "title": title, "label": label}
    if code:
        proposed["lc"] = lc
    if isrc:
        proposed["isrc"] = isrc
    if album:
        proposed["album"] = album
    if composer:
        proposed["composer"] = composer
    if lyricist:
        proposed["lyricist"] = lyricist

    ext = path.suffix.lstrip(".").lower() or "mp3"
    # Zielname nur mit Code UND Labelname — sonst stünde „[_LC99999]“ als Vorschlag da
    proposed_name = naming.build_filename(artist, title, label, code, ext=ext) if code and label else ""

    warnings: list[str] = []
    if not code:
        warnings.append("Kein bestätigter Labelcode — kein Rename/Tagging (Default-Stopp).")
    if rechterueckrufe:
        warnings.append(
            f"Rechterückruf {rechterueckrufe} — manuell auf labelrecherche.gvl.de prüfen "
            f"(rechterueckruf_pending bleibt offen)."
        )

    return TagPlan(
        path=str(path), artist=artist, title=title, label=label, labelcode=code, lc=lc,
        current_tags=current, proposed_tags=proposed,
        current_name=path.name, proposed_name=proposed_name,
        rechterueckrufe=rechterueckrufe, warnings=warnings,
    )


def rollback_last_tag(op_con, path: str) -> bool:
    """Den jüngsten, noch nicht zurückgenommenen Tag-Schritt zu `path` rückgängig machen (alte Tags
    zurückschreiben). Für den Fall: Tags geschrieben, Verschieben gescheitert. True = zurückgesetzt."""
    row = op_con.execute("SELECT id FROM operations WHERE op='tag' AND old_path=? AND undone=0 "
                         "ORDER BY id DESC LIMIT 1", (path,)).fetchone()
    if not row:
        return False
    try:
        return operations.undo(op_con, [row[0]]) > 0
    except Exception:
        return False


def apply(p: TagPlan, *, op_con, confirm: bool = False, move_to: str | Path | None = None) -> dict:
    """Schreibt Tags + benennt um. Gibt {new_path, op_ids} zurück.

    Hartstopp: ohne confirm=True und ohne bestätigten Labelcode passiert NICHTS.
    Jeder Schritt landet im operations_log (undo-fähig).
    """
    if not confirm:
        raise PermissionError("Kein Schreiben ohne confirm=True (kein Auto-Tagging ohne Bestätigung).")
    if not p.can_apply:
        raise ValueError("Kein bestätigter Labelcode — kein Rename/Tagging.")

    src = Path(p.path)
    dst_dir = Path(move_to) if move_to else src.parent
    dst = dst_dir / p.proposed_name
    # Kollisionsschutz VOR dem Schreiben: Path.rename überschreibt sonst still (Datenverlust).
    # Zieldatei existiert schon (und ist nicht die Quelle selbst) -> abbrechen, nichts anfassen.
    if dst.resolve() != src.resolve() and dst.exists():
        raise FileExistsError(f"Zieldatei existiert bereits: {dst.name}")

    # 1) Tags schreiben, alten Stand fürs Undo protokollieren
    tags.write_tags(src, p.proposed_tags)
    op_tag = operations.log(op_con, "tag", str(src), str(src),
                            json.dumps(p.current_tags, ensure_ascii=False))
    # 2) Umbenennen / verschieben. shutil.move (nicht Path.rename): fällt bei
    # Laufwerkswechsel (interne Platte -> externe/LC-verified auf anderem Volume)
    # auf Kopieren+Löschen zurück. Path.rename wirft dort OSError [Errno 18].
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    op_ren = operations.log(op_con, "rename", str(src), str(dst), "")

    return {"new_path": str(dst), "op_ids": [op_tag, op_ren]}
