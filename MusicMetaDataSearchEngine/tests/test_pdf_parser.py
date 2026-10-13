"""Parser-Tests gegen ein generiertes Fixture-PDF mit dem echten Spaltenlayout.

Abgedeckt: mehrzeilig umgebrochener Hersteller, seitenübergreifender Datensatz,
PLM-'Ja', R-im-Firmennamen-Falle, optionale Kurzname/RR-Spalten.
"""
import pymupdf
import pytest

from reconcile.pdf_parser import _is_forced_wrap, _join_cell, _merge_wrapped, parse_pdf


def test_join_cell_collapses_letterspacing():
    assert _join_cell(["K", "D", "B"]) == "KDB"
    assert _join_cell(["G", "L", "I", "S", "S", "A", "N", "D", "O"]) == "GLISSANDO"
    assert _join_cell(["B", "B", "C", "Radio"]) == "BBC Radio"
    assert _join_cell(["W", "a", "Z"]) == "WaZ"  # auch gemischte/kleine Buchstaben
    assert _join_cell(["W", "e", "i", "k", "a"]) == "Weika"


def test_join_cell_all_single_char_collapses():
    # Ganze Zelle nur Einzelzeichen -> Letter-Spacing, zusammenziehen
    assert _join_cell(["W", "a", "Z"]) == "WaZ"
    assert _join_cell(["M", "L"]) == "ML"
    assert _join_cell(["A", "B"]) == "AB"


def test_join_cell_keeps_real_words_and_symbols():
    assert _join_cell(["ZZQ", "MUSIC", "Group"]) == "ZZQ MUSIC Group"
    assert _join_cell(["Max", "E.", "Mustermann"]) == "Max E. Mustermann"
    # Symbol-Fälle bleiben unangetastet (Schreibweise wie in der GVL-PDF)
    assert _join_cell(["L", "&", "G"]) == "L & G"
    assert _join_cell(["A", "+", "P", "Records"]) == "A + P Records"

# Spaltenanker der GVL-PDF
X = {"labelcode": 17, "label": 114, "kurzname": 211, "plm": 308, "hersteller": 362, "rr": 460}
FONT = 9


def _put(page, col, text, y, dx=0):
    page.insert_text((X[col] + dx, y), text, fontsize=FONT)


@pytest.fixture
def fixture_pdf(tmp_path):
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": "D:20260623070950+02'00'"})

    p0 = doc.new_page(width=612, height=792)
    # Footer (muss gefiltert werden)
    _put(p0, "labelcode", "23.06.2026, 07:07:01", 770, dx=-10)
    p0.insert_text((470, 770), "https://labelrecherche.gvl.de/", fontsize=FONT)
    # Spaltenheader (nur Seite 0, y~85)
    for col, label in [("labelcode", "Labelcode"), ("label", "Label"), ("kurzname", "Kurzname"),
                       ("plm", "PLM"), ("hersteller", "Hersteller"), ("rr", "Rechterückrufe")]:
        _put(p0, col, label, 85)

    # 00001: Hersteller über 3 Zeilen umgebrochen + RR
    # Mehrere Wörter EINER Zeile als ein String (wie im echten PDF gerendert),
    # damit pymupdf die internen Leerzeichen erkennt.
    _put(p0, "labelcode", "00001", 110)
    _put(p0, "label", "Ultraphone", 110)
    _put(p0, "kurzname", "Ult", 110)
    _put(p0, "hersteller", "ZZQ MUSIC", 110)
    _put(p0, "hersteller", "Group Testland", 122)
    _put(p0, "hersteller", "Holding GmbH", 134)
    _put(p0, "rr", "R6;R9;R12;R13", 110)

    # 00003: nur Label + Hersteller (Kurzname/PLM/RR leer)
    _put(p0, "labelcode", "00003", 150)
    _put(p0, "label", "nytingale records", 150)
    _put(p0, "hersteller", "Erika Testerin", 150)

    # 01083: PLM 'Ja' + R6 IM Firmennamen (darf nicht als RR gewertet werden)
    _put(p0, "labelcode", "01083", 175)
    _put(p0, "label", "Schwann MUSICA", 175)
    _put(p0, "kurzname", "MUM", 175)
    _put(p0, "plm", "Ja", 175)
    _put(p0, "hersteller", "Nordwand R6", 175)
    _put(p0, "hersteller", "Tontraeger GmbH", 187)

    # 00026: Hersteller bricht auf Seite 1 um (Testmann)
    _put(p0, "labelcode", "00026", 210)
    _put(p0, "label", "VERVETONE", 210)
    _put(p0, "hersteller", "Max Beispiel", 210)

    p1 = doc.new_page(width=612, height=792)
    _put(p1, "hersteller", "Testmann", 35)  # Fortsetzung von 00026, ganz oben
    _put(p1, "labelcode", "00027", 55)
    _put(p1, "label", "Audioson", 55)
    _put(p1, "hersteller", "Johannes", 55); _put(p1, "hersteller", "Beispiel", 55, dx=42)
    _put(p1, "labelcode", "23.06.2026, 07:07:01", 770, dx=-10)
    p1.insert_text((470, 770), "https://labelrecherche.gvl.de/", fontsize=FONT)

    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_source_version(fixture_pdf):
    _, version = parse_pdf(fixture_pdf)
    assert version == "2026-06-23"


