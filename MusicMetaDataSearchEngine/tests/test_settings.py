from core import settings


def test_set_get_status_and_clear(tmp_path):
    p = tmp_path / "settings.json"
    settings.set_keys({"discogs_consumer_key": "abc", "nicht_bekannt": "x"}, path=p)
    assert settings.get("discogs_consumer_key", path=p) == "abc"
    st = {s["key"]: s["set"] for s in settings.status(path=p)}
    assert st["discogs_consumer_key"] is True and st["audd_token"] is False
    # unbekannte Keys werden ignoriert
    assert "nicht_bekannt" not in settings.load(p)
    # leeren -> entfernen
    settings.set_keys({"discogs_consumer_key": ""}, path=p)
    assert settings.get("discogs_consumer_key", path=p) == ""


def test_status_marks_paid(tmp_path):
    p = tmp_path / "settings.json"
    paid = {s["key"]: s["paid"] for s in settings.status(path=p)}
    assert paid["audd_token"] is True and paid["discogs_consumer_key"] is False


def test_texts_set_ignores_unknown_and_clears(tmp_path):
    p = tmp_path / "settings.json"
    settings.set_texts({"unbekannt": "x"}, path=p)
    assert "unbekannt" not in settings.load(p)
    assert settings.texts_status(path=p) == []
    assert not (set(settings.KNOWN_TEXTS) & set(settings.KNOWN_KEYS) & set(settings.KNOWN_PATHS))


def test_partners_migrate_legacy_single_fields_once(tmp_path):
    """Die drei alten Einzelfelder (Host, Client-ID, Secret) werden beim ersten Lesen in EINEN
    Partner überführt und aus der Datei entfernt; ohne vollständiges Tripel bleibt die Liste leer."""
    p = tmp_path / "settings.json"
    settings.save({"trackmeta_host": "api.partner.example", "trackmeta_client_id": "test-client",
                   "trackmeta_client_secret": "S", "discogs_token": "d"}, p)
    ps = settings.partners(path=p)
    assert len(ps) == 1 and ps[0]["host"] == "api.partner.example" and ps[0]["client_secret"] == "S"
    assert ps[0]["id"].startswith("p") and ps[0]["name"] == "Library-Partner"
    d = settings.load(p)
    assert settings.PARTNERS_KEY in d and not any(k.startswith("trackmeta_") and k != settings.PARTNERS_KEY for k in d)
    assert d["discogs_token"] == "d"                                   # andere Keys unangetastet
    assert settings.partners(path=p)[0]["id"] == ps[0]["id"]           # zweites Lesen: stabil
    # unvollständig -> keine Migration, Felder trotzdem weg (sie gelten nicht mehr)
    p2 = tmp_path / "s2.json"
    settings.save({"trackmeta_host": "h"}, p2)
    assert settings.partners(path=p2) == [] and "trackmeta_host" not in settings.load(p2)


def test_set_partners_keeps_secret_when_ui_sends_empty(tmp_path):
    """Die UI schickt Secrets nie zurück: leeres Secret + bekannte id behält das gespeicherte,
    ohne Host/Client-ID fliegt der Eintrag raus, der Status zeigt kein Secret."""
    p = tmp_path / "settings.json"
    st = settings.set_partners([{"name": " Partner  A ", "host": "a.example", "client_id": "test-client", "client_secret": "S1"},
                                {"name": "Zwei", "host": "b.example", "client_id": "test-client-2", "client_secret": "S2"},
                                {"name": "Kaputt", "host": "", "client_id": "test-client-3", "client_secret": "S3"}], path=p)
    assert [x["name"] for x in st] == ["Partner A", "Zwei"]
    assert all("client_secret" not in x and x["secret_set"] and x["complete"] for x in st)
    pid = st[0]["id"]
    st2 = settings.set_partners([{"id": pid, "name": "Partner A", "host": "a.example", "client_id": "test-client", "client_secret": ""}], path=p)
    assert len(st2) == 1 and st2[0]["id"] == pid and st2[0]["secret_set"] is True
    assert settings.partners(path=p)[0]["client_secret"] == "S1"
    st3 = settings.set_partners([{"id": pid, "name": "Partner A", "host": "a.example", "client_id": "test-client", "client_secret": "S9"}], path=p)
    assert settings.partners(path=p)[0]["client_secret"] == "S9"      # rotiert
    assert settings.set_partners([], path=p) == []


def test_every_setting_belongs_to_a_known_group_and_groups_are_ordered():
    keys = set(g["key"] for g in settings.GROUPS)
    for meta in (*settings.KNOWN_KEYS.values(), *settings.KNOWN_PATHS.values(), *settings.KNOWN_TEXTS.values()):
        assert meta["group"] in keys, meta["label"]
    assert [g["key"] for g in settings.GROUPS][:2] == ["ordner", "trackmeta"]
    assert {g["key"] for g in settings.GROUPS if g["test"]} == {"trackmeta", "editingtools", "discogs",
                                                               "genius", "acoustid"}
    st = {s["key"]: s["group"] for s in settings.status()}
    assert st["discogs_token"] == "discogs"
    assert next(g for g in settings.GROUPS if g["key"] == "trackmeta")["partners"] is True
    assert {p["key"]: p["group"] for p in settings.paths_status()}["lc_verified_dir"] == "ordner"


