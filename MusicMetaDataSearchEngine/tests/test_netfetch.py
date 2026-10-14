import time
from datetime import datetime, timedelta, timezone

from core import cache, netfetch


def _age_entry(con, url, seconds):
    """fetched_at künstlich zurückdatieren (statt zu warten)."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    con.execute("UPDATE api_cache SET fetched_at=? WHERE source='http' AND query_key=?", (old, url))
    con.commit()


def test_fetcher_caches_success_and_counts_failures(tmp_path):
    calls = {"n": 0}

    def fake_http(url, ua, headers=None):
        calls["n"] += 1
        return {"u": url} if "ok" in url else None

    con = cache.open_cache(tmp_path / "c.db")
    f = netfetch.Fetcher(cache_con=con, http=fake_http)  # kein Limiter im Test

    assert f("http://x/ok") == {"u": "http://x/ok"}
    assert calls["n"] == 1
    assert f("http://x/ok") == {"u": "http://x/ok"}      # zweiter Aufruf = Cache-Hit
    assert calls["n"] == 1
    assert f.cache_hits == 1

    assert f("http://x/bad") is None                     # Fehler wird NICHT gecached
    assert f("http://x/bad") is None
    assert f.failures == 2
    assert calls["n"] == 3
    con.close()


def test_cache_get_max_age_treats_old_entry_as_miss(tmp_path):
    con = cache.open_cache(tmp_path / "c.db")
    cache.put(con, "http", "u", '{"a":1}')
    assert cache.get(con, "http", "u", max_age=3600) == '{"a":1}'   # frisch
    _age_entry(con, "u", 7200)
    assert cache.get(con, "http", "u", max_age=3600) is None        # zu alt -> Miss
    assert cache.get(con, "http", "u") == '{"a":1}'                 # ohne TTL unverändert
    assert cache.get_entry(con, "http", "u")[0] == '{"a":1}'        # Zeile bleibt als Notreserve
    con.close()


def test_cache_age_seconds_unparsable_is_infinite():
    assert cache.age_seconds("kein-datum") == float("inf")   # nie fälschlich 'frisch'
    assert cache.age_seconds(None) == float("inf")


def test_fetcher_refetches_after_ttl(tmp_path):
    calls = {"n": 0}

    def fake_http(url, ua, headers=None):
        calls["n"] += 1
        return {"n": calls["n"]}

    con = cache.open_cache(tmp_path / "c.db")
    f = netfetch.Fetcher(cache_con=con, http=fake_http, max_age=3600)
    assert f("http://x/a") == {"n": 1}
    assert f("http://x/a") == {"n": 1}          # frisch -> Cache
    assert calls["n"] == 1
    _age_entry(con, "http://x/a", 7200)
    assert f("http://x/a") == {"n": 2}          # abgelaufen -> neu holen
    assert calls["n"] == 2
    assert f("http://x/a") == {"n": 2}          # und wieder frisch gecacht
    assert calls["n"] == 2
    con.close()


def test_fetcher_serves_stale_when_refetch_fails(tmp_path):
    """Pflicht-Fallback: die TTL darf den 503-Schutz nicht umkehren. Ein abgelaufener
    Eintrag + Rate-Limit ergibt das alte Ergebnis, NICHT None."""
    state = {"ok": True}

    def fake_http(url, ua, headers=None):
        return {"v": "frisch"} if state["ok"] else None

    con = cache.open_cache(tmp_path / "c.db")
    f = netfetch.Fetcher(cache_con=con, http=fake_http, max_age=3600)
    assert f("http://x/a") == {"v": "frisch"}
    _age_entry(con, "http://x/a", 7200)
    state["ok"] = False
    assert f("http://x/a") == {"v": "frisch"}   # veraltet, aber besser als leer
    assert f.stale_hits == 1
    assert f.failures == 1                      # Fehlschlag wird trotzdem ehrlich gezählt
    assert f("http://x/b") is None              # ohne Alt-Eintrag bleibt es None
    con.close()


def test_fetcher_max_age_none_never_expires(tmp_path):
    calls = {"n": 0}

    def fake_http(url, ua, headers=None):
        calls["n"] += 1
        return {"n": calls["n"]}

    con = cache.open_cache(tmp_path / "c.db")
    f = netfetch.Fetcher(cache_con=con, http=fake_http, max_age=None)
    f("http://x/a")
    _age_entry(con, "http://x/a", 10 * 365 * 24 * 3600)
    assert f("http://x/a") == {"n": 1}
    assert calls["n"] == 1
    con.close()


def test_ratelimiter_paces_same_host():
    rl = netfetch.RateLimiter()
    t0 = time.monotonic()
    rl.wait("h", 0.05)   # erster Slot: kein Warten
    rl.wait("h", 0.05)   # zweiter Slot: ~0.05s später
    assert time.monotonic() - t0 >= 0.045


def test_ratelimiter_independent_hosts():
    rl = netfetch.RateLimiter()
    rl.wait("a", 10.0)   # reserviert nur Host a
    t0 = time.monotonic()
    rl.wait("b", 10.0)   # anderer Host -> kein Warten auf a
    assert time.monotonic() - t0 < 0.5


def test_backoff_doubles_on_throttle_and_halves_on_success():
    b = netfetch.HostBackoff(cap=32.0)
    assert b.interval("h", 1.1) == 1.1
    assert b.penalize("h", 1.1) == 2.2
    assert b.penalize("h", 1.1) == 4.4
    assert b.interval("h", 1.1) == 4.4      # überlebt den einzelnen Request
    assert b.reward("h", 1.1) == 2.2
    assert b.reward("h", 1.1) == 1.1
    assert b.reward("h", 1.1) == 1.1        # nie unter den Floor


def test_backoff_is_capped_and_per_host():
    b = netfetch.HostBackoff(cap=8.0)
    for _ in range(10):
        b.penalize("a", 1.0)
    assert b.interval("a", 1.0) == 8.0
    assert b.interval("b", 1.0) == 1.0      # anderer Host unbeeindruckt


def test_raw_get_json_reports_throttled_only_for_429_503(monkeypatch):
    """Der kritische Fehlerfall: ohne Status-Unterscheidung drosselt jeder 404 den
    Client auf den Cap."""
    import urllib.error

    def raise_http(code):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return fake_urlopen

    monkeypatch.setattr(netfetch.time, "sleep", lambda s: None)
    monkeypatch.setattr(netfetch.urllib.request, "urlopen", raise_http(503))
    assert netfetch.raw_get_json("http://x/a") == (None, True)
    monkeypatch.setattr(netfetch.urllib.request, "urlopen", raise_http(429))
    assert netfetch.raw_get_json("http://x/a") == (None, True)
    monkeypatch.setattr(netfetch.urllib.request, "urlopen", raise_http(404))
    assert netfetch.raw_get_json("http://x/a") == (None, False)

    def boom(req, timeout=None):
        raise TimeoutError("timeout")

    monkeypatch.setattr(netfetch.urllib.request, "urlopen", boom)
    assert netfetch.raw_get_json("http://x/a") == (None, False)


def test_fetcher_backoff_only_reacts_to_real_throttling(tmp_path):
    outcomes = iter([
        ({"ok": 1}, True),    # 429/503 -> hoch
        (None, True),         # nochmal gedrosselt -> weiter hoch
        (None, False),        # 404 -> unverändert
        ({"ok": 2}, False),   # Erfolg -> halbieren
    ])

    def fake_http(url, ua, headers=None):
        return next(outcomes)

    b = netfetch.HostBackoff(cap=32.0)
    f = netfetch.Fetcher(http=fake_http, backoff=b, intervals={"x": 1.0})
    f("http://x/1"); assert b.interval("x", 1.0) == 2.0
    f("http://x/2"); assert b.interval("x", 1.0) == 4.0
    f("http://x/3"); assert b.interval("x", 1.0) == 4.0     # 404 treibt NICHT hoch
    f("http://x/4"); assert b.interval("x", 1.0) == 2.0
    assert f.throttled == 2


def test_fetcher_accepts_plain_dict_http(tmp_path):
    """Altes Callable-Format (dict|None statt Tupel) bleibt gültig."""
    f = netfetch.Fetcher(http=lambda url, ua, headers=None: {"u": url})
    assert f("http://x/a") == {"u": "http://x/a"}


def test_floor_never_below_operator_policy():
    f = netfetch.Fetcher(intervals={"musicbrainz.org": 0.1})   # Override zu schnell
    assert f.floor_for("musicbrainz.org") == 1.1               # Policy gewinnt
    assert f.floor_for("query.wikidata.org") == 1.1
    assert f.floor_for("api.discogs.com") == 2.5


def test_fetcher_sends_auth_only_to_matching_host(tmp_path):
    seen = {}

    def fake_http(url, ua, headers=None):
        seen[url] = headers
        return {"u": url}

    con = cache.open_cache(tmp_path / "c.db")
    f = netfetch.Fetcher(cache_con=con, http=fake_http,
                         auth={"api.discogs.com": {"Authorization": "Discogs key=K, secret=S"}})
    f("https://api.discogs.com/releases/1")
    f("https://musicbrainz.org/ws/2/recording")
    assert seen["https://api.discogs.com/releases/1"] == {"Authorization": "Discogs key=K, secret=S"}
    assert seen["https://musicbrainz.org/ws/2/recording"] is None   # Auth NUR an Discogs
    con.close()


class _Clock:
    """Simulierte Zeit: jeder HTTP-Abruf kostet `cost` Sekunden (Timeout/503-Retry), Warten kostet nichts."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_fetcher_gives_up_on_permanent_failure_within_budget(tmp_path):
    """Dauerfehler (503 in Serie, Timeouts) -> nach spätestens etwa 60 s kein Netz mehr in dieser
    Auflösung, Rest kommt sofort als Fehlschlag (failures zählt), statt sich 9,5 min durch jede Quelle zu warten."""
    clock = _Clock()
    calls = []

    def dead_http(url, ua, headers=None):
        calls.append(url)
        clock.now += 15.0                                   # ein Timeout
        return None, False
    f = netfetch.Fetcher(http=dead_http, clock=clock)
    start = clock.now
    for i in range(30):
        assert f(f"https://musicbrainz.org/ws/2/x{i}") is None
    assert f.failures == 30                                  # jede ausgefallene Abfrage zählt
    assert len(calls) <= 4 and f.gave_up is True
    assert clock.now - start <= 60.0


