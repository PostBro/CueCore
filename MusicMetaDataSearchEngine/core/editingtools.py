"""editingtools.io Cue-Sheet-Metadaten (Production-Music-Library-Gap).

editingtools löst genau die Production-Library-Cues (UPM/APL/TRL), die unsere
kommerziellen Quellen (MB/Discogs/Deezer/Wikidata) NICHT haben — über Partner-Library-
Datenbanken. Kein einfacher Such-Endpoint: es ist ein PROJEKT-Workflow (offizielle Doku
`en.editingtools.io/api/v2/cuesheets/`):

  1. POST /v2/cuesheets/project  {name, framerate, scanLibraries, entries:[{fileName,
     clipName, timecodeIn, timecodeOut, ...}]}  -> data.projectId (Scan startet async)
  2. GET  /v2/cuesheets/project/{id}            -> scanComplete (bool) — pollen
  3. GET  /v2/cuesheets/project/{id}/entries    -> pro entry .metadata{trackTitle, artist,
     labelName, labelCode, isrc, iswc, albumTitle, interestedParties[...], source} (evtl. PRO)
  4. DELETE /v2/cuesheets/project/{id}          -> Aufräumen

Auth: Basic base64("apikey:KEY"). Rate-Limit 50/min, 1000/h.

Die HTTP-Schicht (`http(method, url, body) -> Resp`) wird injiziert -> Orchestrierung
netzfrei testbar. WICHTIG: editingtools' `labelCode` ist teils eine INTERNE Label-ID,
NICHT der GVL-LC -> der Aufrufer muss ihn gegen den GVL-Master gegenprüfen (nicht hier).
"""
from __future__ import annotations
import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

BASE = "https://api.editingtools.io/v2"


@dataclass
class Resp:
    ok: bool = False
    status: int = 0
    data: dict = field(default_factory=dict)   # bei Erfolg der ausgepackte data-Block
    error: str = ""                            # kurze Fehlerkennung (für die UI)


def auth_header(api_key: str) -> dict:
    tok = base64.b64encode(f"apikey:{api_key}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json", "Accept": "application/json"}


