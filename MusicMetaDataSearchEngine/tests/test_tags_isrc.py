"""ISRC im ID3: Standard-Frame TSRC zuerst, TXXX:ISRC als Rückfall. Testwerte frei erfunden."""
from mutagen.id3 import ID3, TSRC, TXXX

from core import tags


def _mp3(tmp_path, frames=(), name="x.mp3"):
    p = tmp_path / name
    p.write_bytes(b"")
    id3 = ID3()
    for f in frames:
        id3.add(f)
    id3.save(str(p), v2_version=4)
    return p


def test_reads_tsrc(tmp_path):
    p = _mp3(tmp_path, [TSRC(encoding=3, text="DEZZQ2600001")])
    assert tags.read_tags(p)["isrc"] == "DEZZQ2600001"


def test_reads_txxx_isrc_as_fallback(tmp_path):
    p = _mp3(tmp_path, [TXXX(encoding=3, desc="ISRC", text="DEZZQ2600002")])
    assert tags.read_tags(p)["isrc"] == "DEZZQ2600002"


def test_tsrc_wins_over_txxx(tmp_path):
    p = _mp3(tmp_path, [TXXX(encoding=3, desc="ISRC", text="DEZZQ2600003"), TSRC(encoding=3, text="DEZZQ2600004")])
    assert tags.read_tags(p)["isrc"] == "DEZZQ2600004"


def test_writes_tsrc_and_keeps_existing_txxx_in_step(tmp_path):
    p = _mp3(tmp_path)
    tags.write_tags(p, {"isrc": "DEZZQ2600005"})
    id3 = ID3(str(p))
    assert str(id3["TSRC"]) == "DEZZQ2600005" and not id3.getall("TXXX:ISRC")

    q = _mp3(tmp_path, [TXXX(encoding=3, desc="ISRC", text="ALT")], name="y.mp3")
    tags.write_tags(q, {"isrc": "DEZZQ2600006"})
    id3 = ID3(str(q))
    assert str(id3["TSRC"]) == "DEZZQ2600006" and str(id3["TXXX:ISRC"]) == "DEZZQ2600006"


def test_restore_removes_written_tsrc(tmp_path):
    p = _mp3(tmp_path)
    tags.write_tags(p, {"artist": "Zzq", "isrc": "DEZZQ2600007"})
    tags.restore_tags(p, {"artist": "Zzq"})
    assert tags.read_tags(p) == {"artist": "Zzq"}
