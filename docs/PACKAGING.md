# CueCore als App bauen (macOS .app, Windows-Ordner)

Stand 2026-10-13. Beide Plattformen bauen aus **derselben Spec** (`cuecore.spec`)
mit PyInstaller; die App läuft als eigenes Fenster über pywebview (macOS: WKWebView, Windows: WebView2). Der lokale
FastAPI-Server läuft im selben Prozess auf `127.0.0.1:8765`; Fenster schließen (macOS auch „Beenden" im App-Menü)
stoppt ihn. Der Knopf „CueCore beenden" in der Rail erscheint seit 09.10.2026 nur noch im Browser-Tab, im App-Fenster
ist er ausgeblendet (`hideQuitInShell` in index.html).

## Bauen auf dem Mac

```bash
cd MusicMetaDataSearchEngine
uv venv --python 3.12 .venv-build                                   # eigenständiges Python, min. macOS 11
uv pip install --python .venv-build/bin/python -r requirements.txt -r requirements-build.txt
.venv-build/bin/python build_bundle_data.py    # -> ../build_data/ (labels_prebuilt.db + songs.db, ~10 s)
cd ..
MusicMetaDataSearchEngine/.venv-build/bin/pyinstaller --noconfirm cuecore.spec
```

Nicht mit dem Dev-`.venv` (Homebrew-Python) bauen — dessen Framework verlangt macOS 26.

**Eingebauter AcoustID-Key (Open Beta, seit 13.10.2026):** vor `build_bundle_data.py` die Variable
`CUECORE_ACOUSTID_APP_KEY` setzen (Key der App CUECORE auf acoustid.org, nie committen). Das Skript schreibt ihn
verschleiert nach `build_data/builtin_keys.bin`, die Spec nimmt die Datei mit; ohne Variable baut die App ohne
eingebauten Key. Windows liest ihn aus dem Repo-Secret `ACOUSTID_APP_KEY`. Ein eigener Key in den Einstellungen gewinnt.

Ergebnis: `dist/CueCore.app` (rund 190 MB, arm64, ad-hoc signiert, ZIP rund 70 MB; Build vom 11.10.2026). Zum
Weitergeben: `ditto -c -k --keepParent dist/CueCore.app CueCore-mac.zip`.
`dist/`, `build/` und `build_data/` sind gitignored.

Die Spec bricht ab, wenn `build_data/` fehlt — der Schritt `build_bundle_data.py` ist Pflicht und reproduzierbar
aus dem Repo (GVL-Master-CSV, Zusatz-Codes, Seed `MusicMetaDataSearchEngine/data/songs.db`,
`data/library_tracks/*.csv`).

**Beide brechen auch ab, wenn `data/library_tracks/` leer ist oder eine im Git geführte Lieferung auf der Platte
fehlt** (`build_bundle_data.missing_deliveries`). Diese Dateien sind git-versioniert; ein leerer
Ordner heißt „aus Versehen gelöscht", nicht „keine Lieferung". Ohne den Stopp würde der nächste Build eine songs.db
ohne die Lieferungen ausliefern, ohne dass es auffällt. Fehlende Dateien holt `git restore <pfad>` zurück; vor
jedem Build lohnt ein Blick auf `git status --short | grep '^ D'`.

## Was im Bundle ist, was nicht

| Im Bundle (`Contents/Frameworks/data/`)          | Zweck |
|--------------------------------------------------|-------|
| `labels_prebuilt.db`                             | GVL-Master 85.803 Codes inkl. Zusatz-Codes (Stand 13.10.2026); Erststart kopiert sie nur |
| `songs.db`                                       | Seed (eigener Korpus, 5.531) **plus alle Library-Lieferungen** (Cezame 60.938, weitere Lieferung 2.880 Dateien in `library_files`; zusammen 66.417 Songs, weil Dateien mit gleichem Interpret und Titel eine Song-Zeile teilen); eine Neuinstallation startet mit allem |
| `GVL_Labelcodes_synced.csv` + `.meta.json`       | Fallback, falls die prebuilt-DB fehlt; Quell-Version fürs Health |
| `labelcodes_supplemental.csv`, `library_prefixes.csv`, `label_aliases_mb.csv` | Selbstheilung und Matching |
| `library_tracks/*.csv`                           | Selbstheilung älterer Installationen, deren songs.db die Lieferung noch nicht kennt |
| `web/`                                           | index.html, Logos, Schriften (`web/fonts/`) |
| `bin/fpcalc` (macOS `Contents/Frameworks/bin/`, Windows `_internal\bin\fpcalc.exe`) | Chromaprint 1.6.1 für den AcoustID-Fingerabdruck (`core/acoustid.py`); macOS aus dem Repo, Windows lädt der Workflow SHA-256-geprüft nach — siehe `MusicMetaDataSearchEngine/bin/README.md` |

**Nicht im Bundle:** `track_index.db` (6,5 GB, Dump-Index aus Discogs/MusicBrainz). Wer ihn auf einem zweiten
Rechner will, kopiert die Datei in den Datenordner (unten); die App findet sie beim nächsten Start und zeigt
sonst „Track-Index: keiner" in der Rail.

## Ablageorte zur Laufzeit (nutzer-schreibbar, kein Admin)

| Plattform | Datenordner |
|-----------|-------------|
| macOS     | `~/Library/Application Support/MusicMeta/` (labels.db, songs.db, cache.db, operations.db, settings.json, labels_manual.json, library_lists/, optional track_index.db; in der gebündelten App dazu cuecore.log, nach einem Port-Ausweichen cuecore.port, nach dem ersten GVL-PDF-Update gvl/) |
| Windows   | `%LOCALAPPDATA%\CueCore\` |

`CUECORE_DB=<pfad>/labels.db` verlegt den Datenordner (Tests, zweite Instanz); `CUECORE_PORT`, `CUECORE_HOST`
wie bisher; `CUECORE_WINDOW=0` erzwingt den Browser-Tab statt des App-Fensters. `settings.json` wandert mit
`CUECORE_DB` in denselben Ordner (`core/settings.py` nimmt `default_data_dir()`); `CUECORE_SETTINGS` verlegt nur sie.

Die gebündelte App schreibt **stdout/stderr nach `<Datenordner>/cuecore.log`** (rotiert ab 2 MB nach `.log.1`) —
das ist die erste Anlaufstelle bei Rückmeldungen von Testenden. Ist Port 8765 von einem fremden Dienst belegt, weicht die
App auf einen freien Port aus (steht im Log) und vermerkt ihn in `<Datenordner>/cuecore.port` — ein zweiter
Doppelklick findet die laufende Instanz dort (seit 12.10.2026); startet der Server gar nicht, zeigt sie ein
Fehlerfenster mit dem Logpfad.
Das GVL-PDF-Update arbeitet in der App auf `<Datenordner>/gvl/` (Master-CSV, Archiv, Report), beim
ersten Mal aus dem Bundle geseedet — nie im Bundle selbst.

Erststart: fehlt `labels.db` im Datenordner, wird die prebuilt-DB kopiert; fehlt `songs.db`, die gebündelte
(volle) songs.db. Vorhandene Dateien werden **nie** überschrieben — eine Installation mit eigenem PDF-Stand
oder eigenen verifizierten Zeilen behält sie. Zweiter Doppelklick bei laufender App: neues Fenster auf die
laufende Instanz, kein zweiter Server.

## Gatekeeper (macOS, unsigniert)

Es gibt keinen Apple-Developer-Account (Stand 21.09.2026), die App ist nur **ad-hoc signiert** (PyInstaller
macht das automatisch, nötig auf Apple Silicon). Beim ersten Start einer heruntergeladenen Kopie meldet macOS,
die App könne nicht geöffnet werden, weil Apple sie nicht auf Schadsoftware prüfen konnte. Zwei Wege, je
Rechner einmalig:

1. **macOS 15 (Sequoia) und neuer:** Meldung schließen → **Systemeinstellungen → Datenschutz & Sicherheit** →
   unten bei „CueCore wurde blockiert" auf **Trotzdem öffnen** → Passwort/Touch ID → „Öffnen". Der frühere
   Rechtsklick-Öffnen-Trick gilt seit macOS 15 nicht mehr für unsignierte Apps.
2. Terminal: `xattr -d com.apple.quarantine /Pfad/zu/CueCore.app` (entfernt die Quarantäne-Markierung des
   Downloads; beim lokalen Build ist sie gar nicht gesetzt). Bei einer ZIP-Weitergabe die App vor dem Start
   aus der ZIP nach `/Programme` oder in einen eigenen Ordner ziehen.

**Voraussetzungen der Beta-Fassung:** Apple Silicon (arm64, kein Intel-Build) und macOS 11 oder neuer (Build
mit dem eigenständigen Python 3.12, `uv python install 3.12` — ein Build mit dem Homebrew-Python trägt
`minos 26.0` und läuft nur auf macOS 26+, geprüft mit `otool -l … | grep minos`). `LSMinimumSystemVersion` in
`cuecore.spec` steht auf demselben Wert (auf einem macOS 11 ist die App noch nicht gestartet worden). Die Schriften (Saira Condensed,
Inter; OFL) liegen unter `web/fonts/` und werden über `/web/fonts/…` ausgeliefert — **kein Google Fonts mehr**: mit
dem externen Stylesheet im `<head>` wartete WebKit auf fonts.gstatic.com, und hinter einer Firewall mit Nachfrage-Dialog
oder offline blieb das App-Fenster schwarz.

**Firewall-Hinweis für Testende (z.B. Little Snitch, LuLu):** die App ist neu und unsigniert, die erste Netzanfrage
löst eine Nachfrage aus — „Erlauben" wählen. Jeder neue Build hat eine neue Signatur und fragt erneut. Der Start
selbst braucht kein Netz, und die Oberfläche lädt nichts von außen (Schriften und Logos kommen vom lokalen Server).
Alle Hosts, die CueCore ansprechen kann (aus `app.py` und `core/` ermittelt, Stand 12.10.2026), jeweils per HTTPS:

| Host | wofür | Schlüssel |
|------|-------|-----------|
| `musicbrainz.org` | Track-, ISRC-, Album- und Werk-Suche: Label, LC-Kandidaten, Komponist/Texter | ohne |
| `api.discogs.com` | Labelcode aus den Identifiers einer Release; Album-Suche beim Album-Auflösen | Release ohne; die Suche nur mit Token oder Consumer Key/Secret (ohne: 401, kein Treffer — die Anfrage geht trotzdem raus) |
| `api.deezer.com` | Album-Label, ISRC, Album/Datum (weitere Label-Quelle) | ohne |
| `query.wikidata.org` | Record Label und AutorInnen per SPARQL; Suche nur nach Titel | ohne |
| `itunes.apple.com` | Album/Jahr nachfüllen, wo leer | ohne |
| `www.musik-sammler.de` | Label-Seite zum LC als Bestätigung beim Album-Auflösen | ohne |
| `api.genius.com` | Songwriter als Vorschlag | nur mit Client Access Token |
| `api.acoustid.org` | Fingerabdruck-Abgleich; gesendet werden Fingerabdruck und Dauer, nicht das Audio (`fpcalc` rechnet lokal) | nur mit Application-Key |
| `api.audd.io` | Fingerprinting als kostenpflichtiger Rückfall nach AcoustID; schickt einen Ausschnitt der Audiodatei (ohne ffmpeg die ganze Datei) | nur mit API-Token |
| `api.editingtools.io` | Library-Scan der Cue-Liste (Dateinamen der Cues) | nur mit API-Key |
| Host aus den Einstellungen (Track-Metadata-API des Library-Partners) | Dateinamen der Cues → Track-Metadaten | nur mit Host, Client-ID und Secret |

Ohne Schlüssel geht an die unteren fünf keine Anfrage. „Verbindung prüfen" in den Einstellungen spricht dieselben
Hosts an (Discogs, Genius, AcoustID, editingtools, Library-Partner). ACRCloud hat Felder in den Einstellungen, wird
aber nirgends abgerufen. **Nur Links**, die CueCore nie selbst abruft: sie öffnen im Standardbrowser (in der Hülle
über `open_url`), die Nachfrage kommt dann vom Browser — Quellen-Links zu `musicbrainz.org`, `www.discogs.com`,
`www.wikidata.org`, `www.musik-sammler.de`, `genius.com` und die Links „Schlüssel holen" zu `acoustid.org`,
`dashboard.audd.io`, `console.acrcloud.com`, `editingtools.io`, `www.discogs.com`, `genius.com`.

Sobald ein Developer-ID-Zertifikat im Schlüsselbund liegt: `codesign --deep --force --options runtime
--sign "Developer ID Application: <Name>" dist/CueCore.app`, danach `notarytool submit` — eigener Schritt,
noch nicht eingerichtet.

## Fenster-Hülle und Ordner-Drop

`run_cuecore.py` startet uvicorn in einem Thread, wartet auf `/api/health` und öffnet dann das pywebview-Fenster
(1400 × 900, mindestens 960 × 640, Hintergrund wie das dunkle Theme). Der volle Pfad eines gedroppten Ordners
existiert nur auf der Python-Seite (`pywebviewFullPath`, pywebview ≥ 5): `attach_drop` hängt nach jedem Laden
einen `DOMEventHandler` an `#tdrop` und reicht den Pfad an `window.cuecoreDropPath` in index.html, das den
Tagger-Scan startet (Datei → ihr Ordner). Im Browser-Tab bleibt der Hinweis „Im Browser kommt kein Pfad an".
`confirm()` (etwa „Dieses händisch angelegte Label entfernen?") zeigt die Cocoa-Hülle nativ. **Exporte** (Cue-Sheet
xlsx/csv/pdf, Datenbank) laufen in der Hülle über den nativen „Sichern"-Dialog (`window.pywebview.api.save_bytes`,
`ShellApi` in run_cuecore.py) — ein `<a download>` auf eine Blob-URL läuft im WKWebView ins Leere (kein Download,
kein Hinweis; im Dev-Fenster nachgestellt). Im Browser-Tab bleibt es der normale Download. Anbieter-Links öffnen
in der Hülle über `window.pywebview.api.open_url` im Standardbrowser; scheitert das, zeigt die Seite die Adresse
zum Kopieren.

