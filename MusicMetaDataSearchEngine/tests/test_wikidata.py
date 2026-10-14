from core import wikidata


def _sparql(*rows):
    """rows = (item_qid|None, label|None, composer|None, lyricist|None[, performer]) -> SPARQL-JSON."""
    bindings = []
    for row in rows:
        qid, lab, comp, lyr = row[:4]
        perf = row[4] if len(row) > 4 else None
        b = {}
        if qid:
            b["item"] = {"type": "uri", "value": "http://www.wikidata.org/entity/" + qid}
        if lab is not None:
            b["recordLabelLabel"] = {"type": "literal", "value": lab}
        if comp is not None:
            b["composerLabel"] = {"type": "literal", "value": comp}
        if lyr is not None:
            b["lyricistLabel"] = {"type": "literal", "value": lyr}
        if perf is not None:
            b["perfLabel"] = {"type": "literal", "value": perf}
        bindings.append(b)
    return {"results": {"bindings": bindings}}


def test_build_query_uses_search_index_and_escapes():
    q = wikidata.build_query('Sia', 'Chandelier "Live"')
    assert "wikibase:mwapi" in q and "EntitySearch" in q       # schneller Suchindex, kein rdfs:label-Scan
    assert 'Chandelier \\"Live\\"' in q                        # Anführungszeichen escaped
    assert "wdt:P264" in q and "wdt:P86" in q and "wdt:P676" in q
    assert "wdt:P175" in q                                     # Performer-Filter bei gesetztem Interpret


def test_build_query_without_artist_binds_performer_optional():
    # Titel-only: Performer nicht als Filter, aber OPTIONAL binden -> Artist-Kandidaten.
    q = wikidata.build_query('', 'Bohemian Rhapsody')
    assert "OPTIONAL { ?item wdt:P175 ?perf. }" in q
    assert "CONTAINS(LCASE(STR(?pn))" not in q      # kein Performer-Namensfilter
    assert "?perfLabel" in q


def test_search_url_encodes_query():
    url = wikidata.search_url('A', 'B')
    assert url.startswith(wikidata.WIKIDATA + "?")
    assert "format=json" in url and "query=" in url


def test_parse_dedupes_and_keeps_order():
    data = _sparql(
        ("Q1", "Big Machine Records", None, "Taylor Swift"),
        ("Q1", "Republic Records", None, "Max Martin"),
        ("Q1", "Big Machine Records", None, "Taylor Swift"),   # Duplikat
    )
    r = wikidata.parse(data)
    assert r.labels == ["Big Machine Records", "Republic Records"]
    assert r.lyricists == ["Taylor Swift", "Max Martin"]
    assert r.item_url == "https://www.wikidata.org/wiki/Q1"


def test_parse_collects_performers():
    data = _sparql(
        ("Q1", "4AD", None, None, "Bon Iver"),
        ("Q2", "Atlantic", None, None, "Birdy"),
        ("Q2", "Atlantic", None, None, "Birdy"),      # Duplikat-Performer
    )
    r = wikidata.parse(data)
    assert r.performers == ["Bon Iver", "Birdy"]
    assert r.item_url == "https://www.wikidata.org/wiki/Q1"


def test_lookup_returns_result_for_performer_only():
    # Titel-only: nur ein Performer, kein Label/Writer -> trotzdem verwertbar.
    data = _sparql(("Q1", None, None, None, "Bon Iver"))
    r = wikidata.lookup("", "Skinny Love", fetch=lambda url: data, sleep=lambda *_: None)
    assert r is not None and r.performers == ["Bon Iver"]


def test_parse_drops_unresolved_qid_values():
    # Label-Service nicht aufgelöst -> nackte Q-ID; darf NICHT als Labelname zählen.
    data = _sparql(("Q1", "Q12345", None, None), ("Q1", "Columbia Records", None, None))
    r = wikidata.parse(data)
    assert r.labels == ["Columbia Records"]


def test_parse_empty():
    r = wikidata.parse({"results": {"bindings": []}})
    assert r.labels == [] and r.composers == [] and r.lyricists == [] and r.item_url == ""


def test_lookup_mocked():
    data = _sparql(("Q26876", "Republic Records", "Max Martin", "The Weeknd"))
    r = wikidata.lookup("The Weeknd", "Blinding Lights",
                        fetch=lambda url: data, sleep=lambda *_: None)
    assert r.labels == ["Republic Records"] and r.composers == ["Max Martin"]


def test_lookup_no_title_returns_none():
    called = []
    r = wikidata.lookup("Artist", "  ", fetch=lambda url: called.append(url) or {}, sleep=lambda *_: None)
    assert r is None and called == []       # ohne Titel gar kein Call


def test_lookup_none_when_nothing_useful():
    r = wikidata.lookup("A", "B", fetch=lambda url: _sparql(("Q1", None, None, None)),
                        sleep=lambda *_: None)
    assert r is None                        # Item ohne Label/Writer -> kein verwertbarer Treffer


def test_lookup_fetch_fail_returns_none():
    r = wikidata.lookup("A", "B", fetch=lambda url: None, sleep=lambda *_: None)
    assert r is None


# --- per-item grouping (Titel-only) ---

def test_parse_items_groups_by_item_and_pairs():
    data = _sparql(
        ("Q1", "4AD", None, None, "Bon Iver"),
        ("Q2", "Atlantic", "Writer A", None, "Birdy"),
        ("Q2", "Atlantic", "Writer A", None, "Birdy"),   # Duplikat in Item Q2
    )
    items = wikidata.parse_items(data)
    assert len(items) == 2
    assert items[0].performers == ["Bon Iver"] and items[0].labels == ["4AD"]
    assert items[1].performers == ["Birdy"] and items[1].labels == ["Atlantic"]
    assert items[1].composers == ["Writer A"]
    assert items[0].item_url == "https://www.wikidata.org/wiki/Q1"


def test_lookup_items_drops_items_without_performer():
    data = _sparql(
        ("Q1", "4AD", None, None, "Bon Iver"),
        ("Q2", "Atlantic", None, None, None),            # kein Performer -> raus
    )
    items = wikidata.lookup_items("Skinny Love", fetch=lambda url: data, sleep=lambda *_: None)
    assert [i.performers for i in items] == [["Bon Iver"]]


def test_lookup_items_no_title():
    assert wikidata.lookup_items("  ", fetch=lambda url: {}, sleep=lambda *_: None) == []
