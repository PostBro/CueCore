import io
import json
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import run_cuecore


def _serve(payload: dict | None):
    """Mini-HTTP-Server auf einem freien Port. payload=None -> 404 auf /api/health
    (simuliert einen FREMDEN Dienst, der zufällig auf dem Port lauscht)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if payload is None or self.path != "/api/health":
                self.send_response(404); self.end_headers(); return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _prebuilt(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE labels(x)")
    con.execute("INSERT INTO labels VALUES (1)")
    con.commit(); con.close()


def test_ensure_labels_db_copies_prebuilt(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"; (bundle / "data").mkdir(parents=True)
    user = tmp_path / "user"
    _prebuilt(bundle / "data" / "labels_prebuilt.db")
    monkeypatch.setattr(run_cuecore, "_bundle_base", lambda: bundle)
    monkeypatch.setattr(run_cuecore, "_user_data_dir", lambda: user)

    run_cuecore.ensure_labels_db()
    out = user / "labels.db"
    assert out.exists()   # kopiert, nicht aus CSV gebaut
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1


def test_ensure_labels_db_keeps_existing(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"; (bundle / "data").mkdir(parents=True)
    user = tmp_path / "user"; user.mkdir()
    (user / "labels.db").write_text("existing", encoding="utf-8")
    _prebuilt(bundle / "data" / "labels_prebuilt.db")   # vorhanden, darf aber nicht überschreiben
    monkeypatch.setattr(run_cuecore, "_bundle_base", lambda: bundle)
    monkeypatch.setattr(run_cuecore, "_user_data_dir", lambda: user)

    run_cuecore.ensure_labels_db()
    assert (user / "labels.db").read_text(encoding="utf-8") == "existing"


def test_running_instance_false_when_port_closed():
    assert run_cuecore.running_instance("127.0.0.1", _free_port()) is False


def test_running_instance_true_for_cuecore():
    srv = _serve({"product": "CueCore", "ready": True})
    try:
        assert run_cuecore.running_instance("127.0.0.1", srv.server_address[1]) is True
    finally:
        srv.shutdown()


def test_running_instance_false_for_foreign_service():
    """Port belegt, aber /api/health antwortet nicht wie CueCore -> normal weiterstarten
    (sonst gibt der Launcher bei fremdem Dienst auf 8765 kommentarlos auf)."""
    srv = _serve(None)
    try:
        assert run_cuecore.running_instance("127.0.0.1", srv.server_address[1]) is False
    finally:
        srv.shutdown()

    srv2 = _serve({"product": "SomethingElse"})
    try:
        assert run_cuecore.running_instance("127.0.0.1", srv2.server_address[1]) is False
    finally:
        srv2.shutdown()


def test_main_opens_browser_and_returns_when_instance_running(monkeypatch):
    """Zweiter Doppelklick: kein zweites uvicorn (stirbt sonst an address-in-use,
    im fensterlosen Build kommentarlos)."""
    opened = []
    monkeypatch.setenv("CUECORE_WINDOW", "0")          # Browser-Modus (Fenster-Modus: eigener Test unten)
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: True)
    monkeypatch.setattr(run_cuecore.webbrowser, "open", opened.append)

    def boom():
        raise AssertionError("darf bei laufender Instanz nicht aufgerufen werden")

    monkeypatch.setattr(run_cuecore, "ensure_labels_db", boom)
    run_cuecore.main()
    assert opened == [f"http://{run_cuecore.HOST}:{run_cuecore.PORT}"]


def test_ensure_std_streams_replaces_none(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    run_cuecore._ensure_std_streams()
    assert sys.stdout is not None and sys.stderr is not None
    # devnull ist kein TTY -> uvicorns Formatter (sys.stdout.isatty()) crasht nicht
    assert sys.stdout.isatty() is False
    assert sys.stderr.isatty() is False


def test_ensure_std_streams_keeps_real_stream(monkeypatch):
    real = io.StringIO()
    monkeypatch.setattr(sys, "stdout", real)
    run_cuecore._ensure_std_streams()
    assert sys.stdout is real   # vorhandenen Stream nicht ersetzen (Konsolen-Build)


def test_uvicorn_logging_survives_windowed_streams(monkeypatch):
    """Regression: der fensterlose Build (console=False) hat unter Windows
    sys.stdout=None → uvicorn crasht beim Start mit
    'ValueError: Unable to configure formatter default'. Guard verhindert das."""
    import uvicorn

    # Ohne Guard: None-Stream reproduziert den echten Crash.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    with pytest.raises(ValueError):
        uvicorn.Config("app:app", host="127.0.0.1", port=0)

    # Mit Guard: gleiche Logging-Config baut sauber durch.
    run_cuecore._ensure_std_streams()
    uvicorn.Config("app:app", host="127.0.0.1", port=0)


# ---- App-Fenster (pywebview) ---------------------------------------------------------------

class _Ev:
    """Mini-Nachbau von pywebview.Event: `+=` sammelt Handler, fire() ruft sie."""
    def __init__(self):
        self.h = []

    def __iadd__(self, fn):
        self.h.append(fn); return self

    def fire(self, *a):
        for fn in self.h:
            fn(*a)


class _Elem:
    def __init__(self):
        self.events = type("E", (), {})(); self.events.drop = _Ev()


class _Win:
    def __init__(self, elem):
        self.events = type("E", (), {})(); self.events.loaded = _Ev(); self.events.closed = _Ev()
        self.dom = type("D", (), {})(); self.dom.get_element = lambda sel: elem
        self.js = []
        self.evaluate_js = self.js.append
        self.get_current_url = lambda: "http://127.0.0.1:8765/"


def test_use_window_off_by_env(monkeypatch):
    monkeypatch.setenv("CUECORE_WINDOW", "0")
    assert run_cuecore.use_window() is False


def test_use_window_false_without_pywebview(monkeypatch):
    monkeypatch.delenv("CUECORE_WINDOW", raising=False)
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "webview":
            raise ImportError("kein pywebview")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert run_cuecore.use_window() is False


def test_drop_path_takes_first_full_path():
    ev = {"dataTransfer": {"files": [{"name": "a.mp3"}, {"name": "b", "pywebviewFullPath": "/Musik/Ordner"}]}}
    assert run_cuecore.drop_path(ev) == "/Musik/Ordner"
    assert run_cuecore.drop_path({}) == ""
    assert run_cuecore.drop_path({"dataTransfer": None}) == ""
    assert run_cuecore.drop_path({"dataTransfer": {"files": [{"name": "x"}]}}) == ""


def test_attach_drop_wires_zone_after_load_and_hands_path_to_page():
    """Drop auf #tdrop: Python bekommt den vollen Pfad (nur dort gibt es pywebviewFullPath) und
    reicht ihn als JSON an window.cuecoreDropPath — ein Drop ohne Pfad löst nichts aus."""
    pytest.importorskip("webview")
    elem = _Elem(); win = _Win(elem)
    run_cuecore.attach_drop(win)
    assert elem.events.drop.h == []                  # erst nach dem Laden der Seite
    win.events.loaded.fire()
    assert len(elem.events.drop.h) == 1
    h = elem.events.drop.h[0]
    assert h.prevent_default is True                 # der Browser darf die Datei nicht selbst öffnen
    handler = h.callback
    handler({"dataTransfer": {"files": [{"name": "x.wav"}]}})
    assert win.js == []
    handler({"dataTransfer": {"files": [{"pywebviewFullPath": "/Volumes/Musik/Neu \"quoted\""}]}})
    assert win.js == ['window.cuecoreDropPath("/Volumes/Musik/Neu \\"quoted\\"", false)']   # isDir aus Python (Pfad existiert nicht)


def test_main_window_mode_reuses_running_instance(monkeypatch):
    """Zweiter Start im Fenster-Modus: Fenster auf die laufende Instanz, kein zweiter Server, kein Browser."""
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: True)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    calls = []
    monkeypatch.setattr(run_cuecore, "run_window", lambda server=None: calls.append(server))
    monkeypatch.setattr(run_cuecore.webbrowser, "open", lambda *a: calls.append("browser"))
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: calls.append("db"))
    run_cuecore.main()
    assert calls == [None]


def test_main_window_mode_stops_server_when_window_closes(monkeypatch):
    """Fenster zu = Server aus: main() startet uvicorn im Thread, wartet auf Health, öffnet das Fenster
    und setzt should_exit, sobald run_window zurückkommt."""
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: False)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: None)
    monkeypatch.setattr(run_cuecore, "wait_for_server", lambda **k: True)
    seen = {}

    class FakeServer:
        def __init__(self, config): self.should_exit = False; seen["config"] = config
        def run(self): pass

    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(run_cuecore, "run_window_booting", lambda boot: seen.setdefault("win", boot()))
    run_cuecore.main()
    assert isinstance(seen["win"], FakeServer) and seen["win"].should_exit is True
    assert seen["config"].port == run_cuecore.PORT


def test_main_falls_back_to_browser_when_window_fails(monkeypatch):
    """Windows ohne WebView2 (oder kaputtes Backend): run_window wirft -> Browser-Tab, Server läuft weiter,
    kein stiller Tod des fensterlosen Builds."""
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: False)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: None)
    monkeypatch.setattr(run_cuecore, "wait_for_server", lambda **k: True)
    opened = []
    monkeypatch.setattr(run_cuecore.webbrowser, "open", opened.append)

    class FakeServer:
        def __init__(self, config): self.should_exit = False
        def run(self): pass

    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    def boom(boot):
        raise RuntimeError("WebView2 runtime not found")
    monkeypatch.setattr(run_cuecore, "run_window_booting", boom)
    run_cuecore.main()
    assert opened == [run_cuecore.URL]


def test_running_instance_window_failure_opens_browser(monkeypatch):
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: True)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    opened = []
    monkeypatch.setattr(run_cuecore.webbrowser, "open", opened.append)
    monkeypatch.setattr(run_cuecore, "run_window", lambda server=None: (_ for _ in ()).throw(RuntimeError("x")))
    run_cuecore.main()
    assert opened == [run_cuecore.URL]


def test_frozen_build_logs_to_data_dir(tmp_path, monkeypatch):
    """Gebündelte App: stdout/stderr landen in <Datenordner>/cuecore.log (sonst sieht man bei Fehlern nichts)."""
    monkeypatch.setenv("CUECORE_DB", str(tmp_path / "labels.db"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    old_out, old_err = sys.stdout, sys.stderr
    try:
        run_cuecore._ensure_std_streams()
        print("hallo", flush=True)
        assert sys.stdout is sys.stderr and sys.stdout is not old_out
    finally:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.stdout, sys.stderr = old_out, old_err
    text = (tmp_path / "cuecore.log").read_text(encoding="utf-8")
    assert "=== CueCore Start" in text and "hallo" in text


def test_log_rotates_over_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CUECORE_DB", str(tmp_path / "labels.db"))
    big = tmp_path / "cuecore.log"
    big.write_bytes(b"x" * (run_cuecore.LOG_MAX_BYTES + 1))
    f = run_cuecore._open_log(); f.close()
    assert (tmp_path / "cuecore.log.1").stat().st_size > run_cuecore.LOG_MAX_BYTES
    assert (tmp_path / "cuecore.log").stat().st_size < 500


def test_main_switches_port_when_foreign_service_holds_it(monkeypatch):
    """Fremder Dienst auf 8765: kein „address already in use"-Tod, sondern freier Port (steht im Log)."""
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: False)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: False)
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: None)
    monkeypatch.setattr(run_cuecore, "port_is_free", lambda *a, **k: False)
    monkeypatch.setattr(run_cuecore, "pick_free_port", lambda *a, **k: 18765)
    monkeypatch.setattr(run_cuecore.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda s: None})())
    seen = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    run_cuecore.main()
    assert seen["port"] == 18765 and run_cuecore.URL.endswith(":18765")
    run_cuecore._set_port(int(__import__("os").environ.get("CUECORE_PORT", "8765")))