## Windows

`.github/workflows/build-windows.yml` baut mit derselben Spec (Python 3.12, pywebview 6.2.1, PyInstaller
6.22.3, `build_bundle_data.py` vorweg, `fpcalc.exe` 1.6.1 aus dem offiziellen Chromaprint-Release mit
festgeschriebener SHA-256, sonst Abbruch) — **nur auf Zuruf** (Actions → Build Windows → Run workflow, Branch
wählen). **Hauptweg ist das Release `windows-beta`** („CueCore Windows (Beta)", Reiter
Releases, ein nicht öffentlicher Entwurf): der Lauf ersetzt dort die vorige ZIP durch `CueCore-Windows-<JJJJ-MM-TT>-<Commit>.zip`. Release-Assets
zählen nicht gegen den Actions-Speicher des Accounts. Zweiter
Weg: das Artefakt `CueCore-Windows` am Lauf (darin `CueCore-Windows.zip`, 7 Tage), das an der Quota scheitern darf
(`continue-on-error`). Auf dem Zielrechner braucht pywebview die **WebView2-Runtime** (bei Windows 10/11
mit Edge vorhanden). Windows meldet beim ersten Start „Der Computer wurde durch Windows geschützt" → Weitere
Informationen → Trotzdem ausführen. Ablauf im Detail: `WINDOWS_BUILD.md`.

