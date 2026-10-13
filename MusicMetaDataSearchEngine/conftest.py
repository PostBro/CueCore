import os
import sys
from pathlib import Path

# Kein Library-Import beim App-Start in Tests (app._load): jede frische Test-songs.db bekäme sonst die
# 60.938 Cezame-Zeilen aus data/library_tracks (3 s je Test). Der Import selbst hat eigene Tests.
os.environ.setdefault("CUECORE_SKIP_LIBRARY_IMPORT", "1")
# Tests laufen nie gegen die echte settings.json des Nutzers (API-Keys!): ein gesetzter Genius-/Discogs-Token würde
# sonst Netz-Hooks scharf schalten, die Tests nicht mocken. Leere Datei im Temp-Ordner = alle Dienste unkonfiguriert.
import tempfile
_settings_dir = tempfile.mkdtemp(prefix="cuecore-test-settings-")
os.environ.setdefault("CUECORE_SETTINGS", os.path.join(_settings_dir, "settings.json"))
# Und nie gegen die produktive Datenbank des Nutzers: app._load() öffnet beim Import DB_PATH.parent/songs.db, und
# Tests, die nicht selbst configure(tmp_path) rufen, schrieben sonst in die produktive songs.db — ein Test-Datensatz
# bliebe dort stehen und ließe beim nächsten Lauf denselben Test mit 409 Conflict scheitern. CUECORE_DB legt
# Daten- UND Labelordner ins Temp.
_db_dir = tempfile.mkdtemp(prefix="cuecore-test-db-")
os.environ.setdefault("CUECORE_DB", os.path.join(_db_dir, "labels.db"))

sys.path.insert(0, str(Path(__file__).resolve().parent))