def test_main_shows_error_window_when_server_never_answers(monkeypatch):
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: False)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: None)
    monkeypatch.setattr(run_cuecore, "port_is_free", lambda *a, **k: True)
    monkeypatch.setattr(run_cuecore, "wait_for_server", lambda **k: False)
    win = _FakeWin()
    monkeypatch.setattr(run_cuecore, "run_window_booting", lambda boot: run_cuecore._boot_into(win, boot))

    class FakeServer:
        def __init__(self, config): self.should_exit = False
        def run(self): pass
    import uvicorn
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    run_cuecore.main()
    assert win.url is None and "cuecore.log" in win.html          # Grund im schon offenen Fenster, keine App


def test_running_instance_uses_current_port_after_set_port(monkeypatch):
    """Defaults wurden zur Definitionszeit gebunden — nach _set_port pollte wait_for_server weiter 8765."""
    seen = []
    def fake_conn(addr, timeout):
        seen.append(addr); raise OSError
    monkeypatch.setattr(run_cuecore.socket, "create_connection", fake_conn)
    old = run_cuecore.PORT
    try:
        run_cuecore._set_port(19999)
        assert run_cuecore.running_instance() is False
        assert seen == [(run_cuecore.HOST, 19999)]
    finally:
        run_cuecore._set_port(old)


