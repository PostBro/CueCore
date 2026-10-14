#!/bin/bash
# CueCore — Ein-Klick-Starter (macOS). Richtet sich beim ersten Start selbst ein
# (Python-venv + Abhängigkeiten + GVL-Datenbank) und öffnet danach den Browser.
# Spätere Starts sind sofort. Zum Beenden das Fenster schließen.

cd "$(dirname "$0")/MusicMetaDataSearchEngine" || { echo "Ordner MusicMetaDataSearchEngine nicht gefunden."; read -r; exit 1; }
mkdir -p ../docs ../data/archive   # für optionale Admin-Funktionen (schadet nie)

echo "── CueCore ─────────────────────────────────────────"

# 1) Python 3 vorhanden?
if ! command -v python3 >/dev/null 2>&1; then
  echo ""
  echo "Python 3 ist nicht installiert."
  echo "Bitte einmalig hier laden und installieren:  https://www.python.org/downloads/"
  echo "Danach dieses Fenster schließen und CueCore erneut starten."
  echo ""
  read -r -p "Enter zum Schließen …"
  exit 1
fi

# 2) Einmalige Einrichtung: virtuelle Umgebung + Abhängigkeiten
if [ ! -x ".venv/bin/python" ]; then
  echo ""
  echo "Erste Einrichtung … (einmalig, ca. 1–3 Minuten, braucht Internet)"
  python3 -m venv .venv || { echo "Konnte die Umgebung nicht anlegen."; read -r; exit 1; }
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  echo "Installiere Abhängigkeiten …"
  ./.venv/bin/python -m pip install --quiet -r requirements.txt \
    || { echo "Installation fehlgeschlagen (Internet? Python-Version 3.11+?)."; read -r; exit 1; }
  echo "Einrichtung fertig."
fi

# 3) GVL-Label-Datenbank einmalig bauen
DB="$HOME/Library/Application Support/MusicMeta/labels.db"
if [ ! -f "$DB" ]; then
  echo "Baue die GVL-Datenbank (einmalig) …"
  ./.venv/bin/python build_label_db.py || { echo "Datenbank-Aufbau fehlgeschlagen."; read -r; exit 1; }
fi

# 4) Starten + Browser öffnen
PORT=8765
echo ""
echo "CueCore läuft auf:  http://127.0.0.1:$PORT"
echo "(Falls der Port belegt ist: evtl. läuft CueCore schon in einem anderen Fenster.)"
echo "Zum Beenden dieses Fenster schließen."
echo "────────────────────────────────────────────────────"
( sleep 2; open "http://127.0.0.1:$PORT" ) &
exec ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port "$PORT"
