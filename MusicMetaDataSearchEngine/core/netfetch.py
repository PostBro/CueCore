"""Netz-Layer für die externen Quellen: zentraler Rate-Limit-Scheduler + durabler
Cache + ehrliches Failure-Reporting.

Ein `Fetcher` ist EIN aufrufbares `fetch(url) -> dict | None`, das in die Source-
Lookups injiziert wird (musicbrainz.lookup, discogs.lookup_label_codes). Es:
  1. prüft den api_cache (Treffer JÜNGER ALS max_age = kein Netz),
  2. drosselt pro Host (Mindest-Intervall, das sich bei 429/503 erhöht und bei
     Erfolg wieder abbaut — s. HostBackoff),
  3. holt per HTTP (Retry auf 429/503),
  4. cached nur Erfolge (kein 503 wird eingefroren),
  5. gibt bei Netz-Fehlschlag den ABGELAUFENEN Cache-Eintrag zurück, wenn es einen
     gibt (Stale-Fallback — sonst kehrt die TTL den 503-Schutz aus Punkt 4 um),
  6. zählt calls / cache_hits / stale_hits / failures — damit der Aufrufer ein
     unvollständiges Ergebnis ERKENNT statt es still als sicher auszugeben,
  7. gibt bei Dauerfehlern auf (FAIL_BUDGET): kommt seit dem letzten Erfolg nur noch
     Fehlschlag, geht nach dem Budget kein Abruf mehr raus.

time.monotonic/sleep sind hier normale Python-Stdlib (kein Workflow-Sandbox).
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from threading import Lock
from urllib.parse import urlsplit

from . import cache as cachelib

USER_AGENT = "CueCore/0.9 (+https://github.com/PostBro/CueCore)"

# Mindestabstand pro Host in Sekunden (konservativ: MB 1/s, Discogs unauth ~25/min).
HOST_INTERVAL = {
    "musicbrainz.org": 1.1,
    "api.discogs.com": 2.5,
    "api.deezer.com": 0.5,
    "query.wikidata.org": 1.1,   # WDQS: höflich + descriptive UA (Pflicht laut WDQS-Policy)
    "itunes.apple.com": 3.0,     # Search API: „approximately 20 calls per minute“ (performance-partners.apple.com/search-api)
}
_DEFAULT_INTERVAL = 1.1

# Untergrenze, die auch ein Intervall-Override nicht unterschreiten darf — Policy der
# Betreiber (MusicBrainz: 1 req/s; WDQS: "be nice"), nicht unsere Wahl.
HARD_FLOOR = {"musicbrainz.org": 1.1, "query.wikidata.org": 1.1}

# Obergrenze des Backoffs. Höher hilft nicht: wer 32s pro Request wartet, hat den
# Lauf ohnehin verloren — dann lieber ein ehrliches `incomplete`.
BACKOFF_CAP = 32.0

# Dauerfehler: kommt seit dem letzten Erfolg (oder dem Start) nur noch Fehlschlag —
# 503 in Serie, Timeouts, kein Netz —, geht nach FAIL_BUDGET Sekunden in DIESER Auflösung kein Abruf mehr raus; der
# Rest fällt sofort als Fehlschlag aus (failures zählt, Stale-Cache gilt weiter). Sonst wartete sich der Resolver
# mit dem Backoff durch jede Quelle: bei einer Störung minutenlang Spinner, am Ende „kein Match“.
# Plus der eine laufende Abruf (Timeout 15 s, 503-Retries 2+4 s) ergibt das „spätestens etwa 60 s“. Gedrosselt
# bleibt gedrosselt: geprüft wird VOR dem Warten, kein Abruf geht früher raus, als der Limiter erlaubt.
FAIL_BUDGET = 40.0

# Cache-Lebensdauer. Ohne sie ist jede einmal geholte Antwort dauerhaft eingefroren:
# ein falsch aufgelöster Track bliebe auch nach einer MB-Korrektur falsch.
DEFAULT_MAX_AGE = 24 * 3600.0
# Discogs-API-Bedingungen: angezeigte Inhalte höchstens 6 Stunden älter als auf Discogs, Cachen nur so lange wie
# nötig. Deshalb für api.discogs.com kürzer und ohne Stale-Fallback über diese Grenze hinaus.
DISCOGS_MAX_AGE = 6 * 3600.0
_SHORT_TTL = {"api.discogs.com": DISCOGS_MAX_AGE}


class RateLimiter:
    """Threadsicher, pro Host. Reserviert den Slot unter Lock, schläft außerhalb."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = Lock()

    def wait(self, host: str, min_interval: float) -> None:
        with self._lock:
            now = time.monotonic()
            target = max(now, self._last.get(host, 0.0) + min_interval)
            self._last[host] = target
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class HostBackoff:
    """Veränderlicher Mindestabstand pro Host, mit Decay.

    Eine Konstante vergisst nach jedem Request, dass der Server gerade überlastet war:
    der nächste Call nach einem 503 ginge wieder mit 1.1s raus. Hier überlebt „MB ist
    gerade überlastet" den einzelnen Request.

    429/503 -> Verdopplung (exponentiell, gedeckelt auf ``cap``). Erfolg -> Halbierung
    bis zum Floor. Nur ECHTE Drosselung zählt: 404/Timeout dürfen nicht hochtreiben,
    sonst bremst sich der Client bei jedem Fehltreffer auf ``cap`` aus.

    Bewusst KEIN Congestion-Window/ssthresh/Unacked-Zähler: CueCore fährt synchron und
    ohne Parallel-Requests, ein Fenster hätte hier keinen messbaren Effekt.
    """

    def __init__(self, *, cap: float = BACKOFF_CAP) -> None:
        self._delay: dict[str, float] = {}
        self._lock = Lock()
        self.cap = cap

    def interval(self, host: str, floor: float) -> float:
        with self._lock:
            return max(self._delay.get(host, floor), floor)

    def penalize(self, host: str, floor: float) -> float:
        """Nach 429/503: Abstand verdoppeln (ab dem Floor), gedeckelt."""
        with self._lock:
            cur = max(self._delay.get(host, floor), floor)
            new = min(cur * 2.0, max(self.cap, floor))
            self._delay[host] = new
            return new

    def reward(self, host: str, floor: float) -> float:
        """Nach Erfolg: Abstand halbieren, nie unter den Floor."""
        with self._lock:
            new = max(self._delay.get(host, floor) / 2.0, floor)
            self._delay[host] = new
            return new


