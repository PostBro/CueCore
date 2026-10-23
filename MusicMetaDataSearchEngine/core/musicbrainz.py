"""MusicBrainz-Lookup: artist+title -> Labelnamen, ISRCs, Discogs-Release-Links.

Öffentliche API, 1 req/s, User-Agent Pflicht. Die Parser sind rein und netzfrei
testbar; die Netzschicht (http_get_json) wird in resolve() injiziert.
"""
from __future__ import annotations
import difflib, json, re, time, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass, field

from .labelmatch import norm

# Lucene-Sonderzeichen -> Space: für die tolerante (unquoted) Retry-Suche. Eine exakte
# Phrasen-Suche scheitert an kaputter Datei-Schreibweise (ALL CAPS, fehlendes Apostroph,
# Klammern); die tolerante Token-Suche findet trotzdem, die Auswahl trifft danach fuzzy.
_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')

MB = "https://musicbrainz.org/ws/2"
USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"
RATE = 1.2


@dataclass
class MBLookup:
    recording_id: str = ""
    title: str = ""            # Titel der gewählten Aufnahme (Versionsabgleich: Original statt Remix gefunden?)
    score: int = 0
    label_names: list[str] = field(default_factory=list)
    label_quality: dict[str, int] = field(default_factory=dict)  # Labelname -> beste Release-Qualität
    label_release: dict[str, dict] = field(default_factory=dict) # Labelname -> {url, album, date} der besten Release
    label_country: dict[str, int] = field(default_factory=dict)  # Labelname -> bestes Gebiet (country_bonus: DE 3, EU 2)
    isrcs: list[str] = field(default_factory=list)
    discogs_release_ids: list[str] = field(default_factory=list)
    composer: str = ""
    lyricist: str = ""
    publisher: str = ""        # Musikverlag(e) aus den Work-label-rels (type 'publishing'), '; '-getrennt
    iswcs: list[str] = field(default_factory=list)   # ISWC(s) des Werks, aus demselben Work-Abruf
    gema_ids: list[str] = field(default_factory=list)   # Work-Attribut „GEMA ID“ (Community-Daten, nur Vorschlag)
    matched: bool = False
    incomplete: bool = False   # mind. eine Release-Abfrage scheiterte (Rate-Limit) -> Ergebnis lückenhaft
    via_isrc: bool = False     # exakt über /isrc/{code} gefunden, nicht über die Textsuche


def http_get_json(url: str, *, ua: str = USER_AGENT, retries: int = 3) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(8 * (attempt + 1)); continue
            return None
        except Exception:
            return None
    return None


def _artist_matches(rec: dict, artist: str) -> bool:
    na = norm(artist)
    for a in rec.get("artist-credit", []):
        an = norm(a.get("name", "") or (a.get("artist") or {}).get("name", ""))
        if an and (na in an or an in na):
            return True
    return False


def best_recording(artist: str, title: str, recs: list[dict]) -> dict | None:
    """Bestes Recording: Titel exakt + Artist-Match (Score >=75), sonst Titel exakt (>=85)."""
    nt = norm(title)
    for r in recs:
        if int(r.get("score", 0)) >= 75 and norm(r.get("title", "")) == nt and _artist_matches(r, artist):
            return r
    for r in recs:
        if int(r.get("score", 0)) >= 85 and norm(r.get("title", "")) == nt:
            return r
    return None


# Gastauftritt im Titel: „(feat. X)“, „[ft. X]“, „(featuring X)“ oder ohne Klammer am Ende („Song feat. X“)
_FEAT = re.compile(r"\s*(?:[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s[^\)\]]*[\)\]]|\s(?:feat\.|ft\.|featuring)\s.+)\s*$",
                   re.IGNORECASE)
# Gastauftritt im Interpret: „A feat. B“, „A ft. B“, „A featuring B“. „&“, „x“, „und“ bleiben: sie gehören oft zum
# Namen („Simon & Garfunkel“, „Earth, Wind & Fire“, „Malcolm X“).
_FEAT_ARTIST = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.+$", re.IGNORECASE)


