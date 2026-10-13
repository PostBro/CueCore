"""Wikidata-Lookup (frei, KEIN Key): Track -> Record Label (P264) + Writer.

Vierte unabhängige, strukturierte Quelle. Ein einziger SPARQL-Call liefert für
kommerzielle Tracks das Record Label (P264) und die AutorInnen (P86 Komponist,
P676 Texter) — schnell und ohne die mehrstufige Release-Kette von MusicBrainz,
also robust auch dann, wenn MB rate-limitet oder keinen Recording->Werk-Link hat
(Cover/Neuaufnahmen). Die Labelnamen kommen in Wikidata-Schreibweise (z.B.
"Republic Records") und werden vom Resolver via labelmatch gegen den GVL-Master
gemappt; ein bestätigter GVL-Code zählt als weitere Korroboration.

Parser sind netzfrei testbar; die Netzschicht (fetch) wird injiziert.
"""
from __future__ import annotations
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

WIKIDATA = "https://query.wikidata.org/sparql"
USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"

_QID = re.compile(r"^Q\d+$")
_ENTITY_PREFIX = "http://www.wikidata.org/entity/"


@dataclass
class WikidataResult:
    labels: list[str] = field(default_factory=list)      # Record-Label-Namen (P264), Wikidata-Schreibweise
    composers: list[str] = field(default_factory=list)   # P86
    lyricists: list[str] = field(default_factory=list)    # P676
    performers: list[str] = field(default_factory=list)   # P175 (Interpret — für Titel-only-Suche)
    item_url: str = ""                                    # erstes Item (Provenance-Link)


