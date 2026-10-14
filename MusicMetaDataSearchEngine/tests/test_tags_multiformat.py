"""Multi-Format-Tagging (WAV/FLAC/M4A).

FLAC/M4A sind Container-Formate (anders als ID3 keine leeren Dummy-Dateien
möglich) — Tests kopieren minimale Fixtures (tests/fixtures/silence.*,
per ffmpeg einmalig als stille 0.2s-Clips erzeugt). WAV wird live per
Python-Stdlib `wave` gebaut (kein externes Fixture nötig).
"""
import shutil
import wave
from pathlib import Path

import pytest

from core import tags

FIXTURES = Path(__file__).parent / "fixtures"


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)  # 0.1s Stille


@pytest.fixture
def wav_file(tmp_path):
    p = tmp_path / "x.wav"
    _make_wav(p)
    return p


@pytest.fixture
def flac_file(tmp_path):
    p = tmp_path / "x.flac"
    shutil.copy(FIXTURES / "silence.flac", p)
    return p


@pytest.fixture
def m4a_file(tmp_path):
    p = tmp_path / "x.m4a"
    shutil.copy(FIXTURES / "silence.m4a", p)
    return p


FULL_TAGS = {"artist": "Alanis Morissette", "title": "Thank You", "album": "Supposed Former Infatuation Junkie",
            "label": "MAVERICK", "lc": "LC01485", "isrc": "USMA10100001",
            "composer": "Alanis Morissette", "lyricist": "Glen Ballard"}


# --- WAV: exakt dieselben ID3-Frames wie MP3 (mutagen.wave.WAVE.tags ist ID3) ---

def test_wav_read_empty(wav_file):
    assert tags.read_tags(wav_file) == {}


def test_wav_roundtrip_all_fields(wav_file):
    tags.write_tags(wav_file, FULL_TAGS)
    r = tags.read_tags(wav_file)
    assert r["artist"] == "Alanis Morissette"
    assert r["label"] == "MAVERICK" and r["lc"] == "LC01485"
    assert r["composer"] == "Alanis Morissette" and r["lyricist"] == "Glen Ballard"
    assert r["isrc"] == "USMA10100001"


def test_wav_restore(wav_file):
    tags.write_tags(wav_file, {"artist": "Wrong"})
    tags.write_tags(wav_file, FULL_TAGS)
    tags.restore_tags(wav_file, {"artist": "Wrong"})
    r = tags.read_tags(wav_file)
    assert r.get("artist") == "Wrong"
    assert "lc" not in r and "label" not in r  # nicht im alten Stand -> weg


# --- FLAC: Vorbis-Kommentare, freiform ---

def test_flac_read_empty(flac_file):
    assert tags.read_tags(flac_file) == {}


def test_flac_roundtrip_all_fields(flac_file):
    tags.write_tags(flac_file, FULL_TAGS)
    r = tags.read_tags(flac_file)
    assert r == FULL_TAGS


def test_flac_only_given_fields_set(flac_file):
    tags.write_tags(flac_file, {"artist": "Only Artist"})
    r = tags.read_tags(flac_file)
    assert r == {"artist": "Only Artist"}


def test_flac_restore(flac_file):
    tags.write_tags(flac_file, {"artist": "Wrong", "label": "Old Label"})
    tags.write_tags(flac_file, FULL_TAGS)
    tags.restore_tags(flac_file, {"artist": "Wrong", "label": "Old Label"})
    r = tags.read_tags(flac_file)
    assert r == {"artist": "Wrong", "label": "Old Label"}


# --- MP4/M4A: feste Atome (artist/title/album/composer) + Freeform (label/lc/isrc) ---

def test_mp4_read_empty(m4a_file):
    assert tags.read_tags(m4a_file) == {}


def test_mp4_roundtrip_supported_fields(m4a_file):
    tags.write_tags(m4a_file, FULL_TAGS)
    r = tags.read_tags(m4a_file)
    assert r["artist"] == "Alanis Morissette" and r["title"] == "Thank You"
    assert r["composer"] == "Alanis Morissette"
    assert r["label"] == "MAVERICK" and r["lc"] == "LC01485" and r["isrc"] == "USMA10100001"


def test_mp4_lyricist_roundtrip(m4a_file):
    # Texter über das Freeform-Atom '----:com.apple.iTunes:LYRICIST' (Autor-PERSON).
    tags.write_tags(m4a_file, FULL_TAGS)
    assert tags.read_tags(m4a_file)["lyricist"] == "Glen Ballard"


def test_mp4_lyricist_not_written_to_lyrics_atom(m4a_file):
    # '\xa9lyr' ist der SONGTEXT — der Texter darf dort nicht landen.
    from mutagen.mp4 import MP4
    tags.write_tags(m4a_file, FULL_TAGS)
    audio = MP4(str(m4a_file))
    assert "\xa9lyr" not in audio
    assert bytes(audio["----:com.apple.iTunes:LYRICIST"][0]).decode() == "Glen Ballard"


def test_mp4_lyricist_undo(m4a_file):
    # Undo muss den Texter genauso zurücknehmen wie label/lc/isrc.
    tags.write_tags(m4a_file, {"artist": "Wrong", "lyricist": "Alter Texter"})
    tags.write_tags(m4a_file, FULL_TAGS)
    tags.restore_tags(m4a_file, {"artist": "Wrong", "lyricist": "Alter Texter"})
    r = tags.read_tags(m4a_file)
    assert r["lyricist"] == "Alter Texter" and r["artist"] == "Wrong"
    assert "lc" not in r and "label" not in r


def test_mp4_restore(m4a_file):
    tags.write_tags(m4a_file, {"artist": "Wrong", "label": "Old Label"})
    tags.write_tags(m4a_file, FULL_TAGS)
    tags.restore_tags(m4a_file, {"artist": "Wrong", "label": "Old Label"})
    r = tags.read_tags(m4a_file)
    assert r["artist"] == "Wrong" and r["label"] == "Old Label"
    assert "lc" not in r and "composer" not in r


# --- AIFF: ID3-Chunk wie WAV (mutagen.aiff.AIFF) ---

def _make_aiff(path: Path) -> None:
    """Minimale AIFF-Datei von Hand (Python 3.13+ hat kein aifc mehr): FORM/AIFF + COMM + SSND."""
    import struct
    frames = 800
    ssnd_data = b"\x00\x00" * frames
    comm = b"COMM" + struct.pack(">I", 18) + struct.pack(">hIh", 1, frames, 16) + bytes.fromhex("400BFA00000000000000")  # 8000 Hz
    ssnd = b"SSND" + struct.pack(">I", 8 + len(ssnd_data)) + struct.pack(">II", 0, 0) + ssnd_data
    body = b"AIFF" + comm + ssnd
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)


@pytest.fixture
def aiff_file(tmp_path):
    p = tmp_path / "x.aiff"
    _make_aiff(p)
    return p


def test_aiff_supported_extensions():
    assert {".aif", ".aiff", ".aifc"} <= tags.SUPPORTED_EXTENSIONS


def test_aiff_read_empty(aiff_file):
    assert tags.read_tags(aiff_file) == {}


def test_aiff_roundtrip_all_fields(aiff_file):
    full = {"artist": "A", "title": "T", "album": "AL", "label": "L", "composer": "C",
            "lyricist": "LY", "lc": "00121", "isrc": "USX1"}
    tags.write_tags(aiff_file, full)
    assert tags.read_tags(aiff_file) == full
    tags.restore_tags(aiff_file, {})
    assert tags.read_tags(aiff_file) == {}