def strip_feat(title: str) -> str:
    """'Song (feat. X)' -> 'Song'. MB führt Feat.-Credits als artist-credit, nicht im
    Recording-Titel — eine Suche mit dem vollen Klammer-Titel findet oft nichts."""
    return _FEAT.sub("", title).strip()


_FEAT_SEP = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)


def primary_artist(artist: str) -> str:
    """'A feat. B' -> 'A' (Hauptinterpret für die Suche). Ohne Gast-Credit unverändert."""
    return _FEAT_ARTIST.sub("", artist).strip() or artist.strip()


def feat_variants(artist: str) -> list[str]:
    """Gast-Credits von hinten abbauen: 'A feat. B feat. C' -> ['A feat. B', 'A']. Ein zusätzlich angehängter Gast
    fällt zuerst weg, ein Duett-Credit, unter dem MusicBrainz die Aufnahme führt, bleibt so lange wie möglich."""
    cuts = [m.start() for m in _FEAT_SEP.finditer(artist or "")]
    return [v for v in dict.fromkeys(artist[:c].strip() for c in reversed(cuts)) if v]


def matched_recordings(artist: str, title: str, recs: list[dict], min_score: int = 85) -> list[dict]:
    """ALLE Recordings mit exaktem Titel + Artist-Match ab min_score.

    Ein Track existiert in MusicBrainz oft mehrfach (Studio, Live, Remix, Comps) mit
    je eigenen Releases/Labels. Wir sammeln alle, um die Releases breit abzugrasen —
    das eine 'beste' Recording trägt sonst zufällig eine label-lose Comp-Release.
    """
    nt = norm(title)
    return [
        r for r in recs
        if int(r.get("score", 0)) >= min_score and norm(r.get("title", "")) == nt and _artist_matches(r, artist)
    ]


def _loose_terms(s: str) -> str:
    """Lucene-taugliche Token-Folge: Sonderzeichen raus, Mehrfach-Space zusammen."""
    return " ".join(_LUCENE_SPECIAL.sub(" ", s).split())


def _title_ratio(a: str, b: str) -> float:
    """Titel-Ähnlichkeit nach Normalisierung (0..1). norm() macht Apostroph/Interpunktion
    zu Spaces -> 'LAURA PALMERS THEME' ~ 'Laura Palmer's Theme' (Ratio ~0.97)."""
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def fuzzy_recordings(artist: str, title: str, recs: list[dict], *, min_ratio: float = 0.85) -> list[dict]:
    """Recordings mit fuzzy-ähnlichem Titel (norm-Ratio >= min_ratio) + Artist-Match.

    Fängt kaputte Datei-Schreibweisen (ALL CAPS, fehlendes Apostroph), bei denen exakte
    Phrasensuche und MB-Score-Gate versagen. Score-unabhängig — die niedrige MB-Score ist
    nur ein Surface-Artefakt; die Confidence bleibt später über den echten MB-Score
    konservativ (fuzzy = niedrigere Confidence, kein Auto-Confirm). Der Artist-Match hält
    fremde Cover fern (z.B. 'Laura Palmers Theme' von Daniel Bautista)."""
    return [r for r in recs
            if _title_ratio(title, r.get("title", "")) >= min_ratio and _artist_matches(r, artist)]


def parse_release_labels(release: dict) -> tuple[list[str], list[str]]:
    """(label_names, discogs_release_ids) aus einer Release (inc=labels+url-rels)."""
    names: list[str] = []
    for li in release.get("label-info", []):
        name = (li.get("label") or {}).get("name")
        if name and name not in names:
            names.append(name)
    discogs_ids: list[str] = []
    for rel in release.get("relations", []):
        if rel.get("type") == "discogs":
            url = (rel.get("url") or {}).get("resource", "")
            m = re.search(r"/release/(\d+)", url)
            if m and m.group(1) not in discogs_ids:
                discogs_ids.append(m.group(1))
    return names, discogs_ids


