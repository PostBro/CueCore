"""Cue-Liste aus Excel/CSV: eine einfache Tabelle (Interpret, Titel, optional
Clip-/Dateiname, Dauer) statt einer EDL. Jede Zeile wird zu einem EDL-Event, dann läuft derselbe
Weg wie bei der EDL (`edl_import.cross_check`): songs.db → Netz → Karten. Spalten, die die Liste
schon mitbringt (Komponist, Texter, Verlag, Label, LC, ISRC, Album), werden auf die Karte gelegt,
wo sie leer ist — ein LC aus der Liste nur, wenn er im GVL-Master steht (Domain-Regel).
"""
from __future__ import annotations
import math
import re
from pathlib import Path

from . import edl_import, ingest
from .edl import EDLEvent
from .normalize import normalize_labelcode

# Zusatz-Spalten über die Song-Aliasse hinaus (core/ingest._ALIASES): Clip/Datei und Dauer.
LIST_ALIASES = {
    **ingest._ALIASES,
    "clip": ["clip", "clip_name", "clipname", "clip name", "dateiname", "datei", "filename", "file",
             "file name", "source file", "source_file", "quelle", "cue", "cue name"],
    "duration": ["dauer", "duration", "länge", "laenge", "length", "sekunden", "seconds", "sek",
                 "länge sek", "laenge sek", "duration_sec", "duration sec", "dur", "gema-dauer",
                 "gema dauer", "spieldauer", "netto"],
    "tc_in": ["tc in", "tc_in", "in", "timecode in", "record in", "rec in", "start", "beginn"],
    "tc_out": ["tc out", "tc_out", "out", "timecode out", "record out", "rec out", "ende", "end", "stop"],
}
# Felder, die die Liste direkt auf die Karte legen darf (Kartenfeld <- Listenfeld).
_CARD_FIELDS = (("komponist", "komponist"), ("texter", "texter"), ("verlag", "verlag"),
                ("album", "album"), ("isrc", "isrc"), ("iswc", "iswc"), ("gema_werknummer", "gema_werknummer"))

_TC = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[:;.](\d{1,3})$")      # hh:mm:ss:ff (Frames)
_HMS = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[.,](\d+))?$")  # [h:]m:ss[.frac]


def guess_list_mapping(header: list[str]) -> dict:
    """Spalten -> Felder, gleiche Regeln wie `ingest.guess_mapping` (trimmen, kleinschreiben,
    Punkt am Ende weg), plus Clip/Dauer/TC."""
    by_lower = {(h or "").strip().lower().rstrip("."): h for h in header}
    mapping: dict[str, str] = {}
    for field, aliases in LIST_ALIASES.items():
        for a in aliases:
            if a in by_lower:
                mapping[field] = by_lower[a]
                break
    return mapping


def parse_duration(text: str, fps: float = 25.0) -> float | None:
    """Dauer-Zelle -> Sekunden. Erlaubt: 83 · 83,5 · 1:23 · 0:01:23 · 00:01:23:12 (Frames bei fps)
    · '83 s'. Leer/unlesbar -> None."""
    s = (text or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"\s*(sek\.?|sec\.?|s)$", "", s)
    m = _TC.match(s)
    if m:
        h, mi, se, fr = (int(x) for x in m.groups())
        return h * 3600 + mi * 60 + se + (fr / fps if fps else 0)
    m = _HMS.match(s)
    if m:
        h, mi, se, frac = m.groups()
        val = (int(h) if h else 0) * 3600 + int(mi) * 60 + int(se)
        if frac:
            val += float("0." + frac)
        return float(val)
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def seconds_to_tc(seconds: float, fps: float = 25.0) -> str:
    """Sekunden -> hh:mm:ss:ff (non-drop), Frames aufgerundet, damit die GEMA-Dauer
    (`ceil`) nie unter der Listenangabe liegt."""
    frames = int(math.ceil(round(max(seconds, 0.0) * fps, 6)))
    fpi = max(int(round(fps)), 1)
    total_s, ff = divmod(frames, fpi)
    h, rem = divmod(total_s, 3600)
    mi, se = divmod(rem, 60)
    return f"{h:02d}:{mi:02d}:{se:02d}:{ff:02d}"


