import os
import sys
from pathlib import Path

# Kein Library-Import beim App-Start in Tests (app._load): jede frische Test-songs.db bekäme sonst die
# 60.938 Cezame-Zeilen aus data/library_tracks (3 s je Test). Der Import selbst hat eigene Tests.
os.environ.setdefault("CUECORE_SKIP_LIBRARY_IMPORT", "1")
os.environ.setdefault("CUECORE_SKIP_INDEX_AUTOLOAD", "1")
# Test-labels.db tragen eigene, alte Stände: kein Neuaufbau aus dem mitgelieferten Master (hat eigenen Test)
os.environ.setdefault("CUECORE_SKIP_MASTER_REBUILD", "1")   # nie 1,3 GB aus einem Testlauf laden
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
# Track-Metadata-API: erfundenes Partner-Profil (core/trackmeta.py) — das echte steht nie im Repo und wird hier
# überschrieben, damit Tests nie gegen echte Pfade laufen.
import json
os.environ["CUECORE_TRACKMETA_PROFILE"] = json.dumps({
    "base": "/zzq/v1", "token": "/zzq/token", "meta": "/zzq/lookup", "scope": "zzq-scope",
    "batch": 10, "ttl": 900, "list": "names",
    "fields": {"titel": "zz_title", "interpret": "zz_artist", "komponist": "zz_composer", "texter": "zz_writer",
               "album": "zz_album", "labelname": "zz_label", "labelcode": "zz_lc", "tracknummer": "zz_no",
               "isrc": "zz_isrc"}})

sys.path.insert(0, str(Path(__file__).resolve().parent))
