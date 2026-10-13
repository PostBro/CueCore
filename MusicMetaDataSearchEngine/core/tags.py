"""Multi-Format-Reader/Writer (mutagen).

EINE Schnittstelle für alle Aufrufer (tagging.py, intake.py, watch.py, app.py):
read_tags(path)->dict, write_tags(path, tags)->None, restore_tags(path, old)->None.
Dispatch nach Dateiendung passiert intern — nichts außerhalb dieses Moduls
muss wissen, welches Format gerade bearbeitet wird.

Mapping-Realität (aus dem mutagen-Quellcode verifiziert, nicht geraten):
- MP3: ID3v2.4 direkt (TPE1/TIT2/TALB/TPUB/TCOM/TEXT/TXXX:LC/TXXX:ISRC).
- WAV und AIFF: tragen eine echte ID3-Chunk (`mutagen.wave.WAVE(...).tags` ist eine
  `mutagen.id3.ID3`-Instanz) — exakt dieselben Frames wie MP3.
- FLAC: Vorbis-Kommentare, laut mutagen komplett freiform (keine feste
  Feldliste) — eigene Klein-Buchstaben-Keys direkt nutzbar.
- MP4/M4A: feste Apple-Atome nur für artist/title/album/composer ('\\xa9wrt').
  KEIN offizielles Atom für Labelname/LC/ISRC/Lyricist → über Freeform-Atome
  ('----:com.apple.iTunes:LABEL' etc., dieselbe Mechanik wie MusicBrainz-IDs
  in mutagens eigener Doku). Der Texter läuft ebenfalls über ein Freeform-Atom
  ('----:com.apple.iTunes:LYRICIST') — das ist die etablierte Konvention
  bei Tag-Editoren und die einzige Stelle für die Autor-PERSON; '\\xa9lyr'
  ist der Songtext selbst und wird bewusst NICHT benutzt. Freeform-Atome sind
  nicht standardisiert (iTunes zeigt sie nicht an), aber roundtrip-fest.

read_tags ist immer ungefährlich. write_tags schreibt — wird nur über
tagging.apply() mit confirm=True aufgerufen, nie blind.
"""
from __future__ import annotations
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TALB, TCOM, TEXT, TPE1, TPUB, TXXX
from mutagen.id3 import ID3NoHeaderError
from mutagen.wave import WAVE
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4FreeForm, AtomDataType

# internes Feld -> ID3-Frame (einfache Textframes) — MP3 + WAV
_SIMPLE = {"artist": TPE1, "title": TIT2, "album": TALB, "label": TPUB,
           "composer": TCOM, "lyricist": TEXT}
_TXXX = {"lc": "LC", "isrc": "ISRC"}

# Vorbis (FLAC): komplett freiform, unsere Feldnamen direkt als Key.
_VORBIS_FIELDS = ("artist", "title", "album", "label", "composer", "lyricist", "lc", "isrc")

# MP4/M4A: feste Atome nur für diese vier Felder.
_MP4_TEXT = {"artist": "\xa9ART", "title": "\xa9nam", "album": "\xa9alb", "composer": "\xa9wrt"}
# Rest über Freeform-Atome (kein Standard-Atom vorhanden). lyricist = Autor-Person
# (NICHT '\xa9lyr', das ist der Songtext) — Lese-/Schreib-/Undo-Pfad iterieren hierüber.
_MP4_FREEFORM = {"label": "LABEL", "lc": "LC", "isrc": "ISRC", "lyricist": "LYRICIST"}
_MP4_FREEFORM_PREFIX = "----:com.apple.iTunes:"

# Verifiziert (mutagen-Quellcode, s. Moduldoc oben) — für Intake/Scan-Filter.
SUPPORTED_EXTENSIONS = frozenset({".mp3", ".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac", ".m4a"})
_AIFF_EXT = (".aif", ".aiff", ".aifc")   # mutagen.aiff.AIFF: ID3-Chunk wie WAV, gleiche Frames


