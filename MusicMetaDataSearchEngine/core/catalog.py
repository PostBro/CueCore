"""Katalog-Kürzel von Production-Music-Libraries -> GVL-Labelcode.

Library-Dateien heißen oft nur nach Katalognummer (Cezame `CEZ4030_1.mp3`, KPM
`KPMLP1234_05`, Extreme `XCD123_04 Title`), und auch das Album-Tag trägt sie
(`CEZ4030 - Little French Fancies`). Das Kürzel gehört zum SUB-Label der Library — und das
Sub-Label ist, was bei der GEMA gemeldet wird; der Verlag (UPM, KPM,
Frederic Leibovitz Editeur) ist etwas anderes und steht im Verlag-Feld.

Zwei Quellen für Kürzel -> LC, in dieser Reihenfolge:
  1. `data/library_prefixes.csv` — die Listen der Libraries selbst (Extreme „Album-Code", KPM
     „Shortform"), gebaut von build_library_prefixes.py, nur Codes, die im GVL-Master stehen.
     Das ist die feine Auflösung: XCD -> LC01732, XTS -> LC35538, DWCD -> LC06822.
  2. GVL-Master `kurzname` — 2.438 Kürzel, davon 1.280 rein alphabetisch, aber grob:
     CEZ -> LC10347, KPM -> LC02061, UPM -> LC01946.

Das Kürzel allein ist EINE Quelle (der Master). Ein zweites, unabhängiges Signal ist der
TPUB-Tag der Datei, wenn er den GVL-Hersteller des Codes trifft (Cezame: TPUB
'Frederic Leibovitz Editeur' = Hersteller von LC10347). Auch dann bleibt es Band `review`,
nie Auto-Tagging: derselbe Hersteller kann mehrere Kürzel/Labels führen (CEZAME LC04952
neben CMG LC10347) — welches gemeldet wird, entscheidet der Mensch. Für CEZ ist es geklärt:
die Cezame-Website nennt in der Track-Info „LC : 10347“, Discogs trägt LC 10347
auf CEZ 4030 (2003) und CEZ4306/4307 (2017); LC04952 CEZAME liegt dort nur auf einer
CMG-Handels-CD von 2001 (CUB 01054).
"""
from __future__ import annotations
import re

# Buchstaben-Präfix (2–8, optional ein zweites kurzes Wort wie 'KPM WM' / 'SEL COM') + Nummer
# (3–5 Ziffern), optional EIN Trenner dazwischen, danach Ende oder Trenner. Fängt 'CEZ4030_1',
# 'CEZ4030 - Album', 'KPMLP 1234', 'XCD123_04 Title', 'UPM_1234', 'KPM WM 123'.
# Nicht: 'FT_BAM-AL87…' (nach FT kommen Buchstaben), '01 - Titel', 'Interpret - Titel'.
_CATALOG = re.compile(r"^\s*([A-Za-z]{2,8}(?:[ _][A-Za-z]{1,6})?)[ _-]?(\d{3,5})(?=[ _\-.]|$)")


# Dach-Kürzel, die im GVL-`kurzname` etwas ANDERES bedeuten. Belegt im GVL-Master:
#   * GENAU EINE Zeile trägt kurzname 'UPM': LC01946 „UNITY Production Music", Hersteller RING MUSIK GmbH.
#   * Universal Production Music GmbH führt 155 Labels, davon haben nur 10 überhaupt einen GVL-kurzname
#     (BER, SEE, PED, UTS, MODEL, …) — und KEINES davon heißt 'UPM'.
# In Dateinamen steht 'UPM_' dagegen immer für Universal Production Music ('UPM_KOS731_55_…'). Wer das Kürzel
# naiv im Master nachschlägt, landet deshalb zwangsläufig bei UNITY/RING MUSIK. Darum: nie allein
# nachschlagen, sondern über den Hersteller dahinter die Sub-Labels eingrenzen.
UMBRELLA_HERSTELLER = {"UPM": "Universal Production Music", "KPM": "KPM"}
_UMBRELLA_NOT_ALONE = frozenset({"UPM"})
# UPM-Dateien: 'UPM_<SUB><nn>_<track>_…' — das Sub-Kürzel trägt oft nur 1–2 Ziffern (BERTV61, INSD61, KOS7).
# Das Kürzel darf mit einer Ziffer beginnen: '3M' = 3Monkeys Production Music (LC96446), '1K' = 1K Music —
# beide Universal Production Music (Beispiel UPM_3M2_6_DANGER_TRANSPORT…). Mindestens ein
# Buchstabe muss drin sein, sonst wäre 'UPM_12345_6' ein Kürzel; die Nummer wird non-greedy abgetrennt.
_UPM_SUB = re.compile(r"^\s*UPM[ _]+((?=[A-Za-z0-9]{0,8}[A-Za-z])[A-Za-z0-9]{1,8}?)(\d{1,5})(?=[ _\-.]|$)",
                      re.IGNORECASE)


