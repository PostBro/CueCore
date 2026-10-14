#!/usr/bin/env python3
"""CueCore Standalone-Launcher (Einstiegspunkt für den PyInstaller-Build).

Legt beim ersten Start die GVL-Datenbank im beschreibbaren Nutzer-Datenordner an
(kein Admin, außerhalb des Programm-Bundles), startet den lokalen Server und öffnet das
App-Fenster (ohne pywebview oder mit CUECORE_WINDOW=0 den Browser-Tab). Fenster schließen = beenden.

Zwei Hüllen: (1) App-Fenster über pywebview (Default, wenn `webview` importierbar ist) —
eigenes Fenster, echter Ordner-Drop in der Tagger-Drop-Zone (der volle Pfad eines Drops
existiert nur auf der Python-Seite, pywebview 5+), Fenster schließen stoppt den Server;
(2) Browser-Tab wie bisher (CUECORE_WINDOW=0 oder pywebview fehlt).

Umgebungsvariablen (optional): CUECORE_PORT, CUECORE_HOST, CUECORE_DB, CUECORE_WINDOW=0.
"""
from __future__ import annotations
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = os.environ.get("CUECORE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CUECORE_PORT", "8765"))
URL = f"http://{HOST}:{PORT}"
LOG_MAX_BYTES = 2_000_000


def _set_port(port: int) -> None:
    global PORT, URL
    PORT = port
    URL = f"http://{HOST}:{PORT}"
WINDOW_BG = "#0B0B0D"          # = --bg des dunklen Themes: kein weißer Blitz vor dem ersten Paint