def test_wait_for_server_returns_early_when_thread_is_dead(monkeypatch):
    monkeypatch.setattr(run_cuecore, "running_instance", lambda *a, **k: False)
    dead = type("T", (), {"is_alive": lambda s: False})()
    t0 = time.monotonic()
    assert run_cuecore.wait_for_server(timeout=5, thread=dead) is False
    assert time.monotonic() - t0 < 1


def test_ensure_labels_db_seeds_manual_labels_and_copies_atomically(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle" / "data"; bundle.mkdir(parents=True)
    _prebuilt(bundle / "labels_prebuilt.db")
    (bundle / "labels_manual.json").write_text('[{"labelcode":"104994","label":"Input/Output"}]', encoding="utf-8")
    monkeypatch.setattr(run_cuecore, "_bundle_base", lambda: tmp_path / "bundle")
    monkeypatch.setenv("CUECORE_DB", str(tmp_path / "user" / "labels.db"))
    run_cuecore.ensure_labels_db()
    assert (tmp_path / "user" / "labels.db").exists() and not (tmp_path / "user" / "labels.db.tmp").exists()
    assert "104994" in (tmp_path / "user" / "labels_manual.json").read_text(encoding="utf-8")


def test_shell_api_save_bytes_writes_file_and_handles_cancel(tmp_path):
    """Export in der Hülle: nativer Sichern-Dialog (create_file_dialog) statt <a download> im WKWebView."""
    pytest.importorskip("webview")
    import base64
    api = run_cuecore.ShellApi()
    target = tmp_path / "cue.csv"
    class W:
        def create_file_dialog(self, kind, **kw): self.kw = kw; return (str(target),)
    api._window = W()
    res = api.save_bytes("cue.csv", base64.b64encode(b"a;b\n1;2\n").decode())
    assert res == {"saved": True, "path": str(target)} and target.read_bytes() == b"a;b\n1;2\n"
    assert api._window.kw["save_filename"] == "cue.csv"
    class Cancel:
        def create_file_dialog(self, *a, **k): return None
    api._window = Cancel()
    assert api.save_bytes("x.csv", "") == {"saved": False, "cancelled": True}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="TIME_WAIT-Semantik von POSIX; Windows bindet wie uvicorn ohne SO_REUSEADDR")
