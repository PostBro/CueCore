"""GVL-PDF-Parser (pymupdf, Wort-Koordinaten).

Strategie: Spaltenzuordnung über X-Anker (linkester Anker <= x0). Ein Datensatz
beginnt bei einem Labelcode-Token in der LC-Spalte und bleibt offen, bis der
nächste Labelcode auftaucht — auch über Seitengrenzen hinweg (Hersteller-/Label-
Namen brechen mehrzeilig und teils seitenübergreifend um).

Warum X-Koordinaten statt Zeilenreihenfolge: Kurzname, PLM und Rechterückrufe
sind optional. Aus reiner Zeilenfolge ist nicht entscheidbar, ob ein Wert z.B.
Kurzname oder Hersteller ist. Die X-Position macht es eindeutig.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pymupdf

from core.models import LabelRecord

# Spalten-Anker (x0) aus dem PDF-Header gemessen. Layout ist iText-generiert und
# über alle Seiten fix.
_ANCHORS: list[tuple[float, str]] = [
    (17.0, "labelcode"),
    (114.0, "label"),
    (211.0, "kurzname"),
    (308.0, "plm"),
    (362.0, "hersteller"),
    (460.0, "rechterueckrufe"),
]
_TOL = 6.0  # Toleranz für leichten Links-Drift
_FOOTER_Y = 760.0  # Footer (URL + Zeitstempel) sitzt unten bei y~766
_HEADER_Y = 95.0  # Spaltenheader nur auf Seite 0 bei y~84.5
_LC_TOKEN = re.compile(r"^\d{4,6}$")
_TS = re.compile(r"\d{2}\.\d{2}\.\d{4},?\s*\d{2}:\d{2}:\d{2}")
_HEADER_WORDS = {"Labelcode", "Label", "Kurzname", "PLM", "Hersteller", "Rechterückrufe"}


_SINGLE = re.compile(r"^[A-Za-z0-9]$")

# Rechte Spaltengrenze (=nächster Anker) je Spalte, die mehrzeilige Namen tragen kann.
_RIGHT_BOUND = {"label": 211.0, "kurzname": 308.0, "hersteller": 460.0}
_LEFT_BOUND = {"label": 114.0, "kurzname": 211.0, "hersteller": 362.0}
_FILL_THRESHOLD = 0.89  # kalibriert gegen die reale GVL-PDF: echte Mid-Word-Wraps
# liegen bei ~89.0-92%% Spaltenfüllung (u.a. 'Unterhaltungselekt|ronik' bei 89.89%,
# 'Zweigniederlassun|g' bei 89.88%), der engste bekannte legitime Mehrwort-Wrap
# ("WARNER MUSIC") bei ~85.6%, ein kleingeschriebener Stil-Fall ('sächsische
# zeitung') bei 88.6% — 0.89 trennt beide Gruppen sauber (0 Fehler in 90
# manuell geprüften Stichproben über alle 84.760 Records).
_WRAP_STOPWORDS = {  # kurze, echte Bindewörter mehrerer Sprachen — nie zusammenziehen
    "of", "the", "and", "von", "und", "der", "die", "das", "in", "for", "feat",
    "le", "la", "de", "du", "el", "di", "da", "van", "con", "y", "et", "e", "het",
    "les", "los", "al", "dei", "delle", "del", "della", "dello", "des", "zu", "am", "im",
    "ins", "ans", "aufs", "vom", "zum", "zur", "beim",
}
_SYSTEM_WORDLIST = Path("/usr/share/dict/words")
_wordlist_cache: frozenset[str] | None = None


def _default_wordlist() -> frozenset[str]:
    """Systemwörterbuch (macOS) als Sicherheitsnetz gegen das Zusammenziehen echter
    Wörter. Fehlt es, bleibt das Zusammenziehen komplett deaktiviert (sicherer
    Default = heutiges Verhalten), statt ungeprüft zu raten."""
    global _wordlist_cache
    if _wordlist_cache is None:
        try:
            with open(_SYSTEM_WORDLIST, encoding="utf-8", errors="ignore") as f:
                _wordlist_cache = frozenset(w.strip().lower() for w in f if w.strip())
        except OSError:
            _wordlist_cache = frozenset()
    return _wordlist_cache


def _is_forced_wrap(prev_text: str, prev_x1: float, cur_text: str, column: str,
                    word_set: frozenset[str]) -> bool:
    """True, wenn ein GVL-PDF-Zeilenumbruch mitten im Wort erzwungen wurde (keine
    echte Leerstelle im Original) — dann beim Join OHNE Leerzeichen zusammenziehen.

    Bewusst konservativ: nur bei
    (1) sehr hoher Spaltenfüllung der vorherigen Zeile (>= _FILL_THRESHOLD),
    (2) Folge-Token beginnt klein und ist kein bekanntes Bindewort,
    (3) Folge-Token enthält keinen Punkt (Abkürzungsfragmente wie 'S.'+'r.l.' oder
        'Group'+'s.r.l.' bleiben unangetastet — dort ist nicht sicher entscheidbar,
        ob ein Leerzeichen gehört),
    (4) das vorherige Token ist selbst KEIN eigenständiges echtes Wörterbuch-Wort
        (schützt z.B. 'creative'+'media' oder 'COMUNICAZIONE'+'di' vor Verschmelzung).
    Ohne verfügbares Wörterbuch (word_set leer) greift die Regel nie.
    """
    if not word_set or column not in _RIGHT_BOUND:
        return False
    if not cur_text or not (cur_text[0].isalpha() and cur_text[0].islower()):
        return False
    if cur_text.lower() in _WRAP_STOPWORDS or "." in cur_text:
        return False
    left, right = _LEFT_BOUND[column], _RIGHT_BOUND[column]
    fill = (prev_x1 - left) / (right - left)
    if fill < _FILL_THRESHOLD:
        return False
    return prev_text.lower().strip(".,") not in word_set


def _merge_wrapped(toks: list[tuple[str, float, float, tuple]], column: str,
                   word_set: frozenset[str]) -> list[str]:
    """Tokens EINER Zelle (mit Geometrie): erzwungene Mid-Word-Wraps über Zeilen
    hinweg ohne Leerzeichen zusammenziehen, alles andere unverändert lassen —
    der normale Leerzeichen-Join passiert danach in _join_cell()."""
    if not toks:
        return []
    merged: list[tuple[str, float, float, tuple]] = [toks[0]]
    for text, x0, x1, line_id in toks[1:]:
        ptext, px0, px1, pline = merged[-1]
        if pline != line_id and _is_forced_wrap(ptext, px1, text, column, word_set):
            merged[-1] = (ptext + text, px0, x1, line_id)
        else:
            merged.append((text, x0, x1, line_id))
    return [t for t, _, _, _ in merged]


def _join_cell(tokens: list[str]) -> str:
    """Zellwörter zu String. Letter-Spacing-Artefakt korrigieren.

    GVL rendert manche Label mit Tracking ('GLISSANDO' -> 'G L I S S A N D O',
    'WaZ' -> 'W a Z'). pymupdf liest jeden Buchstaben als eigenes Wort.

    Zwei Fälle:
      1. Die GANZE Zelle besteht nur aus Einzelzeichen (>=2) -> komplett ohne
         Leerzeichen zusammenziehen. Ein echtes Label aus N separaten Einzel-
         buchstaben existiert nicht; Symbol-/Mehrwortzellen ('L & R',
         'A B Company') sind dadurch ausgenommen.
      2. Eingebetteter Run von >=3 Einzelzeichen in einer sonst normalen Zelle
         ('B B C Radio') -> nur den Run zusammenziehen.
    """
    alnum = [t for t in tokens if _SINGLE.match(t)]
    if len(tokens) >= 2 and len(alnum) == len(tokens):
        return "".join(tokens)

    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        if _SINGLE.match(tokens[i]):
            j = i
            while j < n and _SINGLE.match(tokens[j]):
                j += 1
            run = tokens[i:j]
            if len(run) >= 3:
                out.append("".join(run))
            else:
                out.extend(run)
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)


def _column_for(x0: float) -> str:
    """Linkester Anker <= x0 (+Toleranz). Robust gegen Mid-Line-Drift."""
    col = _ANCHORS[0][1]
    for anchor_x, name in _ANCHORS:
        if x0 + _TOL >= anchor_x:
            col = name
        else:
            break
    return col


def extract_source_version(doc: pymupdf.Document) -> str:
    """PDF-Kopf-Zeitstempel als Versionsstand, ISO-Datum 'YYYY-MM-DD'.

    Primär aus den PDF-Metadaten (creationDate), Fallback aus dem Seitentext.
    """
    cd = doc.metadata.get("creationDate", "") if doc.metadata else ""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", cd or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # Fallback: Zeitstempel im Text (Format 23.06.2026, 07:07:01)
    text = doc[0].get_text()
    m2 = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m2:
        return f"{m2.group(3)}-{m2.group(2)}-{m2.group(1)}"
    return "unknown"


def _iter_clean_words(doc: pymupdf.Document) -> Iterator[tuple[str, str, float, float, tuple]]:
    """Liefert (column, text, x0, x1, line_id) in Lesereihenfolge über alle Seiten,
    Möbel gefiltert. line_id identifiziert die physische PDF-Druckzeile (für die
    Mid-Word-Wrap-Erkennung in _merge_wrapped)."""
    for pno, page in enumerate(doc):
        words = page.get_text("words")  # (x0,y0,x1,y1,text,block,line,word)
        words.sort(key=lambda w: (round(w[1]), w[0]))
        for w in words:
            x0, y0, x1, text, block, line = w[0], w[1], w[2], w[4], w[5], w[6]
            if y0 > _FOOTER_Y:  # Footer (URL + Zeitstempel)
                continue
            if pno == 0 and y0 < _HEADER_Y and text in _HEADER_WORDS:
                continue
            if text.startswith("https://") or _TS.match(text):
                continue
            yield _column_for(x0), text, x0, x1, (pno, block, line)


def parse_pdf(path: str | Path, *, word_set: frozenset[str] | None = None
             ) -> tuple[list[LabelRecord], str]:
    """Parst das GVL-PDF zu LabelRecords. Gibt (records, source_version) zurück.

    Datensätze werden NICHT normalisiert — das macht der Aufrufer bewusst auf
    beiden Diff-Seiten identisch.

    word_set: Wörterbuch fürs Mid-Word-Wrap-Sicherheitsnetz (s. _is_forced_wrap).
    Default: Systemwörterbuch, sonst leer (= Zusammenziehen deaktiviert). Für
    Tests explizit übergebbar.
    """
    doc = pymupdf.open(str(path))
    try:
        ws = _default_wordlist() if word_set is None else word_set
        source_version = extract_source_version(doc)
        records: list[LabelRecord] = []
        buf: dict[str, list[tuple[str, float, float, tuple]]] | None = None

        def flush():
            if buf is None:
                return
            records.append(
                LabelRecord(
                    labelcode=" ".join(t for t, *_ in buf["labelcode"]),
                    label=_join_cell(_merge_wrapped(buf["label"], "label", ws)),
                    kurzname=_join_cell(_merge_wrapped(buf["kurzname"], "kurzname", ws)),
                    plm=" ".join(t for t, *_ in buf["plm"]),
                    hersteller=_join_cell(_merge_wrapped(buf["hersteller"], "hersteller", ws)),
                    rechterueckrufe=" ".join(t for t, *_ in buf["rechterueckrufe"]),
                )
            )

        for col, text, x0, x1, line_id in _iter_clean_words(doc):
            if col == "labelcode" and _LC_TOKEN.match(text):
                flush()
                buf = {name: [] for _, name in _ANCHORS}
                buf["labelcode"].append((text, x0, x1, line_id))
            elif buf is not None:
                buf[col].append((text, x0, x1, line_id))
            # Wörter vor dem ersten Labelcode (sollte es nicht geben) werden verworfen.
        flush()
        return records, source_version
    finally:
        doc.close()
