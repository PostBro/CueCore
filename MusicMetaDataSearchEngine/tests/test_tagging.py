from pathlib import Path

import pytest

from core import naming, operations, tags
from core.tagging import apply, plan


def _mk(tmp_path, name="x.mp3", **t):
    p = tmp_path / name
    p.write_bytes(b"")
    if t:
        tags.write_tags(p, t)
    return p


# --- naming ---

def test_build_filename_scheme():
    assert naming.build_filename("Alanis Morissette", "Thank You", "MAVERICK", "01485") \
        == "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3"


def test_build_filename_pads_and_keeps_6digit():
    assert "[X_LC00148]" in naming.build_filename("A", "B", "X", "148")      # 3 -> 5-stellig
    assert "[X_LC100148]" in naming.build_filename("A", "B", "X", "100148")  # 6-stellig unverändert


def test_sanitize_unsafe_chars():
    assert naming.build_filename("AC/DC", "Hells: Bells", "X", "00121").startswith("AC DC - Hells Bells")


# --- tags read/write ---

def test_tags_roundtrip(tmp_path):
    p = _mk(tmp_path)
    tags.write_tags(p, {"artist": "Alanis Morissette", "title": "Thank You",
                        "label": "MAVERICK", "lc": "LC01485", "isrc": "USMA10100001"})
    r = tags.read_tags(p)
    assert r["artist"] == "Alanis Morissette"
    assert r["lc"] == "LC01485"
    assert r["isrc"] == "USMA10100001"


def test_read_tags_no_header(tmp_path):
    p = tmp_path / "empty.mp3"
    p.write_bytes(b"")
    assert tags.read_tags(p) == {}


# --- plan (non-destructive) ---

def test_plan_is_non_destructive(tmp_path):
    p = _mk(tmp_path, artist="Wrong", title="Old")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK", rechterueckrufe="R6")
    assert pl.labelcode == "01485" and pl.lc == "LC01485"
    assert pl.proposed_name == "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3"
    assert pl.current_tags.get("artist") == "Wrong"      # nur gelesen
    assert any("Rechterückruf" in w for w in pl.warnings)
    assert tags.read_tags(p).get("title") == "Old"        # Datei unverändert


def test_plan_without_lc_blocks(tmp_path):
    p = _mk(tmp_path)
    pl = plan(p, "A", "B", "", "Some Label")
    assert pl.can_apply is False and pl.proposed_name == ""


# --- apply gate + round-trip + undo ---

def test_apply_requires_confirm(tmp_path):
    p = _mk(tmp_path)
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "A", "B", "00121", "Atlantic")
    with pytest.raises(PermissionError):
        apply(pl, op_con=op, confirm=False)
    assert p.exists()       # nichts passiert


def test_apply_without_lc_raises(tmp_path):
    p = _mk(tmp_path)
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "A", "B", "", "Some Label")
    with pytest.raises(ValueError):
        apply(pl, op_con=op, confirm=True)


def test_apply_blocks_on_collision(tmp_path):
    # Zielname existiert schon (anderer Track) -> kein stilles Überschreiben, Quelle bleibt
    op = operations.open_oplog(tmp_path / "ops.db")
    existing = tmp_path / "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3"
    existing.write_bytes(b"vorhanden")
    p = _mk(tmp_path, "neu.mp3", artist="X", title="Y")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK")
    with pytest.raises(FileExistsError):
        apply(pl, op_con=op, confirm=True)
    assert p.exists()                                   # Quelle unangetastet
    assert existing.read_bytes() == b"vorhanden"        # Ziel unverändert


