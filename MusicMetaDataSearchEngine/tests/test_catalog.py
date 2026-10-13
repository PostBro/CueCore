"""Katalog-Kürzel (core/catalog.py) + GVL-kurzname-Index (labelmatch) + Kandidat (resolver)."""
from core import catalog
from core.labelmatch import build_index, catalog_code, match_hersteller
from core.resolver import catalog_candidate

IDX = build_index([
    ("10347", "CMG", "Frederic Leibovitz Editeur Sarl.", "CEZ"),
    ("04952", "CEZAME", "Frederic Leibovitz Editeur Sarl.", ""),
    ("02061", "KPM MUSIC", "EMI Production Music GmbH", "KPM"),
    ("11111", "Gram A", "X GmbH", "GRAM"),          # GRAM mehrfach vergeben -> verworfen
    ("22222", "Gram B", "Y GmbH", "GRAM"),
    ("00121", "Atlantic", "Warner Music Group", ""),
])


def test_parse_catalog_id_library_patterns():
    assert catalog.parse_catalog_id("CEZ4030_1") == ("CEZ", "4030")                 # Cezame-Datei
    assert catalog.parse_catalog_id("CEZ4030 - Little French Fancies ") == ("CEZ", "4030")   # TALB
    assert catalog.parse_catalog_id("KPMLP 1234_05 Title") == ("KPMLP", "1234")
    assert catalog.parse_catalog_id("xcd123_04 Title") == ("XCD", "123")
    assert catalog.parse_catalog_id("UPM_1234") == ("UPM", "1234")


def test_is_catalog_only_marks_pure_catalog_numbers():
    # Reine Katalognummer (+ Track-Nummer): kein Interpret/Titel-Schema (z.B. CEZ4030_1)
    assert catalog.is_catalog_only("CEZ4030_1")
    assert catalog.is_catalog_only("CEZ4030")
    assert catalog.is_catalog_only("KPMLP1234_05")
    assert catalog.is_catalog_only("XCD123 04")
    assert catalog.is_catalog_only("KPM WM 123")
    # Titel/Interpret dabei -> kein reiner Katalogname
    assert not catalog.is_catalog_only("XCD123_04 Title")
    assert not catalog.is_catalog_only("CEZ4030 - Little French Fancies")
    assert not catalog.is_catalog_only("Where's My Love_SYML")
    assert not catalog.is_catalog_only("ATMO 21")          # 2 Ziffern: keine Katalognummer
    assert not catalog.is_catalog_only("")


def test_parse_catalog_id_rejects_non_catalog_names():
    assert catalog.parse_catalog_id("FT_BAM-AL87_15-Newborn_Day") is None        # Buchstaben nach FT
    assert catalog.parse_catalog_id("01 - Titel") is None
    assert catalog.parse_catalog_id("Alanis Morissette - Thank You") is None
    assert catalog.parse_catalog_id("AURORA - Runaway - [DECCA_LC00171]") is None
    assert catalog.parse_catalog_id("CEZ40301234") is None                       # >5 Ziffern
    assert catalog.parse_catalog_id("") is None and catalog.parse_catalog_id(None) is None


def test_index_keeps_only_unique_kurznamen():
    assert catalog_code("CEZ", IDX) == "10347" and catalog_code("cez", IDX) == "10347"
    assert catalog_code("KPM", IDX) == "02061"
    assert catalog_code("GRAM", IDX) == ""                                       # mehrdeutig -> nicht raten
    assert catalog_code("XYZ", IDX) == "" and catalog_code("", IDX) == ""
    assert build_index([("1", "A")]).short2code == {}                            # 2-Tupel weiter gültig