def extract_writers(work: dict) -> tuple[str, str]:
    """(Komponist, Texter) aus einem MB-Work mit artist-rels. writer = beides."""
    composers, lyricists = [], []
    for rel in work.get("relations", []):
        rtype = (rel.get("type") or "").lower()
        name = (rel.get("artist") or {}).get("name", "")
        if not name:
            continue
        if rtype == "composer":
            composers.append(name)
        elif rtype == "lyricist":
            lyricists.append(name)
        elif rtype == "writer":          # Songwriter = Musik + Text
            composers.append(name); lyricists.append(name)
        elif rtype == "arranger":
            composers.append(name)
    return "; ".join(dict.fromkeys(composers)), "; ".join(dict.fromkeys(lyricists))


def extract_iswcs(work: dict) -> list[str]:
    """ISWC(s) eines MB-Works (Feld 'iswcs' der Work-Antwort, ohne Zusatz-inc). Mehrere sind möglich
    (z.B. Bearbeitungen unter einem Werk) -> alle, dedupliziert, in MB-Reihenfolge; der Aufrufer rät nicht."""
    return list(dict.fromkeys(i.strip() for i in (work.get("iswcs") or []) if i and i.strip()))


def extract_gema_ids(work: dict) -> list[str]:
    """GEMA-Werknummer(n) aus den Work-Attributen (Typ „GEMA ID“, z.B. '1274594-002' mit Fassung), so wie
    MusicBrainz sie führt, dedupliziert. Steht in der normalen Work-Antwort, kein Zusatz-inc."""
    return list(dict.fromkeys((a.get("value") or "").strip() for a in (work.get("attributes") or [])
                              if a.get("type") == "GEMA ID" and (a.get("value") or "").strip()))


def extract_publishers(work: dict) -> str:
    """Musikverlag(e) aus einem MB-Work mit label-rels: Relation-Typ 'publishing' zeigt auf
    ein Label (MB modelliert Verlage als Label-Entities). Nur dieser Typ — 'copyright'/
    'phonographic copyright' u.a. sind KEIN Verlag. '; '-getrennt, dedupliziert."""
    pubs = []
    for rel in work.get("relations", []):
        if (rel.get("type") or "").lower() != "publishing":
            continue
        name = (rel.get("label") or {}).get("name", "")
        if name:
            pubs.append(name)
    return "; ".join(dict.fromkeys(pubs))


# Deutschland zuerst, dann Europa: der Labelcode ist ein deutsches Register, und derselbe Song läuft
# hier oft über ein anderes Label als in den USA. Gibt es keine deutsche oder europäische Pressung, zählt
# die gefundene, denn ein US-Label ist besser als gar keins.
# Deshalb Bonus statt Filter — eine US-Release bleibt gültig, sie steht nur hinten an.
_EU_EEA = frozenset("""AT BE BG HR CY CZ DK EE FI FR GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE
                       IS LI NO CH GB""".split())
_EUROPE_WIDE = frozenset({"XE"})      # MusicBrainz: „Europe" als Veröffentlichungsgebiet
_WORLDWIDE = frozenset({"XW"})
# DDR-Pressungen zählen wie deutsche. MusicBrainz führt die DDR als
# Gebiet „East Germany“ mit dem Code XG.
_GERMANY = frozenset({"DE", "XG"})


def release_countries(release: dict) -> set[str]:
    """Alle Ländercodes einer Release — `country` und die Gebiete der release-events (ISO-3166-1).
    Beides steht schon im Such-Stub, die Reihenfolge lässt sich also vor dem Detail-Abruf setzen."""
    out = set()
    c = (release.get("country") or "").strip().upper()
    if c:
        out.add(c)
    for ev in release.get("release-events") or []:
        for code in ((ev.get("area") or {}).get("iso-3166-1-codes") or []):
            if code:
                out.add(str(code).strip().upper())
    return out


def country_bonus(release: dict) -> int:
    """0 bis 3 Punkte je nach Veröffentlichungsgebiet. Ohne Länderangabe: 0 (kein Abzug)."""
    codes = release_countries(release)
    if codes & _GERMANY:
        return 3
    if codes & _EU_EEA or codes & _EUROPE_WIDE:
        return 2
    if codes & _WORLDWIDE:
        return 1
    return 0


