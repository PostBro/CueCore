"""Track-Metadata-API-Client (core/trackmeta.py) — netzfrei über injiziertes http/now."""
import io
import json
import urllib.error
from email.message import Message

from core import trackmeta as tm
from core.trackmeta import Resp

P = tm.load_profile()          # erfundenes Profil aus conftest.py
F = P["fields"]


class FakeHttp:
    """Zeichnet Aufrufe auf, antwortet nach Skript (Resp oder callable(call)->Resp)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, form=None):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body, "form": form})
        assert self.responses, f"unerwarteter Aufruf: {method} {url}"
        r = self.responses.pop(0)
        return r(self.calls[-1]) if callable(r) else r


def _token_ok(tok="tok1", ttl=3600):
    return Resp(ok=True, status=200, data={"token_type": "Bearer", "expires_in": ttl, "access_token": tok})


def _meta_ok(data):
    return Resp(ok=True, status=200, data=data)


def _client(responses, clock=None):
    clock = clock if clock is not None else {"t": 1000.0}
    http = FakeHttp(responses)
    return tm.Client("api.example.com", "test-client", "SECRET", http=http, now=lambda: clock["t"]), http, clock


EXAMPLE = {
    F["titel"]: "Nordwand Eins", F["interpret"]: "Erika Testerin / Max Beispiel", F["komponist"]: "Johannes Beispiel",
    F["texter"]: "Anna Muster", F["labelname"]: "Testlabel Records", F["labelcode"]: "LC-54321", F["album"]: "Testalbum",
    F["tracknummer"]: 7, F["isrc"]: "ZZ-ZQ1-26-00001",
}


# --- URL / Konfiguration ---

def test_base_url_tolerates_schema_slash_and_full_path():
    assert tm.base_url("api.example.com", P) == "https://api.example.com/zzq/v1"
    assert tm.base_url("https://api.example.com/", P) == "https://api.example.com/zzq/v1"
    assert tm.base_url("https://api.example.com/zzq/v1", P) == "https://api.example.com/zzq/v1"
    assert tm.base_url("  ", P) == ""
    assert tm.base_url("api.example.com", None) == ""


def test_profile_comes_from_bundle_and_broken_profiles_are_rejected(tmp_path, monkeypatch):
    raw = tm.os.environ[tm.PROFILE_ENV]
    f = tmp_path / "builtin_keys.bin"
    f.write_bytes(tm.settings.encode_builtin({"trackmeta_profile": raw}))
    assert b"/zzq/" not in f.read_bytes()                                  # nicht im Klartext im Bundle
    monkeypatch.delenv(tm.PROFILE_ENV)
    monkeypatch.setattr(tm.settings, "BUILTIN_PATH", f)
    assert tm.load_profile() == P
    monkeypatch.setattr(tm.settings, "BUILTIN_PATH", tmp_path / "fehlt.bin")
    assert tm.load_profile() is None
    assert not tm.Client("h", "1", "s").configured()                      # ohne Profil ist die Funktion aus
    assert tm.parse_profile("kein json") is None
    assert tm.parse_profile({**P, "fields": {**F, "isrc": ""}}) is None
    assert tm.parse_profile({**P, "batch": 0}) is None


def test_client_configured_needs_all_three():
    assert tm.Client("h", "1", "s").configured()
    assert not tm.Client("", "1", "s").configured()
    assert not tm.Client("h", "", "s").configured()
    assert not tm.Client("h", "1", " ").configured()


# --- Token ---

def test_token_request_is_form_encoded_client_credentials():
    c, http, _ = _client([_token_ok()])
    r = c.token()
    assert r.ok and r.data == {"access_token": "tok1", "cached": False}
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://api.example.com/zzq/v1/zzq/token"
    assert call["form"] == {"grant_type": "client_credentials", "client_id": "test-client",
                            "client_secret": "SECRET", "scope": "zzq-scope"}
    assert call["body"] is None


def test_token_is_cached_and_refreshed_shortly_before_expiry():
    c, http, clock = _client([_token_ok("tok1", ttl=3600), _token_ok("tok2", ttl=3600)])
    assert c.token().data["access_token"] == "tok1"
    assert c.token().data == {"access_token": "tok1", "cached": True}      # kein zweiter Aufruf
    clock["t"] = 1000.0 + 3600 - tm.TOKEN_MARGIN_SEC - 1                  # knapp VOR der Marge
    assert c.token().data["cached"] is True
    clock["t"] = 1000.0 + 3600 - tm.TOKEN_MARGIN_SEC                      # Marge erreicht -> neu
    assert c.token().data == {"access_token": "tok2", "cached": False}
    assert len(http.calls) == 2


def test_token_missing_expires_in_falls_back_to_default_ttl():
    c, _, clock = _client([Resp(ok=True, status=200, data={"access_token": "t"})])
    c.token()
    assert c._expires_at == clock["t"] + P["ttl"]


def test_token_error_surfaces_oauth_code_and_is_not_cached():
    c, http, _ = _client([Resp(ok=False, status=401, error="invalid_client"), _token_ok("tok9")])
    r = c.token()
    assert r.ok is False and r.error == "invalid_client"
    assert c.token().data["access_token"] == "tok9"                        # neuer Versuch, kein Cache
    assert len(http.calls) == 2


def test_token_without_access_token_field_is_error():
    c, _, _ = _client([Resp(ok=True, status=200, data={"token_type": "Bearer"})])
    assert c.token().error == "no_token"


# --- Metadaten ---

def test_lookup_batch_sends_bearer_and_json_tracks():
    c, http, _ = _client([_token_ok(), _meta_ok({"a.mp3": None})])
    r = c.lookup_batch(["a.mp3"])
    assert r.ok
    call = http.calls[1]
    assert call["url"] == "https://api.example.com/zzq/v1/zzq/lookup"
    assert call["headers"] == {"Authorization": "Bearer tok1"}
    assert call["body"] == {"names": ["a.mp3"]} and call["form"] is None


def test_lookup_batch_rejects_empty_and_oversized():
    c, http, _ = _client([])
    assert c.lookup_batch([]).error == "bad_batch"
    assert c.lookup_batch(["x"] * (P["batch"] + 1)).error == "bad_batch"
    assert http.calls == []


def test_lookup_batch_renews_token_once_on_401():
    c, http, _ = _client([_token_ok("tok1"), Resp(ok=False, status=401, error="auth"),
                          _token_ok("tok2"), _meta_ok({"a.mp3": None})])
    r = c.lookup_batch(["a.mp3"])
    assert r.ok
    assert [x["url"].rsplit("/", 1)[-1] for x in http.calls] == ["token", "lookup", "token", "lookup"]
    assert http.calls[3]["headers"]["Authorization"] == "Bearer tok2"


def test_lookup_batch_second_401_gives_up():
    c, http, _ = _client([_token_ok("tok1"), Resp(ok=False, status=401, error="auth"),
                          _token_ok("tok2"), Resp(ok=False, status=401, error="auth")])
    r = c.lookup_batch(["a.mp3"])
    assert r.ok is False and r.error == "auth" and len(http.calls) == 4


def test_lookup_batch_token_failure_short_circuits():
    c, http, _ = _client([Resp(ok=False, status=400, error="invalid_scope")])
    r = c.lookup_batch(["a.mp3"])
    assert r.error == "invalid_scope" and len(http.calls) == 1


# --- Normalisierung ---

def test_normalize_result_maps_example_response():
    n = tm.normalize_result("ZQ_TEST-AL1_07-Nordwand_Eins.mp3", EXAMPLE, F)
    assert n["matched"] is True and n["source"] == "trackmeta"
    assert n["fileName"] == n["query_filename"] == "ZQ_TEST-AL1_07-Nordwand_Eins.mp3"
    assert n["titel"] == "Nordwand Eins" and n["interpret"] == "Erika Testerin / Max Beispiel"
    assert n["komponist"] == "Johannes Beispiel" and n["texter"] == "Anna Muster"
    assert n["labelname"] == "Testlabel Records" and n["labelcode_raw"] == "54321"      # 'LC-54321' -> Ziffern
    assert n["album"] == "Testalbum" and n["tracknummer"] == 7 and n["isrc"] == "ZZ-ZQ1-26-00001"


def test_normalize_result_joins_multiple_writers_with_semicolon():
    n = tm.normalize_result("x", {**EXAMPLE, F["komponist"]: "A One / B Two / A One", F["texter"]: " C Three "}, F)
    assert n["komponist"] == "A One; B Two" and n["texter"] == "C Three"


def test_normalize_result_null_and_nullable_fields():
    n = tm.normalize_result("nix.mp3", None, F)
    assert n["matched"] is False and n["labelcode_raw"] == "" and n["tracknummer"] is None
    n2 = tm.normalize_result("x", {**EXAMPLE, F["labelcode"]: None, F["isrc"]: None, F["tracknummer"]: "7"}, F)
    assert n2["labelcode_raw"] == "" and n2["isrc"] == "" and n2["tracknummer"] == 7 and n2["matched"]
    n3 = tm.normalize_result("x", {F["titel"]: "", F["labelname"]: "", F["labelcode"]: None}, F)
    assert n3["matched"] is False


def test_normalize_result_keeps_six_digit_lc():
    assert tm.normalize_result("x", {**EXAMPLE, F["labelcode"]: "LC-101303"}, F)["labelcode_raw"] == "101303"
    assert tm.normalize_result("x", {**EXAMPLE, F["labelcode"]: "LC 309"}, F)["labelcode_raw"] == "00309"


def test_map_batch_keyed_then_positional_then_none():
    chunk = ["a.mp3", "b.mp3"]
    keyed = tm._map_batch(chunk, {"b.mp3": EXAMPLE, "a.mp3": None}, F)
    assert [e["matched"] for e in keyed] == [False, True]                    # per Key, nicht per Position
    positional = tm._map_batch(chunk, {"A.mp3": None, "B.mp3": EXAMPLE}, F)  # Server normalisiert Namen
    assert [e["matched"] for e in positional] == [False, True]
    assert positional[1]["query_filename"] == "b.mp3"
    mismatch = tm._map_batch(chunk, {"zzz": EXAMPLE}, F)
    assert [e["matched"] for e in mismatch] == [False, False]


# --- lookup: Batching (Batch-Größe aus dem Profil, hier 10) und Fehlerdurchreichung ---

def test_lookup_batches_by_profile_size_dedupes_and_keeps_order():
    names = [f"t{i}.mp3" for i in range(24)] + ["t3.mp3", " t7.mp3 ", "", None]
    c, http, _ = _client([_token_ok(), lambda call: _meta_ok({n: (EXAMPLE if n == "t0.mp3" else None) for n in call["body"]["names"]}),
                          lambda call: _meta_ok({n: None for n in call["body"]["names"]}),
                          lambda call: _meta_ok({n: None for n in call["body"]["names"]})])
    res = tm.lookup(names, client=c)
    assert res["ok"] and res["requests"] == 3 and res["matched"] == 1
    sizes = [len(x["body"]["names"]) for x in http.calls if x["body"]]
    assert sizes == [10, 10, 4]
    assert [e["query_filename"] for e in res["entries"]][:3] == ["t0.mp3", "t1.mp3", "t2.mp3"]
    assert len(res["entries"]) == 24
    assert len([x for x in http.calls if x["form"]]) == 1                    # EIN Token für alle Batches


def test_lookup_error_aborts_and_keeps_earlier_entries():
    names = [f"t{i}.mp3" for i in range(12)]
    c, _, _ = _client([_token_ok(), lambda call: _meta_ok({n: None for n in call["body"]["names"]}),
                       Resp(ok=False, status=429, error="rate_limited")])
    res = tm.lookup(names, client=c)
    assert res["ok"] is False and res["error"] == "rate_limited" and res["status"] == 429
    assert len(res["entries"]) == 10 and res["requests"] == 2


def test_lookup_no_files_and_not_configured():
    c, http, _ = _client([])
    assert tm.lookup(["", "  "], client=c)["error"] == "no_files"
    unconfigured = tm.Client("", "", "", http=http)
    assert tm.lookup(["a.mp3"], client=unconfigured)["error"] == "not_configured"
    assert http.calls == []


# --- http_request: Fehler-Mapping und 429-Retry ---

def _http_error(code, body=None, retry_after=None):
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    fp = io.BytesIO(json.dumps(body).encode() if body is not None else b"")
    return urllib.error.HTTPError("https://x", code, "err", hdrs, fp)


class _FakeResponse:
    status = 200

    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_request_retries_429_with_retry_after_then_succeeds(monkeypatch):
    seq = [_http_error(429, retry_after=3), _http_error(429), _FakeResponse({"ok": 1})]
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req)
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(tm.urllib.request, "urlopen", fake_urlopen)
    sleeps = []
    r = tm.http_request("POST", "https://x/y", body={"names": ["a"]}, sleep=sleeps.append)
    assert r.ok and r.data == {"ok": 1} and len(seen) == 3
    assert sleeps == [3.0, 4.0]                                              # Retry-After, sonst 2*(attempt+1)
    assert seen[0].get_header("Content-type") == "application/json"          # Pflicht-Header
    assert json.loads(seen[0].data) == {"names": ["a"]}


def test_http_request_form_body_and_oauth_error_code(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout=0):
        seen.append(req)
        raise _http_error(400, body={"error": "invalid_scope", "message": "nope"})
    monkeypatch.setattr(tm.urllib.request, "urlopen", fake_urlopen)
    r = tm.http_request("POST", "https://x/token", form={"grant_type": "client_credentials"}, sleep=lambda s: None)
    assert r.ok is False and r.status == 400 and r.error == "invalid_scope"
    assert seen[0].get_header("Content-type") == "application/x-www-form-urlencoded"
    assert seen[0].data == b"grant_type=client_credentials"


def test_http_request_maps_status_without_body(monkeypatch):
    monkeypatch.setattr(tm.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(_http_error(403)))
    assert tm.http_request("POST", "https://x", body={}, sleep=lambda s: None).error == "forbidden"
    monkeypatch.setattr(tm.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(_http_error(422)))
    assert tm.http_request("POST", "https://x", body={}, sleep=lambda s: None).error == "bad_request"


def test_http_request_429_exhausted_reports_rate_limited(monkeypatch):
    monkeypatch.setattr(tm.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(_http_error(429)))
    r = tm.http_request("POST", "https://x", body={}, retries=2, sleep=lambda s: None)
    assert r.error == "rate_limited" and r.status == 429


def test_retry_after_is_capped_at_the_minute_window():
    assert tm._retry_after(_http_error(429, retry_after=600), 0) == 60.0
    assert tm._retry_after(_http_error(429, retry_after="bogus"), 2) == 6.0
