"""musik-sammler.de als LC-Bestätigungsquelle.

Die Label-Seite `https://www.musik-sammler.de/label/<LC>/` ist per Labelcode
adressierbar (live verifiziert). Sie gibt IMMER HTTP 200 zurück und echot den Code
im Titel — der Labelname steht nur im Body. Ein Kandidaten-LC gilt daher als
bestätigt, wenn der offizielle GVL-Labelname im Seitentext auftaucht. Freie
Album-Suche bietet die Seite nicht (kein JSON-Endpoint) — dies ist die
Bestätigungs-Schicht für einen bereits gefundenen LC, keine Discovery-Quelle.

Reines HTML (kein JSON), daher eigener Text-Fetcher — nicht der JSON-Fetcher aus
netfetch. `get` ist injizierbar, damit Tests ohne Netz laufen.
"""
from __future__ import annotations
import re
import urllib.request

from .labelmatch import norm

MS = "https://www.musik-sammler.de"
_UA = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"
_TAGS = re.compile(r"<[^>]+>")


def label_url(code: str) -> str:
    return f"{MS}/label/{code}/"


def _html_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def confirms(code: str, label: str, *, get=_html_get) -> bool:
    """True, wenn die Label-Seite zum Code den GVL-Labelnamen nennt (LC-Bestätigung).

    Bewusst konservativ: nur für schon per GVL-Master gematchte Codes aufgerufen —
    also Bestätigung, keine Discovery. Kurze/leere Labelnamen (<3 Zeichen) werden
    nicht bestätigt (zu hohes Zufallstreffer-Risiko im Fließtext)."""
    nlabel = norm(label)
    if not code or len(nlabel) < 3:
        return False
    html = get(label_url(code))
    if not html:
        return False
    return nlabel in norm(_TAGS.sub(" ", html))