def _unwrap(payload):
    """editingtools verpackt Erfolge in {code,status,timestamp,data:…} — den data-Block
    auspacken. `data` ist ein dict (create/get-project) ODER eine LISTE (get-entries).
    Beides unverändert zurückgeben."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def http_request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None,
                 retries: int = 3, timeout: int = 30) -> Resp:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return Resp(ok=True, status=r.status, data=_unwrap(json.loads(raw) if raw else {}))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            emap = {401: "auth", 403: "forbidden", 402: "quota", 404: "not_found", 422: "bad_request"}
            return Resp(ok=False, status=e.code, error=emap.get(e.code, f"http_{e.code}"))
        except Exception as ex:  # Netz/Timeout
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return Resp(ok=False, status=0, error=f"network:{type(ex).__name__}")
    return Resp(ok=False, error="unknown")


def build_http(api_key: str):
    """http(method, url, body=None) -> Resp mit fest verdrahteter Basic-Auth."""
    headers = auth_header(api_key)
    def http(method: str, url: str, body: dict | None = None) -> Resp:
        return http_request(method, url, headers=headers, body=body)
    return http


# --- Client (jede Funktion nimmt den injizierten http) ---

def create_project(name: str, entries: list[dict], *, http, scan_libraries: str = "all",
                   framerate: str = "25") -> Resp:
    body = {"name": name, "framerate": framerate, "scanLibraries": scan_libraries, "entries": entries}
    return http("POST", f"{BASE}/cuesheets/project", body)


def get_project(project_id: str, *, http) -> Resp:
    return http("GET", f"{BASE}/cuesheets/project/{project_id}")


def get_entries(project_id: str, *, http) -> Resp:
    return http("GET", f"{BASE}/cuesheets/project/{project_id}/entries")


def delete_project(project_id: str, *, http) -> Resp:
    return http("DELETE", f"{BASE}/cuesheets/project/{project_id}")


# --- Normalisierung ---

_COMPOSER_ROLES = ("composer", "compositeur", "komponist", "arranger", "ca", "c ", "adaptor")
_LYRICIST_ROLES = ("author", "lyricist", "writer", "textdichter", "texter", "a ", "sa")
_PUBLISHER_ROLES = ("publisher", "verlag", "editor", "se", "e ", "sub-publisher", "subpublisher")


def _role_bucket(role: str) -> str:
    r = (role or "").strip().lower()
    if any(r == k or r.startswith(k) for k in _COMPOSER_ROLES):
        return "composer"
    if any(r == k or r.startswith(k) for k in _LYRICIST_ROLES):
        return "lyricist"
    if any(r == k or r.startswith(k) for k in _PUBLISHER_ROLES):
        return "publisher"
    return ""


def _parties(parties: list[dict]) -> tuple[str, str, str]:
    """interestedParties -> (Komponist, Texter, Verlag) — dedupliziert, Reihenfolge stabil."""
    buckets: dict[str, list[str]] = {"composer": [], "lyricist": [], "publisher": []}
    for p in parties or []:
        name = (p.get("name") or "").strip()
        if not name:
            fn, ln = (p.get("_firstName_") or "").strip(), (p.get("_lastName_") or "").strip()
            name = f"{fn} {ln}".strip()
        if not name:
            continue
        b = _role_bucket(p.get("role", ""))
        if b and name not in buckets[b]:
            buckets[b].append(name)
    return "; ".join(buckets["composer"]), "; ".join(buckets["lyricist"]), "; ".join(buckets["publisher"])


def _merge_names(*vals: str) -> str:
    """Mehrere '; '-getrennte Namenslisten zu einer deduplizierten zusammenführen."""
    out: list[str] = []
    for v in vals:
        for part in (v or "").split(";"):
            p = part.strip()
            if p and p not in out:
                out.append(p)
    return "; ".join(out)


def normalize_entry(entry: dict) -> dict:
    """editingtools-Entry -> unser Feldschema. Die Metadaten haben FLACHE Felder
    (`composer`/`author`/`publisher`, live verifiziert) UND ggf. `interestedParties`
    (CISAC) — beide werden gemergt. `matched`=echtes Library-Signal (Source/Label/ISRC/
    Autoren/Artist), NICHT der auf den Dateinamen defaultende `trackTitle`. `labelcode_raw`
    ist NICHT verifiziert (teils interne editingtools-ID) — der Aufrufer prüft gegen GVL."""
    m = entry.get("metadata") or {}
    ip_c, ip_l, ip_p = _parties(m.get("interestedParties") or [])
    comp = _merge_names(m.get("composer", ""), ip_c)
    lyr = _merge_names(m.get("author", ""), m.get("lyricist", ""), ip_l)
    pub = _merge_names(m.get("publisher", ""), ip_p)
    file_name = entry.get("fileName", "") or ""
    label = (m.get("labelName", "") or "").strip()
    lc_raw = str(m.get("labelCode", "") or "").strip()
    isrc = (m.get("isrc", "") or "").strip()
    artist = (m.get("artist", "") or "").strip()
    source = (m.get("source", "") or "").strip()
    tt = (m.get("trackTitle", "") or "").strip()
    matched = bool(source or label or lc_raw or isrc or comp or lyr or artist)
    return {
        "fileName": file_name,
        "scan_status": entry.get("scanStatus", "") or "",
        "matched": matched,
        # Dateiname-als-Titel (kein Match) NICHT durchreichen — sonst Rausch-Merge.
        "titel": tt if (matched and tt.lower() != file_name.lower()) else "",
        "interpret": artist,
        "album": (m.get("albumTitle", "") or "").strip(),
        "album_catalog": (m.get("albumCatalogNumber", "") or "").strip(),
        "labelname": label,
        "labelcode_raw": lc_raw,
        "isrc": isrc,
        "iswc": (m.get("iswc", "") or "").strip(),
        "komponist": comp,
        "texter": lyr,
        "publisher": pub,
        "source": source,
    }


def _sec_to_tc(seconds: float, fps: int = 25) -> str:
    """Sekunden -> HH:MM:SS:FF (nur um die Pflicht-TC-Felder zu füllen; der Scan matcht
    per fileName, die TC-Genauigkeit ist für die Metadaten unerheblich)."""
    s = max(0, int(round(seconds or 0)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}:00"


def build_entries(files: list[dict], *, fps: int = 25) -> list[dict]:
    """files: [{'fileName': str, 'duration_sec': num}] -> editingtools-Entries mit den
    Pflichtfeldern (fileName, timecodeIn, timecodeOut). Leere Dateinamen fallen raus."""
    out = []
    for f in files:
        name = (f.get("fileName") or "").strip()
        if not name:
            continue
        dur = f.get("duration_sec") or 0
        out.append({"fileName": name, "clipName": name,
                    "timecodeIn": "00:00:00:00", "timecodeOut": _sec_to_tc(dur, fps)})
    return out


def scan_files(files: list[dict], *, http, sleep=time.sleep, poll_interval: float = 2.0,
               max_polls: int = 40, scan_libraries: str = "all", framerate: str = "25",
               project_name: str = "CueCore Scan") -> dict:
    """Voller Workflow: Projekt anlegen -> auf scanComplete pollen -> Entries holen ->
    Projekt löschen (Aufräumen). Rückgabe:
      {ok, entries: [normalize_entry...], error, scan_complete, polls}
    Fehler (kein Match, Auth, Quota) werden über `error` durchgereicht, nie geraten.
    `http` und `sleep` injiziert -> netzfrei testbar."""
    entries = build_entries(files, fps=int(float(framerate)) if str(framerate).isdigit() else 25)
    if not entries:
        return {"ok": False, "error": "no_files", "entries": []}

    cr = create_project(project_name, entries, http=http, scan_libraries=scan_libraries, framerate=framerate)
    if not cr.ok:
        return {"ok": False, "error": cr.error or "create_failed", "status": cr.status, "entries": []}
    pid = cr.data.get("projectId")
    if not pid:
        return {"ok": False, "error": "no_project_id", "entries": []}

    scan_complete, polls = False, 0
    for polls in range(1, max_polls + 1):
        pr = get_project(pid, http=http)
        if pr.ok and pr.data.get("scanComplete"):
            scan_complete = True
            break
        if not pr.ok and pr.error in ("auth", "forbidden", "not_found"):
            delete_project(pid, http=http)
            return {"ok": False, "error": pr.error, "status": pr.status, "entries": []}
        sleep(poll_interval)

    er = get_entries(pid, http=http)
    delete_project(pid, http=http)                     # immer aufräumen
    if not er.ok:
        # 402/403 hier = 'Get Entries' evtl. PRO-only -> klar melden statt still leer.
        return {"ok": False, "error": er.error or "entries_failed", "status": er.status,
                "scan_complete": scan_complete, "entries": []}
    raw = er.data.get("entries") if isinstance(er.data, dict) else er.data
    norm = [normalize_entry(e) for e in (raw or [])]
    # editingtools gibt den fileName BEREINIGT zurück (.COPY/.new/Extension gestrippt) —
    # damit der Aufrufer das Ergebnis dem ORIGINAL-Cue zuordnen kann, den gesendeten
    # Dateinamen per Index zurückheften (Reihenfolge bleibt erhalten, live verifiziert).
    for i, n in enumerate(norm):
        n["query_filename"] = entries[i]["fileName"] if i < len(entries) else n["fileName"]
    return {"ok": True, "error": "", "scan_complete": scan_complete, "polls": polls, "entries": norm}