# --- Eingebauter AcoustID-Key: nicht im Repo, nicht in der Oberfläche ---

def test_eingebauter_key_verschleiert_und_eigener_key_gewinnt(tmp_path, monkeypatch):
    blob = settings.encode_builtin({"acoustid_api_key": "zzqAppKey123"})
    assert b"zzqAppKey123" not in blob                                  # nicht im Klartext im Bundle
    f = tmp_path / "builtin_keys.bin"
    f.write_bytes(blob)
    monkeypatch.setattr(settings, "BUILTIN_PATH", f)
    p = tmp_path / "settings.json"
    assert settings.builtin("acoustid_api_key") == "zzqAppKey123"
    assert settings.effective("acoustid_api_key", p) == "zzqAppKey123"
    settings.set_keys({"acoustid_api_key": "eigenerKey"}, p)
    assert settings.effective("acoustid_api_key", p) == "eigenerKey"     # eigener Key gewinnt
    st = {s["key"]: s for s in settings.status(p)}
    assert st["acoustid_api_key"]["builtin"] is True and st["discogs_token"]["builtin"] is False
    assert "zzqAppKey123" not in repr(settings.status(p))                # nie den Wert preisgeben


def test_ohne_datei_kein_eingebauter_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BUILTIN_PATH", tmp_path / "fehlt.bin")
    assert settings.builtin("acoustid_api_key") == ""
    assert settings.effective("acoustid_api_key", tmp_path / "s.json") == ""


# --- Eingebauter Partner-Zugang (Profil-Schlüssel `partner`), Testwerte erfunden ---
import json as _json

_PROFILE = {"base": "/zzq", "token": "/t", "meta": "/m", "scope": "s", "batch": 50, "ttl": 60, "list": "tracks",
            "fields": {}, "partner": {"name": "Zzq Library", "host": "zzq.example", "client_id": "7", "client_secret": "geheim-zzq"}}


def test_eingebauter_partner_ergaenzt_die_eigenen_und_wird_nie_gespeichert(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv(settings.BUILTIN_ENV["trackmeta_profile"], _json.dumps(_PROFILE))
    st = settings.partners_status(path)
    assert st == [{"id": "builtin", "name": "Zzq Library", "host": "", "client_id": "", "secret_set": True,
                   "complete": True, "builtin": True}]
    assert settings.active_partners(path)[0]["client_secret"] == "geheim-zzq"
    settings.set_partners([{"id": "builtin", "name": "x", "host": "h", "client_id": "1", "client_secret": "y"},
                           {"name": "Eigen", "host": "eigen.example", "client_id": "3", "client_secret": "z"}], path)
    assert [p["name"] for p in settings.active_partners(path)] == ["Eigen", "Zzq Library"]   # eigene zuerst
    assert "builtin" not in path.read_text() and "geheim-zzq" not in path.read_text()
    # eigener Zugang mit demselben Host und derselben Client-ID ersetzt den eingebauten
    settings.set_partners([{"name": "Meins", "host": "zzq.example", "client_id": "7", "client_secret": "anders"}], path)
    assert [p["name"] for p in settings.active_partners(path)] == ["Meins"]


def test_ohne_vollstaendigen_zugang_kein_eingebauter_partner(tmp_path, monkeypatch):
    for broken in ({**_PROFILE, "partner": {"host": "zzq.example", "client_id": "7"}}, {k: v for k, v in _PROFILE.items() if k != "partner"}):
        monkeypatch.setenv(settings.BUILTIN_ENV["trackmeta_profile"], _json.dumps(broken))
        assert settings.builtin_partner() is None and settings.partners_status(tmp_path / "s.json") == []
    monkeypatch.setenv(settings.BUILTIN_ENV["trackmeta_profile"], "kein json")
    assert settings.builtin_partner() is None


COLS = ("Interpret", "Titel", "Komponist", "LC", "Länge Sek.")


def test_export_vorlagen_vorgabe_speichern_und_saeubern(tmp_path):
    path = tmp_path / "settings.json"
    rows, active = settings.export_templates(COLS, path)
    assert rows == [{"id": "standard", "name": "Standard", "required": ["labelcode", "komponist", "gema_werknummer"],
                     "columns": list(COLS)}] and active == "standard"
    rows, active = settings.set_export_templates([
        {"id": "a", "name": "  Kurz  ", "required": ["labelcode", "erfunden"], "columns": ["LC", "Titel", "Quatsch"]},
        {"id": "b", "name": "", "columns": []},                                       # ohne Namen: fällt weg
        {"name": "Voll"}], "a", COLS, path)
    assert [r["name"] for r in rows] == ["Kurz", "Voll"] and active == "a"
    assert rows[0]["required"] == ["labelcode"] and rows[0]["columns"] == ["Titel", "LC"]   # Reihenfolge wie Export
    assert rows[1]["columns"] == list(COLS) and rows[1]["id"]
    assert settings.set_export_templates(rows, "weg", COLS, path)[1] == "a"
