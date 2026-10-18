from pathlib import Path

from core import operations, tags, watch
from core.resolver import Candidate, ResolveResult


def _mk(tmp_path, name, **t):
    p = tmp_path / name
    p.write_bytes(b"")
    if t:
        tags.write_tags(p, t)
    return p


def _res(conf, code="24584", label="MOM & POP", isrcs=None):
    r = ResolveResult(artist="A", title="B", matched=True, mb_score=100, isrcs=isrcs or [])
    if code:
        r.candidates = [Candidate(labelcode=code, label=label, confidence=conf)]
    return r


# --- identify ---

def test_identify_from_tags(tmp_path):
    p = _mk(tmp_path, "whatever.mp3", artist="Alanis Morissette", title="Thank You")
    assert watch.identify(p) == ("Alanis Morissette", "Thank You")


def test_identify_from_filename(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    assert watch.identify(p) == ("Alanis Morissette", "Thank You")


def test_identify_strips_label_block(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3")
    assert watch.identify(p) == ("Alanis Morissette", "Thank You")


def test_identify_unparseable(tmp_path):
    p = _mk(tmp_path, "randomname.mp3")
    assert watch.identify(p) == ("", "")


# --- Underscore-Ripper-Schema '{Titel}_{Album}_{Interpret}_{Bitrate}' (keine Tags) ---

def test_parse_underscore_title_album_artist_bitrate():
    a, ti = watch.parse_artist_title_from_stem(
        "Nordwand (Zzq Remix)_Zzq Testserie (Limited Event Series)_ ZZQ BAND_192")
    assert a == "ZZQ BAND"
    assert ti == "Nordwand (Zzq Remix)"


def test_parse_underscore_hyphenated_artist():
    a, ti = watch.parse_artist_title_from_stem(
        "NORDWAND_ZZQ TESTSERIE SOUNDTRACK_ERIKA TESTERIN-ZZQ BAND_192")
    assert a == "ERIKA TESTERIN-ZZQ BAND" and ti == "NORDWAND"


def test_parse_underscore_without_album():
    # Titel_Interpret_Bitrate (kein Album in der Mitte) — Interpret bleibt vorletztes Feld
    assert watch.parse_artist_title_from_stem("Some Song_Some Artist_320") == ("Some Artist", "Some Song")


def test_parse_underscore_needs_bitrate_marker():
    # ohne Bitrate-End-Marker zu mehrdeutig -> nicht erraten
    assert watch.parse_artist_title_from_stem("Just_Some_Random_Name") == ("", "")
    # 4-stellige Nicht-Bitrate (Jahr) zählt nicht als Marker
    assert watch.parse_artist_title_from_stem("Song_Artist_2017") == ("", "")


def test_dash_scheme_still_wins_over_underscore():
    # bestehendes 'Interpret - Titel' hat Vorrang, auch wenn Underscores da sind
    assert watch.parse_artist_title_from_stem("Alanis Morissette - Thank_You") == ("Alanis Morissette", "Thank_You")


def test_identify_underscore_scheme_from_filename(tmp_path):
    p = _mk(tmp_path, "LAURA PALMERS THEME_ZZQ TESTSERIE SOUNDTRACK_ANGELO BADALAMENTI_192.mp3")
    assert watch.identify(p) == ("ANGELO BADALAMENTI", "LAURA PALMERS THEME")


def test_parse_album_from_underscore_scheme():
    assert watch.parse_album_from_stem(
        "FALLING_ZZQ TESTSERIE SOUNDTRACK_ANGELO BADALAMENTI_192") == "ZZQ TESTSERIE SOUNDTRACK"


def test_parse_album_empty_without_album_field():
    assert watch.parse_album_from_stem("Some Song_Some Artist_320") == ""   # nur 3 Felder, kein Album
    assert watch.parse_album_from_stem("Alanis - Thank You") == ""          # dash-Schema hat kein Album


def test_identify_album_from_filename(tmp_path):
    p = _mk(tmp_path, "FALLING_ZZQ TESTSERIE SOUNDTRACK_ANGELO BADALAMENTI_192.mp3")
    assert watch.identify_album(p) == "ZZQ TESTSERIE SOUNDTRACK"


def test_identify_album_prefers_tag(tmp_path):
    p = _mk(tmp_path, "FALLING_ZZQ TESTSERIE SOUNDTRACK_ANGELO BADALAMENTI_192.mp3", album="Tag-Album")
    assert watch.identify_album(p) == "Tag-Album"


# --- extract_lc_from_stem (Dateiname als 1. LC-Quelle, VOR dem Klammerblock-Verwurf) ---

def test_extract_lc_from_schema_name():
    assert watch.extract_lc_from_stem("Alanis - Thank You - [MAVERICK_LC01485]") == "01485"


def test_extract_lc_from_underscore_name():
    assert watch.extract_lc_from_stem("Groove Attack - Trax_LC86295") == "86295"


def test_extract_lc_six_digits_unchanged():
    # 6-stellig bleibt unverändert (LC101515 != LC01515) — normalize füllt NICHT auf
    assert watch.extract_lc_from_stem("X - Y - [MOM & POP_LC101515]") == "101515"


def test_extract_lc_tolerates_space():
    assert watch.extract_lc_from_stem("Some Track (LC 24584)") == "24584"


def test_extract_lc_pads_short_code():
    # <=5 Stellen -> führende Nullen (LC0309 == LC00309)
    assert watch.extract_lc_from_stem("Foo - Bar_LC0309") == "00309"


def test_extract_lc_none_when_absent():
    assert watch.extract_lc_from_stem("just a filename with no code") == ""


# --- is_stable ---

def test_is_stable_static_file(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"12345")
    assert watch.is_stable(p, checks=2, interval=0, sleep=lambda *_: None) is True


def test_is_stable_missing_file(tmp_path):
    assert watch.is_stable(tmp_path / "nope.mp3", sleep=lambda *_: None) is False


# --- process_one ---

def test_process_preview_does_not_write(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9), label_lookup=lambda c: ("MOM & POP", "R12"),
                          op_con=op, apply=False)
    assert r.action == "review" and r.labelcode == "24584"
    assert p.exists()       # Preview: Datei unangetastet


def test_process_apply_tags_and_renames(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9, isrcs=["USMA10100001"]),
                          label_lookup=lambda c: ("MOM & POP", "R12"),
                          op_con=op, apply=True, min_band="auto")
    assert r.action == "applied"
    new = Path(r.new_path)
    assert new.name == "Alanis Morissette - Thank You - [MOM & POP_LC24584].mp3"
    assert not p.exists() and new.exists()
    assert tags.read_tags(new)["lc"] == "LC24584"
    assert "R12" in r.note      # Rechterückruf weitergereicht


