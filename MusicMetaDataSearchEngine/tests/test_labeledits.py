"""Label-Datenbank als Liste: Paging/Filter und die eigene Schicht label_edits.json (Kürzel, LC-Korrekturen).
Testdaten frei erfunden."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app as appmod
from app import app
from core import labeledits
from core.labeldb import build_label_db
from core.labelmatch import LabelIndex, catalog_lookup, match_label
from core.models import LabelRecord

client = TestClient(app)

SAMPLE = [
    LabelRecord("91001", "Zzq Library", "ZZQ", "Ja", "Zzq Verlag", ""),
    LabelRecord("91002", "Zzq Archive", "", "Ja", "Zzq Verlag", "R6"),
    LabelRecord("91003", "Testprojekt Records", "", "", "Testprojekt GmbH", "R6;R12"),
    LabelRecord("91004", "Quorx Sounds", "", "", "Quorx", ""),
]


@pytest.fixture(autouse=True)
def _nur_testdaten(monkeypatch):
    """Ohne die mitgelieferten Zusatz-Codes: die Liste soll genau die erfundenen Zeilen zeigen."""
    monkeypatch.setattr(appmod, "read_supplemental", lambda *a, **k: [])


def _setup(tmp_path, prefixes: str = "prefix,labelcode,label_gvl,library,list_label,source\nQXA,91004,Quorx Sounds,Quorx,,t\n"):
    db = tmp_path / "labels.db"
    build_label_db(SAMPLE, db, "2026-10-13")
    (tmp_path / "library_prefixes_user.csv").write_text(prefixes, encoding="utf-8")
    appmod.configure(db)
    return tmp_path


# --- Modul ---------------------------------------------------------------------------------------

def test_set_prefix_merkt_nur_abweichungen(tmp_path):
    data = {"prefixes": [], "names": []}
    assert labeledits.set_prefix(data, "qxa", "91004", base_code="91004", note="", date="2026-10-23") is None
    e = labeledits.set_prefix(data, "qxa", "91001", base_code="91004", note="laut Anbieter", date="2026-10-23")
    assert e["prefix"] == "QXA" and data["prefixes"] == [e]
    labeledits.set_prefix(data, "QXA", "91004", base_code="91004", note="", date="2026-10-23")   # zurück = weg
    assert data["prefixes"] == []


def test_lesen_ueberlebt_kaputte_datei(tmp_path):
    (tmp_path / labeledits.FILE).write_text("{kaputt", encoding="utf-8")
    assert labeledits.read(tmp_path) == {"prefixes": [], "names": []}
    (tmp_path / labeledits.FILE).write_text(json.dumps({"prefixes": [1, {"prefix": ""}, {"prefix": "AB", "labelcode": 5}],
                                                        "names": [{"name": "x"}]}), encoding="utf-8")
    assert labeledits.read(tmp_path) == {"prefixes": [{"prefix": "AB", "labelcode": "5", "note": "", "date": ""}], "names": []}


def test_apply_korrektur_gewinnt_vor_master_und_kuerzel():
    idx = LabelIndex(code2name={"91001": "Zzq Library", "91004": "Quorx Sounds"},
                     full={"zzq library": {"91001"}, "quorx sounds": {"91004"}},
                     prefix2code={"QXA": {"labelcode": "91004", "library": "Quorx", "prefix": "QXA"}})
    data = {"prefixes": [{"prefix": "QXA", "labelcode": "91001", "note": "n", "date": ""},
                         {"prefix": "NEU", "labelcode": "99999", "note": "", "date": ""}],   # Code unbekannt: greift nicht
            "names": [{"name": "Quorx Sounds", "from": "91004", "labelcode": "91001", "note": "n", "date": ""}]}
    labeledits.apply(idx, data)
    assert catalog_lookup("QXA", idx)[0] == "91001"
    assert "NEU" not in idx.prefix2code
    assert match_label("Quorx Sounds", idx) == "91001"


# --- Liste ---------------------------------------------------------------------------------------

def test_liste_seitenweise_und_filter(tmp_path):
    _setup(tmp_path)
    j = client.get("/api/labels/list", params={"limit": 2}).json()
    assert j["total"] == 4 and [r["label"] for r in j["rows"]] == ["Quorx Sounds", "Testprojekt Records"]
    j2 = client.get("/api/labels/list", params={"limit": 2, "offset": 2}).json()
    assert [r["label"] for r in j2["rows"]] == ["Zzq Archive", "Zzq Library"]
    assert {r["labelcode"] for r in client.get("/api/labels/list", params={"plm": True}).json()["rows"]} == {"91001", "91002"}
    k = client.get("/api/labels/list", params={"kuerzel": True}).json()["rows"]
    assert [(r["labelcode"], [x["prefix"] for x in r["kuerzel"]]) for r in k] == [("91004", ["QXA"])]
    assert k[0]["kuerzel"][0]["origin"] == "nutzerliste"


def test_liste_suche_nach_lc_name_und_kuerzel(tmp_path):
    _setup(tmp_path)
    assert [r["labelcode"] for r in client.get("/api/labels/list", params={"q": "LC 91003"}).json()["rows"]] == ["91003"]
    assert [r["labelcode"] for r in client.get("/api/labels/list", params={"q": "zzq"}).json()["rows"]] == ["91002", "91001"]
    assert [r["labelcode"] for r in client.get("/api/labels/list", params={"q": "zzq library"}).json()["rows"]][0] == "91001"
    assert [r["labelcode"] for r in client.get("/api/labels/list", params={"q": "qxa"}).json()["rows"]] == ["91004"]


# --- Kürzel bearbeiten ---------------------------------------------------------------------------

def test_kuerzel_anlegen_umhaengen_entfernen(tmp_path):
    base = _setup(tmp_path)
    r = client.post("/api/labels/edits/prefix", json={"prefix": "zzx", "labelcode": "91002"})
    assert r.status_code == 200 and r.json()["before"] == "" and r.json()["after"] == "91002"
    assert catalog_lookup("ZZX", appmod.INDEX) == ("91002", "Library-Kürzel ZZX (eigene Zuordnung)")
    # Umhängen gegen die Tabelle braucht eine Notiz
    r = client.post("/api/labels/edits/prefix", json={"prefix": "QXA", "labelcode": "91003"})
    assert r.status_code == 409 and "LC91004 Quorx Sounds" in r.json()["detail"] and "Notiz" in r.json()["detail"]
    r = client.post("/api/labels/edits/prefix", json={"prefix": "QXA", "labelcode": "LC91003", "note": "Notiz Zzq"})
    assert r.status_code == 200 and r.json()["before"] == "91004"
    rows = {x["labelcode"]: x for x in client.get("/api/labels/list", params={"herkunft": "eigen"}).json()["rows"]}
    assert set(rows) == {"91002", "91003", "91004"}
    assert [(k["prefix"], k["origin"]) for k in rows["91003"]["kuerzel"]] == [("QXA", "korrektur")]
    assert [(k["origin"], k["to"]) for k in rows["91004"]["kuerzel"]] == [("verschoben", "91003")]
    assert rows["91003"]["herkunft"] == "korrektur" and rows["91002"]["herkunft"] == "gvl"
    # Gelieferte Zuordnung entfernen: bleibt als „entfernt“ sichtbar; Rückgängig = alten Code setzen
    client.post("/api/labels/edits/prefix", json={"prefix": "QXA", "labelcode": ""})
    assert "QXA" not in appmod.INDEX.prefix2code
    k = client.get("/api/labels/list", params={"q": "91004"}).json()["rows"][0]["kuerzel"]
    assert [(x["prefix"], x["origin"]) for x in k] == [("QXA", "entfernt")]
    client.post("/api/labels/edits/prefix", json={"prefix": "QXA", "labelcode": "91004"})
    assert [p["prefix"] for p in labeledits.read(base)["prefixes"]] == ["ZZX"]
    client.post("/api/labels/edits/prefix", json={"prefix": "ZZX", "labelcode": ""})
    assert labeledits.read(base)["prefixes"] == []


def test_kuerzel_unbekannter_code_und_gvl_kurzname(tmp_path):
    _setup(tmp_path)
    r = client.post("/api/labels/edits/prefix", json={"prefix": "ABC", "labelcode": "12345"})
    assert r.status_code == 422 and "erst als Label anlegen" in r.json()["detail"]
    r = client.post("/api/labels/edits/prefix", json={"prefix": "ZZQ", "labelcode": ""})
    assert r.status_code == 422 and "GVL-Kurzname" in r.json()["detail"]
    assert client.post("/api/labels/edits/prefix", json={"prefix": "ZZQ", "labelcode": "91002"}).status_code == 409


def test_eigene_schicht_ueberlebt_neuaufbau(tmp_path):
    """Ein GVL-Update baut labels.db neu — label_edits.json liegt daneben und greift beim nächsten Laden wieder."""
    _setup(tmp_path)
    client.post("/api/labels/edits/prefix", json={"prefix": "QXA", "labelcode": "91003", "note": "n"})
    client.post("/api/labels/edits/name", json={"name": "Quorx Sounds", "from_code": "91004", "labelcode": "91001", "note": "n"})
    build_label_db(SAMPLE, tmp_path / "labels.db", "2026-11-01")
    appmod.configure(tmp_path / "labels.db")
    assert catalog_lookup("QXA", appmod.INDEX)[0] == "91003"
    assert match_label("Quorx Sounds", appmod.INDEX) == "91001"


# --- LC-Korrektur am Namen -----------------------------------------------------------------------

def test_lc_korrektur_braucht_notiz_und_ist_markiert(tmp_path):
    base = _setup(tmp_path)
    body = {"name": "Testprojekt Records", "from_code": "91003", "labelcode": "91002"}
    assert client.post("/api/labels/edits/name", json=body).status_code == 422
    r = client.post("/api/labels/edits/name", json={**body, "note": "Pressung geprüft"})
    assert r.status_code == 200 and r.json()["before"] is None
    assert match_label("Testprojekt Records", appmod.INDEX) == "91002"
    row = client.get("/api/labels/list", params={"q": "91003"}).json()["rows"][0]
    assert row["herkunft"] == "korrektur"
    assert row["korrektur"]["labelcode"] == "91002" and row["korrektur"]["label"] == "Zzq Archive" and row["korrektur"]["valid"]
    # GVL-Master selbst unverändert
    assert client.get("/api/search", params={"q": "91003"}).json()["results"][0]["label"] == "Testprojekt Records"
    # Aufheben
    r = client.post("/api/labels/edits/name", json={"name": "Testprojekt Records", "from_code": "91003", "labelcode": ""})
    assert r.json()["before"]["labelcode"] == "91002"
    assert labeledits.read(base)["names"] == [] and match_label("Testprojekt Records", appmod.INDEX) == "91003"


def test_gelernte_aliasse_kommen_nach_dem_neustart_in_den_index(tmp_path):
    """label_aliases_user.csv wurde beim Laden gelesen, aber nie in den geteilten Index übernommen (fehlte in
    _INDEX_MAPS) — eine gelernte Korrektur galt nur bis zum nächsten Start."""
    (tmp_path / "label_aliases_user.csv").write_text("name,labelcode,quelle,datum\nZzq Imprint,91002,test,2026-10-23\n",
                                                     encoding="utf-8")
    _setup(tmp_path)
    assert match_label("Zzq Imprint", appmod.INDEX) == "91002"