def release_quality(release: dict) -> int:
    """Qualitäts-Score einer Release: kanonisches Studio-Album rauf,
    Bootleg/Live/Compilation/Broadcast runter, deutsche und europäische Pressung vor der
    außereuropäischen. Deren Tonträgerhersteller (= Labelcode) ist der maßgebliche.
    Funktioniert auf Such-Stub UND voller Release (gleiche Felder).
    """
    score = country_bonus(release)
    status = release.get("status") or ""
    if status == "Official":
        score += 3
    elif status == "Bootleg":
        score -= 3
    rg = release.get("release-group") or {}
    primary = rg.get("primary-type") or ""
    sec = rg.get("secondary-types") or []
    if primary == "Album":
        score += 2
    elif primary in ("Single", "EP"):
        score += 1
    elif primary in ("", "Broadcast", "Other"):
        score -= 1
    if primary == "Album" and not sec:
        score += 2          # reine Studio-Album-Release (kein Live/Comp) stark bevorzugen
    if "Compilation" in sec:
        score -= 3
    if "Live" in sec:
        score -= 2
    if release.get("date"):
        score += 1
    return score


@dataclass
class MBReleaseHit:
    title: str = ""
    status: str = ""
    labels: list[str] = field(default_factory=list)
    date: str = ""
    score: int = 0
    release_id: str = ""
    country_bonus: int = 0     # DE 3, EU 2, weltweit 1 (country_bonus) — DE/EU-Regel auch im Album-Pfad


def parse_release_search(data: dict | None) -> list[MBReleaseHit]:
    """MB-Release-Suchergebnis -> Treffer mit Labelnamen und Länderbonus (netzfrei testbar)."""
    hits: list[MBReleaseHit] = []
    for r in (data or {}).get("releases", []):
        labels: list[str] = []
        for li in r.get("label-info", []):
            name = (li.get("label") or {}).get("name")
            if name and name not in labels:
                labels.append(name)
        hits.append(MBReleaseHit(
            title=r.get("title", "") or "", status=r.get("status") or "",
            labels=labels, date=r.get("date", "") or "",
            score=int(r.get("score", 0)), release_id=r.get("id", "") or "",
            country_bonus=country_bonus(r),
        ))
    return hits


def search_releases(album: str, artist: str = "", *, fetch=http_get_json, sleep=time.sleep,
                    limit: int = 10) -> list[MBReleaseHit]:
    """Album -> Release-Treffer (mit Labelnamen). Für die Album-basierte LC-Auflösung,
    wenn die Recording-Suche über den Interpreten scheitert (z.B. Score-/Komponisten-
    Tracks auf einem Soundtrack). Interpret NUR als Query-Zusatz, wenn gesetzt —
    bei Various-Artists-/Soundtrack-Alben verengt ein Komponisten-Name sonst zu stark."""
    album = album.strip()
    if not album:
        return []
    q = f'release:"{album}"'
    if artist.strip():
        q += f' AND artist:"{artist.strip()}"'
    params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": str(limit)})
    hits = parse_release_search(fetch(f"{MB}/release?{params}"))
    sleep(RATE)
    if not hits:                                       # tolerant nachfassen: unquoted Tokens
        terms = _loose_terms(album)                    # 'TWIN PEAKS SOUNDTRACK' -> 'Soundtrack from Twin Peaks'
        if terms:
            q2 = f"release:({terms})"
            at = _loose_terms(artist)
            if at:
                q2 += f" AND artist:({at})"
            params2 = urllib.parse.urlencode({"query": q2, "fmt": "json", "limit": str(limit)})
            hits = parse_release_search(fetch(f"{MB}/release?{params2}"))
            sleep(RATE)
    return hits


def parse_work_search(data: dict | None) -> list[dict]:
    """MB-Werk-Suchergebnis -> [{id, title, score}] (netzfrei testbar)."""
    return [{"id": w.get("id", "") or "", "title": w.get("title", "") or "", "score": int(w.get("score", 0))}
            for w in (data or {}).get("works", [])]