def test_process_apply_below_min_band_goes_review(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.2),  # band review_low
                          label_lookup=lambda c: ("MOM & POP", "R12"),
                          op_con=op, apply=True, min_band="auto")
    assert r.action == "review" and p.exists()   # zu unsicher -> kein Auto-Write


def test_process_unidentifiable(tmp_path):
    p = _mk(tmp_path, "randomname.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9), label_lookup=lambda c: ("", ""), op_con=op)
    assert r.identified is False and r.action == "skip"


def test_process_no_label(tmp_path):
    p = _mk(tmp_path, "Some Artist - Some Song.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0, code=""),  # keine Kandidaten
                          label_lookup=lambda c: ("", ""), op_con=op)
    assert r.action == "review" and "kein GVL-Label" in r.note


# --- process_one: AudD-Fingerprinting-Fallback + possible_cover ---

def test_process_uses_recognize_fallback_when_unidentifiable(tmp_path):
    p = _mk(tmp_path, "randomname.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    recognize = lambda path, ea, et: {"artist": "Alanis Morissette", "title": "Thank You",
                                       "possible_cover": False}
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9), label_lookup=lambda c: ("MOM & POP", ""),
                          op_con=op, recognize=recognize)
    assert r.identified is True and r.artist == "Alanis Morissette" and r.possible_cover is False


