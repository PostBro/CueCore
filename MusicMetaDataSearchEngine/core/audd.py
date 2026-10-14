"""AudD-Fingerprinting (Token nötig, KOSTENPFLICHTIG): Audiodatei -> Identifikation.

Erkennt Tracks ohne Tags/Namen. Ohne Token -> None (übersprungen).
Liefert Artist/Title/Album/Label/ISRC/Streaming-Links. Multipart-POST der Datei via httpx.

Zwei Realitäten aus der offiziellen Doku (docs.audd.io), die die Integration bestimmen:
- **Dateigröße-Limit 10 MB / 25 s** (Fehler #400 darüber). Vollständige Musikdateien sind größer
  (voller Track + Cover-Art). Darum wird vor dem Upload per ffmpeg ein kurzer Ausschnitt
  extrahiert (Doku empfiehlt ~20 s). Ohne ffmpeg -> Fallback auf die Originaldatei.
- **ISRC steht NICHT auf Top-Level** der Response, nur in result.apple_music.isrc bzw.
  result.spotify.external_ids.isrc. Streaming-Links: result.apple_music.url /
  result.spotify.external_urls.spotify.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

AUDD_URL = "https://api.audd.io/"

# ffmpeg-Ausschnitt: mono, niedrige Bitrate genügt fürs Fingerprinting.
SNIPPET_SECONDS = 18            # < 25 s Limit, mit Sicherheitsabstand
SNIPPET_START_DEFAULT = 20      # überspringt oft Intro/Stille, wenn der Track lang genug ist


def _duration_seconds(path: str | Path) -> float | None:
    """Track-Länge via mutagen, oder None wenn nicht lesbar."""
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(str(path))
        return float(mf.info.length) if mf is not None and mf.info else None
    except Exception:
        return None


def extract_snippet(src: str | Path, *, seconds: int = SNIPPET_SECONDS, ffmpeg: str = "ffmpeg",
                    duration_fn=_duration_seconds) -> str | None:
    """Extrahiert einen kurzen Audio-Ausschnitt (AudD-Limit 10 MB/25 s umgehen).

    Gibt den Pfad einer temporären .mp3 zurück, oder None wenn ffmpeg fehlt/scheitert.
    Der Aufrufer ist fürs Aufräumen der temporären Datei zuständig.
    """
    dur = duration_fn(src)
    start = SNIPPET_START_DEFAULT if (dur and dur > SNIPPET_START_DEFAULT + seconds) else 0
    fd, out = tempfile.mkstemp(suffix=".mp3", prefix="audd_")
    os.close(fd)
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW   # sonst blitzt aus dem fensterlosen Build eine Konsole auf
    try:
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(start), "-t", str(seconds), "-i", str(src),
             "-map", "0:a", "-ac", "1", "-ar", "22050", "-b:a", "96k", out],
            check=True, capture_output=True, timeout=60, **kw)
    except Exception:
        try:
            os.remove(out)
        except OSError:
            pass
        return None
    return out


def _default_post(data: dict, file_path: str) -> dict | None:
    try:
        with open(file_path, "rb") as fh:
            r = httpx.post(AUDD_URL, data=data, files={"file": fh}, timeout=40)
        return r.json()
    except Exception:
        return None


def _extract_isrc(res: dict) -> str:
    """ISRC aus der AudD-Response — Top-Level ist laut Doku nicht garantiert, darum
    Fallback auf die Sub-Objekte apple_music/spotify."""
    if res.get("isrc"):
        return res["isrc"]
    am = res.get("apple_music") or {}
    if am.get("isrc"):
        return am["isrc"]
    ext = (res.get("spotify") or {}).get("external_ids") or {}
    return ext.get("isrc", "") or ""


def parse_result(payload: dict) -> dict | None:
    """AudD-Response -> flaches Metadaten-dict, oder None bei keinem Treffer."""
    if not payload or payload.get("status") != "success":
        return None
    res = payload.get("result")
    if not res:
        return None
    apple_music = res.get("apple_music") or {}
    spotify_urls = (res.get("spotify") or {}).get("external_urls") or {}
    return {
        "artist": res.get("artist", "") or "",
        "title": res.get("title", "") or "",
        "album": res.get("album", "") or "",
        "label": res.get("label", "") or "",
        "isrc": _extract_isrc(res),
        "date": res.get("release_date", "") or "",
        "apple_music_url": apple_music.get("url", "") or "",
        "spotify_url": spotify_urls.get("spotify", "") or "",
    }


def is_possible_cover(expected_artist: str, expected_title: str, result_artist: str, result_title: str) -> bool:
    """Der Fingerprint hat sicher getroffen (sonst kein Ergebnis), aber
    ein bereits bekanntes Teilfragment (z.B. Artist aus unvollständigen ID3-Tags, Titel fehlt)
    weicht vom Treffer ab -> vermutlich eine Cover-Version. Vergleicht nur die Felder, die
    tatsächlich schon bekannt waren (recognize() wird ja gerade deshalb aufgerufen, weil
    artist UND title nie beide vollständig vorlagen) — beide leer heisst nichts zu vergleichen."""
    ea, et = expected_artist.strip(), expected_title.strip()
    if not (ea or et):
        return False
    if ea and ea.casefold() != result_artist.strip().casefold():
        return True
    if et and et.casefold() != result_title.strip().casefold():
        return True
    return False


def recognize(file_path: str | Path, token: str, *, poster=_default_post,
              expected_artist: str = "", expected_title: str = "", make_snippet=extract_snippet) -> dict | None:
    """Erkennt die Datei per Fingerprint. None ohne Token oder ohne Treffer.

    Vor dem Upload wird ein kurzer Ausschnitt extrahiert (AudD-Größenlimit). Scheitert das
    (kein ffmpeg), wird die Originaldatei gesendet — bei großen Tracks liefert AudD dann #400
    (kein Treffer). expected_artist/expected_title speisen den Cover-Check (possible_cover)."""
    if not token:
        return None
    snippet = make_snippet(file_path)
    upload_path = snippet or str(file_path)
    try:
        data = {"api_token": token, "return": "apple_music,spotify,musicbrainz"}
        payload = poster(data, upload_path)
    finally:
        if snippet:
            try:
                os.remove(snippet)
            except OSError:
                pass
    result = parse_result(payload) if payload else None
    if result is not None:
        result["possible_cover"] = is_possible_cover(
            expected_artist, expected_title, result["artist"], result["title"])
    return result