# Prozessweiter Limiter + Backoff (über Requests UND Fetcher-Instanzen hinweg
# koordiniert — jeder Resolve baut sich einen neuen Fetcher, der Zustand darf
# dabei nicht verloren gehen).
GLOBAL_LIMITER = RateLimiter()
GLOBAL_BACKOFF = HostBackoff()


def raw_get_json(url: str, ua: str = USER_AGENT, *, headers: dict | None = None,
                 retries: int = 3) -> tuple[dict | None, bool]:
    """(payload, throttled). ``throttled`` ist NUR bei 429/503 gesetzt — der Server
    hat aktiv gebremst. 404, Timeout und Parse-Fehler sind KEINE Drosselung; würden
    sie den Backoff hochtreiben, bremste sich der Client bei jedem Fehltreffer aus."""
    h = {"User-Agent": ua, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    throttled = False
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()), throttled
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                throttled = True
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
            return None, throttled
        except Exception:
            return None, throttled
    return None, throttled


def _as_outcome(out) -> tuple[dict | None, bool]:
    """Das injizierte http-Callable darf ``dict|None`` ODER ``(dict|None, throttled)``
    liefern. Fakes ohne Status-Interesse bleiben damit gültig (throttled=False)."""
    if isinstance(out, tuple):
        data, throttled = out
        return data, bool(throttled)
    return out, False