def test_fetcher_budget_never_shortens_the_rate_limit(tmp_path):
    """Abgebrochen wird VOR dem Warten: ein Abruf, der erst nach dem Budget dran wäre, geht gar nicht raus —
    keiner geht früher raus, als der Limiter erlaubt."""
    clock = _Clock()
    waits = []

    class Limiter:
        def wait(self, host, interval):
            waits.append(interval)
            clock.now += interval

    def throttled(url, ua, headers=None):
        clock.now += 1.0
        return None, True                                    # 503: Backoff verdoppelt
    f = netfetch.Fetcher(http=throttled, clock=clock, limiter=Limiter(), backoff=netfetch.HostBackoff())
    start = clock.now
    for i in range(20):
        f(f"https://musicbrainz.org/ws/2/x{i}")
    assert waits == sorted(waits) and waits[0] == 1.1         # Wartezeiten wachsen mit dem Backoff, nie kürzer
    assert clock.now - start <= netfetch.FAIL_BUDGET + 1.0
    assert f.failures == 20 and f.gave_up is True


def test_fetcher_keeps_going_while_successes_come_in(tmp_path):
    """Einzelne Fehlschläge (404, ein Timeout) in einer langen, sonst gesunden Auflösung brechen nichts ab:
    das Budget zählt ab dem letzten Erfolg."""
    clock = _Clock()

    def mixed(url, ua, headers=None):
        clock.now += 10.0
        return (None, False) if url.endswith("bad") else ({"ok": 1}, False)
    f = netfetch.Fetcher(http=mixed, clock=clock)
    for i in range(20):
        f(f"https://musicbrainz.org/ws/2/{i}/ok")
        f(f"https://musicbrainz.org/ws/2/{i}/bad")
    assert f.calls == 40 and f.failures == 20 and f.gave_up is False     # 400 s simuliert, nie abgebrochen


def test_fetcher_gave_up_still_serves_stale_cache(tmp_path):
    con = cache.open_cache(tmp_path / "c.db")
    cache.put(con, "http", "https://musicbrainz.org/old", '{"alt": 1}')
    _age_entry(con, "https://musicbrainz.org/old", 10 * 24 * 3600)
    clock = _Clock()

    def dead_http(url, ua, headers=None):
        clock.now += 15.0
        return None, False
    f = netfetch.Fetcher(cache_con=con, http=dead_http, clock=clock)
    for i in range(5):
        f(f"https://musicbrainz.org/x{i}")
    assert f.gave_up is True
    n = f.calls
    assert f("https://musicbrainz.org/old") == {"alt": 1}            # veraltet ist besser als leer
    assert f.calls == n and f.stale_hits == 1
    con.close()
