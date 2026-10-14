import shutil
from pathlib import Path

from core import intake, tags
from core.resolver import Candidate, ResolveResult

FIXTURES = Path(__file__).parent / "fixtures"


def _mk(folder, name, **t):
    p = folder / name
    p.write_bytes(b"")
    if t:
        tags.write_tags(p, t)
    return p


def test_is_schema_named():
    assert intake.is_schema_named("Alanis - Thank You - [MAVERICK_LC01485].mp3")
    assert intake.is_schema_named("X - Y - [MOM & POP_LC101515].mp3")
    assert intake.is_schema_named("X - Y - [Label_LC01485].flac")
    assert intake.is_schema_named("X - Y - [Label_LC01485].m4a")
    assert not intake.is_schema_named("irgendwas.mp3")


def test_scan_folder_includes_flac_and_m4a(tmp_path):
    shutil.copy(FIXTURES / "silence.flac", tmp_path / "Artist - Song.flac")
    shutil.copy(FIXTURES / "silence.m4a", tmp_path / "Artist - Other.m4a")

    cards = intake.scan_folder(tmp_path)
    names = {c["name"] for c in cards}
    assert names == {"Artist - Song.flac", "Artist - Other.m4a"}
    byname = {c["name"]: c for c in cards}
    assert byname["Artist - Song.flac"]["artist"] == "Artist"
    assert byname["Artist - Song.flac"]["title"] == "Song"


def test_scan_folder_identifies_and_filters(tmp_path):
    _mk(tmp_path, "The Weeknd - Blinding Lights.mp3")
    _mk(tmp_path, "tagged.mp3", artist="AURORA", title="Runaway")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "LC verified").mkdir()
    _mk(tmp_path / "LC verified", "schon - fertig - [X_LC00121].mp3")  # Unterordner -> nicht gescannt

    cards = intake.scan_folder(tmp_path)
    byname = {c["name"]: c for c in cards}
    assert set(byname) == {"The Weeknd - Blinding Lights.mp3", "tagged.mp3"}  # nur top-level .mp3
    assert byname["The Weeknd - Blinding Lights.mp3"]["artist"] == "The Weeknd"
    assert byname["The Weeknd - Blinding Lights.mp3"]["title"] == "Blinding Lights"
    assert byname["tagged.mp3"]["artist"] == "AURORA" and byname["tagged.mp3"]["identified"]


def test_scan_is_case_insensitive_on_ext(tmp_path):
    _mk(tmp_path, "Track A - Song.MP3")          # Großschreibung
    _mk(tmp_path, "Track B - Song.mp3")
    names = {c["name"] for c in intake.scan_folder(tmp_path)}
    assert names == {"Track A - Song.MP3", "Track B - Song.mp3"}


def test_scan_marks_schema_named(tmp_path):
    _mk(tmp_path, "Alanis - Thank You - [MAVERICK_LC01485].mp3")
    assert intake.scan_folder(tmp_path)[0]["already_named"] is True


def test_scan_extracts_filename_lc_verified_against_master(tmp_path):
    _mk(tmp_path, "Alanis - Thank You - [MAVERICK_LC01485].mp3")
    # label_lookup bestätigt nur 01485 als gültigen GVL-Code
    lookup = lambda code: ("MAVERICK", "R6") if code == "01485" else ("", "")
    card = intake.scan_folder(tmp_path, label_lookup=lookup)[0]
    assert card["filename_lc"] == "01485"
    assert card["filename_label"] == "MAVERICK" and card["filename_rr"] == "R6"


def test_scan_drops_filename_lc_not_in_master(tmp_path):
    _mk(tmp_path, "Foo - Bar - [Fantasie_LC99999].mp3")
    lookup = lambda code: ("", "")   # nichts im Master -> kein Vorschlag (kein Rate)
    card = intake.scan_folder(tmp_path, label_lookup=lookup)[0]
    assert card["filename_lc"] == "" and card["filename_label"] == ""


def test_scan_without_lookup_leaves_filename_lc_empty(tmp_path):
    _mk(tmp_path, "Alanis - Thank You - [MAVERICK_LC01485].mp3")
    card = intake.scan_folder(tmp_path)[0]           # kein label_lookup
    assert card["filename_lc"] == ""