def _bundle_base() -> Path:
    """Ordner mit den gebündelten Read-only-Dateien (web/, data/)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(__file__).resolve().parent


def _user_data_dir() -> Path:
    if os.environ.get("CUECORE_DB"):
        return Path(os.environ["CUECORE_DB"]).parent
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CueCore"
    return Path.home() / "Library" / "Application Support" / "MusicMeta"


def ensure_labels_db() -> None:
    """GVL-Label-DB im Nutzerordner bereitstellen (einmalig).

    Bevorzugt die im Build gebündelte `labels_prebuilt.db` — nur kopieren, sofort
    fertig (der fensterlose Build hat kein Konsolenfenster für einen 'baue …'-Hinweis).
    Fehlt sie (dev-Lauf ohne prebuilt), aus der gebündelten CSV bauen.
    """
    import shutil
    out = _user_data_dir() / "labels.db"
    base = _bundle_base() / "data"
    manual_src, manual_dst = base / "labels_manual.json", out.parent / "labels_manual.json"
    if manual_src.exists() and not manual_dst.exists():
        # Fremde Codes einer Library-Lieferung, die build_bundle_data als manuelle Labels angelegt hat:
        # ohne diese Datei wären sie nach dem ersten GVL-PDF-Update des Nutzers weg.
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(manual_src, manual_dst)
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    prebuilt = base / "labels_prebuilt.db"
    if prebuilt.exists():
        tmp = out.with_suffix(".db.tmp")
        shutil.copy(prebuilt, tmp)         # Sofortstart: fertige DB nur kopieren — erst .tmp, dann
        os.replace(tmp, out)               # atomar umbenennen: eine abgebrochene Kopie bleibt nie als labels.db liegen
        return
    from core.labeldb import build_label_db
    from reconcile.csv_io import read_master_with_supplemental
    csv_path = base / "GVL_Labelcodes_synced.csv"
    meta_path = base / "GVL_Labelcodes_synced.meta.json"
    version = "unknown"
    if meta_path.exists():
        version = json.loads(meta_path.read_text(encoding="utf-8")).get("source_version", "unknown")
    print("Baue GVL-Datenbank (einmalig, kurz) …", flush=True)
    build_label_db(read_master_with_supplemental(csv_path), out, version)   # + labelcodes_supplemental.csv


def _open_browser() -> None:
    time.sleep(2.0)
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:
        pass


def running_instance(host: str | None = None, port: int | None = None, *, timeout: float = 0.5) -> bool:
    """Läuft auf dem Port bereits eine CueCore-Instanz?

    Zweiter Doppelklick startet sonst ein zweites uvicorn, das an „address already in
    use" stirbt — im fensterlosen Windows-Build kommentarlos, der Nutzer sieht nichts.

    Zwei Stufen: (1) Portprobe (billig, kein DNS/HTTP-Timeout wenn nichts lauscht),
    (2) ``/api/health`` mit ``product == "CueCore"``. Stufe 2 ist Pflicht — ein FREMDER
    Dienst auf 8765 würde uns sonst zum kommentarlosen Aufgeben bringen.

    Bewusst KEIN Lockfile mit ``os.kill(pid, 0)``: Pythons ``os.kill`` leitet unter
    Windows jeden Signalwert an ``TerminateProcess`` weiter, sig=0 inklusive — der
    Liveness-Check würde die laufende Instanz abschießen.

    Der macOS-Doppelklick-Pfad („cuecore.command") ruft uvicorn direkt und kommt hier
    gar nicht vorbei; die Funktion ist trotzdem plattformneutral.
    """
    host = host or HOST                   # zur Aufrufzeit: _set_port() kann PORT geändert haben
    port = PORT if port is None else port
    try:
        with socket.create_connection((host, port), timeout):
            pass
    except OSError:
        return False
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/health",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.0) as r:
            data = json.loads(r.read())
    except Exception:
        return False                      # Port belegt, aber nicht von uns -> normal weiter
    return isinstance(data, dict) and data.get("product") == "CueCore"


PORT_FILE = "cuecore.port"     # Ausweichport der laufenden Instanz, im Datenordner


def _remember_port(port: int) -> None:
    try:
        p = _user_data_dir() / PORT_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(port), encoding="utf-8")
    except OSError:
        pass                              # ohne Vermerk startet ein zweiter Doppelklick wie bisher


def _remembered_port() -> int | None:
    try:
        return int((_user_data_dir() / PORT_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def find_running_instance() -> bool:
    """Läuft CueCore schon — auf dem konfigurierten Port ODER auf dem Ausweichport, den eine Instanz mit
    demselben Datenordner vermerkt hat? Sonst wich ein zweiter Doppelklick nach einer Port-Ausweichung
    ebenfalls aus und startete einen zweiten Server auf dieselben DBs. Trifft der
    Ausweichport, zeigen PORT/URL danach dorthin. Ein veralteter Vermerk schadet nicht: dort antwortet
    dann kein CueCore-Health."""
    if running_instance():
        return True
    alt = _remembered_port()
    if alt and alt != PORT and running_instance(port=alt):
        _set_port(alt)
        return True
    return False


def _ensure_std_streams() -> None:
    """Im fensterlosen PyInstaller-Build (``console=False``) sind ``sys.stdout`` und
    ``sys.stderr`` unter Windows ``None``. Uvicorns Log-Formatter ruft beim Start
    ``sys.stdout.isatty()`` und crasht dann mit ``ValueError: Unable to configure
    formatter 'default'``. Fehlende Streams auf ``os.devnull`` umbiegen — ``isatty()``
    liefert dann ``False`` und ``print()`` bleibt gefahrlos."""
    if getattr(sys, "frozen", False):
        log = _open_log()
        if log is not None:
            sys.stdout = sys.stderr = log
            import logging
            logging.basicConfig(stream=log, level=logging.INFO,
                                format="%(asctime)s %(name)s %(levelname)s: %(message)s")   # pywebview + uvicorn ins Log
            return
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def log_path() -> Path:
    return _user_data_dir() / "cuecore.log"


def _open_log():
    """Gebündelte App: stdout/stderr in <Datenordner>/cuecore.log — der Nutzer sieht sonst nichts von
    Fehlern (macOS `open` und Windows console=False verschlucken beides). Über 2 MB wird rotiert (.1)."""
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            p.replace(p.with_suffix(".log.1"))
        f = open(p, "a", encoding="utf-8", buffering=1)
        f.write(f"\n=== CueCore Start {time.strftime('%Y-%m-%d %H:%M:%S')} · {sys.platform} · Python {sys.version.split()[0]}\n")
        return f
    except Exception:
        return None


def port_is_free(host: str = HOST, port: int | None = None) -> bool:
    """Kann uvicorn hier binden? Dieselbe Probe wie asyncio.create_server: unter POSIX mit SO_REUSEADDR, sonst
    hielten Verbindungen in TIME_WAIT/FIN_WAIT nach schnellem Neustart den Port „belegt“ — die App wich auf
    einen Zufallsport aus, neuer Origin, localStorage leer. Windows bindet wie asyncio ohne."""
    try:
        with socket.socket() as s:
            if os.name == "posix":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port if port is not None else PORT))
        return True
    except OSError:
        return False


def pick_free_port(host: str = HOST) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def use_window() -> bool:
    """App-Fenster (pywebview) statt Browser-Tab? Aus per CUECORE_WINDOW=0; sonst nur, wenn
    pywebview samt Plattform-Backend importierbar ist (im Bundle immer, im Dev-venv optional)."""
    if os.environ.get("CUECORE_WINDOW", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def drop_path(event: dict) -> str:
    """Vollen Pfad aus einem pywebview-Drop-Event holen (nur dort gibt es ihn: pywebviewFullPath
    an event['dataTransfer']['files'][i]). Erster Eintrag mit Pfad gewinnt, sonst ''."""
    try:
        files = (event.get("dataTransfer") or {}).get("files") or []
    except AttributeError:
        return ""
    for f in files:
        if isinstance(f, dict) and f.get("pywebviewFullPath"):
            return str(f["pywebviewFullPath"])
    return ""


def attach_drop(window, selector: str = "#tdrop") -> None:
    """Nach jedem Laden der Seite: Drop auf die Tagger-Drop-Zone in Python abfangen und den Pfad
    an window.cuecoreDropPath (index.html) geben — der Browser-Handler dort sieht keinen Pfad."""
    from webview.dom import DOMEventHandler

    def on_drop(e):
        p = drop_path(e or {})
        if p:
            # isDir aus Python: WebView2 gibt Ordner ohne abschließenden Separator zurück, die JS-Heuristik
            # („hat Endung = Datei") würde „Cezame Vol.2024" als Datei lesen und den Elternordner scannen.
            window.evaluate_js(f"window.cuecoreDropPath({json.dumps(p)}, {json.dumps(os.path.isdir(p))})")

    def on_loaded():
        print(f"Seite geladen: {window.get_current_url()}", flush=True)
        try:
            el = window.dom.get_element(selector)
            if el is not None:
                el.events.drop += DOMEventHandler(on_drop, prevent_default=True)
        except Exception as err:            # kein Grund, das Fenster zu verlieren
            print(f"Drop-Zone nicht verdrahtet: {err}", flush=True)

    window.events.loaded += on_loaded


def wait_for_server(*, timeout: float = 30.0, poll: float = 0.15, thread=None) -> bool:
    """Bis der eigene uvicorn im Thread antwortet (Health mit product == CueCore). Ist der Thread schon
    tot (Bind-Fehler, Import-Fehler), sofort False statt 30 s warten."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if running_instance(timeout=0.3):
            return True
        if thread is not None and not thread.is_alive():
            return False
        time.sleep(poll)
    return False


class ShellApi:
    """window.pywebview.api — was nur die Hülle kann. save_bytes: Export per nativem „Sichern"-Dialog.
    Ein Blob-Download über <a download> läuft im WKWebView ins Leere (kein Downloads-Ordner, kein
    Hinweis), deshalb schreibt Python die Datei dorthin, wo der Nutzer will."""

    def __init__(self):
        self._window = None          # mit _: pywebview durchsucht js_api rekursiv, nie ins Fenster

    def open_url(self, url: str) -> dict:
        """Anbieter-Link im Systembrowser öffnen. Im WKWebView würde target=_blank entweder nichts tun
        oder die Anbieter-Seite IN der App laden — beides falsch für einen Link nach draußen."""
        import webbrowser
        if not str(url).startswith(("http://", "https://")):
            return {"opened": False, "error": "nur http/https"}
        try:
            return {"opened": bool(webbrowser.open(url))}
        except Exception as err:          # z.B. webbrowser.Error — die Seite zeigt dann die Adresse
            return {"opened": False, "error": f"{err.__class__.__name__}: {err}"}

    def save_bytes(self, filename: str, content_b64: str) -> dict:
        import base64
        import webview
        try:
            res = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=filename)
        except Exception as err:
            return {"saved": False, "error": f"{err.__class__.__name__}: {err}"}
        path = res[0] if isinstance(res, (list, tuple)) and res else (res if isinstance(res, str) else "")
        if not path:
            return {"saved": False, "cancelled": True}
        try:
            Path(path).write_bytes(base64.b64decode(content_b64))
        except Exception as err:
            return {"saved": False, "error": f"{err.__class__.__name__}: {err}"}
        return {"saved": True, "path": str(path)}


