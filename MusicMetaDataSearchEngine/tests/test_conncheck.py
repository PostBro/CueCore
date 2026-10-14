"""Verbindungstests (core/conncheck.py) — netzfrei über injiziertes http/urlopen."""
import io
import urllib.error
from email.message import Message

from core import conncheck, trackmeta
from core.editingtools import Resp as ETResp


def _http_err(code):
    return urllib.error.HTTPError("https://x", code, "err", Message(), io.BytesIO(b"{}"))


def test_check_trackmeta_forces_fresh_token():
    calls = []
    def http(method, url, *, headers=None, body=None, form=None):
        calls.append(url); return trackmeta.Resp(ok=True, status=200, data={"access_token": "t", "expires_in": 900})
    c = trackmeta.Client("api.example.com", "test-client", "S", http=http)
    c.token()                                   # gecacht
    monkey = []
    import functools
    real_partial = functools.partial
    # Der Probe-Client nutzt einen eigenen http mit kurzem Timeout/ohne Retries — hier abfangen
    conncheck.functools.partial = lambda f, **kw: (monkey.append(kw), http)[1]
    try:
        r = conncheck.check_trackmeta(c)
    finally:
        conncheck.functools.partial = real_partial
    assert r["ok"] and len(calls) == 2 and "Token" in r["detail"]     # force=True -> zweiter Call
    assert monkey == [{"retries": 1, "timeout": conncheck.PROBE_TIMEOUT}]
    assert c.token().data["cached"] is True                              # Cache des Clients unberührt
    conncheck.functools.partial = lambda f, **kw: (lambda *a, **k: trackmeta.Resp(ok=False, status=401, error="invalid_client"))
    try:
        bad = trackmeta.Client("api.example.com", "test-client", "S")
        assert conncheck.check_trackmeta(bad) == {"ok": False, "status": 401, "error": "invalid_client", "detail": ""}
    finally:
        conncheck.functools.partial = real_partial
    assert conncheck.check_trackmeta(None)["error"] == "not_configured"
    assert conncheck.check_trackmeta(trackmeta.Client("", "", ""))["error"] == "not_configured"


def test_check_editingtools_404_means_key_accepted():
    seen = {}
    def http(method, url, *, headers=None, body=None, retries=3, timeout=30):
        seen.update(method=method, url=url, headers=headers); return ETResp(ok=False, status=404, error="not_found")
    r = conncheck.check_editingtools("KEY", http=http)
    assert r["ok"] and r["status"] == 404 and seen["method"] == "GET" and conncheck.ET_PROBE_ID in seen["url"]
    assert seen["headers"]["Authorization"].startswith("Basic ")
    assert conncheck.check_editingtools("KEY", http=lambda *a, **k: ETResp(ok=False, status=401, error="auth"))["error"] == "auth"
    assert conncheck.check_editingtools("KEY", http=lambda *a, **k: ETResp(ok=False, status=403, error="forbidden")) == {"ok": False, "status": 403, "error": "forbidden", "detail": ""}
    assert conncheck.check_editingtools("")["error"] == "not_configured"


class _Ok:
    status = 200
    def read(self): return b'{"results": []}'
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_check_discogs_token_and_key_secret():
    seen = []
    def urlopen(req, timeout=0):
        seen.append(req.get_header("Authorization")); return _Ok()
    r = conncheck.check_discogs("TOK", urlopen=urlopen)
    assert r["ok"] and "Personal Token" in r["detail"] and seen[-1] == "Discogs token=TOK"
    r = conncheck.check_discogs("", "K", "S", urlopen=urlopen)
    assert r["ok"] and "Consumer" in r["detail"] and seen[-1] == "Discogs key=K, secret=S"
    assert conncheck.check_discogs("", "", "")["error"] == "not_configured"


def test_check_discogs_maps_errors():
    def raise_(code):
        def urlopen(req, timeout=0): raise _http_err(code)
        return urlopen
    assert conncheck.check_discogs("T", urlopen=raise_(401))["error"] == "auth"
    assert conncheck.check_discogs("T", urlopen=raise_(429))["error"] == "rate_limited"
    assert conncheck.check_discogs("T", urlopen=raise_(500))["error"] == "http_500"
    def boom(req, timeout=0): raise OSError("down")
    assert conncheck.check_discogs("T", urlopen=boom)["error"] == "network:OSError"


def test_acoustid_trennt_application_key_vom_user_key():
    """Auf acoustid.org liegen zwei Schlüssel nebeneinander; der Lookup braucht den Application-Key.
    Mit dem User-Key kommt „invalid API key" — das muss der Test sagen, nicht stumm nichts finden."""
    assert conncheck.check_acoustid("")["error"] == "not_configured"
    ok = conncheck.check_acoustid("KEY", poster=lambda p: {"status": "ok", "results": []})
    assert ok["ok"] and "Application-Key" in ok["detail"]
    bad = conncheck.check_acoustid("KEY", poster=lambda p: {"status": "error",
                                                            "error": {"message": "invalid API key"}})
    assert not bad["ok"] and bad["error"] == "auth" and "new-application" in bad["detail"]
    net = conncheck.check_acoustid("KEY", poster=lambda p: None)
    assert not net["ok"] and net["error"] == "network"
    other = conncheck.check_acoustid("KEY", poster=lambda p: {"status": "error",
                                                              "error": {"message": "invalid duration"}})
    assert not other["ok"] and other["error"] == "http"


def test_acoustid_invalid_fingerprint_heisst_key_angenommen():
    """api.acoustid.org lehnt den Probe-Fingerabdruck ab („invalid fingerprint"), prüft den Schlüssel aber
    vorher; derselbe Aufruf mit dem User-Key ergibt „invalid API key". Ein gültiger Application-Key gilt
    deshalb als angenommen."""
    r = conncheck.check_acoustid("KEY", poster=lambda p: {"status": "error",
                                                          "error": {"message": "invalid fingerprint"}})
    assert r["ok"] and r["error"] == "" and "Application-Key" in r["detail"]