def test_match_hersteller_exact_and_prefix():
    assert match_hersteller("Frederic Leibovitz Editeur Sarl.", IDX) == "Frederic Leibovitz Editeur Sarl."
    assert match_hersteller("Frederic Leibovitz Editeur", IDX) == "Frederic Leibovitz Editeur Sarl."   # TPUB ohne Rechtsform
    assert match_hersteller("EMI Production Music", IDX) == "EMI Production Music GmbH"
    assert match_hersteller("Warner", IDX) == ""                                 # zu kurz für Präfix-Match
    short = build_index([("1", "X", "THE", ""), ("2", "Y", "Frederic Leibovitz Editeur Sarl.", "")])
    assert match_hersteller("The Nerve Media Group Ltd", short) == ""           # 'THE' ist kein Präfix-Treffer
    assert match_hersteller("Frederic Leibovitz Editeur", short) == "Frederic Leibovitz Editeur Sarl."
    assert match_hersteller("Decca", IDX) == "" and match_hersteller("", IDX) == ""


def test_catalog_candidate_review_only_and_tpub_corroboration():
    c = catalog_candidate("CEZ4030_1", IDX)
    assert c.labelcode == "10347" and c.label == "CMG" and c.confidence == 0.5 and c.dual_source is False
    assert c.sources == ["GVL-Kürzel CEZ"]
    c2 = catalog_candidate("CEZ4030_1", IDX, tpub="Frederic Leibovitz Editeur")   # TPUB trifft Hersteller
    assert c2.confidence == 0.65 and c2.dual_source is True and "TPUB = Hersteller" in c2.sources
    assert c2.confidence < 0.8                                                   # nie Band auto
    # fremder Hersteller in der Datei widerspricht dem Kandidaten -> kein Vorschlag (kein Raten)
    assert catalog_candidate("CEZ4030_1", IDX, tpub="Warner Music Group") is None
    # unbekannter TPUB (kein GVL-Hersteller) -> Kandidat bleibt, nur ohne Bonus
    c3 = catalog_candidate("CEZ4030_1", IDX, tpub="Irgendein Verlag")
    assert c3.confidence == 0.5 and c3.dual_source is False
    assert catalog_candidate("GRAM123_1", IDX) is None and catalog_candidate("Interpret - Titel", IDX) is None


# --- Library-Kürzel-Tabelle (data/library_prefixes.csv), Kandidaten-Reihenfolge, TPUB-Signale ---
from core.labelmatch import attach_library_prefixes, catalog_lookup

IDX2 = build_index([
    ("07877", "Berlin Production Music", "Universal Production Music GmbH", "BER"),
    ("01946", "UNITY Production Music", "RING MUSIK GmbH", "UPM"),          # GVL-Kürzel UPM ≠ Universal!
    ("01732", "THE EXTREME MUSIC LIBRARY", "Extreme Musik GmbH", ""),
    ("35538", "TWO STEPS FROM HELL", "Extreme Musik GmbH", ""),
    ("02061", "KPM MUSIC", "EMI Production Music GmbH", "KPM"),
    ("30515", "KPM World Music Series", "EMI Production Music GmbH", "KPM WM"),
])


