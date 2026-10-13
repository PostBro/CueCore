"""Track-Metadata-API eines Library-Partners: Dateiname -> Track-Metadaten (Reporting).

Basis `https://{host}/api-v2/public`:

  1. POST /oauth/token  — OAuth2 client_credentials (Form-Body).
     Antwort {token_type, expires_in (Sekunden), access_token}. Token cachen und
     wiederverwenden, NICHT pro Anfrage neu holen; erneuern kurz vor Ablauf oder nach
     401. Fehler: 400 invalid_scope, 401 invalid_client.
  2. POST /api/external/track-metadata  {"tracks": [<dateiname>, ...]}  1–50 pro Anfrage,
     Bearer-Token. Antwort: Objekt, keyed by angefragtem Dateinamen in Anfrage-Reihenfolge.
     `null` = nicht auflösbar (unbekannt, nicht mehr veröffentlicht, außerhalb des
     Katalogs) — KEIN Fehler, HTTP 200. Felder: songtitle, musician, composer,
     writer (Mehrfachnamen mit ' / '), labelname, labelcode ('LC-12345' oder null), album,
     tracknummer (int), isrc (oder null).
  3. Bei Überschreitung des Rate-Limits antwortet der Server mit 429: warten und
     wiederholen. Fehler-Codes 400/401/403/422/429.

Die HTTP-Schicht wird injiziert (wie editingtools) -> Orchestrierung netzfrei testbar.
Host, Client-ID und Secret kommen aus den Settings (core/settings.py), nie aus dem Code.
`labelcode` ist die Angabe des Anbieters, NICHT GVL-doppelt-verifiziert -> der Aufrufer
(app.py) prüft gegen den GVL-Master, nicht dieses Modul.
"""
from __future__ import annotations
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from core.normalize import normalize_labelcode

BASE_PATH = "/api-v2/public"
TOKEN_PATH = "/oauth/token"
META_PATH = "/api/external/track-metadata"
SCOPE = "track-metadata"
MAX_TRACKS = 50              # 1–50 Dateinamen pro Anfrage
TOKEN_MARGIN_SEC = 60        # so lange VOR Ablauf schon erneuern
DEFAULT_TTL_SEC = 7200       # Sekunden — Fallback, falls expires_in fehlt
SOURCE = "trackmeta"

_ERR = {400: "bad_request", 401: "auth", 403: "forbidden", 422: "bad_request", 429: "rate_limited"}


@dataclass
class Resp:
    ok: bool = False
    status: int = 0
    data: dict = field(default_factory=dict)   # geparster JSON-Body (bei Erfolg)
    error: str = ""                            # Kurzkennung für die UI


def base_url(host: str) -> str:
    """Settings-Wert 'host' -> 'https://{host}/api-v2/public'. Toleriert Schema, Slash am
    Ende und einen schon vollständig eingetragenen Basis-Pfad."""
    h = (host or "").strip().rstrip("/")
    if not h:
        return ""
    if not h.startswith(("http://", "https://")):
        h = "https://" + h
    return h if h.endswith(BASE_PATH) else h + BASE_PATH


def _error_code(e: urllib.error.HTTPError) -> str:
    """OAuth-Fehlercode aus dem Body (invalid_client/invalid_scope) hat Vorrang
    vor der generischen Status-Kennung."""
    try:
        payload = json.loads(e.read() or b"{}")
        code = (payload.get("error") or "").strip() if isinstance(payload, dict) else ""
    except Exception:
        code = ""
    return code or _ERR.get(e.code, f"http_{e.code}")


def _retry_after(e: urllib.error.HTTPError, attempt: int) -> float:
    """Retry-After (Sekunden) wenn gesetzt, sonst 2/4/6 s. Nie länger als 60 s."""
    try:
        val = float(e.headers.get("Retry-After", ""))
    except (TypeError, ValueError, AttributeError):
        val = 2.0 * (attempt + 1)
    return max(0.0, min(val, 60.0))