def parse_catalog_id(text: str) -> tuple[str, str] | None:
    """(PRÄFIX, Nummer) aus Dateiname/Clip-Name/Album — oder None. Präfix in Großbuchstaben,
    weil die GVL-Kürzel so geführt werden ('KPM WM' bleibt mit Leerzeichen; zum Nachschlagen
    normalisiert normalize_prefix)."""
    mu = _UPM_SUB.match(text or "")
    if mu:
        return "UPM_" + mu.group(1).upper(), mu.group(2)
    m = _CATALOG.match(text or "")
    if not m:
        return None
    return m.group(1).upper(), m.group(2)


# Nur Katalog-Token plus optionale Track-Nummer ('CEZ4030_1', 'KPMLP1234_05', 'XCD123 04'): kein
# Interpret/Titel-Schema. Der Dateiname-Parser darf so einen Namen nicht in Interpret '1' / Titel
# 'CEZ4030' zerlegen (sonst stünde das im Cue-Sheet, dazu käme ein sinnloser Netz-Resolve).
_CATALOG_ONLY = re.compile(r"^\s*[A-Za-z]{2,8}(?:[ _][A-Za-z]{1,6})?[ _-]?\d{3,5}(?:[ _\-.]+\d{1,3})?\s*$")


def is_catalog_only(text: str) -> bool:
    """True, wenn der Text NUR aus einer Katalognummer (+ optionaler Track-Nummer) besteht —
    'CEZ4030_1' ja, 'XCD123_04 Title' und 'Interpret - Titel' nein."""
    return parse_catalog_id(text) is not None and bool(_CATALOG_ONLY.match(text or ""))


def prefix_candidates(prefix: str) -> list[str]:
    """Aus 'UPM_BER' (Library-Kürzel + Sub-Label-Kürzel, UPM-Dateien) die Nachschlage-Reihenfolge:
    erst beides zusammen ('KPM WM' ist ein echtes Kürzel), dann das ZWEITE Wort allein (das
    Sub-Label: BER -> Berlin Production Music), zuletzt das erste (UPM allein wäre bei der GVL
    'UNITY Production Music' — deshalb ganz hinten, und catalog_candidate prüft es gegen TPUB)."""
    parts = [p for p in re.split(r"[ _]+", (prefix or "").strip()) if p]
    if not parts:
        return []
    if len(parts) == 1:
        # Ein Dach-Kürzel ohne Sub ('UPM_12345_…') darf NICHT nachgeschlagen werden: der GVL-kurzname UPM
        # gehört UNITY Production Music (RING MUSIK). Lieber kein Vorschlag als der falsche.
        return [] if parts[0].upper() in _UMBRELLA_NOT_ALONE else [parts[0]]
    out = [" ".join(parts), parts[-1]]
    if parts[0].upper() not in _UMBRELLA_NOT_ALONE:       # 'UPM' allein wäre UNITY — nie
        out.append(parts[0])
    return out


