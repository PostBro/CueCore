"""AcoustID-Fingerprinting: Audiodatei -> Chromaprint-Fingerabdruck -> Treffer bei AcoustID.

Warum zusätzlich zu AudD: der Fingerabdruck erkennt eine Datei am
Audio statt am Dateinamen, im Bundle liegt dafür `fpcalc`. AudD kostet pro Anfrage, AcoustID ist für
nicht-kommerzielle Nutzung frei.

Zwei Teile:
- **fpcalc** (Chromaprint) rechnet lokal den Fingerabdruck. Das Binary wird mitgeliefert; gefunden
  wird es über CUECORE_FPCALC, das App-Bundle oder den PATH. Ohne fpcalc -> None (übersprungen).
- **Lookup** gegen https://api.acoustid.org/v2/lookup (offizielle Doku acoustid.org/webservice):
  Pflichtfelder `client` (Application-Key), `duration` (ganze Sekunden) und `fingerprint`; `meta`
  steuert, was zurückkommt, Mehrfachwerte in der URL mit `+` (im POST-Formular also Leerzeichen, siehe META).
  POST ist für lange Fingerabdrücke vorgesehen.
  Höchstens 3 Anfragen pro Sekunde.

**Lizenz:** laut Doku ist der Dienst nur für nicht-kommerzielle Nutzung frei; kommerzielle
Anwendungen brauchen eine eigene Anmeldung. Steht als Hinweis am Schlüsselfeld, wie bei Genius.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from .audd import is_possible_cover
from .netfetch import RateLimiter

LOOKUP_URL = "https://api.acoustid.org/v2/lookup"
HOST = "api.acoustid.org"
MIN_INTERVAL = 0.34                 # Doku: höchstens 3 Anfragen pro Sekunde
# Interpret/Titel/Album + ISRC. Mit Leerzeichen getrennt: die Doku zeigt „+" in der GET-URL, wo es für ein
# Leerzeichen steht; im POST-Formular käme ein „+" wörtlich an und AcoustID lieferte keine Aufnahmen.
META = "recordings releases isrcs"
_LIMITER = RateLimiter()


def fpcalc_path() -> str | None:
    """Pfad zu fpcalc: ausdrücklich gesetzt, mitgeliefert im Bundle, sonst aus dem PATH."""
    env = os.environ.get("CUECORE_FPCALC")
    if env and Path(env).is_file():
        return env
    name = "fpcalc.exe" if sys.platform.startswith("win") else "fpcalc"
    base = getattr(sys, "_MEIPASS", None)           # PyInstaller-Bundle
    if base:
        p = Path(base) / "bin" / name
        if p.is_file():
            return str(p)
    local = Path(__file__).resolve().parent.parent / "bin" / name
    if local.is_file():
        return str(local)
    return shutil.which(name)


def fingerprint(path: str | Path, *, runner=subprocess.run, timeout: float = 120) -> tuple[int, str] | None:
    """(Dauer in ganzen Sekunden, Fingerabdruck) — None, wenn fpcalc fehlt oder nicht durchkommt.

    fpcalc liest selbst fast jedes Format (Chromaprint bringt den Decoder mit), ein ffmpeg-Umweg
    wie bei AudD ist nicht nötig. `-json` liefert {"duration": float, "fingerprint": str}.
    """
    exe = fpcalc_path()
    if not exe:
        return None
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW   # sonst blitzt aus dem fensterlosen Build je Datei eine Konsole auf
    try:
        r = runner([exe, "-json", str(path)], capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError):
        return None
    # Kein Blick auf den Exit-Code: bei einem kaputten Frame rechnet fpcalc fertig und meldet trotzdem Exit 3.
    # Was zählt, ist ein gültiges JSON mit Dauer und Fingerabdruck.
    if not getattr(r, "stdout", ""):
        return None
    try:
        d = json.loads(r.stdout)
        dur, fp = d["duration"], d["fingerprint"]
    except (ValueError, KeyError, TypeError):
        return None
    if not fp or not dur:
        return None
    return int(round(float(dur))), str(fp)


def _default_post(params: dict) -> dict | None:
    _LIMITER.wait(HOST, MIN_INTERVAL)
    try:
        r = httpx.post(LOOKUP_URL, data=params, timeout=30,
                       headers={"User-Agent": "CueCore/0.9 (+https://github.com/PostBro/CueCore)"})
        return r.json()
    except Exception:
        return None


def lookup(fp: str, duration: int, api_key: str, *, poster=_default_post, meta: str = META) -> dict | None:
    """Rohantwort der Lookup-Schnittstelle. None ohne Schlüssel oder bei Netzfehler."""
    if not api_key or not fp or not duration:
        return None
    return poster({"client": api_key, "duration": int(duration), "fingerprint": fp,
                   "meta": meta, "format": "json"})


def parse_result(payload: dict | None, *, min_score: float = 0.5) -> dict | None:
    """Beste Aufnahme aus der Antwort -> flaches dict wie bei AudD, sonst None.

    Gewählt wird das Ergebnis mit dem höchsten `score`; innerhalb davon die Aufnahme, die einen
    Interpret UND einen Titel trägt (AcoustID liefert gelegentlich Aufnahmen ohne Interpret).
    Unter `min_score` zählt der Treffer nicht — ein schwacher Fingerprint-Treffer ist schlechter
    als kein Vorschlag, weil er ungeprüft in ein Cue-Sheet wandern würde.
    """
    if not payload or payload.get("status") != "ok":
        return None
    results = [r for r in (payload.get("results") or []) if (r.get("score") or 0) >= min_score]
    if not results:
        return None
    results.sort(key=lambda r: r.get("score") or 0, reverse=True)
    for res in results:
        for rec in res.get("recordings") or []:
            artists = [a for a in (rec.get("artists") or []) if a.get("name")]
            title = (rec.get("title") or "").strip()
            if not artists or not title:
                continue
            # Credit-Schreibweise: die AcoustID-API liefert `joinphrase`, wenn MusicBrainz eine führt
            # („Queen“ + „ & “ + „David Bowie“); ohne sie trennt „; “.
            artist = "".join(a["name"] + (a.get("joinphrase") or ("; " if i < len(artists) - 1 else ""))
                             for i, a in enumerate(artists)).strip()
            releases = rec.get("releases") or []
            isrcs = rec.get("isrcs") or []
            return {
                "artist": artist,
                "title": title,
                "album": (releases[0].get("title", "") if releases else "") or "",
                "label": "",                      # AcoustID führt kein Label — kommt aus unserer Kette
                "isrc": (isrcs[0] if isrcs else "") or "",
                "date": "",
                "apple_music_url": "", "spotify_url": "",
                "acoustid": res.get("id", "") or "",
                "recording_mbid": rec.get("id", "") or "",
                "score": round(float(res.get("score") or 0), 3),
            }
    return None


def recognize(file_path: str | Path, api_key: str, *, expected_artist: str = "", expected_title: str = "",
              fingerprinter=fingerprint, poster=_default_post, min_score: float = 0.5) -> dict | None:
    """Datei erkennen. None ohne Schlüssel, ohne fpcalc oder ohne ausreichend sicheren Treffer."""
    if not api_key:
        return None
    fpr = fingerprinter(file_path)
    if not fpr:
        return None
    duration, fp = fpr
    result = parse_result(lookup(fp, duration, api_key, poster=poster), min_score=min_score)
    if result is not None:
        result["possible_cover"] = is_possible_cover(
            expected_artist, expected_title, result["artist"], result["title"])
        result["duration_sec"] = duration
    return result
