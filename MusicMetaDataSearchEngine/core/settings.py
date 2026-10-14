"""Settings + API-Keys. Lokal, außerhalb von Cloud-synchronisierten Ordnern, NIE im Repo.

Keys werden nirgends hartcodiert. settings.json liegt im Datenordner der App
(trackindex.default_data_dir: macOS ~/Library/Application Support/MusicMeta/, Windows
%LOCALAPPDATA%\\CueCore, $CUECORE_DB verlegt beides). Fehlt ein Key, wird die
betroffene Quelle sauber übersprungen (kein Crash) — das prüft der jeweilige Client selbst.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path

from .trackindex import default_data_dir

DEFAULT_PATH = default_data_dir() / "settings.json"
SETTINGS_PATH = Path(os.environ.get("CUECORE_SETTINGS", DEFAULT_PATH))

# Bekannte Keys -> Beschreibung + ob kostenpflichtig (für die Settings-UI).
KNOWN_KEYS = {
    "discogs_token": {"label": "Discogs Personal Token", "group": "discogs", "paid": False,
                      "hint": "Einfachster Weg: ein persönliches Zugangs-Token (Discogs → Settings → Developers → Generate token). Hebt das Rate-Limit (~25→60/min). Hat Vorrang vor Consumer Key/Secret."},
    "discogs_consumer_key": {"label": "Discogs Consumer Key", "group": "discogs", "paid": False,
                             "hint": "Alternative zum Personal Token: Consumer Key deiner Discogs-Application. Braucht auch das Secret. OAuth-Flow NICHT nötig."},
    "discogs_consumer_secret": {"label": "Discogs Consumer Secret", "group": "discogs", "paid": False,
                                "hint": "Gehört zum Consumer Key."},
    "genius_token": {"label": "Genius Client Access Token", "group": "genius", "paid": False,
                     "hint": "Songwriter (Komponist/Texter) für Songs, die MusicBrainz und Discogs ohne Autoren führen. genius.com/api-clients → New API Client → „Generate Access Token“. Community-gepflegt: Vorschlag mit Quellenlink, kein Labelcode. Genius erlaubt kommerzielle Nutzung nur mit Lizenz (api-sales@genius.com)."},
    "acoustid_api_key": {"label": "AcoustID Application API-Key", "group": "acoustid", "paid": False,
                         "hint": "Akustischer Fingerabdruck (Chromaprint) statt Dateiname — erkennt Tracks ohne Tags und ohne brauchbaren Namen. Kostenloser Application-Key: acoustid.org → New Application. Laut AcoustID-Doku nur für NICHT-kommerzielle Nutzung frei; für kommerzielle Anwendungen eine eigene Anmeldung."},
    "audd_token": {"label": "AudD Token", "group": "audd", "paid": True,
                   "hint": "Audio-Fingerprinting (kaputte Files erkennen). Pay-as-you-go."},
    "editingtools_api_key": {"label": "editingtools.io API-Key", "group": "editingtools", "paid": True,
                             "hint": "Für den Library-Metadaten-Scan im EDL-Tab (Production-Music-Cues). Kontingent-basiert (ab einer Anfrage-Menge kostenpflichtig). editingtools.io → Account → API-Key."},
    "acrcloud_access_key": {"label": "ACRCloud Access Key", "group": "acrcloud", "paid": True,
                            "hint": "Fingerprinting-Alternative."},
    "acrcloud_access_secret": {"label": "ACRCloud Access Secret", "group": "acrcloud", "paid": True,
                               "hint": "Gehört zum ACRCloud Access Key."},
}

# Dienst-Gruppen für die Einstellungs-Seite (Reihenfolge = Anzeige). `url` = wo man den Schlüssel holt.
# `test` = es gibt einen
# Verbindungstest (core/conncheck.py); für AudD/ACRCloud ist keiner dokumentiert, der ohne
# Kosten oder ohne Audio auskommt — deshalb dort keiner statt einem geratenen.
GROUPS = [
    {"key": "ordner", "label": "Ordner", "test": False,
     "hint": "Wohin getaggte und umbenannte Tracks wandern."},
    {"key": "trackmeta", "label": "Track-Metadata-API (Library-Partner)", "test": True, "partners": True,
     "hint": "Dateiname → Titel, Interpret, Autoren, Label, LC. Mehrere Partner möglich (je Name, Host, Client-ID, Secret aus den Zugangsdaten des Anbieters); „Metadaten vervollständigen“ im Cue-Sheet-Tab fragt sie der Reihe nach."},
    {"key": "editingtools", "label": "editingtools.io", "test": True,
     "url": "https://editingtools.io",
     "hint": "Production-Music-Cues im EDL-Tab. Kontingent-basiert."},
    {"key": "discogs", "label": "Discogs", "test": True,
     "url": "https://www.discogs.com/settings/developers",
     "hint": "Hebt das Rate-Limit der Discogs-Abfragen (~25 → 60/min). Personal Token reicht."},
    {"key": "genius", "label": "Genius", "test": True,
     "url": "https://genius.com/api-clients",
     "hint": "Songwriter als Vorschlag, wenn MusicBrainz/Discogs keine Autoren kennen. Kostenloser Client Access Token; kommerzielle Nutzung nur mit Genius-Lizenz."},
    {"key": "acoustid", "label": "AcoustID", "test": True,
     "url": "https://acoustid.org/new-application",
     "hint": "Fingerabdruck der Audiodatei (Chromaprint/fpcalc) gegen die AcoustID-Datenbank — findet Tracks, deren Name und Tags nichts hergeben. Kostenlos für nicht-kommerzielle Nutzung. Wird vor AudD gefragt."},
    {"key": "audd", "label": "AudD", "test": False,
     "url": "https://dashboard.audd.io",
     "hint": "Audio-Fingerprinting für Dateien ohne Tags und ohne Schema-Namen. Pay-as-you-go."},
    {"key": "acrcloud", "label": "ACRCloud", "test": False,
     "url": "https://console.acrcloud.com",
     "hint": "Fingerprinting-Alternative."},
]

# Bekannte Pfad-Einstellungen (kein Geheimnis -> Wert wird sichtbar angezeigt).
KNOWN_PATHS = {
    "lc_verified_dir": {"label": "LC-verified-Ordner", "group": "ordner",
                        "hint": "Zielordner für getaggte + umbenannte Tracks. Pflicht für den Tagger-Apply."},
}

# Bekannte sichtbare Text-Einstellungen (kein Geheimnis, kein Ordner -> Wert wird angezeigt,
# ohne Ordner-Wähler). Zurzeit leer — die Track-Metadata-API lebt in der Partnerliste (unten).
KNOWN_TEXTS: dict[str, dict] = {}

# --- Track-Metadata-Partner: Liste statt Einzelfeld (Fresh Tracks ist nur EINER von mehreren möglichen) ---
# settings.json: "trackmeta_partners": [{"id","name","host","client_id","client_secret"}]. Die drei
# alten Einzelfelder trackmeta_host/_client_id/_client_secret werden beim ersten Lesen in einen
# Partner überführt und entfernt (Migration in partners()).
PARTNERS_KEY = "trackmeta_partners"
_LEGACY_PARTNER_KEYS = ("trackmeta_host", "trackmeta_client_id", "trackmeta_client_secret")
PARTNER_FIELDS = {
    "name": {"label": "Name", "hint": "Anzeigename, z.B. Fresh Tracks — steht später als Quelle am Cue."},
    "host": {"label": "Host", "hint": "z.B. api.library-partner.com (ohne https://, ohne Pfad — der API-Pfad wird automatisch ergänzt)."},
    "client_id": {"label": "Client-ID", "hint": "Client-ID aus den Zugangsdaten des Anbieters."},
    "client_secret": {"label": "Client-Secret", "hint": "Client-Secret aus den Zugangsdaten des Anbieters. Wird maskiert gespeichert; leer lassen zum Behalten."},
}


def _new_partner_id() -> str:
    import uuid
    return "p" + uuid.uuid4().hex[:8]


def _clean_partner(r: dict, existing: dict[str, dict]) -> dict | None:
    """Einen Partner-Eintrag säubern. Leeres Secret übernimmt das gespeicherte des Partners mit
    derselben id (die UI schickt Secrets nie zurück). Ohne Host oder Client-ID: verworfen."""
    if not isinstance(r, dict):
        return None
    pid = str(r.get("id") or "").strip() or _new_partner_id()
    host = str(r.get("host") or "").strip()
    cid = str(r.get("client_id") or "").strip()
    if not host or not cid:
        return None
    secret = str(r.get("client_secret") or "").strip() or (existing.get(pid) or {}).get("client_secret", "")
    name = " ".join(str(r.get("name") or "").split()) or host
    return {"id": pid, "name": name, "host": host, "client_id": cid, "client_secret": secret}


def partners(path: str | Path | None = None) -> list[dict]:
    """Alle Track-Metadata-Partner (mit Secret — nur für Backend-Clients). Migriert die alten
    Einzelfelder einmalig in die Liste und schreibt die Datei dann um."""
    d = load(path)
    raw = d.get(PARTNERS_KEY)
    if not isinstance(raw, list):
        raw = []
        legacy = {k: (d.get(k) or "").strip() for k in _LEGACY_PARTNER_KEYS}
        if legacy["trackmeta_host"] and legacy["trackmeta_client_id"] and legacy["trackmeta_client_secret"]:
            raw = [{"id": _new_partner_id(), "name": "Library-Partner", "host": legacy["trackmeta_host"],
                    "client_id": legacy["trackmeta_client_id"], "client_secret": legacy["trackmeta_client_secret"]}]
        if any(k in d for k in _LEGACY_PARTNER_KEYS) or raw:
            for k in _LEGACY_PARTNER_KEYS:
                d.pop(k, None)
            d[PARTNERS_KEY] = raw
            save(d, path)
    out: list[dict] = []
    seen: set[str] = set()
    for r in raw:
        c = _clean_partner(r, {})
        if c and c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


def set_partners(rows: list, path: str | Path | None = None) -> list[dict]:
    """Die Partnerliste komplett ersetzen (Reihenfolge = Abfrage-Reihenfolge). Rückgabe: Status."""
    existing = {p["id"]: p for p in partners(path)}
    cleaned: list[dict] = []
    seen: set[str] = set()
    for r in rows or []:
        c = _clean_partner(r, existing)
        if c and c["id"] not in seen:
            seen.add(c["id"])
            cleaned.append(c)
    d = load(path)
    d[PARTNERS_KEY] = cleaned
    for k in _LEGACY_PARTNER_KEYS:
        d.pop(k, None)
    save(d, path)
    return partners_status(path)


def partners_status(path: str | Path | None = None) -> list[dict]:
    """Partner für die UI — OHNE Secret, nur ob eins gesetzt ist; `complete` = abfragbar."""
    return [{"id": p["id"], "name": p["name"], "host": p["host"], "client_id": p["client_id"],
             "secret_set": bool(p["client_secret"]), "complete": bool(p["client_secret"])}
            for p in partners(path)]


def load(path: str | Path | None = None) -> dict:
    p = Path(path or SETTINGS_PATH)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Eingebaute Anwendungs-Schlüssel (AcoustID, für die nicht-kommerzielle Nutzung) und das Profil der
# Track-Metadata-API (core/trackmeta.py: Pfade, Scope, Feldnamen; Zugangsdaten trägt jeder selbst ein).
# Sie stehen NICHT im Repo: build_bundle_data.py schreibt sie aus der Umgebung (BUILTIN_ENV) verschleiert nach
# build_data/builtin_keys.bin, die Spec legt die Datei ins Bundle, app.py setzt BUILTIN_PATH. Ein eigener Key in den
# Einstellungen gewinnt. Verschleiert heißt: nicht im Klartext lesbar und nie in der Oberfläche — geheim ist es nicht.
BUILTIN_ENV = {"acoustid_api_key": "CUECORE_ACOUSTID_APP_KEY", "trackmeta_profile": "CUECORE_TRACKMETA_PROFILE"}
BUILTIN_PATH: Path | None = None
_MASK = b"CueCore-builtin-keys"
_builtin_cache: tuple[Path | None, dict] | None = None


def encode_builtin(keys: dict) -> bytes:
    raw = json.dumps(keys, sort_keys=True).encode("utf-8")
    return base64.b64encode(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw)))


def decode_builtin(blob: bytes) -> dict:
    raw = base64.b64decode(blob)
    return json.loads(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw)).decode("utf-8"))


def builtin(key: str) -> str:
    """Eingebauter Schlüssel aus dem Bundle ('' ohne Datei, etwa beim Start aus dem Quellcode)."""
    global _builtin_cache
    if _builtin_cache is None or _builtin_cache[0] != BUILTIN_PATH:
        keys: dict = {}
        try:
            if BUILTIN_PATH and Path(BUILTIN_PATH).is_file():
                keys = decode_builtin(Path(BUILTIN_PATH).read_bytes())
        except (ValueError, OSError):
            keys = {}
        _builtin_cache = (BUILTIN_PATH, keys)
    return (_builtin_cache[1].get(key) or "").strip()


def effective(key: str, path: str | Path | None = None) -> str:
    """Wirksamer Schlüssel: der eigene aus den Einstellungen, sonst der eingebaute."""
    return get(key, path) or builtin(key)


def save(data: dict, path: str | Path | None = None) -> None:
    p = Path(path or SETTINGS_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get(key: str, path: str | Path | None = None) -> str:
    return (load(path).get(key) or "").strip()


def _set(updates: dict, allowed: dict, path: str | Path | None) -> None:
    """Setzt/löscht bekannte Einträge (nur Keys aus `allowed`). Leerer Wert -> entfernen."""
    d = load(path)
    for k, v in updates.items():
        if k not in allowed:
            continue
        if (v or "").strip():
            d[k] = v.strip()
        else:
            d.pop(k, None)
    save(d, path)


def set_keys(updates: dict, path: str | Path | None = None) -> None:
    """Setzt/löscht mehrere Keys. Leerer Wert -> Key entfernen."""
    _set(updates, KNOWN_KEYS, path)


def status(path: str | Path | None = None) -> list[dict]:
    """Pro bekanntem Key: gesetzt? (OHNE den Wert preiszugeben.)"""
    d = load(path)
    return [{"key": k, "label": meta["label"], "paid": meta["paid"], "hint": meta["hint"],
             "group": meta.get("group", ""), "set": bool((d.get(k) or "").strip()), "builtin": bool(builtin(k))}
            for k, meta in KNOWN_KEYS.items()]


def set_paths(updates: dict, path: str | Path | None = None) -> None:
    """Setzt/löscht bekannte Pfad-Einstellungen. Leerer Wert -> entfernen."""
    _set(updates, KNOWN_PATHS, path)


def paths_status(path: str | Path | None = None) -> list[dict]:
    """Pro bekanntem Pfad: aktueller Wert (sichtbar — kein Geheimnis)."""
    d = load(path)
    return [{"key": k, "label": meta["label"], "hint": meta["hint"], "group": meta.get("group", ""),
             "value": (d.get(k) or "").strip()} for k, meta in KNOWN_PATHS.items()]


def set_texts(updates: dict, path: str | Path | None = None) -> None:
    """Setzt/löscht bekannte sichtbare Text-Einstellungen. Leerer Wert -> entfernen."""
    _set(updates, KNOWN_TEXTS, path)


def texts_status(path: str | Path | None = None) -> list[dict]:
    """Pro bekanntem Text-Setting: aktueller Wert (sichtbar — kein Geheimnis)."""
    d = load(path)
    return [{"key": k, "label": meta["label"], "hint": meta["hint"], "group": meta.get("group", ""),
             "value": (d.get(k) or "").strip()} for k, meta in KNOWN_TEXTS.items()]