def run_window(server=None) -> None:
    """Fenster auf die laufende Instanz (eigener uvicorn im Thread oder fremd gestartet). Blockt bis
    das Fenster zu ist; dann wird der eigene Server gestoppt."""
    import webview
    api = ShellApi()
    win = webview.create_window("CueCore", URL, width=1400, height=900, min_size=(960, 640),
                                background_color=WINDOW_BG, text_select=True, js_api=api)
    api._window = win
    attach_drop(win)
    if server is not None:
        win.events.closed += lambda: setattr(server, "should_exit", True)
    webview.start()


def _try_window(server=None) -> bool:
    """run_window, aber ein Fehler beim Öffnen (fehlende WebView2-Runtime, kaputtes Cocoa-Backend) ist
    kein Grund zu sterben — der Aufrufer fällt auf den Browser-Tab zurück. True = Fenster lief und ist zu."""
    try:
        run_window(server)
        return True
    except Exception as err:
        print(f"App-Fenster nicht verfügbar ({err.__class__.__name__}: {err}) — Browser-Tab stattdessen.", flush=True)
        return False


def _message_html(title: str, detail: str) -> str:
    import html
    return (f"<body style='font:14px -apple-system,Segoe UI,sans-serif;background:#0B0B0D;color:#eee;padding:28px'>"
            f"<h2 style='margin:0 0 12px'>CueCore</h2><p>{html.escape(title)}</p>"
            f"<pre style='white-space:pre-wrap;color:#aaa'>{html.escape(detail)}</pre></body>")


