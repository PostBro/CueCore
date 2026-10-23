"""Intake-Orchestrierung für den Tagger-UI. Nicht-destruktiv.

scan_folder(): Audiodateien eines Ordners + Identify (Tags/Name) — kein Netz,
kein Schreiben. resolve_card(): EINE Datei auflösen (resolve_fn injiziert) ->
Vorschlags-Karte mit LC, offizieller GVL-Schreibweise, Plan (current vs
proposed). Der eigentliche Schreibvorgang läuft NICHT hier, sondern über
tagging.apply() (confirm + LC).

Formate: siehe tags.SUPPORTED_EXTENSIONS (MP3/WAV/FLAC/M4A; Mapping-Lücken pro
Format in tags.py dokumentiert).
"""
from __future__ import annotations
import re
from pathlib import Path

from . import tagging, tags, watch

# schon nach Schema benannt? '... - [Label_LCXXXXX].<ext>'
_SCHEMA_NAME = re.compile(
    r"\[[^\]]*_LC\d{5,6}\]\.(" + "|".join(e.lstrip(".") for e in tags.SUPPORTED_EXTENSIONS) + r")$",
    re.IGNORECASE,
)


def is_schema_named(name: str) -> bool:
    return bool(_SCHEMA_NAME.search(name))


def scan_folder(folder: str | Path, *, label_lookup=None, library_lookup=None) -> list[dict]:
    """Audiodateien direkt im Ordner (nicht rekursiv -> Zielordner/'_done'
    bleiben aussen vor). Pro Datei: Pfad, Name, identifizierte(r) Artist/Titel,
    aktuelle Tags, Flags.

    label_lookup(code)->(offizielle GVL-Schreibweise, rechterueckrufe): optional.
    Ist es gesetzt und trägt der Dateiname einen Labelcode, der IM GVL-Master
    existiert, kommt er als geprüfter Vorschlag (filename_lc/filename_label/
    filename_rr) mit — nicht im Master gefundene Codes werden verworfen (kein Rate).
    library_lookup(name)->dict|None: Library-Lieferung (songsdb.find_by_file) — trifft der
    Dateiname („CEZ4030_1.mp3“), sind Artist/Titel identifiziert und `library_file` trägt
    Sub-Katalog, Katalognummer, Label und Code der Datei.
    """
    folder = Path(folder)
    cards: list[dict] = []
    # case-insensitiv + nicht-rekursiv (iterdir, kein glob): '.MP3' wird mitgenommen,
    # Unterordner (z.B. der Zielordner) bleiben aussen vor.
    audio_files = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in tags.SUPPORTED_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )
    for p in audio_files:
        artist, title = watch.identify(p)
        lib = library_lookup(p.name) if library_lookup is not None else None
        if lib:
            artist, title = lib["interpret"], lib["titel"]
        card = {
            "path": str(p), "name": p.name,
            "artist": artist, "title": title,
            "album": watch.identify_album(p),
            "current_tags": tags.read_tags(p),
            "identified": bool(artist and title),
            "already_named": is_schema_named(p.name),
            "filename_lc": "", "filename_label": "", "filename_rr": "",
            "library_file": ({"file": lib["file"], "library": lib.get("library", ""), "sublibrary": lib.get("sublibrary") or "",
                              "catno": lib.get("catno") or "", "labelcode": lib.get("labelcode") or "",
                              "labelname": lib.get("labelname") or ""} if lib else None),
        }
        code = watch.extract_lc_from_stem(p.stem)
        if code and label_lookup is not None:
            label, rr = label_lookup(code)
            if label:                      # nur wenn der Code IM GVL-Master steht
                card["filename_lc"], card["filename_label"], card["filename_rr"] = code, label, rr
        cards.append(card)
    return cards


