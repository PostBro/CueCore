from core import audd, discogs, itunes


# --- Discogs-Auth (Personal Token oder Consumer Key+Secret, kein OAuth-Flow) ---

def test_discogs_auth_header_personal_token():
    assert discogs.auth_header(token="TOK") == {"Authorization": "Discogs token=TOK"}


def test_discogs_auth_header_consumer_key_secret():
    h = discogs.auth_header(consumer_key="KEY", consumer_secret="SECRET")
    assert h == {"Authorization": "Discogs key=KEY, secret=SECRET"}


def test_discogs_auth_header_token_takes_precedence():
    h = discogs.auth_header(token="TOK", consumer_key="KEY", consumer_secret="SECRET")
    assert h == {"Authorization": "Discogs token=TOK"}


def test_discogs_auth_header_empty_when_incomplete():
    assert discogs.auth_header(consumer_key="KEY") == {}      # Secret fehlt
    assert discogs.auth_header(consumer_secret="SECRET") == {}
    assert discogs.auth_header() == {}


# --- iTunes ---

def test_itunes_parse_first():
    r = itunes.parse_first({"results": [{"collectionName": "Future Nostalgia",
                                         "releaseDate": "2020-03-27T12:00:00Z", "primaryGenreName": "Pop"}]})
    assert r.album == "Future Nostalgia" and r.date == "2020-03-27" and r.genre == "Pop"


def test_itunes_parse_empty():
    assert itunes.parse_first({"results": []}) is None


# --- AudD ---

def test_audd_parse_result():
    p = {"status": "success", "result": {"artist": "A", "title": "T", "label": "L",
                                         "isrc": "I", "album": "Alb", "release_date": "2020"}}
    r = audd.parse_result(p)
    assert r["label"] == "L" and r["isrc"] == "I" and r["artist"] == "A"


def test_audd_no_match():
    assert audd.parse_result({"status": "success", "result": None}) is None
    assert audd.parse_result({"status": "error"}) is None


def test_audd_recognize_without_token():
    assert audd.recognize("/x.mp3", "") is None


_NO_SNIPPET = lambda p: None   # überspringt ffmpeg in Tests -> Originalpfad (Mock-poster ignoriert ihn)


def test_audd_recognize_mocked():
    poster = lambda data, fp: {"status": "success", "result": {"artist": "A", "title": "T", "label": "Maverick"}}
    r = audd.recognize("/x.mp3", "tok", poster=poster, make_snippet=_NO_SNIPPET)
    assert r["label"] == "Maverick" and r["artist"] == "A"


def test_audd_recognize_requests_spotify():
    captured = {}
    def poster(data, fp):
        captured.update(data)
        return {"status": "success", "result": {"artist": "A", "title": "T"}}
    audd.recognize("/x.mp3", "tok", poster=poster, make_snippet=_NO_SNIPPET)
    assert "spotify" in captured["return"].split(",")


def test_audd_parse_result_extracts_streaming_links():
    p = {"status": "success", "result": {"artist": "A", "title": "T",
         "apple_music": {"url": "https://music.apple.com/x"},
         "spotify": {"external_urls": {"spotify": "https://open.spotify.com/track/x"}}}}
    r = audd.parse_result(p)
    assert r["apple_music_url"] == "https://music.apple.com/x"
    assert r["spotify_url"] == "https://open.spotify.com/track/x"


def test_audd_parse_result_missing_streaming_links_are_empty():
    p = {"status": "success", "result": {"artist": "A", "title": "T"}}
    r = audd.parse_result(p)
    assert r["apple_music_url"] == "" and r["spotify_url"] == ""


# --- ISRC-Fallback (AudD liefert kein Top-Level-ISRC, nur in apple_music/spotify) ---

def test_audd_isrc_from_apple_music_when_no_toplevel():
    p = {"status": "success", "result": {"artist": "A", "title": "T",
         "apple_music": {"isrc": "USUM71414163"}}}
    assert audd.parse_result(p)["isrc"] == "USUM71414163"


def test_audd_isrc_from_spotify_external_ids():
    p = {"status": "success", "result": {"artist": "A", "title": "T",
         "spotify": {"external_ids": {"isrc": "GBAYE0601498"}}}}
    assert audd.parse_result(p)["isrc"] == "GBAYE0601498"


def test_audd_isrc_toplevel_takes_precedence():
    p = {"status": "success", "result": {"artist": "A", "title": "T", "isrc": "TOP123",
         "apple_music": {"isrc": "AM456"}}}
    assert audd.parse_result(p)["isrc"] == "TOP123"


