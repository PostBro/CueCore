from core.labeldb import build_label_db, connect, lookup_by_code, search_labels
from core.models import LabelRecord

SAMPLE = [
    LabelRecord("00121", "Atlantic", "", "", "Warner", "R6;R9;R12;R13"),
    LabelRecord("0309", "Foo Bar", "", "ja", "Some GmbH", "R6"),   # 4-stellig -> 00309, plm 1
    LabelRecord("07552", "Republic", "", "", "Universal", "R6"),   # 1:n LC -> mehrere Label
    LabelRecord("07552", "Z Records", "", "", "Z Music", ""),
    LabelRecord("101515", "Mom and Pop", "", "", "Sony", "R12"),
]


def _build(tmp_path):
    db = tmp_path / "labels.db"
    n = build_label_db(SAMPLE, db, "2026-06-23")
    return db, n


def test_row_count(tmp_path):
    _, n = _build(tmp_path)
    assert n == len(SAMPLE)


def test_labelcode_is_text_with_leading_zeros(tmp_path):
    db, _ = _build(tmp_path)
    con = connect(db)
    row = con.execute(
        "SELECT labelcode, typeof(labelcode) AS t FROM labels WHERE label='Foo Bar'"
    ).fetchone()
    con.close()
    assert row["labelcode"] == "00309"   # 4-stellig auf 5 gepaddet
    assert row["t"] == "text"            # labelcode NIE Integer


def test_plm_flag(tmp_path):
    db, _ = _build(tmp_path)
    con = connect(db)
    plm = {r["label"]: r["plm"] for r in con.execute("SELECT label, plm FROM labels")}
    con.close()
    assert plm["Foo Bar"] == 1           # 'ja' -> 1
    assert plm["Atlantic"] == 0


def test_lookup_by_code_1_to_n(tmp_path):
    db, _ = _build(tmp_path)
    con = connect(db)
    rows = lookup_by_code(con, "LC07552")   # Präfix 'LC' + Normalisierung
    con.close()
    assert sorted(r["label"] for r in rows) == ["Republic", "Z Records"]


def test_search_labels_fts_covers_label_and_hersteller(tmp_path):
    db, _ = _build(tmp_path)
    con = connect(db)
    by_label = [r["label"] for r in search_labels(con, "republic")]
    by_hersteller = [r["label"] for r in search_labels(con, "universal")]
    con.close()
    assert "Republic" in by_label
    assert "Republic" in by_hersteller   # FTS deckt label UND hersteller


def test_meta_stored(tmp_path):
    db, n = _build(tmp_path)
    con = connect(db)
    meta = dict(con.execute("SELECT key, value FROM meta"))
    con.close()
    assert meta["source_version"] == "2026-06-23"
    assert meta["row_count"] == str(n)


def test_idempotent_rebuild(tmp_path):
    db, _ = _build(tmp_path)
    n2 = build_label_db(SAMPLE, db, "2026-06-23")   # zweiter Lauf
    con = connect(db)
    total = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    con.close()
    assert n2 == len(SAMPLE)
    assert total == len(SAMPLE)                      # kein Doppel-Insert


# --- Händisch angelegte Labels (überleben den Neuaufbau aus dem GVL-PDF) ---

def test_manual_labels_are_inserted_once_and_searchable(tmp_path):
    from core.labeldb import add_manual_labels, ensure_source_column
    db, _ = _build(tmp_path)
    con = connect(db)
    ensure_source_column(con)
    rows = [{"labelcode": "LC 99001", "label": "Fresh Tracks Music", "hersteller": "Fresh Tracks GmbH"},
            {"labelcode": "00121", "label": "Atlantic", "hersteller": ""}]          # steht schon im Master
    assert add_manual_labels(con, rows) == 1                                          # nur das neue
    assert add_manual_labels(con, rows) == 0                                          # idempotent
    hit = lookup_by_code(con, "99001")
    assert len(hit) == 1 and hit[0]["label"] == "Fresh Tracks Music" and hit[0]["source"] == "manuell"
    assert lookup_by_code(con, "00121")[0]["source"] == "gvl"
    assert any(r["labelcode"] == "99001" for r in search_labels(con, "fresh tracks"))   # FTS kennt es
    assert any(r["labelcode"] == "99001" for r in search_labels(con, "Fresh Tracks GmbH"))
    con.close()


