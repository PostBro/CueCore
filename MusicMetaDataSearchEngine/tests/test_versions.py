"""Versionserkennung im Titel (C3). Testtitel frei erfunden, bis auf die Formen aus dem Eval-Korpus."""
from core.musicbrainz import MBLookup
from core.resolver import resolve
from core.versions import detect_version
from tests.test_resolver import IDX


def test_detects_versions_only_in_the_suffix():
    assert detect_version("New Love Cassette (Mark Ronson Remix)") == "Remix (Mark Ronson)"
    assert detect_version("Nordwand (Remix)") == "Remix"
    assert detect_version("Nordwand - Live") == "Live"
    assert detect_version("Chasing Highs (Acoustic Piano Version)") == "Acoustic"
    assert detect_version("In the Morning Light (Radio Mix)") == "Radio Edit"
    assert detect_version("Nordwand (Live) [Remastered 2011]") == "Remaster"
    assert detect_version("Nordwand (Instrumental)") == "Instrumental"
    for plain in ("Live Forever", "Remix Culture", "Nordwand", "Nordwand (feat. Erika)", ""):
        assert detect_version(plain) == "", plain


def _mb(title, code="Atlantic"):
    return lambda a, t: MBLookup(matched=True, score=100, title=title, label_names=[code], label_quality={code: 5})


def test_resolver_notes_version_and_flags_original_instead_of_version():
    dz = lambda a, t: None   # noqa: E731
    hit = resolve("Zzq", "Nordwand (Zzq Remix)", IDX, mb_lookup=_mb("Nordwand (Zzq remix)"),
                  discogs_label_codes=lambda rid: [], deezer_lookup=dz)
    assert hit.version == "Remix (Zzq)" and not hit.version_mismatch
    assert any(n.startswith("Version erkannt: Remix (Zzq). Label und Urheber") for n in hit.notes)
    orig = resolve("Zzq", "Nordwand (Zzq Remix)", IDX, mb_lookup=_mb("Nordwand"),
                   discogs_label_codes=lambda rid: [], deezer_lookup=dz)
    assert orig.version_mismatch and any("nur die Originalfassung" in n for n in orig.notes)
    plain = resolve("Zzq", "Nordwand", IDX, mb_lookup=_mb("Nordwand"), discogs_label_codes=lambda rid: [])
    assert plain.version == "" and not plain.version_mismatch


def test_version_mismatch_is_never_auto():
    from core.resolver import Candidate, ResolveResult
    r = ResolveResult(artist="A", title="B", candidates=[Candidate(labelcode="00121", label="X", confidence=0.9)])
    assert r.band == "auto"
    r.version_mismatch = True
    assert r.band == "review"