def _format(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".flac":
        return "vorbis"
    if ext in (".m4a", ".mp4", ".m4b"):
        return "mp4"
    return "id3"  # .mp3, .wav/.wave, unbekannte Endungen (bisheriges Verhalten)


# --- ID3-Familie (MP3 direkt, WAV über die WAVE-Hülle — gleiche Frames) ---

def _id3_for_read(path: Path) -> ID3 | None:
    try:
        if path.suffix.lower() in (".wav", ".wave"):
            return WAVE(str(path)).tags  # kann None sein (kein id3-Chunk)
        if path.suffix.lower() in _AIFF_EXT:
            return AIFF(str(path)).tags
        return ID3(str(path))
    except Exception:
        return None


def _id3_for_write(path: Path) -> ID3:
    if path.suffix.lower() in (".wav", ".wave") or path.suffix.lower() in _AIFF_EXT:
        w = WAVE(str(path)) if path.suffix.lower() in (".wav", ".wave") else AIFF(str(path))
        if w.tags is None:
            w.add_tags()
        return w.tags
    try:
        return ID3(str(path))
    except ID3NoHeaderError:
        return ID3()


def _extract_id3(id3: ID3) -> dict:
    out: dict[str, str] = {}
    for field, frame in _SIMPLE.items():
        key = frame.__name__
        if key in id3:
            out[field] = str(id3[key])
    for f in id3.getall("TXXX"):
        for field, desc in _TXXX.items():
            if f.desc == desc:
                out[field] = str(f)
    return out


def _apply_id3_fields(id3: ID3, tags: dict) -> None:
    for field, frame in _SIMPLE.items():
        if tags.get(field):
            id3.setall(frame.__name__, [frame(encoding=3, text=tags[field])])
    for field, desc in _TXXX.items():
        if tags.get(field):
            id3.delall(f"TXXX:{desc}")
            id3.add(TXXX(encoding=3, desc=desc, text=tags[field]))


# --- Vorbis (FLAC) ---

def _vorbis_read(path: Path) -> dict:
    try:
        audio = FLAC(str(path))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for field in _VORBIS_FIELDS:
        vals = audio.get(field)
        if vals:
            out[field] = vals[0]
    return out


def _vorbis_apply(audio: FLAC, tags: dict) -> None:
    for field in _VORBIS_FIELDS:
        if tags.get(field):
            audio[field] = [tags[field]]


# --- MP4/M4A ---

def _mp4_read(path: Path) -> dict:
    try:
        audio = MP4(str(path))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for field, atom in _MP4_TEXT.items():
        vals = audio.get(atom)
        if vals:
            out[field] = str(vals[0])
    for field, name in _MP4_FREEFORM.items():
        vals = audio.get(_MP4_FREEFORM_PREFIX + name)
        if vals:
            out[field] = bytes(vals[0]).decode("utf-8", errors="replace")
    return out


def _mp4_apply(audio: MP4, tags: dict) -> None:
    for field, atom in _MP4_TEXT.items():
        if tags.get(field):
            audio[atom] = [tags[field]]
    for field, name in _MP4_FREEFORM.items():
        if tags.get(field):
            audio[_MP4_FREEFORM_PREFIX + name] = [
                MP4FreeForm(tags[field].encode("utf-8"), dataformat=AtomDataType.UTF8)
            ]


# --- Öffentliche Schnittstelle (formatunabhängig) ---

def read_tags(path: str | Path) -> dict:
    """Aktueller Tag-Stand als flaches dict. Datei ohne Tags -> leeres dict."""
    path = Path(path)
    fmt = _format(path)
    if fmt == "vorbis":
        return _vorbis_read(path)
    if fmt == "mp4":
        return _mp4_read(path)
    id3 = _id3_for_read(path)
    return _extract_id3(id3) if id3 is not None else {}


def write_tags(path: str | Path, tags: dict, *, v24: bool = True) -> None:
    """Schreibt die gegebenen Felder. Nur übergebene Felder werden gesetzt."""
    path = Path(path)
    fmt = _format(path)
    if fmt == "vorbis":
        audio = FLAC(str(path))
        _vorbis_apply(audio, tags)
        audio.save()
        return
    if fmt == "mp4":
        audio = MP4(str(path))
        _mp4_apply(audio, tags)
        audio.save()
        return
    id3 = _id3_for_write(path)
    _apply_id3_fields(id3, tags)
    id3.save(str(path), v2_version=4 if v24 else 3)


def restore_tags(path: str | Path, old_tags: dict) -> None:
    """Alle verwalteten Felder entfernen, dann alten Stand setzen (für Undo).

    Anders als write_tags ist das ein vollständiger Reset der verwalteten Felder —
    sonst blieben beim Rückgängigmachen neu geschriebene LC/ISRC stehen.
    """
    path = Path(path)
    fmt = _format(path)
    if fmt == "vorbis":
        audio = FLAC(str(path))
        for field in _VORBIS_FIELDS:
            if field in audio:
                del audio[field]
        _vorbis_apply(audio, old_tags)
        audio.save()
        return
    if fmt == "mp4":
        audio = MP4(str(path))
        for atom in _MP4_TEXT.values():
            audio.pop(atom, None)
        for name in _MP4_FREEFORM.values():
            audio.pop(_MP4_FREEFORM_PREFIX + name, None)
        _mp4_apply(audio, old_tags)
        audio.save()
        return
    id3 = _id3_for_write(path)
    for frame in _SIMPLE.values():
        id3.delall(frame.__name__)
    for desc in _TXXX.values():
        id3.delall(f"TXXX:{desc}")
    _apply_id3_fields(id3, old_tags)
    id3.save(str(path), v2_version=4)