def http_request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None,
                 form: dict | None = None, retries: int = 3, timeout: int = 30,
                 sleep=time.sleep) -> Resp:
    """JSON-Body (`body`, Metadaten-Endpoint) ODER Form-Body (`form`, Token-Endpoint).
    429/503 -> warten und erneut, max. `retries`. HTTP-Fehler -> Kurzkennungen."""
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdr = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    elif body is not None:
        data = json.dumps(body).encode()
        hdr = {"Content-Type": "application/json", "Accept": "application/json"}   # Pflicht-Header
    else:
        data, hdr = None, {"Accept": "application/json"}
    hdr.update(headers or {})
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                payload = json.loads(raw) if raw else {}
                return Resp(ok=True, status=r.status, data=payload if isinstance(payload, dict) else {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                sleep(_retry_after(e, attempt))
                continue
            return Resp(ok=False, status=e.code, error=_error_code(e))
        except Exception as ex:                      # Netz/Timeout
            if attempt < retries - 1:
                sleep(2 * (attempt + 1))
                continue
            return Resp(ok=False, status=0, error=f"network:{type(ex).__name__}")
    return Resp(ok=False, error="unknown")


class Client:
    """Token-Cache + Metadaten-Abruf für EIN Tripel (host, client_id, client_secret).
    `http(method, url, *, headers=None, body=None, form=None) -> Resp` und `now()` sind
    injizierbar. Lock, weil FastAPI Anfragen parallel bedient und das Token geteilt ist."""

    def __init__(self, host: str, client_id: str, client_secret: str, *, http=None, now=time.time):
        self.base = base_url(host)
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self._http = http or http_request
        self._now = now
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def configured(self) -> bool:
        return bool(self.base and self.client_id and self.client_secret)

    def token(self, *, force: bool = False) -> Resp:
        """Gecachtes Token, solange es nicht in < TOKEN_MARGIN_SEC abläuft; sonst neu holen
        Resp.data = {access_token, cached}."""
        with self._lock:
            if not force and self._token and self._now() < self._expires_at - TOKEN_MARGIN_SEC:
                return Resp(ok=True, status=200, data={"access_token": self._token, "cached": True})
            r = self._http("POST", self.base + TOKEN_PATH,
                           form={"grant_type": "client_credentials", "client_id": self.client_id,
                                 "client_secret": self.client_secret, "scope": SCOPE})
            if not r.ok:
                self._token, self._expires_at = "", 0.0
                return r
            tok = str(r.data.get("access_token") or "").strip()
            if not tok:
                return Resp(ok=False, status=r.status, error="no_token")
            try:
                ttl = float(r.data.get("expires_in") or 0)
            except (TypeError, ValueError):
                ttl = 0.0
            self._token = tok
            self._expires_at = self._now() + (ttl if ttl > 0 else DEFAULT_TTL_SEC)
            return Resp(ok=True, status=r.status, data={"access_token": tok, "cached": False})

    def invalidate(self) -> None:
        with self._lock:
            self._token, self._expires_at = "", 0.0

    def lookup_batch(self, tracks: list[str]) -> Resp:
        """EIN Aufruf des Metadaten-Endpoints (1–MAX_TRACKS Namen). Antwortet er 401 (Token
        abgelaufen/ungültig), einmal ein frisches Token erzwingen und wiederholen."""
        if not tracks or len(tracks) > MAX_TRACKS:
            return Resp(ok=False, error="bad_batch")
        r = Resp(ok=False, error="unknown")
        for attempt in (0, 1):
            t = self.token(force=(attempt == 1))
            if not t.ok:
                return t
            r = self._http("POST", self.base + META_PATH,
                           headers={"Authorization": f"Bearer {t.data['access_token']}"},
                           body={"tracks": list(tracks)})
            if r.status == 401 and attempt == 0:
                self.invalidate()
                continue
            return r
        return r


# --- Normalisierung ---

def _names(v) -> str:
    """Anbieter verbindet mehrere Namen mit ' / ' -> unsere '; '-Konvention
    (wie editingtools/songs.db), dedupliziert, Reihenfolge stabil."""
    out: list[str] = []
    for part in str(v or "").split("/"):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return "; ".join(out)


def _tracknumber(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize_result(filename: str, obj) -> dict:
    """Ein Antwort-Eintrag -> unser Feldschema (gleiche Keys wie editingtools.normalize_entry,
    damit das EDL-Frontend beide Quellen identisch mergt). `null`/kein Objekt -> matched=False.
    `labelcode_raw` = Anbieter-LC ('LC-12345' -> '12345'), NICHT verifiziert."""
    base = {"fileName": filename, "query_filename": filename, "matched": False, "titel": "",
            "interpret": "", "album": "", "labelname": "", "labelcode_raw": "", "isrc": "",
            "komponist": "", "texter": "", "publisher": "",     # Verlag liefert die API nicht
            "tracknummer": None, "source": SOURCE}
    if not isinstance(obj, dict):
        return base
    lc = obj.get("labelcode")
    base.update({
        "titel": str(obj.get("songtitle") or "").strip(),
        "interpret": str(obj.get("musician") or "").strip(),
        "album": str(obj.get("album") or "").strip(),
        "labelname": str(obj.get("labelname") or "").strip(),
        "labelcode_raw": normalize_labelcode(str(lc)) if lc not in (None, "") else "",
        "isrc": str(obj.get("isrc") or "").strip(),
        "komponist": _names(obj.get("composer")),
        "texter": _names(obj.get("writer")),
        "tracknummer": _tracknumber(obj.get("tracknummer")),
    })
    base["matched"] = any(base[k] for k in ("titel", "interpret", "labelname", "labelcode_raw", "isrc", "komponist"))
    return base


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _map_batch(chunk: list[str], data: dict) -> list[dict]:
    """Antwort ist per Dateiname gekeyt. Fehlt ein Key (etwa weil der Server den
    Namen normalisiert zurückgibt), per Position zuordnen — die Antwort kommt in
    Anfrage-Reihenfolge. Passt auch die Länge nicht: nicht auflösbar."""
    values = list(data.values()) if isinstance(data, dict) else []
    out = []
    for i, name in enumerate(chunk):
        if isinstance(data, dict) and name in data:
            obj = data[name]
        elif len(values) == len(chunk):
            obj = values[i]
        else:
            obj = None
        out.append(normalize_result(name, obj))
    return out


def lookup(filenames: list[str], *, client: Client) -> dict:
    """Alle Dateinamen (getrimmt, dedupliziert, Reihenfolge stabil) in Batches à MAX_TRACKS
    auflösen. Rückgabe {ok, error, status, entries, matched, requests}. Ein fehlgeschlagener
    Batch (Auth/Rate-Limit/Netz) bricht ab und wird durchgereicht; die Treffer der Batches
    davor bleiben in `entries`."""
    names = _dedupe([str(n).strip() for n in (filenames or []) if n and str(n).strip()])
    if not names:
        return {"ok": False, "error": "no_files", "status": 0, "entries": [], "matched": 0, "requests": 0}
    if not client.configured():
        return {"ok": False, "error": "not_configured", "status": 0, "entries": [], "matched": 0, "requests": 0}
    entries: list[dict] = []
    requests = 0
    for i in range(0, len(names), MAX_TRACKS):
        chunk = names[i:i + MAX_TRACKS]
        r = client.lookup_batch(chunk)
        requests += 1
        if not r.ok:
            return {"ok": False, "error": r.error or "lookup_failed", "status": r.status, "entries": entries,
                    "matched": sum(1 for e in entries if e["matched"]), "requests": requests}
        entries.extend(_map_batch(chunk, r.data))
    return {"ok": True, "error": "", "status": 200, "entries": entries,
            "matched": sum(1 for e in entries if e["matched"]), "requests": requests}