def test_process_flags_possible_cover_from_recognize(tmp_path):
    # ID3-Tag hat nur den Artist (Cover-Band), kein Titel -> Fingerprinting-Fallback greift.
    p = _mk(tmp_path, "whatever.mp3", artist="Cover Band")
    op = operations.open_oplog(tmp_path / "ops.db")
    recognize = lambda path, ea, et: {"artist": "Original Artist", "title": "Some Title",
                                       "possible_cover": ea != "Original Artist"}
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9), label_lookup=lambda c: ("MOM & POP", ""),
                          op_con=op, recognize=recognize)
    assert r.possible_cover is True


# --- parse_cue_from_stem: EDL-Clip-Namen aus Schnittsystemen ---

import pytest

_CUE_CASES = [
    # (clip_name, interpret, titel, labelcode, label)
    # Klammer-Schema '{Interpret} - {Titel} - [Label_LCxxxxx]'
    ("ZZQ BAND - NORDWAND - [COLUMBIA_LC00162].MP3", "ZZQ BAND", "NORDWAND", "00162", "COLUMBIA"),
    ("ERIKA TESTERIN - NORDLICHT - [ISLAND RECORDS_LC00407].MP3", "ERIKA TESTERIN", "NORDLICHT", "00407", "ISLAND RECORDS"),
    ("Zzq Chor - Morgenrot - [NETTWERK PRODUCTIONS_LC11269]", "Zzq Chor", "Morgenrot", "11269", "NETTWERK PRODUCTIONS"),
    ("Nordwand Trio - Kiesel - [ATLANTIC_LC00121]", "Nordwand Trio", "Kiesel", "00121", "ATLANTIC"),
    # '{Interpret} - {Titel} LCxxxxx' bzw. 'LC xxxxx' (ohne Klammer, inline)
    ("ZZQ SOLO - BAR NORD LC00317.MP3.NEW.01", "ZZQ SOLO", "BAR NORD", "00317", ""),
    ("ERIKA TESTERIN - WURZELN LC 00304.MP3", "ERIKA TESTERIN", "WURZELN", "00304", ""),
    # '{Interpret} - {Titel}_LCxxxxx' (LC ohne Space an Titel geklebt)
    ("ZZQ! - NORDWIND ZWEI_LC02604.M4A.NEW.01", "ZZQ!", "NORDWIND ZWEI", "02604", ""),
    ("ZZQ & NORDWAND - LET IT GO_LC00121.M4A.NEW.01", "ZZQ & NORDWAND", "LET IT GO", "00121", ""),
    ("ZZQ SOLO - Fade Into Dusk_LC19813.m4a", "ZZQ SOLO", "Fade Into Dusk", "19813", ""),
    # Underscore '{Titel}_{Interpret}_LCxxxxx'
    ("Where's My Zzq_NORDWAND_LC11269.Copy.01.Copy.01.new.01", "NORDWAND", "Where's My Zzq", "11269", ""),
    ("Be the Fox_ZZQ DUO_LC11269.Copy.01.new.01", "ZZQ DUO", "Be the Fox", "11269", ""),
    ("I Found It_Zzq Run_LC12723.Copy.01.new.01", "Zzq Run", "I Found It", "12723", ""),
    # Underscore-Ripper mit Bitrate '{Titel}_…_{Interpret}_{Bitrate}[_LC xxxxx]'
    ("DONT FORGET ABOUT US_ZZQ TESTFILM - TEST MOVIE (SOURCE)_ ZZQ CLOVES_192_LC 06406.MP3.NEW.01",
     "ZZQ CLOVES", "DONT FORGET ABOUT US", "06406", ""),
    ("READ ALL ABOUT NORDWAND PT III_ZZQ SAMPLER 29 CD1 (D)_ERIKA TESTERIN_48_LC27996.WAV.NEW.01",
     "ERIKA TESTERIN", "READ ALL ABOUT NORDWAND PT III", "27996", ""),
    ("BAD WATER_ZZQ PERFORMS NORDWAND_ ZZQ STRING QUARTET_128.MP3",
     "ZZQ STRING QUARTET", "BAD WATER", "", ""),
    # Track-Nummer-Prefix '{NN} - {Titel}_{Interpret}' bzw. nur Titel
    ("05 - LOOK AT MY ROOM_ZZQ DUO.MP3", "ZZQ DUO", "LOOK AT MY ROOM", "", ""),
    ("04 - SAME ERROR_ERIKA TESTERIN.MP3", "ERIKA TESTERIN", "SAME ERROR", "", ""),
    ("01 - ORBITS (INSTRUMENTAL).MP3", "", "ORBITS (INSTRUMENTAL)", "", ""),
    # Sync-/Source-Cue '{Titel} - songs in cinema…' -> erstes Feld ist der Titel, Interpret leer
    ("An Invitation To Nordwand - songs in cinema_From _Zzq Testserie_LC02604.Copy.01.Copy.01.new.01",
     "", "An Invitation To Nordwand", "02604", ""),
    # Titel-Underscore-Junk wird abgeschnitten
    ("Erika Testerin - Learning Curve_instrumental.mp3.Copy.01.new.01", "Erika Testerin", "Learning Curve", "", ""),
    ("Zzq Duo - Daydream_LC11269_instrumental_zzqjunk.mp3", "Zzq Duo", "Daydream", "11269", ""),
    # Atmo/SFX: kein Interpret
    ("ATMO - WALD 21.AIF.NEW.01", "", "WALD 21", "", ""),
    # Reine Katalognummern (Library-Dateien): NICHT splitten — vorher Interpret '1' / Titel 'CEZ4030';
    # der Katalog-Hint (core/catalog.py) löst den Cue.
    ("CEZ4030_1.mp3", "", "CEZ4030_1", "", ""),
    ("KPMLP1234_05.wav.new.01", "", "KPMLP1234_05", "", ""),
    ("CEZ4030.mp3", "", "CEZ4030", "", ""),
    ("XCD123_04 Title.mp3", "04 Title", "XCD123", "", ""),   # unverändert: kein reiner Katalogname (Titel dabei)
]