def test_scan_includes_album_from_filename(tmp_path):
    _mk(tmp_path, "FALLING_TWIN PEAKS SOUNDTRACK_ANGELO BADALAMENTI_192.mp3")
    card = intake.scan_folder(tmp_path)[0]
    assert card["album"] == "TWIN PEAKS SOUNDTRACK"


def test_resolve_card_builds_plan_non_destructive(tmp_path):
    p = _mk(tmp_path, "x.mp3", artist="Falsch", title="Alt")
    res = ResolveResult(artist="The Weeknd", title="Blinding Lights", matched=True, mb_score=100,
                        isrcs=["USUG11904251"], album="After Hours",
                        composer="Abel Tesfaye", lyricist="Abel Tesfaye")
    res.candidates = [Candidate(labelcode="07552", label="REPUBLIC", confidence=0.5,
                                dual_source=True, sources=["gvl+musicbrainz", "deezer"])]
    card = intake.resolve_card(
        p, "The Weeknd", "Blinding Lights",
        resolve_fn=lambda a, t, i="": res,
        label_lookup=lambda code: ("REPUBLIC", "R6"),
    )
    assert card["best"]["labelcode"] == "07552" and card["best"]["lc"] == "LC07552"
    assert card["best"]["rechterueckrufe"] == "R6"
    assert card["plan"]["proposed_name"] == "The Weeknd - Blinding Lights - [REPUBLIC_LC07552].mp3"
    assert card["plan"]["proposed_tags"]["isrc"] == "USUG11904251"
    # Album/Komponist/Texter müssen aus dem Resolve-Ergebnis in den Tag-Plan durchgereicht werden
    assert card["plan"]["proposed_tags"]["album"] == "After Hours"
    assert card["plan"]["proposed_tags"]["composer"] == "Abel Tesfaye"
    assert card["plan"]["proposed_tags"]["lyricist"] == "Abel Tesfaye"
    assert any("Rechterückruf" in w for w in card["plan"]["warnings"])
    assert tags.read_tags(p).get("title") == "Alt"          # Datei unverändert


def test_resolve_card_no_match(tmp_path):
    p = _mk(tmp_path, "x.mp3")
    res = ResolveResult(artist="A", title="B", matched=False)
    card = intake.resolve_card(p, "A", "B", resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: ("", ""))
    assert card["best"] is None and card["plan"] is None
    assert card["band"] == "no_match"


# --- AudD-Fingerprinting-Fallback im Tagger ---

def test_resolve_card_uses_fingerprint_when_unidentified(tmp_path):
    p = _mk(tmp_path, "track01.mp3")   # keine Tags, kein Schema-Name
    res = ResolveResult(artist="Aldous Harding", title="One Stop", matched=True, mb_score=100)
    res.candidates = [Candidate(labelcode="05807", label="4 AD", confidence=1.0)]
    recognize = lambda path, ea, et: {"artist": "Aldous Harding", "title": "One Stop",
                                       "possible_cover": False}
    card = intake.resolve_card(p, "", "", resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: ("4 AD", ""), recognize=recognize)
    assert card["fingerprinted"] is True
    assert card["artist"] == "Aldous Harding" and card["best"]["labelcode"] == "05807"
    assert card["fingerprint_source"] == ""          # Erkenner ohne Quellenangabe


def test_resolve_card_passes_fingerprint_source(tmp_path):
    """Die Karte nennt, wer erkannt hat: AcoustID wird vor AudD gefragt, der Text darf nicht immer AudD lauten."""
    p = _mk(tmp_path, "track01.mp3")
    res = ResolveResult(artist="Aldous Harding", title="One Stop", matched=False)
    for src in ("acoustid", "audd"):
        hit = {"artist": "Aldous Harding", "title": "One Stop", "fingerprint_source": src}
        card = intake.resolve_card(p, "", "", resolve_fn=lambda a, t, i="": res, label_lookup=lambda c: ("", ""),
                                   recognize=lambda path, ea, et: hit)
        assert card["fingerprinted"] is True and card["fingerprint_source"] == src
    miss = intake.resolve_card(p, "", "", resolve_fn=lambda a, t, i="": res, label_lookup=lambda c: ("", ""),
                               recognize=lambda path, ea, et: {"artist": "", "title": "", "fingerprint_source": "audd"})
    assert miss["fingerprinted"] is False and miss["fingerprint_source"] == ""