# PRO-Marker '[GEMA]' / '(PRS)', Anteile '50%' und IPI-Nummern '[100000001]' aus Library-Tags:
# 'Zac Jordan (PRS) 50% [100000001]' -> 'Zac Jordan'.
# Die Liste muss auch die selteneren Gesellschaften (IMRO bis KOMCA) enthalten, sonst bliebe
# 'Max Beispiel [IMRO]' oder 'Erika Testerin [MUST]' mit Marker in der songs.db stehen.
_PRO = (r"GEMA|PRS|MCPS|ASCAP|BMI|SESAC|SACEM|SOCAN|APRA|AMCOS|SIAE|SGAE|BUMA|STEMRA|STIM|KODA|TONO|TEOSTO|"
        r"AKM|SUISA|SABAM|IMRO|JASRAC|KOMCA|MUST|SADAIC|SACM|SAYCO|ECAD|SAMRO|SCD|ZAIKS|ARTISJUS|ACUM|IPRS|"
        r"NS|PRO|IPI|CAE")
_PRO_MARKER = re.compile(
    # „(PRS)" / „[GEMA]" / „( NS)" — der Marker allein, auch mit Leerzeichen in der Klammer
    rf"\s*[\[(]\s*(?:{_PRO})\s*[\])]"
    # Jede Klammer, in der eine 6+-stellige Zahl steht: das ist die IPI/CAE-Nummer, egal in welcher
    # Reihenfolge und mit welchem Anteil — „(PRS: 100000001, 100%)", „(100000002 SUISA, 100%)",
    # „[100000003]". Ein echter Name trägt keine sechsstellige Zahl in Klammern.
    # Diese Regel muss VOR dem Komma-Split greifen, sonst zerlegt der Split die Klammer und ein Rest
    # bleibt stehen („ZZQ VERLAG (100000002 SU)" bliebe sonst so in der Verlagszeile).
    r"|\s*[\[(][^()\[\]]*\d{6,}[^()\[\]]*[\])]"
    # Gesellschaft samt kurzer Nummer und/oder Anteil, in beiden Reihenfolgen: „(PRS 50%)", „(GEMA: 12345,
    # 50%)", „(50% GEMA)".
    rf"|\s*[\[(]\s*(?:{_PRO})\s*[:#]?\s*\d[\d\s.,%]*[\])]"
    rf"|\s*[\[(]\s*\d[\d\s.,%]*\s*(?:{_PRO})\s*[\])]"
    # Die IPI-Klammer, aber nicht geschlossen — „… (IMRO: 100000004" am Zeilenende. Entsteht, wenn eine
    # Quelle den Rest abgeschnitten hat. Nur bis zum nächsten Trenner: steht
    # sie mitten in der Liste, sind die Namen dahinter echt und bleiben stehen.
    r"|\s*[\[(][^()\[\],;/|]*\d{6,}[^()\[\],;/|]*(?=[,;/|]|$)"
    # „[12345]" — eine Nummer allein in eckigen Klammern
    r"|\s*\[\d{5,}\]"
    # nackter Anteil
    r"|\s*\d{1,3}(?:[.,]\d+)?\s*%",
    re.IGNORECASE)


def clean_writer_names(value: str) -> str:
    """Library-Tags schreiben 'Wanja Janeva [GEMA],David Quaas [GEMA]' oder 'Zac Jordan (PRS) 50%
    [100000001]' — PRO-Marker, Anteile und IPI raus, Komma/Slash-Listen zu unserer
    '; '-Konvention, dedupliziert, Reihenfolge stabil."""
    out: list[str] = []
    for part in re.split(r"[,;/]", _PRO_MARKER.sub("", value or "")):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return "; ".join(out)


# Katalog-Token IRGENDWO im Namen ('APL_MUSIC_-_THENERVE333_TK030_…' -> THENERVE333, TK030).
_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,10})[ _-]?(\d{3,5})(?=[ _\-.]|$)")
# Track-/Disc-Zähler sehen aus wie Katalog-Token, sind aber keine.
_TRACK_MARKERS = frozenset({"TK", "TR", "TRK", "TRACK", "CD", "DISC", "NO", "NR", "VOL", "BPM", "ID"})


def catalog_tokens(text: str) -> list[tuple[str, str]]:
    """Alle Katalog-Token in Lese-Reihenfolge, das start-verankerte zuerst (so bleibt 'UPM_BER'
    als Zweiwort-Kürzel erhalten), Track-Marker (TK030) raus, dedupliziert."""
    out: list[tuple[str, str]] = []
    first = parse_catalog_id(text)
    if first:
        out.append(first)
    for m in _TOKEN.finditer(text or ""):
        tok = (m.group(1).upper(), m.group(2))
        if tok[0] in _TRACK_MARKERS or tok in out:
            continue
        out.append(tok)
    return out