@pytest.mark.parametrize("clip,interpret,titel,lc,label", _CUE_CASES)
def test_parse_cue_from_stem_edl_clip_names(clip, interpret, titel, lc, label):
    r = watch.parse_cue_from_stem(clip)
    assert r["interpret"] == interpret, f"interpret: {r['interpret']!r} != {interpret!r} for {clip!r}"
    assert r["titel"] == titel, f"titel: {r['titel']!r} != {titel!r} for {clip!r}"
    assert r["labelcode"] == lc, f"lc: {r['labelcode']!r} != {lc!r} for {clip!r}"
    assert r["label"] == label, f"label: {r['label']!r} != {label!r} for {clip!r}"


def test_parse_cue_leaves_artist_title_from_stem_untouched():
    # Regression: der schlanke Vorgänger bleibt unverändert (Tagger/Watchfolder nutzt ihn).
    assert watch.parse_artist_title_from_stem("Alanis - Thank You") == ("Alanis", "Thank You")


def test_process_one_feeds_isrc_from_tags_into_resolver(tmp_path):
    """watch reicht den Datei-ISRC an die Stufe 0 des Resolvers durch."""
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    tags.write_tags(p, {"isrc": "USMA10100001"})
    op = operations.open_oplog(tmp_path / "ops.db")
    seen = {}

    def rf(a, t, i=""):
        seen["isrc"] = i
        return _res(0.9)

    watch.process_one(p, resolve_fn=rf, label_lookup=lambda c: ("MOM & POP", ""), op_con=op)
    assert seen["isrc"] == "USMA10100001"