def search_work_writers(title: str, *, fetch=http_get_json, sleep=time.sleep, max_works: int = 3
                        ) -> tuple[str, str, str]:
    """Titel -> (Komponist, Texter, Werk-URL) über die MB-WERK-Suche. Für Cover/Neu-
    aufnahmen, deren Recording keinen Werk-Link trägt: das Original-Werk existiert in MB
    trotzdem. Nur EXAKTE (normalisierte) Titel-Treffer, erstes Werk mit Autoren-Relationen
    gewinnt. Titel-only ist mehrdeutig (mehrere Songs gleichen Titels) -> der Aufrufer
    muss das als 'bitte prüfen' kennzeichnen. ('','','') wenn nichts Eindeutiges."""
    title = title.strip()
    if not title:
        return "", "", ""
    q = f'work:"{title}"'
    params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": "10"})
    hits = parse_work_search(fetch(f"{MB}/work?{params}"))
    sleep(RATE)
    nt = norm(title)
    exact = [h for h in hits if h["id"] and norm(h["title"]) == nt]
    for h in exact[:max_works]:
        w = fetch(f"{MB}/work/{h['id']}?inc=artist-rels&fmt=json")
        sleep(RATE)
        if not w:
            continue
        comp, lyr = extract_writers(w)
        if comp or lyr:
            return comp, lyr, f"https://musicbrainz.org/work/{h['id']}"
    return "", "", ""


def _search(artist: str, title: str, fetch, sleep) -> list[dict]:
    q = f'artist:"{artist}" AND recording:"{title}"'
    params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": "25"})
    data = fetch(f"{MB}/recording?{params}")
    sleep(RATE)
    return data.get("recordings", []) if data else []


def _search_loose(artist: str, title: str, fetch, sleep) -> list[dict]:
    """Tolerante Recording-Suche: unquoted Lucene-Tokens statt exakter Phrase. Bringt
    Kandidaten auch bei fehlender Interpunktion/Groß-Klein; die Auswahl trifft danach
    fuzzy_recordings()."""
    tt = _loose_terms(title)
    if not tt:
        return []
    q = f"recording:({tt})"
    at = _loose_terms(artist)
    if at:
        q = f"artist:({at}) AND " + q
    params = urllib.parse.urlencode({"query": q, "fmt": "json", "limit": "25"})
    data = fetch(f"{MB}/recording?{params}")
    sleep(RATE)
    return data.get("recordings", []) if data else []


def _find_matches(artist: str, title: str, recs: list[dict]) -> list[dict]:
    matches = matched_recordings(artist, title, recs)
    if not matches:
        best = best_recording(artist, title, recs)  # Fallback: schwächerer Einzeltreffer
        matches = [best] if best else []
    return matches



def title_suggestions(artist: str, title: str, *, fetch=http_get_json, sleep=time.sleep, limit: int = 5) -> list[dict]:
    """„Meintest du …?“ bei einem Teil-Titel (z. B. fand „rolling stones“ + „sympathy“ „Sympathy for the
    Devil“ nicht, weil die Auswahl den genauen Titel verlangt). Nur Aufnahmen des angegebenen Interpreten, deren Titel
    jedes Suchwort als Wortanfang enthält. Nur ein Vorschlag: der Klick sucht mit dem vollen Titel neu, dann gelten
    die normalen Regeln. Dieselbe Abfrage wie _search_loose, kommt also aus dem Cache des Fetchers."""
    words = norm(title).split()
    if not words or not artist.strip():
        return []
    out, seen = [], {norm(title)}
    for r in _search_loose(artist, title, fetch, sleep):
        rt = r.get("title", "") or ""
        tw = norm(rt).split()
        if not _artist_matches(r, artist) or not all(any(t.startswith(w) for t in tw) for w in words):
            continue
        if norm(rt) in seen:
            continue
        seen.add(norm(rt))
        credit = "".join((a.get("name") or "") + (a.get("joinphrase") or "") for a in r.get("artist-credit", []))
        out.append({"interpret": credit.strip() or artist, "titel": rt})
        if len(out) >= limit:
            break
    return out

_ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def normalize_isrc(isrc: str) -> str:
    """ISRC auf die kanonische 12-Zeichen-Form: alles außer Buchstaben/Ziffern raus,
    Großbuchstaben ('de-a62-19-00123' -> 'DEA621900123'). Was danach nicht dem
    ISRC-Aufbau entspricht (2 Länder-Buchstaben + 3 Registrant + 2 Jahr + 5 lfd.),
    ist keiner -> '' (kein Endpoint-Aufruf auf Tag-Müll)."""
    code = "".join(ch for ch in (isrc or "") if ch.isalnum()).upper()
    return code if _ISRC.match(code) else ""


def lookup_by_isrc(isrc: str, *, fetch=http_get_json, sleep=time.sleep,
                   max_releases: int = 8) -> MBLookup:
    """ISRC -> MBLookup über den exakten Endpoint ``/isrc/{code}``. ID-basiert, damit
    ohne Fuzzy-Suche und ohne Score-Gate — genau der Pfad, an dem ALL-CAPS-Tags,
    fehlende Apostrophe und feat.-Suffixe sonst scheitern.

    WICHTIG: Der ISRC identifiziert die AUFNAHME, der Labelcode hängt an der
    VERÖFFENTLICHUNG. Er ersetzt daher nur Suche und Recording-Auswahl — die
    Release-Bewertung (``release_quality``) läuft unverändert dahinter, und ohne
    zweite Quelle gibt es weiterhin kein Auto-Confirm.

    Kein Treffer (unbekannter/ungültiger ISRC) -> leeres MBLookup (matched=False),
    der Aufrufer fällt dann auf die normale Textsuche zurück."""
    code = normalize_isrc(isrc)
    if not code:
        return MBLookup()
    data = fetch(f"{MB}/isrc/{code}?inc=artists+releases+isrcs&fmt=json")
    sleep(RATE)
    recs = list((data or {}).get("recordings", []) or [])
    if not recs:
        return MBLookup()
    # Exakter ID-Treffer -> voller MB-Score. Die Score-Komponente modelliert „wie sicher
    # ist das UNSER Track"; ein ISRC-Match ist das stärkste Signal, das MB kennt.
    out = _from_matches(recs, fetch=fetch, sleep=sleep, max_releases=max_releases, score=100)
    out.via_isrc = True
    return out


def lookup(artist: str, title: str, *, fetch=http_get_json, sleep=time.sleep, max_releases: int = 8) -> MBLookup:
    """artist+title -> MBLookup. Releases über alle Treffer aggregiert + qualitätsbewertet."""
    recs = _search(artist, title, fetch, sleep)
    matches = _find_matches(artist, title, recs)
    if not matches:
        stripped = strip_feat(title)
        if stripped != title:                                    # nur bei echtem Feat.-Titel erneut fragen
            recs = _search(artist, stripped, fetch, sleep)
            matches = _find_matches(artist, stripped, recs)
    for lead in ([] if matches else feat_variants(artist)):      # Gast im Interpret: von hinten abbauen
        recs = _search(lead, strip_feat(title), fetch, sleep)
        matches = _find_matches(lead, strip_feat(title), recs)
        if matches:
            break
    if not matches:                                              # letzter Versuch: tolerant + fuzzy
        matches = fuzzy_recordings(artist, title, _search_loose(artist, title, fetch, sleep))
    if not matches:
        return MBLookup()
    return _from_matches(matches, fetch=fetch, sleep=sleep, max_releases=max_releases)


