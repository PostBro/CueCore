#!/bin/bash
# CueCore — Doppelklick-Starter (macOS). Baut die Label-DB einmalig, startet das
# Backend und öffnet den Browser.
cd "$(dirname "$0")" || exit 1

DB="$HOME/Library/Application Support/MusicMeta/labels.db"
if [ ! -f "$DB" ]; then
  echo "Baue GVL-Label-DB (einmalig) …"
  .venv/bin/python build_label_db.py || { echo "DB-Build fehlgeschlagen."; read -r; exit 1; }
fi

PORT=8765
echo "CueCore läuft auf http://127.0.0.1:$PORT  (Strg+C beendet)"
( sleep 1.6; open "http://127.0.0.1:$PORT" ) &
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port "$PORT"
