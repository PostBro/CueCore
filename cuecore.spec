# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Build für CueCore (onedir). Von der Repo-Wurzel aufrufen, vorher die DBs einbacken:
#   python MusicMetaDataSearchEngine/build_bundle_data.py        # -> build_data/ (labels_prebuilt.db, songs.db)
#   pyinstaller --noconfirm cuecore.spec
# Ergebnis macOS:   dist/CueCore.app   (Doppelklick, eigenes Fenster über pywebview; unsigniert -> docs/PACKAGING.md)
# Ergebnis Windows: dist/CueCore/      (Ordner mit CueCore.exe + _internal/, ohne Installation/Admin)
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ENGINE = "MusicMetaDataSearchEngine"
MAC = sys.platform == "darwin"
WIN = sys.platform.startswith("win")

for req in ("build_data/labels_prebuilt.db", "build_data/songs.db"):
    if not Path(req).exists():
        raise SystemExit(f"{req} fehlt — zuerst: python {ENGINE}/build_bundle_data.py")
if not list(Path("data/library_tracks").glob("*.csv")):
    raise SystemExit("data/library_tracks/ ist leer — die Library-Lieferungen sind git-versioniert: "
                     "git restore data/library_tracks/ (sonst fehlt die Library im Bundle).")
sys.path.insert(0, ENGINE)
from build_bundle_data import missing_deliveries   # jede im Git geführte Lieferung, nicht nur „mindestens eine"
_missing = missing_deliveries(Path(".").resolve())
if _missing:
    raise SystemExit(f"Library-Lieferung fehlt: {', '.join(_missing)} — git restore data/library_tracks/ "
                     "(sonst fehlen ihre Tracks im Bundle).")

# Read-only-Dateien, die zur Laufzeit gebraucht werden (Ziel = Unterordner im Bundle).
# build_data/ wird VOR pyinstaller gebaut: labels_prebuilt.db (Erststart kopiert nur) und songs.db mit
# Seed + allen Library-Lieferungen (ganze Library ab dem ersten Start).
# NICHT im Bundle: track_index.db (6,5 GB) — bleibt im Datenordner des Nutzers.
datas = [
    (f"{ENGINE}/web", "web"),
    ("data/GVL_Labelcodes_synced.csv", "data"),
    ("data/GVL_Labelcodes_synced.meta.json", "data"),
    ("data/labelcodes_supplemental.csv", "data"),   # Zusatz-Labelcodes (GVL gewinnt) — Selbstheilung + CSV-Fallback
    ("data/gvl_name_fixes.csv", "data"),            # Korrekturen offensichtlicher Fehler der GVL-Quelle (Selbstheilung)
    ("data/library_prefixes.csv", "data"),           # Sub-Label-Kürzel der Libraries (Extreme, KPM) -> LC
    ("data/library_prefixes_lieferungen.csv", "data"),   # aus den Library-Lieferungen gelernte Kürzel -> LC
    ("data/library_prefix_hints.csv", "data"),       # Kürzel ohne festen Code: Kandidaten als Suchhinweis
    ("data/label_aliases_anbieter.csv", "data"),     # Labelnamen laut Anbieter -> GVL-Code (Namensregel)
    ("data/label_aliases_mb.csv", "data"),           # MusicBrainz-Label-Namen/-Aliasse (nur GVL-geprüfte Codes)
    ("data/library_tracks", "data/library_tracks"),  # Lieferungen als CSV: Selbstheilung älterer Installationen
    ("build_data/labels_prebuilt.db", "data"),
    ("build_data/songs.db", "data"),
]
if Path("build_data/labels_manual.json").exists():   # fremde Codes einer Lieferung (build_bundle_data)
    datas.append(("build_data/labels_manual.json", "data"))
# Eingebauter AcoustID-Key der Open Beta: build_bundle_data schreibt ihn nur, wenn CUECORE_ACOUSTID_APP_KEY gesetzt
# ist (verschleiert, nie im Repo). Ohne die Variable baut die App ohne eingebauten Key.
if Path("build_data/THIRD-PARTY-LICENSES.txt").exists():   # packaging/collect_licenses.py (CI)
    datas.append(("build_data/THIRD-PARTY-LICENSES.txt", "."))
for lic in ("LICENSE", "LIZENZEN.md"):                 # AGPL-Text und Drittlizenzen gehören in jede App (nur im
    if Path(lic).exists():                               # öffentlichen Repo vorhanden, dort baut die CI)
        datas.append((lic, "."))
if Path("build_data/builtin_keys.bin").exists():
    datas.append(("build_data/builtin_keys.bin", "data"))

# fpcalc (Chromaprint) fuer AcoustID: core/acoustid.py sucht es unter <Bundle>/bin/. Das offizielle
# Release haengt nur an System-Bibliotheken, laeuft also ohne Homebrew (siehe bin/README.md). Im Repo liegt nur
# das macOS-Binary; fpcalc.exe legt build-windows.yml vor dem Build hin (SHA-256-geprueft) und bricht ab, wenn es fehlt.
# Als `binaries`, nicht `datas` — nur so behandelt PyInstaller es als ausfuehrbar und signiert es mit.
_fpcalc = Path(ENGINE) / "bin" / ("fpcalc.exe" if WIN else "fpcalc")
binaries = [(str(_fpcalc), "bin")] if _fpcalc.is_file() else []
if not binaries:
    print(f"WARNUNG: {_fpcalc} fehlt — AcoustID-Fingerprinting ist im Build nicht verfuegbar.")

hiddenimports = collect_submodules("uvicorn") + ["app", "webview"]
if MAC:
    hiddenimports += ["webview.platforms.cocoa"]
elif WIN:
    hiddenimports += ["webview.platforms.winforms", "webview.platforms.edgechromium"]

a = Analysis(
    [f"{ENGINE}/run_cuecore.py"],
    pathex=[ENGINE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "PIL", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi"],   # PIL nur fürs Icon-Skript
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CueCore",
    icon="packaging/cuecore.ico" if WIN else "packaging/cuecore.icns",
    console=False,         # windowed: kein Konsolenfenster. Beenden = Fenster schließen (UI-Knopf nur im Browser-Tab).
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="CueCore",
)
if MAC:
    app = BUNDLE(
        coll,
        name="CueCore.app",
        icon="packaging/cuecore.icns",
        bundle_identifier="de.postbro.cuecore",
        info_plist={
            "CFBundleName": "CueCore",
            "CFBundleDisplayName": "CueCore",
            "CFBundleShortVersionString": "1.0.0",   # = app.APP_VERSION ohne -beta
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",   # = minos aller Mach-O (Standalone-Python), PACKAGING.md
            "NSHumanReadableCopyright": "POSTBRO",
            # Kein App Nap: mit dem Fenster im Hintergrund entpackt CueCore den Track-Index deutlich langsamer;
            # lange Läufe (Index, Cue-Sheet-Abgleich) laufen oft im Hintergrund.
            "NSAppSleepDisabled": True,
        },
    )