def show_error_window(title: str, detail: str) -> None:
    """Statt eines stillen Endes: ein kleines Fenster mit dem Grund und dem Pfad der Logdatei."""
    try:
        import webview
        webview.create_window("CueCore", html=_message_html(title, detail), width=620, height=300,
                              background_color=WINDOW_BG)
        webview.start()
    except Exception as err:
        print(f"Fehlerfenster nicht möglich: {err}", flush=True)


# Sofort sichtbar, solange CueCore die Datenbanken einrichtet (nach einem Update bis zu einer Minute). Sonst sähe man
# in dieser Zeit nichts, startete ein zweites Mal, und der zweite Start bräche mit „database is locked" ab.
STARTING_HTML = (
    "<body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0B0B0D;"
    "color:#eee;font:15px -apple-system,Segoe UI,sans-serif'><div style='text-align:center;max-width:460px'>"
    "<div style='font-weight:700;font-size:24px;letter-spacing:.04em;margin-bottom:14px'>CueCore</div>"
    "<p style='margin:0 0 6px'>CueCore startet …</p><p style='margin:0 0 18px;color:#999'>CueCore is starting …</p>"
    "<p style='margin:0;color:#888;font-size:13px'>Nach einem Update richtet CueCore einmalig die Datenbanken ein, "
    "das kann bis zu einer Minute dauern. / After an update CueCore sets up its databases once, this can take up to "
    "a minute.</p></div></body>")

LOCK_FILE = "cuecore.lock"


def acquire_instance_lock(data_dir: Path | None = None):
    """Exklusive Sperre im Datenordner für die ganze Laufzeit; None, wenn eine andere Instanz sie hält.

    find_running_instance() erkennt eine Instanz erst, wenn ihr Server antwortet — während der Selbstheilung
    nach einem Update (bis zu einer Minute) also nicht. Ein zweiter Doppelklick in dieser Zeit fasste dieselben
    Datenbanken an (der zweite Start meldete „database is locked"). Das Betriebssystem gibt die Sperre
    frei, wenn der Prozess endet, auch nach einem Absturz."""
    d = Path(data_dir) if data_dir else _user_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / LOCK_FILE, "a+b")
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def release_instance_lock(lock) -> None:
    if lock is None:
        return
    try:
        if sys.platform.startswith("win"):
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    lock.close()