def test_port_is_free_after_quick_restart_with_time_wait():
    """Nach dem Beenden bleiben Verbindungen in TIME_WAIT/FIN_WAIT. bind ohne SO_REUSEADDR scheitert dann,
    die App meldete „fremder Dienst“ und wich auf einen Zufallsport aus (neuer Origin = Sprache, Theme und
    „Zuletzt“ weg). uvicorn bindet mit SO_REUSEADDR — derselbe Test muss den Port dann frei nennen."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen()
    port = srv.getsockname()[1]
    cli = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()                   # Server schließt zuerst -> seine Seite geht in TIME_WAIT
    cli.close()
    srv.close()
    time.sleep(0.2)
    assert run_cuecore.port_is_free("127.0.0.1", port) is True


def test_port_is_free_false_while_someone_listens():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0)); srv.listen()
    try:
        assert run_cuecore.port_is_free("127.0.0.1", srv.getsockname()[1]) is False
    finally:
        srv.close()


def test_second_start_finds_instance_on_remembered_fallback_port(tmp_path, monkeypatch):
    """Standardport fremd belegt -> erste Instanz weicht aus. Ein zweiter Doppelklick prüfte nur den
    Standardport, wich ebenfalls aus und startete einen zweiten Server auf dieselben DBs. Der Ausweichport
    steht jetzt im Datenordner; der zweite Start findet die laufende Instanz dort und öffnet nur sie."""
    monkeypatch.setenv("CUECORE_DB", str(tmp_path / "labels.db"))
    monkeypatch.setenv("CUECORE_WINDOW", "0")
    foreign = _serve(None)                                        # fremder Dienst auf dem Standardport
    first = _serve({"product": "CueCore", "ready": True})         # erste Instanz, auf den Ausweichport gewichen
    old = run_cuecore.PORT

    class Stop(Exception):
        pass

    def stop():
        raise Stop
    try:
        run_cuecore._set_port(foreign.server_address[1])
        monkeypatch.setattr(run_cuecore, "pick_free_port", lambda *a, **k: first.server_address[1])
        monkeypatch.setattr(run_cuecore, "ensure_labels_db", stop)
        with pytest.raises(Stop):                                 # erster Start bis kurz vor dem Server
            run_cuecore.main()
        run_cuecore._set_port(foreign.server_address[1])          # zweiter Start: frischer Prozess, Standardport
        opened = []
        monkeypatch.setattr(run_cuecore.webbrowser, "open", opened.append)
        monkeypatch.setattr(run_cuecore, "ensure_labels_db",
                            lambda: (_ for _ in ()).throw(AssertionError("zweiter Server gestartet")))
        run_cuecore.main()
        assert opened == [f"http://127.0.0.1:{first.server_address[1]}"]
    finally:
        run_cuecore._set_port(old)
        foreign.shutdown(); first.shutdown()


def test_shell_api_open_url_only_http_and_reports_failure(monkeypatch):
    """open_url: nur http/https, Ergebnis ehrlich zurück — auch wenn der
    Systembrowser scheitert oder wirft (dann zeigt die Seite die Adresse zum Kopieren statt still nichts)."""
    import webbrowser
    api = run_cuecore.ShellApi()
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    assert api.open_url("https://acoustid.org/new-application") == {"opened": True}
    assert api.open_url("file:///etc/passwd")["opened"] is False
    assert api.open_url("javascript:alert(1)")["opened"] is False
    assert opened == ["https://acoustid.org/new-application"]
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    assert api.open_url("https://example.org")["opened"] is False

    def boom(url):
        raise webbrowser.Error("could not locate runnable browser")
    monkeypatch.setattr(webbrowser, "open", boom)
    res = api.open_url("https://example.org")
    assert res["opened"] is False and "runnable browser" in res["error"]



# --- Doppelstart: der zweite Start kam in die Selbstheilung des ersten ---

class _FakeEvents:
    def __init__(self): self.closed = _Hook()


class _Hook(list):
    def __iadd__(self, fn): self.append(fn); return self


class _FakeWin:
    def __init__(self): self.html = ""; self.url = None; self.events = _FakeEvents()
    def load_html(self, html): self.html = html
    def load_url(self, url): self.url = url


def test_instance_lock_is_exclusive_and_released(tmp_path):
    a = run_cuecore.acquire_instance_lock(tmp_path)
    assert a is not None
    assert run_cuecore.acquire_instance_lock(tmp_path) is None       # zweiter Start: Sperre belegt
    run_cuecore.release_instance_lock(a)
    b = run_cuecore.acquire_instance_lock(tmp_path)
    assert b is not None
    run_cuecore.release_instance_lock(b)


def test_second_start_waits_for_starting_instance_then_attaches(monkeypatch):
    """Die erste Instanz richtet noch die Datenbanken ein (Server antwortet noch nicht, Sperre belegt): der
    zweite Start fasst keine DB an, wartet und öffnet dann ein Fenster auf die laufende Instanz."""
    monkeypatch.setattr(run_cuecore, "find_running_instance", lambda: False)
    monkeypatch.setattr(run_cuecore, "acquire_instance_lock", lambda *a: None)
    monkeypatch.setattr(run_cuecore, "wait_for_instance", lambda **k: True)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    calls = []
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: calls.append("db"))
    monkeypatch.setattr(run_cuecore, "run_window", lambda server=None: calls.append(("window", server)))
    monkeypatch.setattr(run_cuecore, "run_window_booting", lambda boot: calls.append("eigener Start"))
    run_cuecore.main()
    assert calls == [("window", None)]


def test_second_start_explains_when_first_never_answers(monkeypatch):
    monkeypatch.setattr(run_cuecore, "find_running_instance", lambda: False)
    monkeypatch.setattr(run_cuecore, "acquire_instance_lock", lambda *a: None)
    monkeypatch.setattr(run_cuecore, "wait_for_instance", lambda **k: False)
    monkeypatch.setattr(run_cuecore, "use_window", lambda: True)
    monkeypatch.setattr(run_cuecore, "ensure_labels_db", lambda: (_ for _ in ()).throw(AssertionError("keine DB")))
    shown = []
    monkeypatch.setattr(run_cuecore, "show_error_window", lambda t, d: shown.append(t))
    run_cuecore.main()
    assert shown and "bereits" in shown[0]


def test_boot_into_loads_app_and_stops_server_on_close():
    class S: should_exit = False
    server, win = S(), _FakeWin()
    run_cuecore._boot_into(win, lambda: server)
    assert win.url == run_cuecore.URL
    for fn in win.events.closed: fn()
    assert server.should_exit is True


def test_boot_into_shows_reason_in_the_open_window():
    """Ein Fehler beim Einrichten landet im schon offenen Fenster (mit Logdatei), nicht in einem zweiten."""
    win = _FakeWin()
    def boot():
        raise RuntimeError("database is locked")
    run_cuecore._boot_into(win, boot)
    assert win.url is None and "database is locked" in win.html and "cuecore.log" in win.html


def test_starting_page_is_shown_immediately():
    assert "CueCore startet" in run_cuecore.STARTING_HTML
    assert "einige Minuten" in run_cuecore.STARTING_HTML and "keep the window open" in run_cuecore.STARTING_HTML


def test_shell_api_exposes_only_its_methods_to_pywebview():
    """pywebview geht bei jedem Seitenladen rekursiv durch alle öffentlichen Attribute
    von js_api. Über ShellApi.window lief es in den .NET-Objektbaum des Fensters (window.native.AccessibilityObject.
    Bounds.Empty…), schrieb Megabytes Fehler ins Log und blockierte den Server. Das Fenster ist jetzt _window."""
    api = run_cuecore.ShellApi()
    api._window = object()
    assert [n for n in dir(api) if not n.startswith("_")] == ["open_url", "save_bytes"]