def rows_to_events(rows: list[dict], mapping: dict, fps: float = 25.0) -> tuple[list[EDLEvent], list[dict], list[str]]:
    """Zeilen -> (Events, Zusatzfelder je Event, Warnungen). Clip-Name = Clip-Spalte, sonst
    'Interpret - Titel' (so liest ihn `watch.parse_cue_from_stem` wie einen Dateinamen), sonst
    nur der Titel. Dauer aus Dauer-Spalte, sonst aus TC in/out, sonst 0."""
    events: list[EDLEvent] = []
    extras: list[dict] = []
    warnings: list[str] = []
    get = lambda row, f: (row.get(mapping[f], "") or "").strip() if f in mapping else ""
    skipped = 0
    for i, row in enumerate(rows, 1):
        artist, title, clip = get(row, "interpret"), get(row, "titel"), get(row, "clip")
        if not (artist or title or clip):
            skipped += 1
            continue
        name = clip or (f"{artist} - {title}" if artist and title else (title or artist))
        if clip and not Path(clip).suffix and not (artist or title):
            pass                                                   # nackter Clip-Name: der Parser versucht ihn
        secs = parse_duration(get(row, "duration"), fps)
        rec_in, rec_out = "00:00:00:00", ""
        if secs is None and "tc_in" in mapping and "tc_out" in mapping:
            a, b = parse_duration(get(row, "tc_in"), fps), parse_duration(get(row, "tc_out"), fps)
            if a is not None and b is not None and b >= a:
                secs = b - a
        if secs is None:
            if get(row, "duration"):
                warnings.append(f"Zeile {i}: Dauer „{get(row, 'duration')}“ nicht lesbar — 0 Sek.")
            secs = 0.0
        rec_out = seconds_to_tc(secs, fps)
        events.append(EDLEvent(f"{len(events) + 1:03d}", "AX", "AA", "C", rec_in, rec_out, rec_in, rec_out,
                               from_clip_name=name))
        ex = {card_f: get(row, list_f) for card_f, list_f in _CARD_FIELDS}
        ex["labelname"], ex["labelcode"] = get(row, "labelname"), get(row, "labelcode")
        ex["_artist"], ex["_title"] = artist, title
        extras.append(ex)
    if skipped:
        warnings.append(f"{skipped} leere Zeile(n) übersprungen.")
    return events, extras, warnings


def apply_row_fields(card: dict, ex: dict, label_lookup=None) -> None:
    """Werte aus der Liste auf die Karte legen, wo sie leer ist. Interpret/Titel aus eigenen
    Spalten schlagen den Dateinamen-Parser. Ein LC aus der Liste zählt nur, wenn der GVL-Master
    ihn kennt — dann mit dem offiziellen Label; sonst bleibt er weg (Note)."""
    if ex.get("_artist"):
        card["artist"] = ex["_artist"]
    if ex.get("_title"):
        card["title"] = ex["_title"]
    for k, _ in _CARD_FIELDS:
        if ex.get(k) and not (card.get(k) or ""):
            card[k] = ex[k]
    if card.get("labelcode"):
        return
    code = normalize_labelcode(ex.get("labelcode", "") or "")
    if code and label_lookup is not None:
        master_label, rr = label_lookup(code)
        if master_label:
            card["labelcode"], card["labelname"] = code, master_label
            if rr:
                card["rechterueckrufe"] = rr
            card["status"] = "list_lc"
            return
        card.setdefault("notes", []).append(f"LC{code} aus der Liste steht nicht im GVL-Master — nicht übernommen.")
    if ex.get("labelname") and not card.get("labelname"):
        card["labelname"] = ex["labelname"]


def import_list(path: str | Path, con, *, fps: float = 25.0, resolve_fn=None, label_lookup=None,
                catalog_fn=None) -> dict:
    """Datei (CSV/XLSX/XLS) -> Karten wie aus einer EDL. Rückgabe {cards, events, mapping,
    fieldnames, rows, warnings} oder {error}."""
    try:
        header, rows = ingest.read_table(path)
    except ValueError as e:
        return {"error": str(e)}
    mapping = guess_list_mapping(header)
    if not any(f in mapping for f in ("interpret", "titel", "clip")):
        return {"error": "Keine Spalte für Interpret, Titel oder Clip/Dateiname erkannt — "
                         f"gefunden: {', '.join(h for h in header if h) or '(keine Kopfzeile)'}"}
    events, extras, warnings = rows_to_events(rows, mapping, fps)
    if not events:
        return {"error": "Keine Zeilen mit Interpret, Titel oder Clip in der Liste."}
    cards = edl_import.cross_check(events, con, resolve_fn=resolve_fn, label_lookup=label_lookup,
                                   fps=fps, dedupe=False, catalog_fn=catalog_fn)
    for card, ex in zip(cards, extras):
        apply_row_fields(card, ex, label_lookup)
    return {"cards": cards, "events": len(events), "mapping": mapping, "fieldnames": header,
            "rows": len(rows), "warnings": warnings}