def test_record_count_no_loss(fixture_pdf):
    records, _ = parse_pdf(fixture_pdf)
    assert [r.labelcode for r in records] == ["00001", "00003", "01083", "00026", "00027"]


def test_wrapped_hersteller_merged(fixture_pdf):
    recs = {r.labelcode: r for r in parse_pdf(fixture_pdf)[0]}
    assert recs["00001"].hersteller == "ZZQ MUSIC Group Testland Holding GmbH"
    assert recs["00001"].kurzname == "Ult"
    assert recs["00001"].rechterueckrufe == "R6;R9;R12;R13"


def test_optional_columns(fixture_pdf):
    recs = {r.labelcode: r for r in parse_pdf(fixture_pdf)[0]}
    r3 = recs["00003"]
    assert r3.label == "nytingale records"
    assert r3.kurzname == ""
    assert r3.plm == ""
    assert r3.hersteller == "Erika Testerin"
    assert r3.rechterueckrufe == ""


def test_plm_and_r_in_company_name(fixture_pdf):
    from core.normalize import normalize_record

    recs = {r.labelcode: r for r in parse_pdf(fixture_pdf)[0]}
    r = normalize_record(recs["01083"])
    assert r.plm == "Ja"
    assert r.kurzname == "MUM"
    assert r.hersteller == "Nordwand R6 Tontraeger GmbH"
    assert r.rechterueckrufe == ""  # R6 im Namen ist KEIN Rechterückruf


def test_cross_page_record(fixture_pdf):
    recs = {r.labelcode: r for r in parse_pdf(fixture_pdf)[0]}
    assert recs["00026"].hersteller == "Max Beispiel Testmann"
    assert recs["00027"].hersteller == "Johannes Beispiel"


# --- Mid-Word-Wrap-Erkennung (GVL-Parser-Bug, 2026-07-02) ---
#
# Manche Label-/Herstellernamen brechen im GVL-PDF MITTEN IM WORT um (kein
# echtes Leerzeichen im Original), z.B. 'Nordwand Ente' + 'rtainment' ->
# muss 'Entertainment' werden, nicht 'Ente rtainment'. _is_forced_wrap()
# erkennt das konservativ über Spaltenfüllung + Wörterbuch-Sicherheitsnetz.

WORDS = frozenset({"nordwand", "group", "creative", "media", "comunicazione",
                   "father", "records", "zzq", "music", "testland", "holding",
                   "gmbh", "testlabel", "testcompany"})


def test_is_forced_wrap_true_for_tight_fragment():
    # 'Ente' füllt die Hersteller-Spalte (362-460) zu ~91%, 'rtainment' ist kein
    # eigenständiges Wörterbuch-Wort -> Mid-Word-Wrap, ohne Leerzeichen zusammenziehen.
    assert _is_forced_wrap("Ente", 451.0, "rtainment", "hersteller", WORDS) is True


def test_is_forced_wrap_false_below_fill_threshold():
    # Normaler Wortumbruch mit viel Platz übrig -> Leerzeichen bleibt.
    assert _is_forced_wrap("Group", 400.0, "records", "hersteller", WORDS) is False


def test_is_forced_wrap_false_for_stopword_continuation():
    # 'di' ist ein echtes eigenständiges Bindewort (italienisch 'von') -> nie zusammenziehen.
    assert _is_forced_wrap("Comunicazione", 451.0, "di", "hersteller", WORDS) is False


def test_is_forced_wrap_false_for_dotted_continuation():
    # Abkürzungsfragmente ('S.' + 'r.l.') sind nicht sicher entscheidbar -> unangetastet.
    assert _is_forced_wrap("S", 451.0, "r.l.", "hersteller", WORDS) is False


