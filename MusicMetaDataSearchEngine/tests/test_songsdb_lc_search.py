"""Suche nach Labelcode in der Song-Datenbank (C6). Testdaten frei erfunden."""
from core import songsdb


def _db(tmp_path):
    con = songsdb.open(tmp_path / "s.db")
    songsdb.upsert(con, interpret="Zzq", titel="Nordwand", labelname="Zzq Label", labelcode="LC07552")
    songsdb.upsert(con, interpret="Zzq", titel="Andere", labelname="X", labelcode="LC17552")
    songsdb.upsert(con, interpret="Zzq", titel="Werk", labelname="Y", labelcode="LC00121", gema_werknummer="1075521")
    songsdb.upsert(con, interpret="Erika Testerin", titel="1999", labelname="Y", labelcode="LC00121")
    con.commit()
    return con


def _titles(con, q):
    return sorted(r["titel"] for r in songsdb.list_songs(con, query=q)["rows"])


def test_labelcode_in_every_spelling(tmp_path):
    con = _db(tmp_path)
    for q in ("LC07552", "LC 07552", "lc7552", "LC-7552"):
        assert _titles(con, q) == ["Nordwand"], q


def test_bare_number_is_exact_on_the_labelcode_but_a_substring_elsewhere(tmp_path):
    con = _db(tmp_path)
    assert _titles(con, "7552") == ["Nordwand", "Werk"]        # nicht LC17552; Werk über die GEMA-Werknummer
    assert _titles(con, "07552") == ["Nordwand", "Werk"]
    assert _titles(con, "1999") == ["1999"]                    # Zahl als Titel bleibt auffindbar
    assert _titles(con, "zzq 7552") == ["Nordwand", "Werk"]


# --- „Meintest du …?“ (C5) ---

def test_suggest_query_fixes_typos_only_when_it_then_finds_something(tmp_path):
    con = _db(tmp_path)
    songsdb.upsert(con, interpret="Erika Testerin", titel="Nordwind Sonate", labelcode="LC00121")
    con.commit()
    assert songsdb.suggest_query(con, "Nordwnad") == "Nordwand"
    assert songsdb.suggest_query(con, "erika testrin sonate") == "erika Testerin sonate"
    assert songsdb.suggest_query(con, "Nordwand") == ""                  # trifft schon
    assert songsdb.suggest_query(con, "Qxjzv") == ""                     # nichts Ähnliches
    assert songsdb.suggest_query(con, "LC07553") == "" and songsdb.suggest_query(con, "") == ""
