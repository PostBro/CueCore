# CueCore — Windows-Programm über GitHub bauen (ohne Windows-Rechner)

Dieses Repo baut per **GitHub Actions** auf Knopfdruck ein **Windows-Standalone**: ein
`.exe` im Ordner, das auf Rechnern **ohne Installation und ohne Admin-Rechte**
läuft — Python ist eingebacken.

## Schritt 1 — Stand auf GitHub bringen (lokal)
Das Repo liegt auf GitHub unter **`PostBro/CueCore`**. Gebaut wird, was auf GitHub liegt: den Branch, aus dem das Programm
entstehen soll, committen und pushen.

```bash
git status --short | grep -E '^ ?D'           # muss leer sein, sonst: git restore --staged --worktree <pfad>
git push origin <branch>                     # z. B. main oder der aktuelle Arbeitsbranch
```

Der Blick auf gelöschte Dateien lohnt vor dem Commit: eine gelöschte Lieferungs-CSV, die mit committet wird,
fällt dem Build auf GitHub nicht auf (er prüft gegen den eigenen Checkout und bricht nur ab, wenn
`data/library_tracks/` ganz leer ist) — das Programm hätte dann still weniger Tracks.

## Schritt 2 — Build von Hand starten
Ein Push baut **nichts**: der Workflow hat bewusst keinen push-Trigger (ein Windows-Build
nur auf ausdrücklichen Wunsch). Starten: Reiter **Actions** → „Build Windows" → **Run workflow**, unter
„Use workflow from" den gepushten Branch wählen (~5–10 Min).

## Schritt 3 — Fertiges Programm herunterladen
Der Lauf legt die ZIP im **Release „windows-beta"** ab (Reiter **Releases**, „CueCore Windows
(Beta)", immer nur die letzte ZIP, `CueCore-Windows-<JJJJ-MM-TT>-<Commit>.zip`). Das ist ein
**Entwurf**, nur für Mitglieder des Repos sichtbar; für die Weitergabe kommt die ZIP in ein Versions-Release.
Grund: Release-Assets zählen nicht gegen den Actions-Artefakt-Speicher des Accounts. Zusätzlich hängt die ZIP als Artefakt am Lauf (`CueCore-Windows.zip`, 7 Tage), wenn
der Speicher es zulässt.

## Schritt 4 — Weitergeben
Die ZIP aus dem Release verschicken. Wer sie erhält:
1. entpackt die ZIP → Ordner **`CueCore`**,
2. doppelklickt **`CueCore.exe`** (kein Setup, kein Admin),
3. CueCore öffnet ein eigenes Fenster (WebView2-Runtime nötig — bei Windows 10/11 mit Edge
   vorhanden); die GVL-Datenbank und die Track-Datenbank mit allen Library-Lieferungen sind
   eingebacken, der erste Start kopiert sie nur. Fenster schließen beendet CueCore.

Beim allerersten Start meldet Windows evtl. „Der Computer wurde durch Windows geschützt"
→ **Weitere Informationen → Trotzdem ausführen**. Normaler Nutzer, kein Admin —
sofern keine Richtlinie unsignierte Programme sperrt.

## Was drin ist
Voller Funktionsumfang inkl. **Tagger** (schreibt die Tags lokaler MP3-, WAV-, AIFF-, FLAC- und M4A-Dateien,
Ordner-Drop in die Drop-Zone), Track Resolver, Datenbank (Import/Export), EDL/Cue-Sheet, GVL-Suche und
**AcoustID-Fingerabdruck**: der Workflow legt `fpcalc.exe` (Chromaprint 1.6.1, offizielles Release,
SHA-256-geprüft, siehe `MusicMetaDataSearchEngine/bin/README.md`) ins Paket; ein
Windows-Lauf damit steht noch aus. Die Datenbanken
landen beim ersten Start in `%LOCALAPPDATA%\CueCore` (nutzer-schreibbar, kein Admin). Nicht
dabei: der Track-Index (6,5 GB) — siehe `docs/PACKAGING.md`.

---
*Getestet: der Build-Mechanismus (Bündelung, DB-Kopie beim ersten Start, Server) wurde
auf macOS mit demselben PyInstaller-Setup verifiziert. Den echten Windows-Build erzeugt
GitHub; den ersten Start auf einem Windows-Rechner bitte einmal bestätigen.*
