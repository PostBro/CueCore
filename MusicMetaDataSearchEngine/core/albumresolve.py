"""Album-basierte LC-Auflösung.

Wenn die Recording-Suche über den Interpreten scheitert (Score-/Komponisten-Tracks
auf einem Soundtrack), löst dieses Modul über das ALBUM auf: das Album einmal
suchen, dessen Label (= Tonträgerhersteller) gegen den GVL-Master matchen, den LC
liefern. Der gilt für alle Tracks desselben Albums (Album-/Compilation-LC).

Alle Netz-Zugriffe werden injiziert -> die Orchestrierung ist deterministisch testbar.
Quellen: MusicBrainz-Release-Suche (Discovery), Discogs-Album-Suche (Discovery, gegen
GVL-Master gegengeprüft), musik-sammler.de (Bestätigung eines Kandidaten-LC). Ein LC
ist nur ein Kandidat, wenn er IM GVL-Master steht — die Hartstopp-Regel bleibt.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .labelmatch import LabelIndex, match_label


@dataclass
class AlbumCandidate:
    labelcode: str
    label: str                                        # offizielle GVL-Schreibweise
    sources: list[str] = field(default_factory=list)  # musicbrainz | discogs | musik-sammler
    source_urls: list[dict] = field(default_factory=list)
    dual_source: bool = False
    confidence: float = 0.0
    country_bonus: int = 0                            # bestes Veröffentlichungsgebiet der MB-Releases (DE 3 … 0)


@dataclass
class AlbumResult:
    album: str
    artist: str = ""
    candidates: list[AlbumCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> AlbumCandidate | None:
        return self.candidates[0] if self.candidates else None


def resolve_album(album: str, artist: str, idx: LabelIndex, *, mb_search,
                  discogs_search=None, discogs_codes=None, ms_confirm=None,
                  max_discogs: int = 3) -> AlbumResult:
    """mb_search(album)->list[MBReleaseHit]; discogs_search(album)->list[release_id];
    discogs_codes(release_id)->list[code]; ms_confirm(code, label)->bool.

    album-only (kein Interpret in der Suche): bei Various-Artists-/Soundtrack-Alben
    verengt ein Komponisten-/Composite-Interpret die Suche fälschlich (genau der Fall,
    der die Recording-Suche scheitern ließ)."""
    result = AlbumResult(album=album, artist=artist)
    if not album.strip():
        result.notes.append("Kein Albumname vorhanden")
        return result
    cand: dict[str, AlbumCandidate] = {}

    def _get(code: str) -> AlbumCandidate:
        c = cand.get(code)
        if c is None:
            c = AlbumCandidate(labelcode=code, label=idx.code2name.get(code, ""))
            cand[code] = c
        return c

    # 1) MusicBrainz-Release-Suche: Official/hohe-Score-Treffer zuerst (deren Label zählt), darin die deutsche
    #    vor der europäischen vor der übrigen Pressung (wie im Recording-Pfad)
    hits = mb_search(album) or []
    hits = sorted(hits, key=lambda h: (h.status != "Official", -h.country_bonus, -h.score))
    for h in hits:
        for name in h.labels:
            code = match_label(name, idx)
            if not code:
                continue
            c = _get(code)
            c.country_bonus = max(c.country_bonus, h.country_bonus)
            if "musicbrainz" not in c.sources:
                c.sources.append("musicbrainz")
                if h.release_id:
                    c.source_urls.append({"name": "MusicBrainz",
                                          "url": f"https://musicbrainz.org/release/{h.release_id}"})

    # 2) Discogs-Album-Suche -> Release -> Label Code, gegen GVL-Master gegengeprüft
    if discogs_search and discogs_codes:
        for rid in (discogs_search(album) or [])[:max_discogs]:
            for code in (discogs_codes(rid) or []):
                if code not in idx.code2name:
                    result.notes.append(f"Discogs-LC {code} nicht im GVL-Master — verworfen")
                    continue
                c = _get(code)
                if "discogs" not in c.sources:
                    c.sources.append("discogs")
                    c.source_urls.append({"name": "Discogs",
                                          "url": f"https://www.discogs.com/release/{rid}"})

    # 3) musik-sammler.de bestätigt jeden Kandidaten-LC über den Labelnamen (kein Discovery)
    if ms_confirm:
        for code, c in cand.items():
            if ms_confirm(code, c.label):
                if "musik-sammler" not in c.sources:
                    c.sources.append("musik-sammler")
                    c.source_urls.append({"name": "musik-sammler",
                                          "url": f"https://www.musik-sammler.de/label/{code}/"})

    for c in cand.values():
        n = len(set(c.sources))
        c.dual_source = n >= 2                        # ≥2 unabhängige Quellen = doppelt verifiziert
        c.confidence = round(min(0.95, 0.5 + 0.2 * (n - 1)), 3)

    # Land nur bei gleicher Belegung (Bonus, kein Filter: ein US-Label ist besser als gar keins), sonst entschiede
    # dort der kleinere Labelcode, also oft die US-Pressung.
    result.candidates = sorted(cand.values(),
                               key=lambda c: (-c.confidence, -len(c.sources), -c.country_bonus, c.labelcode))
    if not result.candidates:
        result.notes.append("Kein GVL-Label über das Album gefunden" if hits
                            else "Kein Album-Treffer in den Quellen")
    return result