class Fetcher:
    """Aufrufbar wie fetch(url) -> dict | None. Cache + Rate-Limit + Failure-Zähler.

    auth: pro Host optionale Extra-Header (z.B. Discogs-Consumer-Auth) — geht NUR an
    den passenden Host, nie an die anderen Quellen. intervals: pro Host Override des
    Mindestabstands (mit Auth darf Discogs schneller, s. build_fetcher).
    max_age: Cache-TTL in Sekunden (None = unbegrenzt, altes Verhalten).
    backoff: HostBackoff — ohne Angabe eine eigene Instanz (Tests bleiben isoliert);
    build_fetcher reicht den prozessweiten GLOBAL_BACKOFF durch.
    fail_budget: Sekunden ohne Erfolg, nach denen bei Fehlschlägen aufgegeben wird (None = nie);
    clock: Zeitquelle (Tests: simulierte Uhr)."""

    def __init__(self, *, cache_con=None, limiter: RateLimiter | None = None,
                 http=raw_get_json, ua: str = USER_AGENT,
                 auth: dict[str, dict] | None = None, intervals: dict[str, float] | None = None,
                 max_age: float | None = DEFAULT_MAX_AGE,
                 backoff: "HostBackoff | None" = None,
                 fail_budget: float | None = FAIL_BUDGET, clock=time.monotonic) -> None:
        self.cache = cache_con
        self.limiter = limiter
        self.http = http
        self.ua = ua
        self.auth = auth or {}
        self.intervals = intervals or {}
        self.max_age = max_age
        self.backoff = backoff if backoff is not None else HostBackoff()
        self.calls = 0
        self.cache_hits = 0
        self.stale_hits = 0   # abgelaufener Eintrag als Notreserve ausgeliefert
        self.failures = 0
        self.throttled = 0    # Antworten mit 429/503 (Backoff hochgesetzt)
        self.fail_budget = fail_budget
        self.clock = clock
        self._last_ok = clock()   # Start bzw. letzter Erfolg
        self._failing = False     # seit dem letzten Erfolg mindestens ein Fehlschlag
        self.gave_up = False      # Dauerfehler: kein Netz mehr für diesen Fetcher

    def floor_for(self, host: str) -> float:
        """Kleinstmöglicher Abstand für diesen Host: konfiguriertes Intervall, aber nie
        unter der Betreiber-Untergrenze."""
        base = self.intervals.get(host) or HOST_INTERVAL.get(host, _DEFAULT_INTERVAL)
        return max(base, HARD_FLOOR.get(host, 0.0))

    def _cached(self, url: str) -> tuple[dict | None, str | None]:
        """(frischer Treffer, abgelaufener Payload). Beides None = gar kein Eintrag.
        Ein unlesbarer Payload zählt als 'kein Eintrag' (nie eine Exception aus dem Cache)."""
        if self.cache is None:
            return None, None
        entry = cachelib.get_entry(self.cache, "http", url)
        if entry is None:
            return None, None
        payload, fetched_at = entry
        short = _SHORT_TTL.get(urlsplit(url).hostname or "")
        if self.max_age is not None and short is not None and cachelib.age_seconds(fetched_at) > min(self.max_age, short):
            return None, None                   # zu alt für die Bedingungen der Quelle: auch nicht als Notreserve
        if self.max_age is not None and cachelib.age_seconds(fetched_at) > self.max_age:
            return None, payload
        try:
            return json.loads(payload), None
        except ValueError:
            return None, None

    def _gives_up(self, interval: float) -> bool:
        """Dauerfehler? Seit dem letzten Erfolg nur Fehlschläge, und mit dem nächsten Wartefenster wären
        fail_budget Sekunden ohne Erfolg verstrichen. Einmal aufgegeben, bleibt es dabei."""
        if not self.gave_up and self.fail_budget is not None and self._failing:
            self.gave_up = self.clock() + interval - self._last_ok >= self.fail_budget
        return self.gave_up

    def _failed(self, stale: str | None) -> dict | None:
        self.failures += 1              # NICHT cachen — Rate-Limit darf nicht einfrieren
        if stale is not None:           # aber: lieber veraltet als leer (Pflicht-Fallback)
            try:
                out = json.loads(stale)
            except ValueError:
                return None
            self.stale_hits += 1
            return out
        return None

    def __call__(self, url: str) -> dict | None:
        fresh, stale = self._cached(url)
        if fresh is not None:
            self.cache_hits += 1
            return fresh
        host = urlsplit(url).hostname or ""
        floor = self.floor_for(host)
        interval = self.backoff.interval(host, floor)
        if self._gives_up(interval):
            return self._failed(stale)  # kein Abruf mehr — zählt wie ein gescheiterter
        if self.limiter is not None:
            self.limiter.wait(host, interval)
        data, throttled = _as_outcome(self.http(url, self.ua, headers=self.auth.get(host)))
        self.calls += 1
        if throttled:
            self.throttled += 1
            self.backoff.penalize(host, floor)   # der Server hat gebremst -> langsamer
        elif data is not None:
            self.backoff.reward(host, floor)     # nur Erfolg baut ab; 404/Timeout ändern nichts
        if data is None:
            self._failing = True
            return self._failed(stale)
        self._failing, self._last_ok = False, self.clock()
        if self.cache is not None:
            cachelib.put(self.cache, "http", url, json.dumps(data))
        return data


def build_fetcher(db_path) -> Fetcher:
    """Standard-Fetcher: cache.db neben labels.db + globaler Limiter. Liest die Discogs-
    Consumer-Keys aus den Settings; sind beide gesetzt, geht der Auth-Header an Discogs
    und der Mindestabstand sinkt (25→60/min entsprechen ~1.0s)."""
    from pathlib import Path
    from . import discogs, genius, settings
    con = cachelib.open_cache(Path(db_path).parent / "cache.db")
    dh = discogs.auth_header(settings.get("discogs_token"),
                             settings.get("discogs_consumer_key"),
                             settings.get("discogs_consumer_secret"))
    auth = {"api.discogs.com": dh} if dh else {}
    intervals = {"api.discogs.com": 1.0} if dh else {}
    gh = genius.auth_header(settings.get("genius_token"))
    if gh:
        auth[genius.HOST] = gh          # Bearer nur an api.genius.com
    auth = auth or None
    intervals = intervals or None
    return Fetcher(cache_con=con, limiter=GLOBAL_LIMITER, backoff=GLOBAL_BACKOFF,
                   auth=auth, intervals=intervals)
