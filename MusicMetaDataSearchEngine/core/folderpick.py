"""Nativer OS-Ordnerdialog übers Backend.

Eine Browser-App bekommt aus dem Datei-Dialog nie einen echten Ordnerpfad
(Sandbox) — daher öffnet das Backend den systemeigenen Dialog und gibt den Pfad
zurück. Kein tkinter/Qt nötig: reines subprocess auf Bordmittel.

  macOS:   osascript  (choose folder -> POSIX path)
  Windows: PowerShell  (System.Windows.Forms.FolderBrowserDialog)

Abbruch oder Fehler -> '' (leerer Pfad). Der Aufrufer behandelt '' als 'nichts
gewählt' und lässt das Feld unverändert.
"""
from __future__ import annotations
import subprocess
import sys

_PROMPT = "Ordner für CueCore wählen"
_MAC = ["osascript", "-e", f'POSIX path of (choose folder with prompt "{_PROMPT}")']

# -STA: FolderBrowserDialog braucht Single-Threaded-Apartment. Pfad geht auf stdout,
# bei Abbruch bleibt stdout leer.
_PS = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    f'$d.Description = "{_PROMPT}"; '
    "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
    "{ [Console]::Out.Write($d.SelectedPath) }"
)
_WIN = ["powershell", "-NoProfile", "-STA", "-Command", _PS]


def _run(cmd: list[str]) -> str:
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW   # sonst blitzt aus dem fensterlosen Build eine Konsole auf
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
    if proc.returncode != 0 and "cancel" not in (proc.stderr or "").lower():
        print(f"Ordnerdialog fehlgeschlagen (rc={proc.returncode}): {(proc.stderr or '').strip()[:300]}", flush=True)
    return proc.stdout


def choose_folder(*, run=None) -> str:
    """Öffnet den nativen Ordnerdialog, gibt den gewählten Pfad zurück ('' bei
    Abbruch/Fehler). `run` ist injizierbar, damit Tests ohne echten Dialog laufen."""
    cmd = _WIN if sys.platform.startswith("win") else _MAC
    runner = run or _run
    try:
        return (runner(cmd) or "").strip()
    except Exception:
        return ""