# --- Ausschnitt-Extraktion (ffmpeg umgeht das 10 MB / 25 s Limit) ---

def test_audd_extract_snippet_short_track_starts_at_zero():
    calls = {}
    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return None
    import subprocess as _sp
    orig = _sp.run
    _sp.run = fake_run
    try:
        out = audd.extract_snippet("/x.mp3", duration_fn=lambda p: 10.0)  # kürzer als Start+Länge
        assert out and out.endswith(".mp3")
        i = calls["cmd"].index("-ss")
        assert calls["cmd"][i + 1] == "0"     # kurzer Track -> ab Sekunde 0
    finally:
        _sp.run = orig
        import os as _os
        try: _os.remove(out)
        except (OSError, TypeError): pass


def test_audd_extract_snippet_long_track_skips_intro():
    calls = {}
    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return None
    import subprocess as _sp
    orig = _sp.run
    _sp.run = fake_run
    try:
        out = audd.extract_snippet("/x.mp3", duration_fn=lambda p: 200.0)  # langer Track
        i = calls["cmd"].index("-ss")
        assert calls["cmd"][i + 1] == str(audd.SNIPPET_START_DEFAULT)   # überspringt Intro
    finally:
        _sp.run = orig
        import os as _os
        try: _os.remove(out)
        except (OSError, TypeError): pass


def test_audd_extract_snippet_without_console_window_on_windows(monkeypatch):
    """ffmpeg aus dem fensterlosen Windows-Build ohne aufblitzende Konsole."""
    import os as _os
    import subprocess as _sp
    import sys as _sys
    seen = {}
    monkeypatch.setattr(_sp, "run", lambda cmd, **kw: seen.update(kw))
    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setattr(_sp, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    out = audd.extract_snippet("/x.mp3", duration_fn=lambda p: 10.0)
    try:
        assert seen.get("creationflags") == _sp.CREATE_NO_WINDOW
    finally:
        _os.remove(out)


def test_audd_extract_snippet_returns_none_when_ffmpeg_missing():
    def fake_run(cmd, **kw):
        raise FileNotFoundError("ffmpeg")
    import subprocess as _sp
    orig = _sp.run
    _sp.run = fake_run
    try:
        assert audd.extract_snippet("/x.mp3", duration_fn=lambda p: 200.0) is None
    finally:
        _sp.run = orig


def test_audd_recognize_cleans_up_snippet(tmp_path):
    snip = tmp_path / "snippet.mp3"
    snip.write_bytes(b"data")
    poster = lambda data, fp: {"status": "success", "result": {"artist": "A", "title": "T"}}
    r = audd.recognize("/x.mp3", "tok", poster=poster, make_snippet=lambda p: str(snip))
    assert r["artist"] == "A"
    assert not snip.exists()     # temporärer Ausschnitt wird aufgeräumt


def test_audd_recognize_uploads_snippet_not_original(tmp_path):
    snip = tmp_path / "snippet.mp3"
    snip.write_bytes(b"data")
    seen = {}
    def poster(data, fp):
        seen["fp"] = fp
        return {"status": "success", "result": {"artist": "A", "title": "T"}}
    audd.recognize("/original/huge.mp3", "tok", poster=poster, make_snippet=lambda p: str(snip))
    assert seen["fp"] == str(snip)   # nicht die (zu große) Originaldatei


# --- possible_cover ---

def test_is_possible_cover_no_expected_values():
    assert audd.is_possible_cover("", "", "Real Artist", "Real Title") is False


def test_is_possible_cover_artist_mismatch():
    assert audd.is_possible_cover("Cover Band", "", "Original Artist", "Some Title") is True


def test_is_possible_cover_title_mismatch():
    assert audd.is_possible_cover("", "Cover Version Title", "Some Artist", "Original Title") is True


def test_is_possible_cover_matches_case_insensitive():
    assert audd.is_possible_cover("alanis morissette", "thank you",
                                  "Alanis Morissette", "Thank You") is False


def test_audd_recognize_flags_possible_cover():
    poster = lambda data, fp: {"status": "success",
                                "result": {"artist": "Original Artist", "title": "Same Title"}}
    r = audd.recognize("/x.mp3", "tok", poster=poster,
                       expected_artist="Cover Band", expected_title="Same Title")
    assert r["possible_cover"] is True


def test_audd_recognize_no_expected_values_no_cover_flag():
    poster = lambda data, fp: {"status": "success",
                                "result": {"artist": "Original Artist", "title": "Same Title"}}
    r = audd.recognize("/x.mp3", "tok", poster=poster)
    assert r["possible_cover"] is False
