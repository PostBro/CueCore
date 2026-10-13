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
    settings.save({"trackmeta_host": "api.fresh.example", "trackmeta_client_id": "test-client",
                   "trackmeta_client_secret": "S", "discogs_token": "d"}, p)
    ps = settings.partners(path=p)
    assert len(ps) == 1 and ps[0]["host"] == "api.fresh.example" and ps[0]["client_secret"] == "S"
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
    st = settings.set_partners([{"name": " Fresh  Tracks ", "host": "a.example", "client_id": "test-client", "client_secret": "S1"},
                                {"name": "Zwei", "host": "b.example", "client_id": "test-client-2", "client_secret": "S2"},
                                {"name": "Kaputt", "host": "", "client_id": "test-client-3", "client_secret": "S3"}], path=p)
    assert [x["name"] for x in st] == ["Fresh Tracks", "Zwei"]
    assert all("client_secret" not in x and x["secret_set"] and x["complete"] for x in st)
    pid = st[0]["id"]
    st2 = settings.set_partners([{"id": pid, "name": "Fresh Tracks", "host": "a.example", "client_id": "test-client", "client_secret": ""}], path=p)
    assert len(st2) == 1 and st2[0]["id"] == pid and st2[0]["secret_set"] is True
    assert settings.partners(path=p)[0]["client_secret"] == "S1"
    st3 = settings.set_partners([{"id": pid, "name": "Fresh Tracks", "host": "a.example", "client_id": "test-client", "client_secret": "S9"}], path=p)
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
