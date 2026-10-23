"""LC-Resolver: artist+title -> GVL-LC-Kandidaten mit Confidence + Dual-Source.

Flow: MusicBrainz (Labelnamen, ISRC, Discogs-Links) -> labelmatch gegen den GVL-
Index -> falls Discogs-Release(s) verlinkt, deren Label Codes ziehen und GEGEN den
GVL-Master prüfen (Dual-Source). Confidence nach Quellen-Übereinstimmung (Startgewichte).

resolve() ist netzfrei: die Lookups werden injiziert. Damit ist die ganze
Orchestrierung deterministisch testbar (Tests mocken MusicBrainz/Discogs).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from .labelmatch import LabelIndex, match_label, norm as _lm_norm
from .musicbrainz import primary_artist, strip_feat
from .versions import OWN_RELEASE, detect_version, version_kind

# Confidence nach Quellen-ÜBEREINSTIMMUNG:
# MB-Score-Anteil + je unabhängiger Quelle, die denselben GVL-LC bestätigt.
# Zwei unabhängige Bestätigungen (z.B. Discogs + Deezer) -> Auto-Confirm.
W_MB = 0.2            # MusicBrainz-Recording-Score-Anteil (0..100)
W_CORROB = 0.3       # je weiterer unabhängiger Quelle (Discogs, Deezer, …) auf denselben LC
W_ISRC = 0.1         # Aufnahme exakt über den ISRC identifiziert: wiegt mehr als eine Textsuche, ersetzt keine Zweitquelle
AUTO_CONFIRM = 0.8
REVIEW_FLOOR = 0.5
# Margin-Gate: liegen die zwei besten Kandidaten dichter beieinander als MARGIN, ist das ein
# Gleichstand. Die Confidence zählt nur die ANZAHL der Bestätigungen, nicht welche Quelle —
# zwei Label derselben Veröffentlichung (MB+Deezer gegen MB+Discogs) landen beide auf 0.5, und
# das Ranking bräche das über (-quality, labelcode) still auf. In Stichproben war bei Gleichstand
# öfter der ZWEITE Kandidat der richtige. Deshalb: Gleichstand melden, nie auto, Mensch entscheidet.
MARGIN = 0.15
AMBIGUOUS_NOTE = "Gleichstand — mehrere Label gleich gut belegt, bitte Quellen prüfen"


@dataclass
class Candidate:
    labelcode: str
    label: str
    sources: list[str] = field(default_factory=list)  # 'gvl+musicbrainz' | 'discogs'
    source_urls: list[dict] = field(default_factory=list)  # [{name,url}] zum Nachprüfen
    dual_source: bool = False
    confidence: float = 0.0
    quality: int = 0   # beste Release-Qualität: trennt echtes Label vom Comp-/Promo-Rauschen


@dataclass
class ResolveResult:
    artist: str
    title: str
    matched: bool = False
    mb_score: int = 0
    isrcs: list[str] = field(default_factory=list)
    composer: str = ""
    lyricist: str = ""
    publisher: str = ""        # Musikverlag (GEMA-Seite) — getrennt vom Label (GVL-Seite)
    iswcs: list[str] = field(default_factory=list)   # mehrere: alle zeigen, nicht raten
    gema_werknummer: str = ""  # songs.db (verifiziert) oder, als Vorschlag, die GEMA ID am MusicBrainz-Werk
    gema_source: str = ""      # 'songs.db' | 'musicbrainz' (Community-Daten: nur Vorschlag, bitte prüfen)
    album: str = ""
    date: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    author_source_url: str = ""    # Quellenlink der Autoren, wenn sie nicht aus MusicBrainz kommen (Genius)
    label_hints: list[str] = field(default_factory=list)  # gefundene Labelnamen OHNE GVL-Code (never-empty)
    incomplete: bool = False   # Abfrage lückenhaft (Rate-Limit) -> nicht als sicher behandeln
    via_isrc: bool = False     # Stufe 0 hat getroffen: Aufnahme exakt über ISRC identifiziert
    swapped: bool = False      # Interpret/Titel kamen vertauscht an und wurden getauscht aufgelöst
    ambiguous: bool = False    # Margin-Gate: Top-Kandidaten liegen innerhalb MARGIN -> nie auto
    tied: list[str] = field(default_factory=list)   # die gleichauf liegenden Codes, Top zuerst
    from_index: bool = False   # Treffer aus dem lokalen Track-Index (Dump-Daten, kein Netz, nie auto)
    index_tier: str = ""       # Prüfstufe des Index-Treffers, s. INDEX_TIERS
    index_rule_pick: str = ""  # bei Widerspruch: Code nach Regel (index_rule_pick), nur Vorschlag, nie auto
    version: str = ""          # erkannte Version im Titel („Remix (Mark Ronson)“, „Live“), s. core/versions.py
    version_mismatch: bool = False   # gesucht war eine Version, MusicBrainz fand nur die Originalfassung -> nie auto

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def band(self) -> str:
        """auto (>=0.8) | review (>=0.5) | review_low (<0.5) | no_match."""
        if not self.candidates:
            return "no_match"
        top = self.candidates[0].confidence
        if top >= AUTO_CONFIRM and not self.ambiguous and not self.version_mismatch:
            return "auto"
        if top >= REVIEW_FLOOR:
            return "review"
        return "review_low"


def confidence(*, mb_score: int, corroborations: int, local_verified: bool = False, via_isrc: bool = False) -> float:
    """Confidence aus Quellen-Übereinstimmung.

    local_verified (songs.db-Treffer, auch Library-Lieferungen) = 1.0 (schon verifiziert). Sonst:
    MB-Score-Anteil + W_CORROB je unabhängiger Quelle, die denselben GVL-LC bestätigt, + W_ISRC, wenn die
    Aufnahme über den ISRC exakt identifiziert ist. mb_score ist 0 für Kandidaten, die MusicBrainz nicht stützt
    (der Aufrufer entscheidet das je Kandidat): sonst bekäme ein reiner Deezer-Kandidat den MB-Anteil geschenkt.
    """
    if local_verified:
        return 1.0
    isrc = W_ISRC if via_isrc and mb_score else 0.0
    return round(min(1.0, W_MB * (mb_score / 100.0) + isrc + W_CORROB * corroborations), 3)


def apply_margin_gate(res: ResolveResult, *, margin: float = MARGIN) -> ResolveResult:
    """Gleichstand erkennen: alle Kandidaten, deren Confidence weniger als `margin` unter der
    Top-Confidence liegt, gelten als gleichauf. Setzt `ambiguous`/`tied` und eine Note mit den
    betroffenen Codes; die Kandidatenliste selbst bleibt unverändert (der Mensch wählt)."""
    res.ambiguous, res.tied = False, []
    cands = res.candidates
    if len(cands) < 2:
        return res
    top = cands[0].confidence
    tied = [c for c in cands if (top - c.confidence) < margin]
    if len(tied) < 2:
        return res
    res.ambiguous = True
    res.tied = [c.labelcode for c in tied]
    res.notes.insert(0, AMBIGUOUS_NOTE + ": " + " / ".join(
        f"LC{c.labelcode} {c.label}".strip() + f" ({c.confidence:.2f})" for c in tied))
    return res


def _better(a: ResolveResult, b: ResolveResult) -> bool:
    """Ist Lauf a besser als b? Erst 'nicht mehr lückenhaft', dann mehr Kandidaten,
    dann höhere Top-Confidence."""
    if a.incomplete != b.incomplete:
        return not a.incomplete
    if len(a.candidates) != len(b.candidates):
        return len(a.candidates) > len(b.candidates)
    ca = a.candidates[0].confidence if a.candidates else 0.0
    cb = b.candidates[0].confidence if b.candidates else 0.0
    return ca > cb


def resolve_reliably(run, *, max_passes: int = 2) -> ResolveResult:
    """run() -> ResolveResult. Bei `incomplete` (Rate-Limit-Lücke) automatisch
    nachfassen: der Netz-Cache ist beim 2. Lauf warm (erfolgreiche Calls sind
    gecacht, die zuvor gescheiterten werden nachgeholt). Nimmt den besseren Lauf und
    hört nie 'leer wegen Rate-Limit' auf. Netzfrei testbar: run ist injiziert."""
    res = run()
    passes = 1
    while res.incomplete and passes < max_passes:
        nxt = run()
        passes += 1
        if _better(nxt, res):
            res = nxt
        if not res.incomplete:
            break
    return res


def catalog_candidate(text: str, idx, *, tpub: str = "") -> Candidate | None:
    """Katalog-Kürzel im Dateinamen/Album (CEZ4030_1, KPMLP1234 …) -> Kandidat über das
    GVL-`kurzname` (s. core/catalog.py). EINE Quelle (der Master) -> 0.5. Trifft der TPUB-Tag
    zusätzlich den GVL-Hersteller dieses Codes -> 0.65 (zwei unabhängige Signale: Master +
    Datei). Beides Band `review`, nie `auto` — derselbe Hersteller führt oft mehrere Labels."""
    from .catalog import (_PRO_MARKER, UMBRELLA_HERSTELLER, catalog_tokens, prefix_candidates,
                          sublabel_in_scope, umbrella_words)
    from .labelmatch import (catalog_lookup, hersteller_scope, match_hersteller, match_label,
                             match_label_in_scope, norm)
    # TPUB einer Library-Datei: 'Ed.Berlin Production Music / Universal Production Music GmbH',
    # 'ORIGINAL PUB: EXTREME MUSIC LIBRARY LTD (PRS)' — Teile können den GVL-Hersteller UND das
    # Sub-Label selbst nennen. Beides sind unabhängige Signale aus der Datei; ein Hersteller, der
    # dem Kandidaten WIDERSPRICHT, verwirft ihn (UPM-Datei + GVL-Kürzel 'UPM' = UNITY Production
    # Music / RING MUSIK -> nein).
    # Library als Dach im Dateinamen ('APL_MUSIC_-_THENERVE333') -> Hersteller eingrenzen
    # (APL Publishing GmbH). Ein Kürzel- oder TPUB-Treffer AUSSERHALB dieses Herstellers wäre ein
    # Fehlgriff und wird verworfen.
    scope: set[str] = set()
    for w in umbrella_words(text):
        scope = hersteller_scope(UMBRELLA_HERSTELLER.get(w.upper(), w), idx)   # 'UPM' -> Universal Production Music GmbH
        if scope:
            break
    tpub_parts = [p.strip() for p in re.split(r"\s*[/|]\s*", _PRO_MARKER.sub("", tpub or "")) if p.strip()]
    tpub_hers = ""
    tpub_codes: list[str] = []
    cleaned_parts: list[str] = []
    for part in tpub_parts:
        cleaned = re.sub(r"^(?:original\s+pub(?:lisher)?|orig\.?\s*pub|pub(?:lisher)?|ed\.|edition|éditions?|verlag)\s*:?\s*",
                         "", part, flags=re.IGNORECASE).strip()
        cleaned_parts.append(cleaned)
        h = match_hersteller(cleaned, idx)
        if h:                                          # ein Hersteller-Teil ist KEIN Labelname
            tpub_hers = tpub_hers or h
            continue
        c = match_label(cleaned, idx) or match_label_in_scope(cleaned, idx, scope)
        # 97777 = Universal-Sammellabel, ein Rate-Fallback von match_label — aus einem TPUB
        # 'Universal Production Music …' darf kein Label-Vorschlag werden.
        if c and c != "97777" and c not in tpub_codes and (not scope or c in scope):
            tpub_codes.append(c)

    def build(code: str, source: str) -> Candidate | None:
        hers = idx.code2hersteller.get(code, "")
        hers_ok = bool(tpub_hers and hers and norm(tpub_hers) == norm(hers))
        if tpub_hers and hers and not hers_ok:
            return None                               # Datei sagt anderer Hersteller -> verwerfen
        # TPUB nennt das Label auch dann, wenn es länger ist als der GVL-Name:
        # 'The Nerve Media Group Ltd' trägt 'The Nerve' (LC98597) als Präfix.
        label_ok = code in tpub_codes or any(match_label_in_scope(cp, idx, {code}) == code for cp in cleaned_parts)
        sources = [source] + (["TPUB = Hersteller"] if hers_ok else []) + (["TPUB = Label"] if label_ok and source != "TPUB = Label" else [])
        conf = min(0.75, 0.5 + (0.15 if hers_ok else 0) + (0.15 if label_ok else 0))   # nie Band auto
        return Candidate(labelcode=code, label=idx.code2name.get(code, ""), sources=sources,
                         dual_source=(hers_ok or label_ok) and source != "TPUB = Label" or (hers_ok and label_ok),
                         confidence=conf)

    tokens = catalog_tokens(text)
    for cat in tokens:
        for prefix in prefix_candidates(cat[0]):
            code, source = catalog_lookup(prefix, idx)
            if not code or (scope and code not in scope):
                continue
            cand = build(code, source)
            if cand:
                return cand
    # Sub-Kürzel unter einem Dach, das in keiner Liste steht (UPM_KOS731: KOS): das eine Label des
    # Dach-Herstellers, dessen Name so beginnt (KOSINUS). Eindeutig oder nichts.
    for cat in tokens:
        words = [x for x in re.split(r"[ _]+", cat[0]) if x]
        if len(words) == 2 and words[0].upper() in UMBRELLA_HERSTELLER and scope:
            code = sublabel_in_scope(words[1], scope, idx)
            if code:
                cand = build(code, f"Sub-Label-Kürzel {words[1]} → {idx.code2name.get(code, '')} ({UMBRELLA_HERSTELLER[words[0].upper()]}, Stammlabel — Untermarke prüfen)")
                if cand:
                    return cand
    # Kürzel unbekannt (Extreme 'HPE454' steht in keiner Liste) oder kein Kürzel: nennt TPUB genau
    # EIN GVL-Label, ist das der Vorschlag — mit dem unbekannten Kürzel im Quellen-Text, damit man
    # sieht, dass die Liste eine Lücke hat.
    if len(tpub_codes) == 1:
        cand = build(tpub_codes[0], "TPUB = Label")
        if cand and tokens:
            cand.sources.append(f"Kürzel {tokens[0][0]} unbekannt")
        return cand
    return None


def local_result(artist: str, title: str, row: dict) -> "ResolveResult":
    """ResolveResult aus einem verifizierten songs.db-Treffer (Confidence 1.0, kein Netz)."""
    from .normalize import normalize_labelcode
    res = ResolveResult(artist=row.get("interpret") or artist, title=row.get("titel") or title, matched=True)
    res.composer = row.get("komponist", "") or ""
    res.lyricist = row.get("texter", "") or ""
    res.publisher = row.get("verlag", "") or ""
    res.album = row.get("album", "") or ""          # verifizierte Zusatzfelder aus songs.db mitzeigen
    if row.get("isrc"):
        res.isrcs = [row["isrc"]]
    if row.get("iswc"):
        res.iswcs = [row["iswc"]]
    res.gema_werknummer = row.get("gema_werknummer", "") or ""
    res.gema_source = "songs.db" if res.gema_werknummer else ""
    code = normalize_labelcode(row.get("labelcode", "") or "")
    if code and row.get("review_status") == "review":
        # Aus einer importierten Liste, Code passt nicht zum GVL-Master: Vorschlag zur Prüfung, kein Auto
        res.candidates = [Candidate(labelcode=code, label=row.get("labelname", "") or "",
                                    sources=["lokale DB (Import, ungeprüft)"], dual_source=False,
                                    confidence=0.6, quality=0)]
    elif code:
        c = Candidate(labelcode=code, label=row.get("labelname", "") or "",
                      sources=["lokale DB (verifiziert)"], dual_source=True,
                      confidence=1.0, quality=99)
        res.candidates = [c]
    res.notes.append("Treffer in verifizierter lokaler DB (songs.db) — kein Netz nötig")
    return res


SWAP_NOTE = ("Interpret und Titel kamen vertauscht an (DL-/Sync-Export) — getauscht "
             "aufgelöst, bitte prüfen")


def _swap_worth_trying(artist: str, title: str, res: ResolveResult) -> bool:
    """Lohnt ein zweiter Anlauf mit getauschten Feldern?

    Nur wenn die erste Runde GAR NICHTS ergab — weder einen Kandidaten NOCH einen
    MusicBrainz-Recording-Match. Ein Recording-Match heißt: die Reihenfolge stimmte,
    dem Track fehlt bloß ein GVL-Label; ein Tausch wäre dort eine Verschlechterung.
    Identische Felder (nach norm()) können nicht vertauscht sein."""
    if res.candidates or res.matched:
        return False
    a, t = _lm_norm(artist), _lm_norm(title)
    return bool(a) and bool(t) and a != t


def resolve(artist: str, title: str, idx: LabelIndex, *, mb_lookup, discogs_label_codes,
            deezer_lookup=None, wikidata_lookup=None, work_lookup=None, genius_lookup=None,
            isrc: str = "", isrc_lookup=None, swap_retry: bool = True) -> ResolveResult:
    """Voller Resolve inkl. Vertauschungs-Heuristik. Argumente s. `_resolve_once`.

    VERTAUSCHTE FELDER: DL-/Sync-Exporte liefern den Titel im Interpret-Feld und
    umgekehrt (`core/watch.parse_cue_from_stem` erkennt das Muster im DATEINAMEN,
    aus Tags/EDL-Feldern kommt es ungefiltert hier an). Bleibt die erste Runde
    komplett leer, wird EINMAL mit getauschten Feldern nachgefasst.

    Erst wird nur die MB-Recording-Suche als Probe geschickt: trifft sie nicht, bleibt
    es beim ersten Ergebnis und Deezer/Wikidata/Werk-Suche werden gar nicht erst ein
    zweites Mal befragt (ein leeres Feldpaar darf die Quellen nicht verdoppeln).
    Trifft sie, läuft der volle Resolve mit der getauschten Reihenfolge und das
    Ergebnis trägt ``swapped=True`` + Note — der Tausch ist nie still.

    Der ISRC wird NICHT erneut probiert: Stufe 0 lief schon in der ersten Runde und
    ist von der Feldreihenfolge unabhängig.

    swap_retry=False schaltet die Heuristik ab (Kontrolllauf)."""
    sources = dict(mb_lookup=mb_lookup, discogs_label_codes=discogs_label_codes,
                   deezer_lookup=deezer_lookup, wikidata_lookup=wikidata_lookup,
                   work_lookup=work_lookup, genius_lookup=genius_lookup)
    res = _resolve_once(artist, title, idx, isrc=isrc, isrc_lookup=isrc_lookup, **sources)
    if not swap_retry or not _swap_worth_trying(artist, title, res):
        return res
    probe = mb_lookup(title, artist)
    if probe is None or not probe.matched:
        return res
    alt = _resolve_once(title, artist, idx, mb_pre=probe, **sources)
    alt.swapped = True
    alt.notes.insert(0, SWAP_NOTE)
    return alt


def _resolve_once(artist: str, title: str, idx: LabelIndex, *, mb_lookup, discogs_label_codes,
                  deezer_lookup=None, wikidata_lookup=None, work_lookup=None, genius_lookup=None,
                  isrc: str = "", isrc_lookup=None, mb_pre=None) -> ResolveResult:
    """mb_lookup(a,t)->MBLookup; discogs_label_codes(release_id)->list[codes];
    deezer_lookup(a,t)->DeezerResult|None (dritte Quelle);
    wikidata_lookup(a,t)->WikidataResult|None (vierte Quelle + Writer-Fallback);
    work_lookup(title)->(composer,lyricist,url) (Cover-Fallback: MB-Werk-Titelsuche);
    isrc_lookup(isrc)->MBLookup|None (Stufe 0, s.u.);
    mb_pre: schon geholtes MBLookup — überspringt ISRC-Stufe UND Textsuche (der
    Vertausch-Retry hat die Recording-Suche bereits als Probe bezahlt).

    STUFE 0 — ISRC: Trägt die Datei einen ISRC, wird die Aufnahme zuerst exakt über
    ``/isrc/{code}`` aufgelöst; erst wenn das nichts liefert, läuft die Fuzzy-Textsuche.
    Der ISRC identifiziert die AUFNAHME — der Labelcode hängt am Release und wird
    danach genau wie sonst über die Release-Qualität und die Zweitquellen bewertet.
    Kein Auto-Confirm ohne dual_source.

    Kein Früh-Abbruch mehr bei MB-Fehltreffer: Deezer/Wikidata werden IMMER befragt,
    damit die Suche auch ohne MB-Recording-Match (Cover/Neuaufnahme, Rate-Limit) noch
    einen Labelnamen/Writer liefern kann — 'muss immer etwas ausspucken'."""
    res = ResolveResult(artist=artist, title=title)
    mb = mb_pre
    if mb is None and isrc and isrc_lookup is not None:
        hit = isrc_lookup(isrc)
        if hit is not None and hit.matched:
            mb = hit
            res.via_isrc = True
            res.notes.append("Aufnahme über ISRC exakt identifiziert — Labelcode kommt trotzdem "
                             "aus der Release-Bewertung, nicht aus dem ISRC")
    if mb is None:
        mb = mb_lookup(artist, title)
    res.matched = mb.matched
    res.mb_score = mb.score
    res.isrcs = list(mb.isrcs)
    res.composer = mb.composer
    res.lyricist = mb.lyricist
    res.publisher = mb.publisher
    res.iswcs = list(mb.iswcs)
    if len(res.iswcs) > 1:
        res.notes.append(f"Werk trägt {len(res.iswcs)} ISWCs ({', '.join(res.iswcs)}), bitte die richtige wählen")
    # GEMA ID am MusicBrainz-Werk (Entscheidung 22.10.2026): Vorschlag mit Fassung, nie verifiziert
    gema_ids = list(getattr(mb, "gema_ids", []) or [])
    if len(gema_ids) == 1:
        res.gema_werknummer, res.gema_source = gema_ids[0], "musicbrainz"
        res.notes.append(f"GEMA-Werknr. {gema_ids[0]} laut MusicBrainz (Werk-Attribut), bitte prüfen")
    elif gema_ids:
        res.notes.append(f"Werk trägt {len(gema_ids)} GEMA-Werknummern ({', '.join(gema_ids)}), bitte die richtige wählen")
    res.version = detect_version(title)
    if res.version and version_kind(res.version) in OWN_RELEASE:
        res.notes.append(f"Version erkannt: {res.version}. Label und Urheber können von der Originalfassung abweichen, bitte prüfen")
    elif res.version:
        res.notes.append(f"Version erkannt: {res.version}")
    if res.version and mb.matched and mb.title and not detect_version(mb.title):
        res.version_mismatch = True
        res.notes.append(f"MusicBrainz fand nur die Originalfassung, nicht die Version „{res.version}“. Das Label kann abweichen, bitte prüfen")
    res.incomplete = mb.incomplete
    if mb.incomplete:
        res.notes.append("MusicBrainz-Abfrage unvollständig (Rate-Limit?) — Ergebnis evtl. lückenhaft, erneut versuchen")
    if not mb.matched:
        res.notes.append("kein MusicBrainz-Recording-Match — Zusatzquellen (Deezer/Wikidata) versucht")

    # Zweitquellen ohne Gast-Credits fragen („A feat. B“, „Song (feat. B)“): sie führen den Gast wie MusicBrainz
    # nicht im Titel bzw. nicht als Hauptinterpret, und die Suche mit dem vollen Text findet dort oft nichts.
    qa, qt = primary_artist(artist), strip_feat(title)
    dz = deezer_lookup(qa, qt) if deezer_lookup else None
    wd = wikidata_lookup(qa, qt) if wikidata_lookup else None

    cand: dict[str, Candidate] = {}
    mb_url_for: dict[str, str] = {}
    rel_for: dict[str, dict] = {}   # code -> {url, album, date} der besten Release dieses Labels
    seen_names: list[str] = []      # alle rohen Labelnamen (für label_hints)
    mapped_norm: set[str] = set()   # norm() der Namen, die einen GVL-Code lieferten

    def note_name(name: str, code: str | None) -> None:
        if not name:
            return
        seen_names.append(name)
        if code:
            mapped_norm.add(_lm_norm(name))

    # MB-Labelnamen -> GVL-Code (+ Qualität / Release-Info)
    code_country: dict[str, int] = {}
    for name in mb.label_names:
        code = match_label(name, idx)
        note_name(name, code)
        if not code:
            continue
        c = cand.setdefault(code, Candidate(labelcode=code, label=idx.code2name.get(code, name)))
        if "gvl+musicbrainz" not in c.sources:
            c.sources.append("gvl+musicbrainz")
        code_country[code] = max(code_country.get(code, 0), getattr(mb, "label_country", {}).get(name, 0))
        q = mb.label_quality.get(name, 0)
        if q >= c.quality:
            c.quality = q
            ri = mb.label_release.get(name)
            if ri:
                rel_for[code] = ri
                mb_url_for[code] = ri.get("url", "")

    # Discogs-Codes (zweite Quelle), nur GVL-bestätigte
    discogs_codes: dict[str, str] = {}
    for rid in mb.discogs_release_ids:
        url = f"https://www.discogs.com/release/{rid}"
        for code in discogs_label_codes(rid):
            if code in idx.code2name:
                discogs_codes.setdefault(code, url)
            else:
                res.notes.append(f"Discogs-LC {code} nicht im GVL-Master — verworfen")
    for code in discogs_codes:
        c = cand.setdefault(code, Candidate(labelcode=code, label=idx.code2name.get(code, "")))
        if "discogs" not in c.sources:
            c.sources.append("discogs")

    # Deezer-Label -> GVL-Code (dritte Quelle); ISRC ergänzen falls MB leer
    deezer_code = None
    if dz:
        if dz.label:
            deezer_code = match_label(dz.label, idx)
            note_name(dz.label, deezer_code)
            if deezer_code:
                c = cand.setdefault(deezer_code, Candidate(labelcode=deezer_code,
                                                           label=idx.code2name.get(deezer_code, dz.label)))
                if "deezer" not in c.sources:
                    c.sources.append("deezer")
        if not res.isrcs and dz.isrc:
            res.isrcs = [dz.isrc]

    # Wikidata-Label -> GVL-Code (vierte Quelle); Writer-Fallback wenn MB leer
    wiki_codes: dict[str, str] = {}
    if wd:
        for name in wd.labels:
            code = match_label(name, idx)
            note_name(name, code)
            if not code:
                continue
            c = cand.setdefault(code, Candidate(labelcode=code, label=idx.code2name.get(code, name)))
            if "wikidata" not in c.sources:
                c.sources.append("wikidata")
            wiki_codes.setdefault(code, wd.item_url)
        if not res.composer and wd.composers:
            res.composer = "; ".join(wd.composers)
        if not res.lyricist and wd.lyricists:
            res.lyricist = "; ".join(wd.lyricists)

    # Cover/Neuaufnahme: keine Autoren aus dem Recording-Werk-Link UND keine aus Wikidata
    # -> MB-Werk-Titelsuche. Titel-only ist mehrdeutig -> 'bitte prüfen'. VOR Genius: bei Covern
    # (z. B. „Hurt“) kennt das MB-Werk den Urheber (Trent Reznor), Genius nur den Interpreten der Seite.
    if work_lookup and not res.composer and not res.lyricist:
        comp, lyr, _wurl = work_lookup(qt)
        if comp or lyr:
            res.composer, res.lyricist = comp, lyr
            res.notes.append("Autoren aus MusicBrainz-Werk-Titelsuche (Cover/Neuaufnahme) — bitte prüfen")

    # Genius (Songwriter, community-gepflegt) als letzte Autoren-Quelle: erst wenn MusicBrainz-Werk,
    # Wikidata und die Werk-Titelsuche nichts haben — Interpret + Titel müssen beim Treffer passen
    # (genius.pick_hit), sonst nichts. Genius kennt nur „Written by", keine Trennung Komponist/Texter
    # -> beide Felder gleich, Note + Quellenlink.
    if genius_lookup and not res.composer and not res.lyricist:
        g = genius_lookup(qa, qt)
        if g and g.writers:
            names = "; ".join(g.writers)
            res.composer, res.lyricist = names, names
            res.notes.append("Autoren von Genius (Songwriter, community-gepflegt, ohne Komponist/Texter-Trennung) — bitte prüfen")
            if g.song_url:
                res.author_source_url = g.song_url

    # Confidence aus Quellen-Übereinstimmung + Quell-Links zum Nachprüfen
    for code, c in cand.items():
        corrob = sum(1 for s in c.sources if s != "gvl+musicbrainz")
        c.dual_source = corrob >= 1
        # Gewichten statt zählen: der MB-Anteil („das ist unsere Aufnahme“, dazu der ISRC-Bonus) gilt nur für
        # Kandidaten, die an DIESER Aufnahme hängen: MB-Release oder Discogs-Release aus den Links der MB-Aufnahme.
        # Ein Label, das nur Deezer (oder Wikidata) nennt, bestätigt mit, steht aber nicht allein auf dem MB-Anteil:
        # im Eval-Korpus lag ein solcher Kandidat 14-mal falsch und einmal richtig (22.10.2026).
        backed = "gvl+musicbrainz" in c.sources or "discogs" in c.sources
        c.confidence = confidence(mb_score=mb.score if backed else 0, corroborations=corrob, via_isrc=res.via_isrc)
        urls = []
        if mb_url_for.get(code):
            urls.append({"name": "MusicBrainz", "url": mb_url_for[code]})
        if code in discogs_codes:
            urls.append({"name": "Discogs", "url": discogs_codes[code]})
        if dz and code == deezer_code and dz.track_url:
            urls.append({"name": "Deezer", "url": dz.track_url})
        if code in wiki_codes and wiki_codes[code]:
            urls.append({"name": "Wikidata", "url": wiki_codes[code]})
        c.source_urls = urls

    # Ranking: Confidence zuerst, dann Release-Qualität, dann Code — und Gleichstand melden
    res.candidates = sorted(cand.values(), key=lambda c: (-c.confidence, -c.quality, c.labelcode))
    apply_margin_gate(res)
    # DE/EU gewinnt, internationale Labels gelten aber (Regel): kommt das gewählte Label nur aus Veröffentlichungen
    # außerhalb Deutschlands und Europas, steht das sichtbar am Ergebnis.
    top = res.candidates[0] if res.candidates else None
    if top and top.labelcode in code_country and code_country[top.labelcode] < 2:
        res.notes.append(f"Keine deutsche oder europäische Veröffentlichung gefunden: {top.label} stammt aus einer "
                         "internationalen Veröffentlichung, bitte prüfen")

    # never-empty: Labelnamen, die gefunden, aber KEINEM GVL-Code zuordenbar waren,
    # als Hinweis mitgeben (dedupliziert, Reihenfolge stabil).
    hint_seen: set[str] = set()
    for name in seen_names:
        n = _lm_norm(name)
        if n in mapped_norm or n in hint_seen:
            continue
        hint_seen.add(n)
        res.label_hints.append(name.strip())

    if res.candidates:
        ri = rel_for.get(res.candidates[0].labelcode, {})   # Album/Jahr vom Top-Label, sonst Deezer
        res.album = ri.get("album", "") or (dz.album if dz else "")
        res.date = ri.get("date", "") or (dz.date if dz else "")
    else:
        if dz:
            res.album = res.album or dz.album
            res.date = res.date or dz.date
        if res.label_hints:
            # Namen NICHT anhängen (bliebe unübersetzbar) — sie stehen strukturiert in
            # res.label_hints; die UI zeigt sie separat + übersetzbar.
            res.notes.append("Labelname(n) gefunden, aber kein GVL-Code zuordenbar")
        elif res.matched:
            res.notes.append("MusicBrainz lieferte keinen Labelnamen")
    return res


@dataclass
class TitleCandidate:
    """Ein Interpret-Kandidat aus der Titel-only-Suche. labelcode/label sind ein PREVIEW
    (aus Wikidata gemappt); die verbindliche, doppelt verifizierte LC entsteht erst beim
    normalen Resolve mit dem gewählten Interpret (kein stilles Raten)."""
    artist: str
    label: str = ""            # Label-Preview (GVL-Schreibweise, wenn gemappt; sonst Wikidata-Name)
    labelcode: str = ""        # GVL-Code-Preview ("" wenn nicht mappbar)
    composer: str = ""
    lyricist: str = ""
    source: str = "wikidata"
    source_url: str = ""


@dataclass
class TitleResolveResult:
    title: str
    candidates: list[TitleCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def resolve_by_title(title: str, idx: LabelIndex, *, wikidata_items_lookup) -> TitleResolveResult:
    """Titel-only (kein Interpret bekannt) -> Interpret-Kandidaten. Für Sync-/Score-Cues,
    die nur Titel(+LC) tragen. wikidata_items_lookup(title)->list[WikidataItem] (injiziert,
    netzfrei testbar). Jeder Wikidata-Titel-Treffer mit Performer wird EIN Kandidat
    (Interpret + Label-Preview + Writer). Ranking: mit GVL-Code zuerst, dann mit Writer.
    Kein stilles Raten — die Auswahl trifft der Mensch, danach voller Resolve."""
    res = TitleResolveResult(title=title)
    seen: set[str] = set()
    for it in (wikidata_items_lookup(title) or []):
        artist = "; ".join(it.performers)
        key = _lm_norm(artist)
        if not key or key in seen:
            continue
        seen.add(key)
        code, label = "", (it.labels[0] if it.labels else "")
        for name in it.labels:
            c = match_label(name, idx)
            if c:
                code, label = c, idx.code2name.get(c, name)
                break
        res.candidates.append(TitleCandidate(
            artist=artist, label=label or "", labelcode=code,
            composer="; ".join(it.composers), lyricist="; ".join(it.lyricists),
            source="wikidata", source_url=it.item_url))
    # mit GVL-Code zuerst, dann mit Writer, sonst Fundreihenfolge (stabil)
    res.candidates.sort(key=lambda c: (c.labelcode == "", not (c.composer or c.lyricist)))
    if not res.candidates:
        res.notes.append("Kein Titel-Treffer — Interpret manuell ergänzen und erneut suchen")
    return res


# --- Lokaler Track-Index (core/trackindex.py) ------------------------

INDEX_SOURCE = "index:"           # Quellen-Präfix der Kandidaten: 'index:discogs' | 'index:musicbrainz'
INDEX_NOTE = "Treffer im lokalen Track-Index (Dump-Daten, nutzergepflegt) — bitte prüfen, kein Netz"
# Prüfstufe eines Songs im Index, aus allen seinen Zeilen: ein Song steht je Quelle und Labelcode einmal drin,
# mehrere Zeilen sind also mehrere Belege, keine Dubletten. Stufen, stärkste zuerst:
INDEX_TIERS = ("zwei_quellen",       # ein Code, Discogs UND MusicBrainz nennen ihn
               "mehrere_releases",   # ein Code, eine Quelle, auf mindestens zwei Veröffentlichungen
               "einzelbeleg",        # ein Code, eine Quelle, eine Veröffentlichung
               "widerspruechlich")   # mehrere GVL-Codes (Original und Compilation, Imprints, Länderausgaben)


# Albumtitel, die auf eine Zusammenstellung deuten (Sampler, Best-of, Box): ihr Code verliert gegen das Original
_COMPILATION = re.compile(r"\b(best of|greatest|hits|vol\.?|volume|collection|box|anthology|sampler|compilation|"
                          r"megamix|essential|the very|gold|complete|series|now that|bravo|various)\b", re.I)


def index_rule_pick(hits: list[dict]) -> str:
    """Vorschlag bei widersprüchlichem Index (mehrere GVL-Codes für einen Song), offline und ohne neue Daten:
    Codes unter 00100 fallen weg (meist Fehlzuordnungen kleiner Labels); dann gewinnt der einzige Code mit einem
    Album ohne Compilation-Merkmal, sonst der mit den meisten Veröffentlichungen, sonst das früheste Jahr.
    Stichprobe von 300 Widersprüchen: trifft bei Original-gegen-Compilation in rund 9 von 10 Fällen. Nur ein
    markierter Vorschlag, nie automatisch. '' = keine Entscheidung."""
    by: dict[str, list[dict]] = {}
    for h in hits:
        code = h.get("labelcode") or ""
        if code.isdigit() and int(code) < 100:
            continue
        by.setdefault(code, []).append(h)
    if len(by) == 1:
        return next(iter(by))
    if len(by) < 2:
        return ""
    plain = [c for c, hs in by.items() if any(h.get("album") and not _COMPILATION.search(h["album"]) for h in hs)]
    if len(plain) == 1:
        return plain[0]
    pool = plain or list(by)
    n = {c: sum(int(h.get("n_releases") or 1) for h in by[c]) for c in pool}
    top = sorted(n.values(), reverse=True)
    if top[0] > top[1]:
        return max(n, key=n.get)
    years = {c: min((h["jahr"] for h in by[c] if (h.get("jahr") or "").isdigit()), default="") for c in pool}
    dated = sorted((y, c) for c, y in years.items() if y)
    if dated and (len(dated) == 1 or dated[0][0] < dated[1][0]):
        return dated[0][1]
    return ""


def index_tier(hits: list[dict]) -> str:
    """Prüfstufe aus den Index-Zeilen eines Songs (nur Zeilen mit GVL-Code übergeben)."""
    if not hits:
        return ""
    if len({h.get("labelcode") for h in hits}) > 1:
        return "widerspruechlich"
    if len({h.get("source") for h in hits}) >= 2:
        return "zwei_quellen"
    return "mehrere_releases" if sum(int(h.get("n_releases") or 1) for h in hits) >= 2 else "einzelbeleg"


def index_result(artist: str, title: str, hits: list[dict], idx: LabelIndex) -> ResolveResult | None:
    """ResolveResult aus Zeilen des lokalen Track-Index (trackindex.lookup). None ohne Zeilen.

    Ein Code aus EINER Dump-Quelle = 0.5 (wie Deezer allein: eine unabhängige Quelle mit
    GVL-geprüftem Code), Discogs UND MusicBrainz auf demselben Code = 0.65 — nie Band `auto`:
    Dump-Daten sind nutzergepflegt, nicht human-verifiziert (dieselbe Staffel wie
    catalog_candidate). Codes, die der GVL-Index nicht (mehr) kennt, fliegen. Komponist/Texter
    kommen aus der Zeile (MusicBrainz-Werk-Relationen vor Discogs-Credits), Album/Jahr vom
    Top-Label. Gleichstand meldet das Margin-Gate wie überall."""
    if not hits:
        return None
    res = ResolveResult(artist=artist, title=title, matched=True, from_index=True)
    cand: dict[str, Candidate] = {}
    by_code: dict[str, list[dict]] = {}
    for h in hits:
        code = h.get("labelcode") or ""
        if code not in idx.code2name:
            res.notes.append(f"Index-LC {code} nicht im GVL-Master — verworfen")
            continue
        c = cand.setdefault(code, Candidate(labelcode=code, label=idx.code2name.get(code, h.get("label") or "")))
        src = INDEX_SOURCE + (h.get("source") or "")
        if src not in c.sources:
            c.sources.append(src)
        c.quality += int(h.get("n_releases") or 1)
        by_code.setdefault(code, []).append(h)
    if not cand:
        return res
    res.index_tier = index_tier([h for hs in by_code.values() for h in hs])
    if res.index_tier == "widerspruechlich":
        res.index_rule_pick = index_rule_pick([h for hs in by_code.values() for h in hs])
    for c in cand.values():
        c.dual_source = len(c.sources) >= 2
        c.confidence = 0.65 if c.dual_source else 0.5
    res.candidates = sorted(cand.values(), key=lambda c: (-c.confidence, -c.quality, c.labelcode))
    apply_margin_gate(res)
    # Autoren: MusicBrainz-Zeile zuerst (Werk-Relationen), sonst Discogs-Credits; erste gefüllte gewinnt.
    ordered = sorted(hits, key=lambda h: (h.get("source") != "musicbrainz", -int(h.get("n_releases") or 1)))
    res.composer = next((h["komponist"] for h in ordered if h.get("komponist")), "")
    res.lyricist = next((h["texter"] for h in ordered if h.get("texter")), "")
    top = by_code.get(res.candidates[0].labelcode, [])
    res.album = next((h["album"] for h in top if h.get("album")), "")
    res.date = next((h["jahr"] for h in top if h.get("jahr")), "")
    res.notes.append(INDEX_NOTE)
    return res


def index_settles(res: ResolveResult | None) -> bool:
    """Darf der Index-Treffer das Netz ersetzen? Nur, wenn er EINDEUTIG ist: mindestens ein Kandidat und
    kein Gleichstand. Bei Gleichstand im Index (zwei Label derselben Aufnahme, 0.5/0.5) löst das Netz
    in der Regel eindeutig auf (z. B. AURORA „Runaway“) — deshalb geht ein uneindeutiger Index-
    Treffer ins Netz; seine Autoren werden dort nur nachgefüllt (`merge_index_writers`)."""
    return res is not None and bool(res.candidates) and not res.ambiguous


def merge_index_writers(res: ResolveResult, index_res: ResolveResult | None) -> ResolveResult:
    """Komponist/Texter aus dem (uneindeutigen) Index-Treffer in ein Netz-Ergebnis nachfüllen — nur leere
    Felder, mit Note. Die Label-Kandidaten des Index werden NICHT gemischt (Discogs-Dump = dieselbe Quelle
    wie der Live-Call, würde doppelt zählen)."""
    if index_res is None:
        return res
    filled = []
    if not res.composer and index_res.composer:
        res.composer, filled = index_res.composer, filled + ["Komponist"]
    if not res.lyricist and index_res.lyricist:
        res.lyricist, filled = index_res.lyricist, filled + ["Texter"]
    if filled:
        res.notes.append(f"{'/'.join(filled)} aus dem lokalen Track-Index nachgefüllt — bitte prüfen")
    if index_res.ambiguous:
        res.notes.append("Track-Index uneindeutig (" + " / ".join(f"LC{c}" for c in index_res.tied)
                         + ") — Netz befragt" + (f"; Regel-Vorschlag LC{index_res.index_rule_pick} (Original vor "
                         "Compilation, sonst die meisten Veröffentlichungen), bitte prüfen"
                                                 if index_res.index_rule_pick else ""))
    return res


def resolve_from_index(artist: str, title: str, idx: LabelIndex, *, index_lookup,
                       swap_retry: bool = True) -> ResolveResult | None:
    """Stufe 1b: lokaler Track-Index, netzfrei. index_lookup(a, t) -> list[dict] (injiziert).
    Liefert den Index für Interpret/Titel nichts, wird EINMAL getauscht probiert (derselbe
    DL-/Sync-Export-Fall wie in resolve()); der Tausch ist nie still (swapped + Note).
    None = kein Treffer mit GVL-Code -> der Aufrufer geht ins Netz."""
    res = index_result(artist, title, index_lookup(artist, title), idx)
    if res is not None and res.candidates:
        return res
    if not swap_retry:
        return None
    a, t = _lm_norm(artist), _lm_norm(title)
    if not a or not t or a == t:
        return None
    alt = index_result(title, artist, index_lookup(title, artist), idx)
    if alt is None or not alt.candidates:
        return None
    alt.swapped = True
    alt.notes.insert(0, SWAP_NOTE)
    return alt
