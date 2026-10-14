from core import deezer


def test_parse_first_track():
    data = {"data": [{"id": 1, "isrc": "USX1", "link": "https://deezer.com/track/1", "album": {"id": 92}}]}
    t = deezer.parse_first_track(data)
    assert t["album_id"] == 92 and t["isrc"] == "USX1" and t["link"].endswith("/1")


def test_parse_first_track_empty():
    assert deezer.parse_first_track({"data": []}) is None


def test_parse_album():
    a = deezer.parse_album({"title": "The Collection", "label": "Maverick", "release_date": "2005-11-11"})
    assert a == {"label": "Maverick", "album": "The Collection", "date": "2005-11-11"}


def test_lookup_mocked():
    def fake(url):
        if "search" in url:
            return {"data": [{"isrc": "USX1", "link": "L", "album": {"id": 92}}]}
        if "album/92" in url:
            return {"title": "Alb", "label": "Maverick", "release_date": "2005"}
        return None
    r = deezer.lookup("A", "B", fetch=fake, sleep=lambda *_: None)
    assert r.label == "Maverick" and r.isrc == "USX1" and r.album == "Alb" and r.date == "2005"


def test_lookup_no_results():
    r = deezer.lookup("A", "B", fetch=lambda url: {"data": []}, sleep=lambda *_: None)
    assert r is None