## Absicherungen und Smoke-Test der gebauten App

Absicherungen: der Port-Fallback gilt auch im Fenster-Modus; Tagger-Apply rollt die
Tags zurück, wenn das Verschieben scheitert (Volume voll, Rechte) und nennt den Grund; jeder unerwartete
Server-Fehler kommt als JSON `{"detail": "Typ: Text"}` mit Traceback im Log; `labels.db` wird atomar kopiert;
toter Server-Thread bricht das Warten sofort ab; Ordner-Erkennung beim Drop aus Python (WebView2 liefert Ordner
ohne Schrägstrich); Drop ohne Pfad zeigt nach 0,8 s einen Hinweis; Windows-Ordnerdialog ohne Konsolenblitz;
manuelle Labels aus dem Build (`build_data/labels_manual.json`) kommen mit; UI zeigt Server-Detail statt
„fehlgeschlagen" (Einstellungen speichern, Zielordner, Album-Auflösung, Resolve, Prüfen, Cue-Sheet-Vervollständigen,
Export, Datenbank) und meldet Netzausfall statt „kein Treffer"; Gatekeeper-Text für macOS 15+; Build mit
Standalone-Python (min. macOS 11 statt 26).

Smoke-Test (fresh data dir, Port 8799): Einstellungen, Tagger scan/plan/apply/undo (Datei verschoben, Tags
geschrieben und zurück), EDL-Import, Exporte xlsx/csv/pdf (Cue-Sheet und Datenbank), lokaler Resolve, GVL-Suche,
FTS-Suche, Song anlegen/ändern, Label anlegen, CSV-Import-Vorschau, GVL-PDF-Vorschau (3.640 Seiten, 10 s) — alles 200.

## Manuell geprüft (Mac)

App aus `dist/` mit frischem Datenordner gestartet: labels.db + songs.db (61.314 Tracks) kopiert, Fenster mit
Startseite und Onboarding, `/api/health` ready, „Beenden" über das App-Menü stoppt Prozess und Port.
Zweiter Doppelklick bei laufender App: zweites Fenster auf denselben Server (ein Prozess lauscht), Schließen des
zweiten lässt den ersten laufen. Scheitert das Fenster (kein WebView2 auf Windows, kaputtes Backend), öffnet die
App den Browser-Tab und meldet es auf stdout (in der gebündelten App: `cuecore.log`) — der Server läuft weiter,
Beenden über den Knopf in der Rail.
Nicht von Hand geprüft: ein echter Finder-Drop in die Drop-Zone (Verdrahtung durch `tests/test_run_cuecore.py`
abgedeckt).