def _from_matches(matches: list[dict], *, fetch, sleep, max_releases: int,
                  score: int | None = None) -> MBLookup:
    """Gemeinsamer Rumpf für Text- und ISRC-Auflösung: aus den Recording-Treffern die
    Releases aggregieren (qualitätsbewertet) und ISRCs/Writer nachladen. `score`
    überschreibt den MB-Suchscore (der ISRC-Pfad kennt keinen — er ist exakt)."""
    out = MBLookup()
    top = max(matches, key=lambda r: int(r.get("score", 0)))
    out.recording_id = top.get("id", "")
    out.title = top.get("title", "") or ""
    out.score = int(top.get("score", 0)) if score is None else score
    out.matched = True

    # Alle Release-Stubs sammeln und NACH STUB-QUALITÄT priorisieren: das Studio-Album
    # wird zuerst gefetcht, Bootleg/Live/Comp/Broadcast nach hinten. Stub trägt schon
    # status + release-group, daher Bewertung ohne Detail-Fetch möglich.
    stubs: dict[str, tuple[int, dict]] = {}
    for r in matches:
        for rel in r.get("releases", []):
            rid = rel.get("id")
            if not rid:
                continue
            sq = release_quality(rel)
            if rid not in stubs or sq > stubs[rid][0]:
                stubs[rid] = (sq, rel)
    ranked = sorted(stubs.values(), key=lambda v: -v[0])

    failed = 0
    for _sq, stub in ranked[:max_releases]:
        rid = stub["id"]
        rdata = fetch(f"{MB}/release/{rid}?inc=labels+url-rels+release-groups&fmt=json")
        sleep(RATE)
        if not rdata:
            failed += 1                 # scheiterte (Rate-Limit) -> Ergebnis lückenhaft
            continue
        qual = release_quality(rdata)
        rel_info = {"url": f"https://musicbrainz.org/release/{rid}",
                    "album": rdata.get("title", "") or "", "date": rdata.get("date", "") or ""}
        names, dids = parse_release_labels(rdata)
        for n in names:
            if n not in out.label_names:                 # erste Quelle = beste (ranked best-first)
                out.label_names.append(n)
                out.label_release[n] = rel_info          # Album/Jahr/URL aus der Release DIESES Labels
            out.label_quality[n] = max(out.label_quality.get(n, qual), qual)
            out.label_country[n] = max(out.label_country.get(n, 0), country_bonus(rdata))
        for d in dids:
            if d not in out.discogs_release_ids:
                out.discogs_release_ids.append(d)
    out.incomplete = failed > 0

    # ISRC + Work-Relations in EINEM Call; daraus Writer (Komponist/Texter)
    det = fetch(f"{MB}/recording/{out.recording_id}?inc=isrcs+work-rels&fmt=json")
    sleep(RATE)
    if det:
        out.isrcs = list(det.get("isrcs", []) or [])
        work_ids = [r["work"]["id"] for r in (det.get("relations") or [])
                    if r.get("type") == "performance" and r.get("work")]
        # Alle verknüpften Works prüfen (nicht nur das erste): Re-Recordings/Compilations
        # haben oft mehrere Works, und nur eines trägt die Writer-Relationen. Erstes mit
        # Treffer gewinnt, dann Schluss (spart Calls).
        first_pub = ""
        first_iswcs: list[str] = []
        first_gema: list[str] = []
        for wid in work_ids[:3]:
            w = fetch(f"{MB}/work/{wid}?inc=artist-rels+label-rels&fmt=json")   # + Verlag (label-rels)
            sleep(RATE)
            if not w:
                continue
            comp, lyr = extract_writers(w)
            pub = extract_publishers(w)
            iswcs = extract_iswcs(w)
            gema = extract_gema_ids(w)
            first_pub = first_pub or pub
            first_iswcs = first_iswcs or iswcs
            first_gema = first_gema or gema
            if comp or lyr:
                out.composer, out.lyricist = comp, lyr
                out.publisher = pub or first_pub
                out.iswcs = iswcs or first_iswcs
                out.gema_ids = gema or first_gema
                break
        if not out.publisher:
            out.publisher = first_pub          # Work ohne Writer, aber mit Verlag: trotzdem mitnehmen
        if not out.iswcs:
            out.iswcs = first_iswcs
        if not out.gema_ids:
            out.gema_ids = first_gema
    return out