def test_resolve_card_flags_possible_cover(tmp_path):
    p = _mk(tmp_path, "x.mp3", artist="Cover Band")   # nur Artist -> unvollständig
    res = ResolveResult(artist="Original", title="Song", matched=True, mb_score=100)
    res.candidates = [Candidate(labelcode="00121", label="Atlantic", confidence=1.0)]
    recognize = lambda path, ea, et: {"artist": "Original", "title": "Song", "possible_cover": True}
    card = intake.resolve_card(p, "Cover Band", "", resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: ("Atlantic", ""), recognize=recognize)
    assert card["possible_cover"] is True and card["fingerprinted"] is True


def test_resolve_card_fingerprint_fails_returns_unmatched(tmp_path):
    p = _mk(tmp_path, "track01.mp3")
    called = {"resolve": False}
    def rf(a, t):
        called["resolve"] = True
        return ResolveResult(artist=a, title=t, matched=False)
    card = intake.resolve_card(p, "", "", resolve_fn=rf, label_lookup=lambda c: ("", ""),
                               recognize=lambda path, ea, et: None)   # AudD findet nichts
    assert card["matched"] is False and card["best"] is None
    assert called["resolve"] is False   # kein sinnloser Netz-Call ohne Artist/Titel


# --- ISRC-Durchreichung (Stufe 0) ---

def test_resolve_card_passes_isrc_from_tags(tmp_path):
    """Der ISRC steht in den Datei-Tags und war bisher reiner Output — jetzt speist er
    die exakte Stufe 0. Auch ohne dass der Aufrufer ihn mitschickt."""
    p = _mk(tmp_path, "x.mp3", artist="A", title="B", isrc="USMA10100001")
    seen = {}

    def rf(a, t, i=""):
        seen["isrc"] = i
        return ResolveResult(artist=a, title=t, matched=True, via_isrc=True)

    card = intake.resolve_card(p, "A", "B", resolve_fn=rf, label_lookup=lambda c: ("", ""))
    assert seen["isrc"] == "USMA10100001"
    assert card["via_isrc"] is True


def test_resolve_card_explicit_isrc_wins_over_tags(tmp_path):
    p = _mk(tmp_path, "x.mp3", artist="A", title="B", isrc="USMA10100001")
    seen = {}

    def rf(a, t, i=""):
        seen["isrc"] = i
        return ResolveResult(artist=a, title=t, matched=False)

    intake.resolve_card(p, "A", "B", resolve_fn=rf, label_lookup=lambda c: ("", ""),
                        isrc="GBAYE0601498")
    assert seen["isrc"] == "GBAYE0601498"


# --- Katalog-Kürzel + TPUB->Verlag (Library-Dateien, z.B. Cezame CEZ4030_1.mp3) ---

def test_resolve_card_falls_back_to_catalog_candidate_and_verlag_hint(tmp_path):
    p = _mk(tmp_path, "CEZ4030_1.mp3", artist="Pierre-André Athané", title="A Sunny Day",
            album="CEZ4030 - Little French Fancies", label="Frederic Leibovitz Editeur")
    res = ResolveResult(artist="Pierre-André Athané", title="A Sunny Day", matched=False)   # Netz: nichts
    seen = []

    def catalog_fn(text, tpub):
        seen.append((text, tpub))
        if text.startswith("CEZ4030"):
            return Candidate(labelcode="10347", label="CMG", confidence=0.65, dual_source=True,
                             sources=["GVL-Kürzel CEZ", "TPUB = Hersteller"])
        return None
    card = intake.resolve_card(p, "Pierre-André Athané", "A Sunny Day",
                               resolve_fn=lambda a, t, i="": res, label_lookup=lambda c: ("CMG", ""),
                               catalog_fn=catalog_fn,
                               verlag_hint_fn=lambda name: "Frederic Leibovitz Editeur Sarl.")
    assert seen[0] == ("CEZ4030_1", "Frederic Leibovitz Editeur")                # Dateiname zuerst, mit TPUB
    assert card["best"]["labelcode"] == "10347" and card["band"] == "review"      # Vorschlag, kein Auto
    assert card["catalog_hint"] == "GVL-Kürzel CEZ"
    assert any("GVL-Kurzname" in n for n in card["notes"])
    assert card["verlag_hint"] == "Frederic Leibovitz Editeur Sarl." and card["publisher"] == ""
    assert card["plan"]["proposed_name"] == "Pierre-André Athané - A Sunny Day - [CMG_LC10347].mp3"