def test_is_forced_wrap_false_when_previous_token_is_real_word():
    # 'creative' ist selbst ein vollständiges Wort -> 'media' ist ein neues Wort, kein Fragment.
    assert _is_forced_wrap("creative", 451.0, "media", "hersteller", WORDS) is False


def test_is_forced_wrap_false_for_uppercase_continuation():
    assert _is_forced_wrap("Group", 451.0, "GmbH", "hersteller", WORDS) is False


def test_is_forced_wrap_false_without_wordlist():
    # Ohne Wörterbuch (z.B. Systemwörterbuch fehlt) -> Regel komplett deaktiviert.
    assert _is_forced_wrap("Ente", 451.0, "rtainment", "hersteller", frozenset()) is False


def test_merge_wrapped_joins_forced_wrap_without_space():
    toks = [("Nordwand", 362.0, 420.0, ("p", 0, 0)),
            ("Ente", 423.0, 451.0, ("p", 0, 0)),
            ("rtainment", 362.0, 400.0, ("p", 0, 1))]
    assert _merge_wrapped(toks, "hersteller", WORDS) == ["Nordwand", "Entertainment"]


def test_merge_wrapped_keeps_space_for_legit_word_boundary_wrap():
    toks = [("Group", 362.0, 391.0, ("p", 0, 0)),
            ("s.r.l.", 362.0, 400.0, ("p", 0, 1))]
    assert _merge_wrapped(toks, "hersteller", WORDS) == ["Group", "s.r.l."]


def test_merge_wrapped_same_line_untouched():
    toks = [("ZZQ", 362.0, 409.0, ("p", 0, 0)),
            ("MUSIC", 412.0, 446.0, ("p", 0, 0))]
    assert _merge_wrapped(toks, "hersteller", WORDS) == ["ZZQ", "MUSIC"]


@pytest.fixture
def wrap_fixture_pdf(tmp_path):
    """Kalibrierte Geometrie: 'Verygoodent' füllt die Hersteller-Spalte zu ~92%
    (echter Mid-Word-Wrap-Fall), 'Testcompany'+'media' bleibt bewusst darunter
    bzw. beide Wörter sind im Test-Wörterbuch (Sicherheitsnetz)."""
    doc = pymupdf.open()
    doc.set_metadata({"creationDate": "D:20260630080632+02'00'"})
    p0 = doc.new_page(width=612, height=792)
    for col, label in [("labelcode", "Labelcode"), ("label", "Label"), ("kurzname", "Kurzname"),
                       ("plm", "PLM"), ("hersteller", "Hersteller"), ("rr", "Rechterückrufe")]:
        _put(p0, col, label, 85)

    # 00500: echter Mid-Word-Wrap ('Testlabel Verygoodent' + 'erprise')
    _put(p0, "labelcode", "00500", 110)
    _put(p0, "label", "Testlabel", 110)
    _put(p0, "hersteller", "Testlabel Verygoodent", 110)
    _put(p0, "hersteller", "erprise", 122)

    # 00501: hohe Spaltenfüllung, aber letztes Wort vor dem Umbruch ('Records')
    # ist ein echtes Wörterbuch-Wort -> Sicherheitsnetz greift, bleibt getrennt.
    _put(p0, "labelcode", "00501", 150)
    _put(p0, "label", "Testlabel", 150)
    _put(p0, "hersteller", "Testcompany Records", 150)
    _put(p0, "hersteller", "media", 162)

    path = tmp_path / "wrap_sample.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_parse_pdf_merges_real_mid_word_wrap(wrap_fixture_pdf):
    recs = {r.labelcode: r for r in parse_pdf(wrap_fixture_pdf, word_set=WORDS)[0]}
    assert recs["00500"].hersteller == "Testlabel Verygoodenterprise"


def test_parse_pdf_keeps_space_when_previous_token_is_dictionary_word(wrap_fixture_pdf):
    recs = {r.labelcode: r for r in parse_pdf(wrap_fixture_pdf, word_set=WORDS)[0]}
    assert recs["00501"].hersteller == "Testcompany Records media"


def test_parse_pdf_disables_merge_without_wordlist(wrap_fixture_pdf):
    # Ohne Wörterbuch (leeres Set) bleibt auch der echte Bug-Fall unangetastet -> sicherer Default.
    recs = {r.labelcode: r for r in parse_pdf(wrap_fixture_pdf, word_set=frozenset())[0]}
    assert recs["00500"].hersteller == "Testlabel Verygoodent erprise"
