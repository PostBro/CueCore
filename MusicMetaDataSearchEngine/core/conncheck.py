"""Verbindungstests für die Einstellungs-Seite („Verbindung prüfen").

Jeder Test macht GENAU einen billigen Aufruf, der nichts anlegt und kein Kontingent frisst,
und meldet {ok, status, error, detail}. Nur Dienste mit einem dokumentierten Weg, die
Zugangsdaten zu prüfen — für AudD/ACRCloud gibt es keinen ohne Audio/Kosten, also keinen Test.
HTTP wird injiziert, damit alles netzfrei testbar bleibt.
"""
from __future__ import annotations
import functools
import json
import urllib.error
import urllib.request

from . import discogs, editingtools, trackmeta

PROBE_TIMEOUT = 10   # Sekunden — ein Test darf nicht minutenlang hängen (Client-Default: 3 Retries × 30 s)

DISCOGS_PROBE = "https://api.discogs.com/database/search?q=cuecore&per_page=1"   # braucht Auth (401 ohne)
ET_PROBE_ID = "00000000-0000-0000-0000-000000000000"                              # existiert nie -> 404 = Auth ok


def check_trackmeta(client) -> dict:
    """Token holen — erzwungen frisch, damit ein rotiertes Secret sofort auffällt."""
    if client is None or not client.configured():
        return {"ok": False, "status": 0, "error": "not_configured", "detail": ""}
    # Eigener Probe-Client mit kurzem Timeout und ohne Retries — der Token-Cache des echten
    # Clients bleibt unberührt.
    probe = trackmeta.Client(client.base, client.client_id, client.client_secret,
                             http=functools.partial(trackmeta.http_request, retries=1, timeout=PROBE_TIMEOUT))
    r = probe.token(force=True)
    return {"ok": r.ok, "status": r.status, "error": "" if r.ok else (r.error or "unknown"),
            "detail": "Token erhalten" if r.ok else ""}


def check_editingtools(api_key: str, *, http=None) -> dict:
    """GET auf ein nie existierendes Projekt: 404 heißt „Key akzeptiert, Projekt gibt es nicht"
    (erwartet), 401/403 heißt Key ungültig / kein Zugriff. Legt nichts an, kostet kein Kontingent."""
    if not api_key:
        return {"ok": False, "status": 0, "error": "not_configured", "detail": ""}
    http = http or functools.partial(editingtools.http_request, retries=1, timeout=PROBE_TIMEOUT)
    r = http("GET", f"{editingtools.BASE}/cuesheets/project/{ET_PROBE_ID}",
             headers=editingtools.auth_header(api_key))
    if r.ok or r.status == 404:
        return {"ok": True, "status": r.status, "error": "", "detail": "API-Key akzeptiert"}
    return {"ok": False, "status": r.status, "error": r.error or "unknown", "detail": ""}


def check_discogs(token: str = "", consumer_key: str = "", consumer_secret: str = "", *,
                  urlopen=urllib.request.urlopen) -> dict:
    """Eine authentifizierte Suche (die Suche verlangt Auth): 200 = Zugangsdaten gültig,
    401 = ungültig. Gleicher Weg für Personal Token und Consumer Key/Secret."""
    headers = discogs.auth_header(token, consumer_key, consumer_secret)
    if not headers:
        return {"ok": False, "status": 0, "error": "not_configured", "detail": ""}
    req = urllib.request.Request(DISCOGS_PROBE, headers={"User-Agent": discogs.USER_AGENT,
                                                          "Accept": "application/json", **headers})
    try:
        with urlopen(req, timeout=PROBE_TIMEOUT) as r:
            json.loads(r.read() or b"{}")
            return {"ok": True, "status": r.status, "error": "",
                    "detail": "Personal Token akzeptiert" if token else "Consumer Key/Secret akzeptiert"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "error": "auth" if e.code in (401, 403) else ("rate_limited" if e.code == 429 else f"http_{e.code}"),
                "detail": ""}
    except Exception as ex:
        return {"ok": False, "status": 0, "error": f"network:{type(ex).__name__}", "detail": ""}


GENIUS_PROBE = "https://api.genius.com/search?q=test"


def check_acoustid(api_key: str = "", *, poster=None) -> dict:
    """Ein Lookup mit einem gültigen, aber absichtlich unbekannten Fingerabdruck.

    Trennt die beiden Schlüsselarten, die auf acoustid.org nebeneinander stehen: der LOOKUP braucht
    den **Application**-Key (acoustid.org/new-application), der **User**-Key ist nur fürs Einsenden
    neuer Fingerabdrücke. Wer den User-Key einträgt, bekommt hier „invalid API key" statt stiller
    Fundlosigkeit.
    Der Dienst prüft den Schlüssel vor dem Fingerabdruck: mit gültigem Application-Key kommt „invalid fingerprint"
    (derselbe Aufruf mit dem User-Key ergibt „invalid API key"), also gilt auch das als angenommen.
    """
    from . import acoustid as _acoustid
    if not api_key:
        return {"ok": False, "status": 0, "error": "not_configured", "detail": ""}
    payload = _acoustid.lookup("AQAAAA", 10, api_key, poster=poster or _acoustid._default_post, meta="recordingids")
    if payload is None:
        return {"ok": False, "status": 0, "error": "network", "detail": "keine Antwort von api.acoustid.org"}
    if payload.get("status") == "ok":
        return {"ok": True, "status": 200, "error": "", "detail": "Application-Key akzeptiert"}
    msg = ((payload.get("error") or {}).get("message") or "").strip()
    if msg.lower() == "invalid fingerprint":
        return {"ok": True, "status": 200, "error": "", "detail": "Application-Key akzeptiert"}
    if "api key" in msg.lower():
        return {"ok": False, "status": 401, "error": "auth",
                "detail": f"{msg} — brauchst du den Application-Key von acoustid.org/new-application? "
                          f"Der Schlüssel aus dem Konto ist der User-Key und gilt nur fürs Einsenden."}
    return {"ok": False, "status": 400, "error": "http", "detail": msg or "unbekannte Antwort"}


def check_genius(token: str = "", *, urlopen=urllib.request.urlopen) -> dict:
    """Eine Suche mit Bearer-Token: 200 = Token gültig, 401 = ungültig/abgelaufen (Doku: Client Access
    Token gilt für Lese-Endpunkte ohne Scope, /search ist einer)."""
    from . import genius
    headers = genius.auth_header(token)
    if not headers:
        return {"ok": False, "status": 0, "error": "not_configured", "detail": ""}
    req = urllib.request.Request(GENIUS_PROBE, headers={"User-Agent": discogs.USER_AGENT, "Accept": "application/json", **headers})
    try:
        with urlopen(req, timeout=PROBE_TIMEOUT) as r:
            json.loads(r.read() or b"{}")
            return {"ok": True, "status": r.status, "error": "", "detail": "Client Access Token akzeptiert"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": "auth" if e.code in (401, 403) else "http",
                "detail": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "status": 0, "error": "network", "detail": str(e)[:200]}