def test_apply_and_undo_roundtrip(tmp_path):
    p = _mk(tmp_path, artist="Wrong Artist", title="Old Title", label="Old Label")
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK", isrc="USMA10100001")

    res = apply(pl, op_con=op, confirm=True)
    new = Path(res["new_path"])
    assert new.name == "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3"
    assert not p.exists() and new.exists()
    r = tags.read_tags(new)
    assert r["artist"] == "Alanis Morissette" and r["lc"] == "LC01485" and r["isrc"] == "USMA10100001"

    assert operations.undo(op, res["op_ids"]) == 2
    assert p.exists() and not new.exists()                # Umbenennung zurück
    back = tags.read_tags(p)
    assert back.get("artist") == "Wrong Artist"            # alter Stand
    assert "lc" not in back and "isrc" not in back         # neu geschriebene Frames wieder weg


# --- Cross-Drive (Zielordner liegt auf einem anderen Laufwerk) ---

def _raise_exdev(*a, **k):
    import errno
    raise OSError(errno.EXDEV, "Cross-device link")


def test_apply_moves_across_devices(tmp_path, monkeypatch):
    # os.rename wirft EXDEV (Laufwerkswechsel) -> shutil.move muss auf Kopieren+Löschen zurückfallen
    import os
    p = _mk(tmp_path, "src.mp3", artist="X", title="Y")
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK", isrc="USMA10100001")
    monkeypatch.setattr(os, "rename", _raise_exdev)
    res = apply(pl, op_con=op, confirm=True, move_to=tmp_path / "verified")
    new = Path(res["new_path"])
    assert new.name == "Alanis Morissette - Thank You - [MAVERICK_LC01485].mp3"
    assert new.exists() and not p.exists()
    assert tags.read_tags(new)["lc"] == "LC01485"


def test_undo_moves_back_across_devices(tmp_path, monkeypatch):
    import os
    p = _mk(tmp_path, "src.mp3", artist="Old Artist", title="Old Title")
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK")
    monkeypatch.setattr(os, "rename", _raise_exdev)          # bleibt für apply UND undo aktiv
    res = apply(pl, op_con=op, confirm=True, move_to=tmp_path / "verified")
    new = Path(res["new_path"])
    assert new.exists() and not p.exists()
    assert operations.undo(op, res["op_ids"]) == 2
    assert p.exists() and not new.exists()                   # über Laufwerksgrenze zurück
    assert tags.read_tags(p).get("artist") == "Old Artist"   # alter Tag-Stand restauriert


def test_plan_includes_album_composer_lyricist(tmp_path):
    p = _mk(tmp_path)
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK",
              album="Supposed Former Infatuation Junkie", composer="Alanis Morissette",
              lyricist="Alanis Morissette")
    assert pl.proposed_tags["album"] == "Supposed Former Infatuation Junkie"
    assert pl.proposed_tags["composer"] == "Alanis Morissette"
    assert pl.proposed_tags["lyricist"] == "Alanis Morissette"


def test_apply_writes_album_composer_lyricist(tmp_path):
    p = _mk(tmp_path)
    op = operations.open_oplog(tmp_path / "ops.db")
    pl = plan(p, "Alanis Morissette", "Thank You", "1485", "MAVERICK",
              album="Supposed Former Infatuation Junkie", composer="Glen Ballard; Alanis Morissette",
              lyricist="Alanis Morissette")
    res = apply(pl, op_con=op, confirm=True)
    r = tags.read_tags(Path(res["new_path"]))
    assert r["album"] == "Supposed Former Infatuation Junkie"
    assert r["composer"] == "Glen Ballard; Alanis Morissette"
    assert r["lyricist"] == "Alanis Morissette"


def test_plan_without_label_name_proposes_no_target_name(tmp_path):
    """LC außerhalb des Masters (kein Labelname) ergab den Zielnamen 'A - B - [_LC99999].mp3'.
    Ohne Labelname kein Zielname und kein Apply — sonst landete der leere Name als Ordner-Ziel."""
    p = _mk(tmp_path)
    pl = plan(p, "A", "B", "99999", "")
    assert pl.proposed_name == "" and pl.can_apply is False and pl.lc == "LC99999"
    op = operations.open_oplog(tmp_path / "ops.db")
    with pytest.raises(ValueError):
        apply(pl, op_con=op, confirm=True)
    assert p.exists()