def wait_for_instance(*, timeout: float = 600.0, poll: float = 1.0) -> bool:
    """Bis die gerade startende Instanz antwortet (sie hält die Sperre und richtet noch ein)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if find_running_instance():
            return True
        time.sleep(poll)
    return False


def _attach_to_running(window: bool) -> None:
    print(f"CueCore läuft bereits:  {URL}", flush=True)
    if window and _try_window():       # zweites Fenster auf die laufende Instanz, kein zweiter Server
        return
    try:
        webbrowser.open(URL)           # nur nach vorn holen, nicht neu starten
    except Exception:
        pass


def _boot_server():
    """Datenbanken bereitstellen, App laden (Selbstheilung, kann nach einem Update dauern), uvicorn im Thread.
    (server, thread) oder None, wenn der Server nicht antwortet."""
    ensure_labels_db()
    import uvicorn
    from app import app  # löst intern _load() aus (findet die eben gebaute labels.db)
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    if not wait_for_server(thread=t):
        print("CueCore-Server antwortet nicht — Abbruch.", flush=True)
        server.should_exit = True
        return None
    print(f"CueCore läuft:  {URL}   —   Fenster schließen zum Beenden", flush=True)
    return server, t


def _boot_into(win, boot) -> None:
    """Läuft in pywebviews Hintergrund-Thread: boot() liefert den laufenden Server, dann lädt das schon offene
    Fenster die App. Ein Fehler erscheint im selben Fenster (mit Logdatei), Fenster zu stoppt den Server."""
    try:
        server = boot()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        win.load_html(_message_html("CueCore konnte nicht starten.",
                                    f"{tb.strip().splitlines()[-1]}\n\nLogdatei: {log_path()}"))
        return
    if server is None:
        win.load_html(_message_html("CueCore konnte den lokalen Server nicht starten.",
                                    f"Details stehen in der Logdatei:\n{log_path()}"))
        return
    win.events.closed += lambda: setattr(server, "should_exit", True)
    win.load_url(URL)


def run_window_booting(boot) -> None:
    """Fenster sofort mit STARTING_HTML öffnen, boot() im Hintergrund (_boot_into). Blockt bis das Fenster zu ist."""
    import webview
    api = ShellApi()
    win = webview.create_window("CueCore", html=STARTING_HTML, width=1400, height=900, min_size=(960, 640),
                                background_color=WINDOW_BG, text_select=True, js_api=api)
    api._window = win
    attach_drop(win)
    webview.start(_boot_into, (win, boot))


def main() -> None:
    _ensure_std_streams()
    window = use_window()
    if find_running_instance():
        _attach_to_running(window)
        return
    lock = acquire_instance_lock()
    if lock is None:
        # Eine andere Instanz startet gerade (richtet z. B. nach einem Update die Datenbanken ein) und antwortet noch
        # nicht: warten und dann wie oben anhängen, statt dieselben Datenbanken anzufassen.
        print("CueCore startet bereits — warte auf die laufende Instanz …", flush=True)
        if wait_for_instance():
            _attach_to_running(window)
        else:
            show_error_window("CueCore startet bereits in einem anderen Fenster.",
                              f"Bitte dort warten. Details stehen in der Logdatei:\n{log_path()}")
        return
    try:
        _main_locked(window)
    finally:
        release_instance_lock(lock)


def _main_locked(window: bool) -> None:
    if not port_is_free():
        # Fremder Dienst auf 8765 (running_instance war False): freien Port nehmen statt an
        # „address already in use" zu sterben — im fensterlosen Build sähe der Nutzer nichts.
        _set_port(pick_free_port())
        _remember_port(PORT)
        print(f"Port {os.environ.get('CUECORE_PORT', '8765')} belegt (fremder Dienst) — weiche auf {PORT} aus.", flush=True)
    if not window:
        ensure_labels_db()
        import uvicorn
        from app import app  # löst intern _load() aus (findet die eben gebaute labels.db)
        print(f"CueCore läuft:  {URL}   —   Fenster schließen zum Beenden", flush=True)
        threading.Thread(target=_open_browser, daemon=True).start()
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
        return
    booted: dict = {}

    def boot():
        r = _boot_server()
        if r is None:
            return None
        booted["server"], booted["thread"] = r
        return r[0]

    try:
        run_window_booting(boot)
    except Exception as err:
        # Kein Fenster möglich (z. B. Windows ohne WebView2-Runtime): Browser-Tab, Server läuft weiter,
        # Beenden über den Knopf in der Rail (/api/quit) wie im reinen Browser-Modus.
        print(f"App-Fenster nicht verfügbar ({err.__class__.__name__}: {err}) — Browser-Tab stattdessen.", flush=True)
        if "server" not in booted and boot() is None:
            show_error_window("CueCore konnte den lokalen Server nicht starten.",
                              f"Details stehen in der Logdatei:\n{log_path()}")
            return
        try:
            webbrowser.open(URL)
        except Exception:
            pass
        booted["thread"].join()
        return
    if "server" in booted:
        booted["server"].should_exit = True
        booted["thread"].join(timeout=5)


def run() -> None:
    """Einstiegspunkt des Bundles: main() mit Auffangnetz — ein Traceback vor dem ersten Fenster (Datenordner
    nicht beschreibbar, kaputte DB, fehlende Bundle-Datei) landet im Log und in einem Fehlerfenster."""
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb, flush=True)
        show_error_window("CueCore konnte nicht starten.", f"{tb.strip().splitlines()[-1]}\n\nLogdatei: {log_path()}")
        raise


if __name__ == "__main__":
    run()