def test_resolve_card_catalog_not_used_when_net_has_candidate(tmp_path):
    p = _mk(tmp_path, "CEZ4030_1.mp3", artist="A", title="B")
    res = ResolveResult(artist="A", title="B", matched=True)
    res.candidates = [Candidate(labelcode="00121", label="Atlantic", confidence=0.9)]
    calls = []
    card = intake.resolve_card(p, "A", "B", resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: ("Atlantic", ""),
                               catalog_fn=lambda text, tpub: calls.append(text))
    assert calls == [] and card["best"]["labelcode"] == "00121" and card["catalog_hint"] == ""


def test_resolve_card_falls_back_to_file_tags_for_writers_and_album(tmp_path):
    # UPM-Datei: Netz kennt den Titel nicht, aber TCOM/TALB stehen in den Tags (mit [GEMA]-Markern).
    p = _mk(tmp_path, "UPM_BER1376_1_Hot.mp3", artist="Wanja Janeva [GEMA],David Quaas [GEMA]", title="Hot [Vocal Lead]",
            album="BER1376 Sunshine Motion", composer="Wanja Janeva [GEMA],David Quaas [GEMA]",
            label="Ed.Berlin Production Music / Universal Production Music GmbH")
    res = ResolveResult(artist="Wanja Janeva [GEMA],David Quaas [GEMA]", title="Hot [Vocal Lead]", matched=False)
    card = intake.resolve_card(p, res.artist, res.title, resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: ("Berlin Production Music", ""),
                               catalog_fn=lambda text, tpub: Candidate(labelcode="07877", label="Berlin Production Music",
                                                                        confidence=0.75, dual_source=True,
                                                                        sources=["GVL-Kürzel BER", "TPUB = Hersteller", "TPUB = Label"]))
    assert card["composer"] == "Wanja Janeva; David Quaas" and card["album"] == "BER1376 Sunshine Motion"
    assert card["plan"]["proposed_tags"]["composer"] == "Wanja Janeva; David Quaas"
    assert card["best"]["labelcode"] == "07877" and card["band"] == "review"


def test_resolve_card_tie_blocks_apply_and_lists_candidates(tmp_path):
    """Margin-Gate im Tagger: bei Gleichstand darf die Karte den
    Zufallssieger nicht taggbar vorbelegen — can_apply False, Warnung mit beiden Codes, die
    gleichauf liegenden Kandidaten als Dicts (GVL-Name) auf der Karte, der Mensch wählt."""
    p = _mk(tmp_path, "x.mp3")
    res = ResolveResult(artist="Nobody", title="Two Labels", matched=True, mb_score=100)
    res.candidates = [Candidate(labelcode="00121", label="atlantic", confidence=0.5, sources=["gvl+musicbrainz"]),
                      Candidate(labelcode="07552", label="Republic", confidence=0.5, sources=["deezer"]),
                      Candidate(labelcode="99999", label="Weit weg", confidence=0.2, sources=["discogs"])]
    res.ambiguous, res.tied = True, ["00121", "07552"]
    names = {"00121": ("Atlantic", "R6"), "07552": ("REPUBLIC", ""), "99999": ("", "")}
    card = intake.resolve_card(p, "Nobody", "Two Labels", resolve_fn=lambda a, t, i="": res,
                               label_lookup=lambda c: names.get(c, ("", "")))
    assert card["ambiguous"] is True
    assert [c["labelcode"] for c in card["tied"]] == ["00121", "07552"]
    assert [c["label"] for c in card["tied"]] == ["Atlantic", "REPUBLIC"]      # GVL-Schreibweise
    assert card["best"]["labelcode"] == "00121"                                  # zur Anzeige bleibt der Top
    assert card["plan"]["can_apply"] is False
    assert card["plan"]["warnings"][0].startswith("Gleichstand") and "LC07552" in card["plan"]["warnings"][0]