def resolve_card(path: str | Path, artist: str, title: str, *, resolve_fn, label_lookup,
                 recognize=None, isrc: str = "", catalog_fn=None, verlag_hint_fn=None) -> dict:
    """Eine Datei auflösen -> Karte für die UI. resolve_fn(a,t,isrc)->ResolveResult;
    label_lookup(code)->(offizielle GVL-Schreibweise, rechterueckrufe).

    isrc: für die ISRC-Stufe-0 des Resolvers. Leer -> aus den Datei-Tags gelesen; der
    ISRC steht in allen vier Formaten (tags.py) und war bisher reiner Output.

    recognize(path, expected_artist, expected_title)->{artist,title,possible_cover,...}|None:
    optionaler Fingerprinting-Fallback. Wird NUR aufgerufen,
    wenn Artist/Titel unvollständig sind (Tags + Name haben versagt) — kostet Geld.

    catalog_fn(text, tpub)->Candidate|None: Katalog-Kürzel (CEZ4030_1) -> GVL-Kurzname-Kandidat,
    greift NUR, wenn die Netz-Suche keinen Kandidaten hat (Library-Musik steht selten bei
    MusicBrainz). Band bleibt review — kein Auto-Tagging.
    verlag_hint_fn(name)->str: trifft der TPUB-Tag einen GVL-HERSTELLER statt ein Label
    (Library-Dateien: 'Frederic Leibovitz Editeur'), gehört der Wert ins Verlag-Feld, nicht ins
    Label. Kommt als `verlag_hint` mit, wenn der Resolver keinen Verlag liefert.

    Schreibt nichts: tagging.plan() ist nicht-destruktiv (nur Lesen + Berechnen).
    """
    fingerprinted = False
    fingerprint_source = ""          # 'acoustid' | 'audd' — die Karte nennt, wer erkannt hat
    fp_isrc = ""
    possible_cover = False
    if not (artist and title) and recognize is not None:
        info = recognize(str(path), artist, title)
        if info and info.get("artist") and info.get("title"):
            artist, title = info["artist"], info["title"]
            fp_isrc = info.get("isrc", "") or ""       # AcoustID kennt den ISRC der Aufnahme: exakte Stufe 0
            fingerprinted = True
            fingerprint_source = info.get("fingerprint_source") or ""
            possible_cover = bool(info.get("possible_cover"))

    if not (artist and title):        # weder Tags/Name noch Fingerprint -> nichts aufzulösen
        return {
            "path": str(path), "artist": artist, "title": title,
            "matched": False, "band": "no_match", "incomplete": False,
            "album": "", "date": "", "composer": "", "lyricist": "", "publisher": "", "via_isrc": False,
            "isrcs": [], "iswcs": [], "gema_werknummer": "", "notes": ["nicht identifizierbar (keine Tags/Name, Fingerprint erfolglos)"],
            "fingerprinted": fingerprinted, "fingerprint_source": fingerprint_source, "possible_cover": possible_cover,
            "best": None, "plan": None,
        }

    file_tags = tags.read_tags(path) or {}
    # ISRC der Datei vor dem aus dem Fingerabdruck: der Tag ist die Angabe der Datei selbst
    res = resolve_fn(artist, title, isrc or file_tags.get("isrc", "") or fp_isrc)
    catalog_hint = ""
    if not res.best and catalog_fn is not None:
        # Dateiname zuerst (CEZ4030_1), dann Album-Tag (CEZ4030 - Little French Fancies)
        for text in (Path(path).stem, file_tags.get("album", "")):
            cand = catalog_fn(text, file_tags.get("label", "")) if text else None
            if cand:
                res.candidates.append(cand)
                catalog_hint = cand.sources[0] if cand.sources else "Katalog-Kürzel"
                res.notes.append(f"{catalog_hint} → LC{cand.labelcode} {cand.label} (GVL-Kurzname) — "
                                 "Library-Katalognummer, bitte prüfen; kein Auto-Tagging")
                break
    verlag_hint = ""
    if not res.publisher and verlag_hint_fn is not None and file_tags.get("label"):
        verlag_hint = verlag_hint_fn(file_tags["label"]) or ""
    # Library-Dateien tragen Komponist/Texter/Album oft NUR in den Tags (TCOM 'Name [GEMA],Name
    # [GEMA]'), das Netz kennt sie nicht -> Tags als Rückfall, bereinigt auf '; '-Listen.
    from .catalog import clean_writer_names
    composer = res.composer or clean_writer_names(file_tags.get("composer", ""))
    lyricist = res.lyricist or clean_writer_names(file_tags.get("lyricist", ""))
    album = res.album or (file_tags.get("album", "") or "").strip()
    card: dict = {
        "path": str(path),
        "artist": res.artist or artist, "title": res.title or title,
        "matched": res.matched, "band": res.band, "incomplete": res.incomplete,
        "index": bool(getattr(res, "from_index", False)),   # Track-Index: UI zeigt Dual-Source nicht als Auto-Confirm
        "via_isrc": res.via_isrc,
        "ambiguous": getattr(res, "ambiguous", False), "tied": list(getattr(res, "tied", [])),
        "album": album, "date": res.date,
        "composer": composer, "lyricist": lyricist, "publisher": res.publisher,
        "verlag_hint": verlag_hint, "catalog_hint": catalog_hint,
        "isrcs": list(res.isrcs), "notes": list(res.notes),
        "iswcs": list(res.iswcs), "gema_werknummer": res.gema_werknummer, "gema_source": res.gema_source,
        "version": res.version, "version_mismatch": res.version_mismatch,
        "fingerprinted": fingerprinted, "fingerprint_source": fingerprint_source, "possible_cover": possible_cover,
        "best": None, "plan": None,
    }
    best = res.best
    if not best:
        return card

    if card["ambiguous"]:
        # Margin-Gate: die gleichauf liegenden Kandidaten mit GVL-Namen auf die Karte —
        # der Mensch wählt, nichts wird vorbelegt-und-gesperrt.
        codes = set(res.tied)
        card["tied"] = [{"labelcode": c.labelcode, "label": label_lookup(c.labelcode)[0] or c.label,
                         "confidence": c.confidence, "sources": list(c.sources),
                         "source_urls": list(c.source_urls)}
                        for c in res.candidates if c.labelcode in codes]

    label, rr = label_lookup(best.labelcode)
    label = label or best.label                      # offizielle GVL-Schreibweise hat Vorrang
    isrc = res.isrcs[0] if res.isrcs else ""
    pl = tagging.plan(path, card["artist"], card["title"], best.labelcode, label,
                      isrc=isrc, rechterueckrufe=rr,
                      album=album, composer=composer, lyricist=lyricist)
    card["best"] = {
        "labelcode": best.labelcode, "label": label, "lc": pl.lc,
        "confidence": best.confidence, "dual_source": best.dual_source,
        "sources": list(best.sources), "source_urls": list(best.source_urls),
        "rechterueckrufe": rr,
    }
    warnings, can_apply = list(pl.warnings), pl.can_apply
    if card["ambiguous"]:
        # Gleichstand -> kein Taggen mit dem Zufallssieger; der Plan gilt erst nach der Wahl.
        can_apply = False
        warnings.insert(0, "Gleichstand — Labelcode wählen: " + " / ".join(
            f"LC{c['labelcode']} {c['label']}".strip() for c in card["tied"]))
    card["plan"] = {
        "current_tags": pl.current_tags, "proposed_tags": pl.proposed_tags,
        "current_name": pl.current_name, "proposed_name": pl.proposed_name,
        "warnings": warnings, "can_apply": can_apply,
    }
    return card