def sublabel_in_scope(sub: str, scope: set[str], idx) -> str:
    """Unbekanntes Sub-Kürzel unter einem Dach ('UPM_KOS731…', KOS steht in keiner Liste): das eine
    Label des Dach-Herstellers, dessen Name mit dem Kürzel beginnt (KOS -> KOSINUS, LC07131,
    Universal Production Music GmbH). Eindeutig oder '' — nie raten."""
    from .labelmatch import norm
    s = norm(sub)
    # Ab zwei Zeichen: '3M' -> 3Monkeys Production Music. Kurze Kürzel sind nur deshalb vertretbar, weil der
    # Scope schon auf EINEN Hersteller eingeschränkt ist und unten Eindeutigkeit verlangt wird.
    if len(s) < 2 or not scope:
        return ""
    hits = [code for code in scope if norm(idx.code2name.get(code, "")).startswith(s)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # KOS -> KOSINUS, KOSINUS ARTS, KOSINUS TRAILERS … (7 Kosinus-Labels): das Stammlabel ist der kürzeste
        # Name, den alle anderen als Präfix tragen — Vorschlag, gelb, der Mensch prüft (Recall vor Präzision).
        names = sorted(((norm(idx.code2name.get(code, "")), code) for code in hits), key=lambda x: len(x[0]))
        root = names[0][0]
        if root and all(n == root or n.startswith(root + " ") for n, _ in names):
            return names[0][1]
    return ""


# Library-Dateinamen tragen Titel und Komponisten hinter der Katalognummer:
#   UPM:  UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631
#         <Dach>_<Sub><Album>_<Track>_<Titelwörter>[_<Version>]_<Komponisten-Nachnamen>_<Track-ID 6–8 Ziffern>
#         (belegt durch UPM_BER1376_1_Hot_Vocal_Lead_Janeva_Quaas_2257691 mit Tags Titel 'Hot [Vocal Lead]',
#         Komponisten Janeva/Quaas — tests/test_intake.py). Titel und Nachnamen sind nur scharf trennbar, wenn ein
#         Versionswort (INSTRUMENTAL, VOCAL …) die Grenze markiert; sonst gilt der ganze Text als Titel.
#   KPM:  KPM_KPM_0858_00101_SCARS_A_MATT_BLACK_CHRISTIAN_J_O_LUNDBERG
#         <Dach>_<Sub>_<Album>_<Track>_<Titel>_<Versionsbuchstabe>_<Komponisten-Vollnamen> (JCM_JMOD_… ebenso).
_VERSION_WORDS = frozenset({"INSTRUMENTAL", "INSTR", "INST", "VOCAL", "VOCALS", "UNDERSCORE", "STING", "STINGER",
                            "BED", "LOOP", "ALT", "ALTERNATE", "ALTERNATIVE", "VERSION", "MIX", "EDIT", "CUTDOWN",
                            "REPRISE", "REMIX", "ACOUSTIC", "NO", "DRUMS", "PERC", "PIANO", "STRINGS", "LEAD"})
# Sub-Kürzel wie in _UPM_SUB: darf mit einer Ziffer beginnen ('3M2' = 3Monkeys, Album 2), braucht aber einen
# Buchstaben (sonst stünde bei UPM_3M2_6_… der rohe Dateiname als Titel im Cue-Sheet).
_UPM_NAME = re.compile(r"^\s*UPM[ _]+(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,8}?\d{1,5}[ _]+(\d{1,3})[ _]+(.+?)[ _]+(\d{6,8})\s*$",
                       re.IGNORECASE)
_KPM_NAME = re.compile(r"^\s*([A-Za-z]{2,8})[ _]+([A-Za-z]{2,8})[ _]+(\d{3,5})[ _]+(\d{3,6})[ _]+(.+?)[ _]+([A-Z])[ _]+(.+)$", re.IGNORECASE)


def _words_to_title(words: list[str]) -> str:
    return " ".join(w.capitalize() if w.isupper() else w for w in words)


def parse_library_name(text: str) -> dict | None:
    """Titel (und Komponisten-Hinweis) aus einem Library-Dateinamen — oder None, wenn es keins der bekannten
    Schemata ist. Rückgabe {'title', 'version', 'composer_hint', 'track', 'scheme'}; composer_hint sind
    Nachnamen (UPM) oder Vollnamen (KPM) als Text für die Karte, kein Feldwert (nicht verifiziert)."""
    s = (text or "").strip()
    m = _UPM_NAME.match(s)
    if m:
        words = [w for w in re.split(r"[ _]+", m.group(2)) if w]
        cut = next((i for i, w in enumerate(words) if w.upper() in _VERSION_WORDS), None)
        if cut is not None:
            title_words, rest = words[:cut], words[cut:]
            vers = [w for w in rest if w.upper() in _VERSION_WORDS]
            names = [w for w in rest if w.upper() not in _VERSION_WORDS]
        else:
            title_words, vers, names = words, [], []
        return {"title": _words_to_title(title_words) or _words_to_title(words), "version": " ".join(v.capitalize() for v in vers),
                "composer_hint": ", ".join(n.capitalize() for n in names), "track": m.group(1), "scheme": "upm"}
    m = _KPM_NAME.match(s)
    if m:
        title = _words_to_title([w for w in re.split(r"[ _]+", m.group(5)) if w])
        names = [w for w in re.split(r"[ _]+", m.group(7)) if w]
        # 'MATT_BLACK_CHRISTIAN_J_O_LUNDBERG': Vollnamen ohne Trenner — als ein Hinweistext, nicht zerlegt
        return {"title": title, "version": m.group(6).upper(), "composer_hint": " ".join(n.capitalize() if len(n) > 1 else n for n in names),
                "track": m.group(4).lstrip("0") or "0", "scheme": "kpm"}
    return None


def umbrella_words(text: str) -> list[str]:
    """Wörter VOR dem ersten Katalog-Token = die Library als Dach ('APL_MUSIC_-_THENERVE333' ->
    ['APL', 'MUSIC']). Damit lässt sich der Hersteller eingrenzen (APL Publishing GmbH)."""
    first = parse_catalog_id(text or "")
    if first and len(first[0].split("_")) > 1:
        # Zweiwort-Präfix aus dem start-verankerten Match ('UPM_3M' -> Dach 'UPM'). _TOKEN unten verlangt
        # Buchstaben am Anfang und verfehlt Kürzel wie '3M' — dann bliebe der Scope leer und das
        # Sub-Label würde nicht aufgelöst (Beispiel UPM_3M2_6_DANGER_TRANSPORT…).
        return [first[0].split("_")[0].upper()]
    m = _TOKEN.search(text or "")
    head = (text or "")[:m.start()] if m else ""
    words = [w.upper() for w in re.findall(r"[A-Za-z]{2,}", head)]
    # Steht das Token erst weit hinten ('CROSSROADS_OF_UNCERTAINTY_…_The_Scoring_House_TSH_289'),
    # sind die Wörter davor Titel/Komponist, kein Dach — dann kein Scope.
    return words if len(words) <= 3 else []


def normalize_prefix(prefix: str) -> str:
    """'KPM WM' / 'kpm_wm' / 'Sel Com' -> 'KPMWM' / 'SELCOM' — Schlüssel für beide Tabellen."""
    return re.sub(r"[^A-Z0-9]", "", (prefix or "").upper())


def load_library_prefixes(path) -> dict[str, dict]:
    """data/library_prefixes.csv -> {normalisiertes Kürzel: {labelcode, library, prefix}}.
    Fehlt die Datei -> {} (dann bleibt nur der GVL-kurzname)."""
    import csv
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = normalize_prefix(row.get("prefix", ""))
            code = (row.get("labelcode") or "").strip()
            if key and code and key not in out:
                out[key] = {"labelcode": code, "library": (row.get("library") or "").strip(),
                            "prefix": (row.get("prefix") or "").strip()}
    return out