def test_supplemental_labels_added_once_master_and_manual_win(tmp_path):
    """Selbstheilung: Zusatz-Codes in eine bestehende DB nachtragen — nur unbekannte Codes; GVL-Master
    (00121) und manuelle Zeilen (99002) gewinnen; zweiter Lauf trägt nichts doppelt ein."""
    from core.labeldb import add_manual_labels, add_supplemental_labels
    db, n = _build(tmp_path)
    con = connect(db)
    add_manual_labels(con, [{"labelcode": "99002", "label": "Handangelegt"}])
    supp = [LabelRecord("99001", "Zusatz Library", "", "", "Zusatzquelle: KPM-PDF", ""),
            LabelRecord("00121", "Atlantic Doppelt", "", "", "Zusatzquelle: x", ""),
            LabelRecord("99002", "Zusatz statt manuell", "", "", "Zusatzquelle: x", ""),
            LabelRecord("99001", "Dublette in der Liste", "", "", "Zusatzquelle: x", "")]
    assert add_supplemental_labels(con, supp) == 1
    assert add_supplemental_labels(con, supp) == 0                       # idempotent
    rows = con.execute("SELECT labelcode,label,hersteller,source FROM labels WHERE labelcode IN ('99001','00121','99002') "
                       "ORDER BY labelcode").fetchall()
    assert [tuple(r) for r in rows] == [("00121", "Atlantic", "Warner", "gvl"),
                                        ("99001", "Zusatz Library", "Zusatzquelle: KPM-PDF", "gvl"),
                                        ("99002", "Handangelegt", "", "manuell")]
    assert search_labels(con, "Zusatz Library")[0]["labelcode"] == "99001"   # FTS neu gebaut
    assert con.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == n + 2
    con.close()


def test_manual_label_removed(tmp_path):
    from core.labeldb import add_manual_labels, ensure_source_column, remove_manual_label
    db, _ = _build(tmp_path)
    con = connect(db)
    ensure_source_column(con)
    add_manual_labels(con, [{"labelcode": "99002", "label": "Tmp Label", "hersteller": ""}])
    assert remove_manual_label(con, "99002", "Tmp Label") == 1
    assert lookup_by_code(con, "99002") == []
    assert not search_labels(con, "Tmp Label")
    assert remove_manual_label(con, "00121", "Atlantic") == 0     # GVL-Zeilen sind unantastbar
    con.close()


def test_search_labels_puts_exact_name_first(tmp_path):
    """'Atlantic' steht vor AFRO-ATLANTIC, Atlantic Heights Records …; bei mehr Präfix-Treffern als `limit`
    darf der exakte Name nicht herausfallen."""
    others = [LabelRecord(f"{50000 + i}", f"Atlantic Heights {i} Records", "", "", "Atlantic Atlantic GmbH", "")
              for i in range(40)]
    recs = others + [LabelRecord("100688", "AFRO-ATLANTIC", "", "", "Atlantic", ""),
                     LabelRecord("00121", "Atlantic", "", "", "Warner Music", "R6;R9"),
                     LabelRecord("88888", "PLATTENKÜCHE", "", "", "x", "")]
    db = tmp_path / "labels.db"
    build_label_db(recs, db, "2026-10-12")
    con = connect(db)
    assert [r["labelcode"] for r in search_labels(con, "Atlantic", limit=30)][0] == "00121"
    assert [r["labelcode"] for r in search_labels(con, "  atlantic ", limit=5)][0] == "00121"
    assert search_labels(con, "plattenküche")[0]["labelcode"] == "88888"      # Groß/klein auch mit Umlaut
    con.close()