def _lit(s: str) -> str:
    """String -> sicheres SPARQL-Literal (nur der Inhalt, ohne Anführungszeichen)."""
    return (s.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", " ").replace("\r", " ").replace("\t", " "))


def build_query(artist: str, title: str) -> str:
    """SPARQL über den Wikidata-SUCHINDEX (wikibase:mwapi EntitySearch) auf den Titel,
    dann Filter auf Performer (P175), dessen Name den Interpreten enthält -> Record
    Label + Writer. EntitySearch nutzt den Volltext-/Label-Index (schnell) statt eines
    teuren rdfs:label-Scans mit LCASE-Filter (der auf WDQS timeoutet). CONTAINS auf den
    Performer-Namen ist tolerant gegen 'Taylor Swift' vs. 'Taylor Alison Swift'. Fehlt
    der Interpret, wird ohne Performer-Filter gesucht (breiter, für Titel-only)."""
    t, a = _lit(title.strip()), _lit(artist.strip())
    # Performer (P175): bei gesetztem Interpret als Pflicht-Filter (verengt), bei Titel-only
    # OPTIONAL (dann liefert ?performerLabel die Artist-Kandidaten für die Titel-Suche).
    perf = (f'  ?item wdt:P175 ?perf .\n'
            f'  ?perf rdfs:label ?pn . FILTER(LANG(?pn) = "en" '
            f'&& CONTAINS(LCASE(STR(?pn)), LCASE("{a}")))\n'
            if artist.strip() else "  OPTIONAL { ?item wdt:P175 ?perf. }\n")
    return (
        "SELECT ?item ?recordLabelLabel ?composerLabel ?lyricistLabel ?perfLabel WHERE {\n"
        "  SERVICE wikibase:mwapi {\n"
        '    bd:serviceParam wikibase:endpoint "www.wikidata.org";\n'
        '                    wikibase:api "EntitySearch";\n'
        f'                    mwapi:search "{t}";\n'
        '                    mwapi:language "en";\n'
        '                    mwapi:limit "20".\n'
        "    ?item wikibase:apiOutputItem mwapi:item.\n"
        "  }\n"
        f"{perf}"
        "  OPTIONAL { ?item wdt:P264 ?recordLabel. }\n"
        "  OPTIONAL { ?item wdt:P86 ?composer. }\n"
        "  OPTIONAL { ?item wdt:P676 ?lyricist. }\n"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n'
        "} LIMIT 40"
    )


def search_url(artist: str, title: str) -> str:
    q = urllib.parse.urlencode({"query": build_query(artist, title), "format": "json"})
    return f"{WIKIDATA}?{q}"


def http_get_json(url: str, ua: str = USER_AGENT, *, headers: dict | None = None,
                  retries: int = 2) -> dict | None:
    h = {"User-Agent": ua, "Accept": "application/sparql-results+json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1)); continue
            return None
        except Exception:
            return None
    return None


def _val(binding: dict, key: str) -> str:
    v = (binding.get(key) or {}).get("value", "") or ""
    v = v.strip()
    return "" if _QID.match(v) else v      # Label-Service nicht aufgelöst -> Q-ID verwerfen


def parse(data: dict | None) -> WikidataResult:
    """SPARQL-JSON -> WikidataResult (dedupliziert, Reihenfolge stabil)."""
    res = WikidataResult()
    seen = {"labels": set(), "composers": set(), "lyricists": set(), "performers": set()}
    for b in (data or {}).get("results", {}).get("bindings", []):
        if not res.item_url:
            iv = (b.get("item") or {}).get("value", "")
            if iv.startswith(_ENTITY_PREFIX):
                res.item_url = "https://www.wikidata.org/wiki/" + iv[len(_ENTITY_PREFIX):]
        for key, attr in (("recordLabelLabel", "labels"),
                          ("composerLabel", "composers"),
                          ("lyricistLabel", "lyricists"),
                          ("perfLabel", "performers")):
            v = _val(b, key)
            if v and v.lower() not in seen[attr]:
                seen[attr].add(v.lower())
                getattr(res, attr).append(v)
    return res


def lookup(artist: str, title: str, *, fetch=http_get_json, sleep=time.sleep) -> WikidataResult | None:
    """artist+title -> WikidataResult | None. None, wenn Titel fehlt oder nichts
    Verwertbares (Label/Writer) gefunden wurde."""
    if not title.strip():
        return None
    data = fetch(search_url(artist, title))
    sleep(0.2)
    if not data:
        return None
    res = parse(data)
    return res if (res.labels or res.composers or res.lyricists or res.performers) else None


@dataclass
class WikidataItem:
    """Ein einzelnes Wikidata-Item (Song/Single) mit korrekt gepaartem Performer/Label/
    Writer — für die Titel-only-Suche, wo mehrere Items denselben Titel tragen."""
    performers: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    composers: list[str] = field(default_factory=list)
    lyricists: list[str] = field(default_factory=list)
    item_url: str = ""


def parse_items(data: dict | None) -> list[WikidataItem]:
    """SPARQL-JSON -> je Item EIN WikidataItem (Reihenfolge stabil, pro Item dedupliziert).
    Anders als parse() (das über alle Items flacht) bleibt hier die Performer<->Label-
    Paarung erhalten — nötig, um pro Titel-Treffer einen sauberen Interpret-Kandidaten
    mit passendem Label-Preview zu bauen."""
    groups: dict[str, WikidataItem] = {}
    order: list[str] = []
    for b in (data or {}).get("results", {}).get("bindings", []):
        iv = (b.get("item") or {}).get("value", "")
        if not iv.startswith(_ENTITY_PREFIX):
            continue
        if iv not in groups:
            groups[iv] = WikidataItem(item_url="https://www.wikidata.org/wiki/" + iv[len(_ENTITY_PREFIX):])
            order.append(iv)
        it = groups[iv]
        for key, bucket in (("perfLabel", it.performers), ("recordLabelLabel", it.labels),
                            ("composerLabel", it.composers), ("lyricistLabel", it.lyricists)):
            v = _val(b, key)
            if v and v.lower() not in {x.lower() for x in bucket}:
                bucket.append(v)
    return [groups[i] for i in order]


def lookup_items(title: str, *, fetch=http_get_json, sleep=time.sleep) -> list[WikidataItem]:
    """Titel-only -> Liste der Wikidata-Items mit Performer (Interpret-Kandidaten). Leer,
    wenn Titel fehlt / kein Treffer. Nur Items MIT Performer sind für die Titel-Suche nützlich."""
    if not title.strip():
        return []
    data = fetch(search_url("", title))
    sleep(0.2)
    if not data:
        return []
    return [it for it in parse_items(data) if it.performers]
