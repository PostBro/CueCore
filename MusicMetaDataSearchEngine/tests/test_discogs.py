"""Discogs: Reihenfolge der Releases."""
from core import discogs




def test_deutsche_pressung_zuerst_dann_europa(monkeypatch):
    """Derselbe Song läuft in Deutschland oft über ein anderes Label als
    in den USA, und der Labelcode ist ein deutsches Register. Kein Filter — nur die Reihenfolge."""
    data = {"results": [
        {"id": 1, "country": "US"}, {"id": 2, "country": "Europe"}, {"id": 3, "country": "Germany"},
        {"id": 4, "country": "UK"}, {"id": 5, "country": ""}, {"id": 6, "country": "Japan"}]}
    assert discogs.parse_search_release_ids(data, limit=6) == ["3", "2", "4", "1", "5", "6"]
    assert discogs.parse_search_release_ids(data, limit=2) == ["3", "2"]
    assert discogs.country_rank("Germany") == 0 and discogs.country_rank("germany") == 0
    assert discogs.country_rank("Europe") == 1 and discogs.country_rank("UK") == 1
    assert discogs.country_rank("US") == 2 and discogs.country_rank("") == 2
    assert discogs.parse_search_release_ids(None) == []


def test_kombinierte_laenderangaben_zaehlen_als_europa():
    """Discogs schreibt Mehrländer-Pressungen als Text („UK & France“) und das Gebiet mit Klammerzusatz
    („German Democratic Republic (GDR)“). Ein europäisches Teil-Land reicht für Rang 1."""
    for c in ("UK & France", "UK & Europe", "UK, Europe & US", "Europe & US", "UK & Ireland",
              "France & Benelux", "Germany, Austria & Switzerland", "Germany & Switzerland"):
        assert discogs.country_rank(c) == 1, c
    for c in ("US", "USA & Canada", "Australia & New Zealand", "Japan", ""):
        assert discogs.country_rank(c) == 2, c
    assert discogs.country_rank("Germany") == 0


def test_ddr_pressung_zaehlt_wie_deutschland():
    """DDR-Pressungen zählen bei der Label-Wahl wie Deutschland. Discogs schreibt das Gebiet
    „German Democratic Republic (GDR)“."""
    for c in ("German Democratic Republic (GDR)", "german democratic republic", "Germany & German Democratic Republic (GDR)"):
        assert discogs.country_rank(c) == 0, c
    assert discogs.country_rank("German Democratic Republic (GDR) & Poland") == 1
    data = {"results": [{"id": 1, "country": "Europe"}, {"id": 2, "country": "US"},
                        {"id": 3, "country": "German Democratic Republic (GDR)"}, {"id": 4, "country": "Germany"}]}
    assert discogs.parse_search_release_ids(data, limit=4) == ["3", "4", "1", "2"]
