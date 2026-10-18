# Mitgelieferte Binaries

## `fpcalc` — Chromaprint 1.6.1, macOS universal (x86_64 + arm64)

Rechnet den akustischen Fingerabdruck einer Audiodatei; `core/acoustid.py` ruft ihn mit `-json`.
Nicht selbst gebaut, sondern das offizielle Release:

- Quelle: https://github.com/acoustid/chromaprint/releases/download/v1.6.1/chromaprint-fpcalc-1.6.1-macos-universal.tar.gz
- Release v1.6.1 vom 2026-07-28, geladen am 2026-10-09
- SHA-256 des Archivs: `240aeb5a8c8205af458e3625cb7487b826b711a999e491ef00111f3cebd76f00`
- SHA-256 von `fpcalc`:  `e2913e40da41f596dd547360fc09d685cfff04a24460c3b0f90ca20a5d98e0ca`

Es haengt nur an System-Bibliotheken (libSystem, Accelerate, libz, libc++) — FFmpeg ist statisch
drin. Deshalb laeuft es ueberall, ohne Homebrew. Ein Homebrew-`fpcalc` haengt dagegen an den FFmpeg-Bibliotheken der Homebrew-Installation und startet nur,
solange diese zusammenpassen.

Windows (`fpcalc.exe`) liegt nicht im Repo: `.github/workflows/build-windows.yml` laedt es beim Build aus
demselben Release (`chromaprint-fpcalc-1.6.1-windows-x86_64.zip`, geprueft 12.10.2026) und bricht ab, wenn
die SHA-256 des Archivs nicht `735d6182b38e9f364b84ce6f4ccd682c75e2851de89735711d6b762d12b92a4e` ist.

Pruefen: `shasum -a 256 bin/fpcalc` muss den Wert oben ergeben.