def test_identify_isrc_empty_without_tag(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    assert watch.identify_isrc(p) == ""


# --- vertauschte Felder: der Resolver-Tausch muss in den Plan durchschlagen ---

def test_process_one_takes_over_swapped_fields_for_the_plan(tmp_path):
    """Download-Export 'Titel - Interpret.mp3': der Resolver löst getauscht auf. Ohne
    Übernahme trüge die Datei den richtigen LC, aber weiter den falschherum
    gebauten Namen."""
    p = _mk(tmp_path, "Thank You - Alanis Morissette.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")

    def rf(a, t, i=""):
        r = _res(0.9)
        r.artist, r.title, r.swapped = "Alanis Morissette", "Thank You", True
        return r

    r = watch.process_one(p, resolve_fn=rf, label_lookup=lambda c: ("MOM & POP", ""),
                          op_con=op, apply=True, min_band="auto")
    assert r.swapped is True and (r.artist, r.title) == ("Alanis Morissette", "Thank You")
    assert Path(r.new_path).name == "Alanis Morissette - Thank You - [MOM & POP_LC24584].mp3"


def test_process_one_leaves_fields_alone_without_swap(tmp_path):
    p = _mk(tmp_path, "Alanis Morissette - Thank You.mp3")
    op = operations.open_oplog(tmp_path / "ops.db")
    r = watch.process_one(p, resolve_fn=lambda a, t, i="": _res(0.9),
                          label_lookup=lambda c: ("MOM & POP", ""),
                          op_con=op, apply=True, min_band="auto")
    assert r.swapped is False and (r.artist, r.title) == ("Alanis Morissette", "Thank You")
    assert Path(r.new_path).name == "Alanis Morissette - Thank You - [MOM & POP_LC24584].mp3"


def test_lc_token_with_missing_c_at_end():
    """EDL-Export kappt Zeichen: '…_192_L 01846.MP3' trägt LC01846."""
    from core import watch
    stem = "ZZQ TESTPROJEKT (SOURCE)_ZZQ BAND_NORDWAND LIED_192_L 01846"
    assert watch.extract_lc_from_stem(stem) == "01846"
    assert watch.extract_lc_from_stem("REAL 12345 Title") == ""                  # kein L-Token mitten im Wort
    assert "L 01846" not in watch.parse_cue_from_stem(stem)["titel"]


def test_parse_cue_candidates_cover_artist_title_and_project_artist_title():
    from core import watch
    c = watch.parse_cue_candidates("ZZQ TESTPROJEKT (SOURCE)_ZZQ BAND_NORDWAND LIED_192_L 01846")
    assert ("ZZQ BAND", "NORDWAND LIED") in c
    assert all("(SOURCE)" not in a for a, _ in c[1:])                              # Projekt-Feld ist nie Interpret
    c = watch.parse_cue_candidates("ZZQ BAND_NORDWAND LIED_ZZQ ALBUM EINS ZWEI DREI_ 192_LC06406")
    assert c[1] == ("ZZQ BAND", "NORDWAND LIED")
    c = watch.parse_cue_candidates("ZZQ TESTFILM 2 - TEST MOVIE 2 (SCORE)_NORDLICHT ALWAYS__ERIKA TESTERIN_192_LC99999")
    assert ("ERIKA TESTERIN", "NORDLICHT ALWAYS") in c
    assert watch.parse_cue_candidates("CEZ4030_1") == [("", "CEZ4030_1")]        # Katalognummer: nichts zu paaren
    assert watch.parse_cue_candidates("ZZQ DUO_NORDLICHT_LC00392")[:2] == [("NORDLICHT", "ZZQ DUO"), ("ZZQ DUO", "NORDLICHT")]