def _prefix_csv(tmp_path, rows):
    p = tmp_path / "library_prefixes.csv"
    p.write_text("prefix,labelcode,label_gvl,library,list_label,source\n" + "".join(",".join(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_parse_catalog_id_two_token_prefix_and_candidates():
    assert catalog.parse_catalog_id("UPM_BER1376_1_Hot_Vocal_Lead_Janeva_Quaas_2257691") == ("UPM_BER", "1376")   # UPM-Datei
    assert catalog.parse_catalog_id("BER1376 Sunshine Motion") == ("BER", "1376")                                  # deren TALB
    assert catalog.parse_catalog_id("KPM WM 123 Title") == ("KPM WM", "123")
    assert catalog.prefix_candidates("UPM_BER") == ["UPM BER", "BER"]        # 'UPM' allein wäre UNITY (RING MUSIK) — nie
    assert catalog.prefix_candidates("KPM_WM") == ["KPM WM", "WM", "KPM"]
    assert catalog.prefix_candidates("KPM WM") == ["KPM WM", "WM", "KPM"]
    assert catalog.prefix_candidates("CEZ") == ["CEZ"] and catalog.prefix_candidates("") == []
    assert catalog.normalize_prefix("KPM WM") == "KPMWM" and catalog.normalize_prefix("upm_ber") == "UPMBER"


def test_library_prefixes_take_precedence_over_kurzname(tmp_path):
    p = _prefix_csv(tmp_path, [("XCD", "01732", "THE EXTREME MUSIC LIBRARY", "Extreme Music", "X-Series", "x.xlsx"),
                               ("XTS", "35538", "TWO STEPS FROM HELL", "Extreme Music", "Two Steps", "x.xlsx"),
                               ("KPM WM", "30515", "KPM World Music Series", "KPM", "KPM World", "k.pdf"),
                               ("ZZZ", "99999", "Nix", "Test", "Nix", "t.csv")])          # Code unbekannt -> raus
    n = attach_library_prefixes(IDX2, p)
    assert n == 3 and "ZZZ" not in IDX2.prefix2code
    assert catalog_lookup("XCD", IDX2) == ("01732", "Library-Kürzel XCD (Extreme Music)")
    assert catalog_lookup("xts", IDX2)[0] == "35538"
    assert catalog_lookup("KPM WM", IDX2) == ("30515", "Library-Kürzel KPM WM (KPM)")       # Liste vor GVL-kurzname
    assert catalog_lookup("KPM", IDX2) == ("02061", "GVL-Kürzel KPM")
    assert catalog_lookup("NIX", IDX2) == ("", "")
    assert attach_library_prefixes(IDX2, tmp_path / "fehlt.csv") == 0 and IDX2.prefix2code == {}


def test_upm_file_resolves_sublabel_not_unity(tmp_path):
    # UPM_BER1376: 'UPM' allein wäre bei der GVL UNITY Production Music (RING MUSIK). Die Datei sagt
    # TPUB 'Ed.Berlin Production Music / Universal Production Music GmbH' -> BER gewinnt, mit beiden
    # TPUB-Signalen (Hersteller + Label selbst), Confidence gedeckelt unter auto.
    tpub = "Ed.Berlin Production Music / Universal Production Music GmbH"
    c = catalog_candidate("UPM_BER1376_1_Hot_Vocal_Lead", IDX2, tpub=tpub)
    assert c.labelcode == "07877" and c.label == "Berlin Production Music"
    assert c.sources == ["GVL-Kürzel BER", "TPUB = Hersteller", "TPUB = Label"]
    assert c.confidence == 0.75 and c.dual_source is True and c.confidence < 0.8
    # ohne TPUB: BER steht vor UPM in der Kandidaten-Reihenfolge -> trotzdem Berlin, aber nur 0.5
    c2 = catalog_candidate("UPM_BER1376_1", IDX2)
    assert c2.labelcode == "07877" and c2.confidence == 0.5 and c2.dual_source is False
    # nur 'UPM_1234' + Universal-TPUB: der GVL-Kürzel-Treffer UNITY widerspricht dem Hersteller -> kein Kandidat
    assert catalog_candidate("UPM_1234 Title", IDX2, tpub="Universal Production Music GmbH") is None
    # UPM allein liefert auch OHNE TPUB nichts: in Dateinamen heißt UPM_ praktisch immer Universal Production
    # Music, der GVL-kurzname aber UNITY (RING MUSIK). Ein falscher LC wandert in die GEMA-Meldung, lieber
    # kein Vorschlag.
    assert catalog_candidate("UPM_1234 Title", IDX2) is None


def test_clean_writer_names_strips_pro_markers():
    assert catalog.clean_writer_names("Wanja Janeva [GEMA],David Quaas [GEMA]") == "Wanja Janeva; David Quaas"
    assert catalog.clean_writer_names("Carl Composer (ASCAP) / Anna Writer (PRS)") == "Carl Composer; Anna Writer"
    assert catalog.clean_writer_names("A; B; A") == "A; B" and catalog.clean_writer_names("") == ""


def test_library_prefixes_csv_is_consistent():
    import csv
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "data"
    p = root / "library_prefixes.csv"
    if not p.exists():
        import pytest; pytest.skip("data/library_prefixes.csv fehlt (Build-Layout)")
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    keys = [catalog.normalize_prefix(r["prefix"]) for r in rows]
    assert len(rows) >= 150 and len(keys) == len(set(keys))                                  # eindeutig
    known = {r["labelcode"] for r in csv.DictReader(open(root / "GVL_Labelcodes_synced.csv", encoding="utf-8"))}
    known |= {r["labelcode"] for r in csv.DictReader(open(root / "labelcodes_supplemental.csv", encoding="utf-8"))}
    assert all(r["labelcode"] in known for r in rows)
    assert not {r["labelcode"] for r in rows} & {"84940", "94544"}                            # GVL-Widersprüche draußen
    assert "JUSTE" not in keys and "NIGHTSE" not in keys                                      # mehrdeutig draußen
    by = {catalog.normalize_prefix(r["prefix"]): r for r in rows}
    assert by["XCD"]["labelcode"] == "01732" and by["XTS"]["labelcode"] == "35538" and by["DWCD"]["labelcode"] == "06822"


def test_extreme_file_with_unknown_prefix_falls_back_to_tpub_label():
    # Extreme-Datei HPE454_01_BIG_RIG: 'HPE' steht in keiner Liste; TPUB
    # 'ORIGINAL PUB: EXTREME MUSIC LIBRARY LTD (PRS)' nennt das Label selbst.
    tpub = "ORIGINAL PUB: EXTREME MUSIC LIBRARY LTD (PRS)"
    c = catalog_candidate("HPE454_01_BIG_RIG", IDX2, tpub=tpub)
    assert c is not None and c.labelcode == "01732" and c.label == "THE EXTREME MUSIC LIBRARY"
    assert c.sources == ["TPUB = Label", "Kürzel HPE unbekannt"]
    assert c.confidence == 0.65 and c.dual_source is False          # eine Datei-Quelle, kein zweites Signal
    # ohne Kürzel im Namen ('BIG RIG.mp3'), aber TPUB nennt das Label: gleicher Vorschlag ohne Kürzel-Hinweis
    c2 = catalog_candidate("BIG RIG", IDX2, tpub=tpub)
    assert c2.labelcode == "01732" and c2.sources == ["TPUB = Label"]
    # TPUB nennt ein Label, das dem Kürzel-Treffer widerspricht? Kürzel gewinnt nur ohne Hersteller-Widerspruch:
    assert catalog_candidate("BIG RIG", IDX2, tpub="Irgendwas Unbekanntes") is None
    assert catalog_candidate("BIG RIG", IDX2) is None


# --- APL-Datei: Labelname als Kürzel (THENERVE333), Library-Dach 'APL' grenzt den Hersteller ein ---
IDX3 = build_index([
    ("98597", "The Nerve", "APL Publishing GmbH", ""),
    ("01824", "NERVE", "WARNER MUSIC Group Germany Holding GmbH", ""),
    ("26510", "The Hit House Records", "APL Publishing GmbH", ""),
    ("00121", "Atlantic", "Warner Music Group", ""),
])


def test_catalog_tokens_and_umbrella_words():
    name = "APL_MUSIC_-_THENERVE333_TK030_TENSION_Mark_Fabian__Bethany_Jo_Holloway"
    assert catalog.catalog_tokens(name) == [("THENERVE", "333")]                     # TK030 ist ein Track-Marker
    assert catalog.umbrella_words(name) == ["APL", "MUSIC"]
    assert catalog.catalog_tokens("UPM_BER1376_1_Hot") == [("UPM_BER", "1376"), ("BER", "1376")]
    assert catalog.catalog_tokens("CEZ4030_1") == [("CEZ", "4030")] and catalog.umbrella_words("CEZ4030_1") == []
    assert catalog.catalog_tokens("Alanis Morissette - Thank You") == []


def test_apl_file_resolves_the_nerve_via_label_name_and_scope():
    from core.labelmatch import hersteller_scope, match_label_in_scope
    assert hersteller_scope("APL", IDX3) == {"98597", "26510"} and hersteller_scope("XY", IDX3) == set()
    assert match_label_in_scope("The Nerve Media Group Ltd", IDX3, {"98597", "26510"}) == "98597"
    assert match_label_in_scope("The Nerve Media Group Ltd", IDX3, set()) == ""      # ohne Scope zu unscharf
    name = "APL_MUSIC_-_THENERVE333_TK030_TENSION_Mark_Fabian__Bethany_Jo_Holloway__RK_Masters_The_Nerve_Media_Group_Ltd"
    c = catalog_candidate(name, IDX3, tpub="The Nerve Media Group Ltd")
    assert c.labelcode == "98597" and c.label == "The Nerve"
    assert c.sources == ["Kürzel THENERVE = Labelname", "TPUB = Label"] and c.confidence == 0.65 and c.dual_source
    # ohne Hersteller-Scope (im Master gibt es mehrere 'APL …'-Hersteller) trägt der TPUB-Präfix trotzdem:
    idx_noscope = build_index([("98597", "The Nerve", "APL Publishing GmbH", ""), ("11111", "The Nerve", "Other GmbH", "")])
    assert catalog_candidate("THENERVE333 Album", idx_noscope, tpub="The Nerve Media Group Ltd") is None   # Name mehrdeutig -> nichts
    # TALB allein ('THENERVE333 The Hallways of Power') reicht für den Namens-Treffer
    assert catalog_candidate("THENERVE333 The Hallways of Power", IDX3).labelcode == "98597"
    # 'NERVE' (Warner) kommt nicht in Frage: nicht im APL-Scope, und 'thenerve' ≠ 'nerve'
    assert catalog_candidate("APL_MUSIC_-_NERVE333_TK01", IDX3, tpub="The Nerve Media Group Ltd").labelcode == "98597"


# --- Scoring House (TSH_289_002 am ENDE des Namens) und Color TV ('18-CTV1038_18_…') ---
IDX4 = build_index([
    ("24186", "THE SCORING HOUSE", "Firstworks Music GmbH", "TSH"),
    ("52712", "Color TV", "Warner/Chappell Production Music GmbH", "CTV"),
    ("23979", "Shibuya", "Media Songs S.r.l.", "SH"),
])


def test_scoring_house_and_color_tv_files():
    name = "CROSSROADS_OF_UNCERTAINTY_A_ZAC_JORDAN_PRS_The_Scoring_House_TSH_289_002"
    assert catalog.catalog_tokens(name) == [("TSH", "289")] and catalog.umbrella_words(name) == []   # Token hinten -> kein Dach
    c = catalog_candidate(name, IDX4, tpub="The Scoring House (PRS) 50% [100000003]")
    assert c.labelcode == "24186" and c.sources == ["GVL-Kürzel TSH", "TPUB = Label"] and c.confidence == 0.65
    name2 = "18-CTV1038_18_CE_Arms_Of_Sorrow_Full_Mix"                                                  # Track-Nr. vorn
    assert catalog.catalog_tokens(name2) == [("CTV", "1038")]
    c2 = catalog_candidate(name2, IDX4)
    assert c2.labelcode == "52712" and c2.label == "Color TV" and c2.confidence == 0.5


def test_clean_writer_names_strips_shares_and_ipi():
    assert catalog.clean_writer_names("Zac Jordan (PRS) 50% [100000004]") == "Zac Jordan"
    assert catalog.clean_writer_names("Florian Leissle (GEMA) 50% [100000005], Ernest Ribka (GEMA) 50% [100000006]") == "Florian Leissle; Ernest Ribka"
    assert catalog.clean_writer_names("Mark Fabian , Bethany Jo Holloway , RK Masters") == "Mark Fabian; Bethany Jo Holloway; RK Masters"


def test_upm_sub_prefix_with_short_number_and_never_unity():
    """'UPM_BERTV61_3_…' (2 Ziffern) und 'UPM_KOS731_55_…' sind UPM-Dateien — das GVL-Kürzel
    UPM (UNITY Production Music, RING MUSIK) darf nie das Ergebnis sein."""
    assert catalog.parse_catalog_id("UPM_BERTV61_3_CONTINUITY_SAD_INSTRUMENTAL_FENCHEL_GOEPELT_2224779") == ("UPM_BERTV", "61")
    assert catalog.parse_catalog_id("UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631") == ("UPM_KOS", "731")
    assert "UPM" not in catalog.prefix_candidates("UPM_KOS")


def test_catalog_candidate_upm_unknown_sub_resolves_within_universal_scope():
    """KOS steht in keiner Liste: das eine Label von Universal Production Music GmbH, das mit KOS beginnt
    (KOSINUS LC07131). INSD trifft nichts -> None, nicht UNITY."""
    from core.resolver import catalog_candidate
    idx = build_index([
        ("01946", "UNITY Production Music", "RING MUSIK GmbH", "UPM"),
        ("07131", "KOSINUS", "Universal Production Music GmbH", ""),
        ("00994", "KILLER TRACKS", "Universal Production Music GmbH", ""),
        ("98274", "Berlin Production Music TV", "Universal Production Music GmbH", ""),
    ])
    cand = catalog_candidate("UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631", idx)
    assert cand is not None and cand.labelcode == "07131" and "KOSINUS" in cand.sources[0]
    assert catalog_candidate("UPM_INSD61_7_WILDERNESS_INSTRUMENTAL_JOHNSON_1078690", idx) is None
    assert catalog_candidate("UPM_KOS731_55_X", build_index([("01946", "UNITY Production Music", "RING MUSIK GmbH", "UPM")])) is None


def test_sublabel_in_scope_picks_root_label_among_family():
    """KOS unter Universal Production Music: KOSINUS, KOSINUS ARTS, KOSINUS TRAILERS … -> das Stammlabel KOSINUS
    als Vorschlag; INSD trifft 'Inside Tracks'/'Inside Cuts' nicht per Präfix -> nichts."""
    from core.catalog import sublabel_in_scope
    idx = build_index([
        ("07131", "KOSINUS", "Universal Production Music GmbH", ""),
        ("24742", "KOSINUS ARTS", "Universal Production Music GmbH", ""),
        ("96452", "KOSINUS TRAILERS", "Universal Production Music GmbH", ""),
        ("97101", "Inside Tracks", "Universal Production Music GmbH", ""),
        ("99238", "Inside Cuts", "Universal Production Music GmbH", ""),
    ])
    scope = {"07131", "24742", "96452", "97101", "99238"}
    assert sublabel_in_scope("KOS", scope, idx) == "07131"
    assert sublabel_in_scope("INS", scope, idx) == ""                           # zwei Inside-Labels ohne gemeinsames Stammlabel
    assert sublabel_in_scope("INSD", scope, idx) == ""


def test_parse_library_name_upm_and_kpm():
    """Titel aus Library-Dateinamen: UPM '<Sub><Album>_<Track>_<Titel>_<Version>_<Nachnamen>_<ID>',
    KPM '<Dach>_<Sub>_<Album>_<Track>_<Titel>_<Buchstabe>_<Vollnamen>'. Beispiel: UPM_BER1376_1_Hot_Vocal_Lead_Janeva_Quaas
    hat in den Tags den Titel 'Hot [Vocal Lead]' und die Komponisten Janeva/Quaas (siehe test_intake)."""
    from core.catalog import parse_library_name as p
    u = p("UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631")
    assert u == {"title": "Where I Belong", "version": "Instrumental", "composer_hint": "Juillet", "track": "55", "scheme": "upm"}
    b = p("UPM_BER1376_1_Hot_Vocal_Lead_Janeva_Quaas_2257691")
    assert b["title"] == "Hot" and b["version"] == "Vocal Lead" and b["composer_hint"] == "Janeva, Quaas"
    k = p("KPM_KPM_0858_00101_SCARS_A_MATT_BLACK_CHRISTIAN_J_O_LUNDBERG")
    assert k["title"] == "Scars" and k["version"] == "A" and k["track"] == "101" and "Lundberg" in k["composer_hint"]
    assert p("CEZ4030_1") is None and p("AURORA - Runaway") is None and p("ZZQ_TESTLOOP_KURZ") is None
    from core import watch
    c = watch.parse_cue_from_stem("UPM_KOS731_55_WHERE_I_BELONG_INSTRUMENTAL_JUILLET_1420631")
    assert c["titel"] == "Where I Belong (Instrumental)" and c["interpret"] == "" and c["composer_hint"] == "Juillet"


def test_upm_sub_prefix_may_start_with_a_digit():
    """'UPM_3M2_6_DANGER_TRANSPORT…' ist 3Monkeys Production Music (LC96446), 'UPM_1K7…' ist 1K Music,
    beide Universal Production Music. Auch Kürzel mit führender Ziffer müssen als Kürzel gelesen werden,
    sonst bleibt der Hersteller-Scope leer."""
    assert catalog.parse_catalog_id("UPM_3M2_6_DANGER_TRANSPORT_INSTRUMENTAL_JENSEN_1337616") == ("UPM_3M", "2")
    assert catalog.parse_catalog_id("UPM_1K7_2_SONG") == ("UPM_1K", "7")
    assert catalog.umbrella_words("UPM_3M2_6_DANGER_TRANSPORT") == ["UPM"]      # Scope-Wort trotz Ziffer-Kürzel
    idx = build_index([
        ("01946", "UNITY Production Music", "RING MUSIK GmbH", "UPM"),
        ("96446", "3Monkeys Production Music", "Universal Production Music GmbH", ""),
        ("07131", "KOSINUS", "Universal Production Music GmbH", ""),
    ])
    from core.resolver import catalog_candidate
    c = catalog_candidate("UPM_3M2_6_DANGER_TRANSPORT_INSTRUMENTAL_JENSEN_1337616", idx)
    assert c is not None and c.labelcode == "96446"


def test_bare_umbrella_prefix_never_resolves_to_unity():
    """'UPM_12345_6…' ohne Sub-Kürzel darf KEINEN Vorschlag liefern — der GVL-kurzname UPM gehört UNITY
    Production Music (RING MUSIK). Lieber nichts als der falsche Code."""
    assert catalog.prefix_candidates("UPM") == []
    assert catalog.prefix_candidates("KPM") == ["KPM"]                          # andere Dächer bleiben
    idx = build_index([
        ("01946", "UNITY Production Music", "RING MUSIK GmbH", "UPM"),
        ("07131", "KOSINUS", "Universal Production Music GmbH", ""),
    ])
    from core.resolver import catalog_candidate
    assert catalog_candidate("UPM_12345_6_X_Y", idx) is None


def test_clean_writer_names_unclosed_bracket_keeps_following_names():
    """Eine offene Klammer mit IPI-Nummer mitten in der Liste schnitt alle folgenden Namen ab
    ('Anna Bell (IMRO: 100000007, Tom Roe, Jane Doe' -> 'Anna Bell'). Weggeputzt wird nur bis zum Trenner."""
    c = catalog.clean_writer_names
    assert c("Anna Bell (IMRO: 100000007, Tom Roe, Jane Doe") == "Anna Bell; Tom Roe; Jane Doe"
    assert c("Anna Bell (IMRO: 100000007; Tom Roe / Jane Doe") == "Anna Bell; Tom Roe; Jane Doe"
    assert c("Paul Edward Wilkes (PRS: 100000002") == "Paul Edward Wilkes"            # am Zeilenende wie bisher
    assert c("TRILOGY (100000001 SUISA, 100%)") == "TRILOGY"


def test_tpub_with_unclosed_ipi_bracket_keeps_hersteller_part():
    """TPUB-Weg (resolver): der zweite Teil hinter '/' (Hersteller) darf nicht mit wegfallen."""
    idx = build_index([("26677", "The Scoring House", "Firstworks Music GmbH", "TSH")])
    c = catalog_candidate("TSH_289_X", idx, tpub="The Scoring House (PRS: 100000003 / Firstworks Music GmbH")
    assert c is not None and any("Hersteller" in s for s in c.sources)


def test_clean_writer_names_society_with_share_or_short_number():
    """Klammern mit Gesellschaft und Anteil, aber ohne 6-stellige IPI, und [5 Ziffern] verschwinden ganz."""
    c = catalog.clean_writer_names
    assert c("Zac Jordan (PRS 50%)") == "Zac Jordan"
    assert c("Zac Jordan (50% GEMA)") == "Zac Jordan"
    assert c("Zac Jordan (GEMA: 12345, 50%)") == "Zac Jordan"
    assert c("Anna Writer (PRS: 50%)") == "Anna Writer"
    assert c("Anna Writer (GEMA: 50 %), Bob Comp (PRS: 50 %)") == "Anna Writer; Bob Comp"
    assert c("Hans Muster [12345]") == "Hans Muster"
    # inhaltliche Klammern bleiben
    for keep in ("Song (live)", "Song (1977 remaster)", "Matchbox 20 (20)"):
        assert c(keep) == keep
    assert c("Blink-182, Sum 41") == "Blink-182; Sum 41"


def test_clean_writer_names_knows_more_societies_and_inner_spaces():
    """IMRO, SADAIC, TEOSTO, MUST, SABAM, SCD, KOMCA gehören zu den erkannten Gesellschaften, '( NS)' mit
    Leerzeichen in der Klammer wird ebenfalls erkannt."""
    c = catalog.clean_writer_names
    assert c("Carl David Harms [IMRO]") == "Carl David Harms"
    assert c("Andres Bertoglio [SADAIC]") == "Andres Bertoglio"
    assert c("Zhi Hua Zhou [MUST]; Point Culture Co Ltd [MUST]") == "Zhi Hua Zhou; Point Culture Co Ltd"
    assert c("A [TEOSTO], B [SABAM], C [SCD], D [KOMCA]") == "A; B; C; D"
    assert c("Kully Bhamra; Gussy G [NS]") == "Kully Bhamra; Gussy G"
    assert c("Music 2 Air ( NS)") == "Music 2 Air"
    assert c("Music 2 Air [ GEMA ]") == "Music 2 Air"


def test_parse_library_name_upm_sub_with_leading_digit():
    """Auch zu 'UPM_3M2_6_…' (Sub-Kürzel mit führender Ziffer) wird der Titel gelesen, nicht nur das Label
    (3Monkeys); sonst stünde im Cue-Sheet der rohe Dateiname."""
    from core.catalog import parse_library_name as p
    u = p("UPM_3M2_6_DANGER_TRANSPORT_INSTRUMENTAL_JENSEN_1337616")
    assert u == {"title": "Danger Transport", "version": "Instrumental", "composer_hint": "Jensen", "track": "6", "scheme": "upm"}
    assert p("UPM_1K7_2_NIGHT_DRIVE_SMITH_1234567")["title"] == "Night Drive Smith"
    assert p("UPM_12345_6_X_Y_1234567") is None                                 # kein Buchstabe: kein Sub-Kürzel
    from core import watch
    c = watch.parse_cue_from_stem("UPM_3M2_6_DANGER_TRANSPORT_INSTRUMENTAL_JENSEN_1337616")
    assert c["titel"] == "Danger Transport (Instrumental)" and c["composer_hint"] == "Jensen"
