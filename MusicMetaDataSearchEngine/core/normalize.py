"""Normalisierung — auf PDF- und CSV-Seite identisch, vor dem Diff angewandt.

Niemals labelcode als Integer parsen (führende Nullen).
"""
from __future__ import annotations

import re

from .models import LabelRecord

# Echte Rechterückruf-Codes: exakt R1..R13. Firmennamen wie "R7 MUSIC LTD",
# "R2R Music", "R4 USA" sind KEINE R-Codes.
_R_CODE = re.compile(r"^R(?:[1-9]|1[0-3])$")
_LC_DIGITS = re.compile(r"\d+")
# Tausender-Trenner innerhalb EINES Codes: 'LC 24 584', 'LC 100 148', '24.584' (Leerzeichen, auch
# geschützte, oder Punkt vor genau drei Ziffern). Ohne das nahm _LC_DIGITS nur die erste Gruppe —
# LC00024 ROYALBUNKER statt LC24584 MOM & POP. Zwei Codes ('LC 0309 / LC 1234') oder
# Code + Jahr ('LC24584 2003') bleiben getrennt.
_LC_GROUP_SEP = re.compile(r"(?<=\d)(?:\s+|\.)(?=\d{3}(?!\d))")
_WS = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """Trim + Mehrfach-Whitespace zu einem Space. None -> ''."""
    if not value:
        return ""
    return _WS.sub(" ", value).strip()


def normalize_plm(value: str | None) -> str:
    """PLM ist 'Ja' oder ''. Alles andere (inkl. 'ja', Leerstring) -> normalisiert."""
    return "Ja" if clean_text(value).lower() == "ja" else ""


def normalize_rechterueckrufe(value: str | None) -> str:
    """';'-getrennt, nur echte R1..R13, getrimmt, numerisch sortiert, ';'-join."""
    raw = clean_text(value)
    if not raw:
        return ""
    codes = []
    for token in raw.split(";"):
        token = token.strip()
        if _R_CODE.match(token):
            codes.append(token)
    codes = sorted(set(codes), key=lambda c: int(c[1:]))
    return ";".join(codes)


def normalize_labelcode(value: str) -> str:
    """labelcode als String mit führenden Nullen.

    Stelligkeitslogik:
      - <=5 Ziffern -> links auf 5 auffüllen (LC0309 == LC00309).
      - 6 Ziffern   -> unverändert (LC01303 != LC101303).
    Eingaben dürfen 'LC'-Präfix/Spaces tragen (User/MusicBrainz/Discogs), auch
    Tausender-Gruppen ('LC 24 584'); in CSV/PDF stehen reine Ziffern.
    """
    m = _LC_DIGITS.search(_LC_GROUP_SEP.sub("", value or ""))
    if not m:
        return ""
    digits = m.group(0)
    if len(digits) <= 5:
        return digits.zfill(5)
    return digits


def single_labelcode(value: str | None) -> str:
    """Wie normalize_labelcode, aber nur, wenn der Wert genau EINEN Code trägt ('LC 24 584', '121');
    '' bei keinem oder mehreren ('LC00121 / LC00392') — für Bereinigungen, die nichts verwerfen dürfen."""
    joined = _LC_GROUP_SEP.sub("", value or "")
    return normalize_labelcode(joined) if len(_LC_DIGITS.findall(joined)) == 1 else ""


# Eingabe eines Menschen (Editor, Tagger, Import): genau EIN Code — optional 'LC' (auch 'LC-', 'LC:'), dann
# Ziffern, höchstens als Tausendergruppen mit Leerzeichen oder Punkt ('LC 24 584', '24.584'). Alles andere
# ('LC 24-584', 'LC2458O', 'LC00121 / LC00392') ist nicht lesbar — nie still auf den ersten Ziffernblock kürzen.
_LC_STRICT = re.compile(r"^(?:LC\s*[-:]?\s*)?(\d{1,6}|\d{1,3}(?:[ .\u00a0]\d{3})+)$", re.IGNORECASE)


def strict_labelcode(value: str | None) -> str | None:
    """Labelcode aus einer Eingabe: Ziffern mit führenden Nullen wie normalize_labelcode, '' bei leerer
    Eingabe, None bei allem, was nicht genau ein sauber geschriebener Code ist."""
    s = clean_text(value)
    if not s:
        return ""
    m = _LC_STRICT.match(s)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return normalize_labelcode(digits) if len(digits) <= 6 else None


def to_filename_lc(labelcode: str) -> str:
    """Labelcode -> Dateinamen-Form 'LCXXXXX' (kein Leerzeichen). Zugleich die Speicherform in
    songs.db und jedem Export. '' ohne Ziffern — kein nacktes 'LC'."""
    code = normalize_labelcode(labelcode)
    return "LC" + code if code else ""


def export_lc(labelcode: str | None) -> str:
    """Labelcode für Exporte: immer to_filename_lc ('LC' + Ziffern, kein Leerzeichen), egal ob
    er mit oder ohne 'LC' gespeichert ist. Leer bleibt leer; ein Wert ohne Ziffern ist kein Code und bleibt
    stehen, wie er ist (nichts still verwerfen)."""
    s = clean_text(labelcode)
    code = single_labelcode(s)   # mehrere Codes in einem Feld ('LC00121 / LC00392') bleiben wie sie sind
    return "LC" + code if code else s


_ISRC = re.compile(r"[A-Z]{2}[A-Z0-9]{3}[0-9]{7}")
_ISRC_PREFIX = re.compile(r"^ISRC[\s:]*", re.I)


def isrc_plain(value: str | None) -> str:
    """ISRC für Daten (songs.db, Datei-Tag, CSV/XLSX): die einfache 12-stellige Form ohne Bindestriche und ohne
    Präfix, wie die deutsche ISRC-Agentur (BVMI) es für Datenbanken vorschreibt. Kein gültiger ISRC -> Wert bleibt,
    wie er ist (nichts still verfälschen)."""
    s = clean_text(value)
    code = re.sub(r"[^A-Za-z0-9]", "", _ISRC_PREFIX.sub("", s)).upper()
    return code if _ISRC.fullmatch(code) else s


def isrc_display(value: str | None) -> str:
    """ISRC in schriftlicher Form (PDF): Bestandteile durch Bindestriche getrennt, CC-XXX-YY-NNNNN (IFPI/BVMI).
    Das Präfix „ISRC“ steht als Spaltenkopf. Kein gültiger ISRC -> Wert bleibt, wie er ist."""
    code = isrc_plain(value)
    return f"{code[:2]}-{code[2:5]}-{code[5:7]}-{code[7:]}" if _ISRC.fullmatch(code) else code


def normalize_record(rec: LabelRecord) -> LabelRecord:
    """Vollständige Datensatz-Normalisierung, idempotent."""
    return LabelRecord(
        labelcode=normalize_labelcode(rec.labelcode),
        label=clean_text(rec.label),
        kurzname=clean_text(rec.kurzname),
        plm=normalize_plm(rec.plm),
        hersteller=clean_text(rec.hersteller),
        rechterueckrufe=normalize_rechterueckrufe(rec.rechterueckrufe),
    )
