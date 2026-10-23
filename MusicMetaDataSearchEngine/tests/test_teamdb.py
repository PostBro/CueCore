"""Song-Datenbank fürs Team (Teil D16). Testdaten frei erfunden."""
from __future__ import annotations

from core import songsdb, teamdb


def _db(path, rows):
    con = songsdb.open(path)
    for r in rows:
        songsdb.upsert(con, **r)
    con.commit()
    return con


def test_export_und_import_bei_der_kollegin(tmp_path):
    """Hin und zurück: neue Songs kommen mit Prüfstatus und Datum, verifiziert nur mit LC im Master, eigene Werte
    gewinnen, leere Felder werden gefüllt, Abweichungen stehen in der Liste."""
    mine = _db(tmp_path / "a.db", [
        dict(interpret="Erika Testerin", titel="Nordwand", labelcode="LC00121", labelname="Atlantic", komponist="Hans Zimm",
             quelle="manuell", review_status="verified", date_verified="2026-10-01"),
        dict(interpret="Erika Testerin", titel="Südhang", labelcode="LC24584", komponist="Anna Zonk", verlag="Zzq Edition",
             quelle="manuell", review_status="verified", date_verified="2026-10-02"),
        dict(interpret="Erika Testerin", titel="Fremdcode", labelcode="LC99999", quelle="manuell", review_status="verified"),
        dict(interpret="Erika Testerin", titel="Ungeprüft", labelcode="LC00121", quelle="liste", review_status="review")])
    team = tmp_path / "team.csv"
    teamdb.export_team_csv(songsdb.all_songs(mine), team)
    assert teamdb.is_team_file(team)

    theirs = _db(tmp_path / "b.db", [
        dict(interpret="Erika Testerin", titel="Südhang", labelcode="LC24584", komponist="Anderer Name", quelle="eigen")])
    rep = teamdb.plan(theirs, teamdb.read_team(team), master_codes={"00121", "24584"})
    assert sorted(r["titel"] for r in rep["new"]) == ["Fremdcode", "Nordwand", "Ungeprüft"]
    status = {r["titel"]: r["review_status"] for r in rep["new"]}
    assert status == {"Nordwand": "verified", "Fremdcode": "review", "Ungeprüft": "review"} and rep["review"] == 2
    assert rep["filled"] == [{"interpret": "Erika Testerin", "titel": "Südhang", "fields": {"verlag": "Zzq Edition"}}]
    assert rep["conflicts"][0]["diffs"] == [{"field": "komponist", "own": "Anderer Name", "team": "Anna Zonk"}]

    teamdb.apply(theirs, rep, str(team))
    row = songsdb.find_row(theirs, "Erika Testerin", "Nordwand")
    assert row["review_status"] == "verified" and row["date_verified"] == "2026-10-01" and row["labelcode"] == "LC00121"
    assert theirs.execute("SELECT quelle FROM songs WHERE titel='Nordwand'").fetchone()[0] == "team:team.csv"
    sud = songsdb.find_row(theirs, "Erika Testerin", "Südhang")
    assert sud["komponist"] == "Anderer Name" and sud["verlag"] == "Zzq Edition"     # eigene Werte bleiben
    again = teamdb.plan(theirs, teamdb.read_team(team), master_codes={"00121", "24584"})
    assert again["new"] == [] and again["filled"] == [] and again["same"] == 3       # zweiter Lauf legt nichts neu an


def test_normale_liste_ist_keine_teamdatei(tmp_path):
    p = tmp_path / "liste.csv"
    p.write_text("Interpret,Titel,LC\nErika Testerin,Nordwand,LC00121\n", encoding="utf-8")
    assert not teamdb.is_team_file(p)
