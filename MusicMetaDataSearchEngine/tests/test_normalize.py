from core.models import LabelRecord
from core.normalize import (
    normalize_labelcode,
    normalize_plm,
    normalize_rechterueckrufe,
    normalize_record,
    to_filename_lc,
)


def test_labelcode_keeps_leading_zeros():
    assert normalize_labelcode("00001") == "00001"
    assert normalize_labelcode("00309") == "00309"


def test_labelcode_4digit_equals_5digit():
    assert normalize_labelcode("0309") == "00309"
    assert normalize_labelcode("LC0309") == "00309"


def test_labelcode_6digit_unchanged():
    # 6-stellig bekommt KEINE zusätzliche Null und wird nicht mit 5-stellig verwechselt
    assert normalize_labelcode("101303") == "101303"
    assert normalize_labelcode("01303") == "01303"
    assert normalize_labelcode("101303") != normalize_labelcode("01303")


def test_labelcode_strips_prefix_and_spaces():
    assert normalize_labelcode("LC 24584") == "24584"


def test_filename_lc_no_space():
    assert to_filename_lc("24584") == "LC24584"
    assert to_filename_lc("0148") == "LC00148"


def test_plm():
    assert normalize_plm("Ja") == "Ja"
    assert normalize_plm("ja") == "Ja"
    assert normalize_plm("") == ""
    assert normalize_plm(None) == ""


def test_rechterueckrufe_sorted_unique():
    assert normalize_rechterueckrufe("R12;R6;R9;R13") == "R6;R9;R12;R13"
    assert normalize_rechterueckrufe("R6;R6") == "R6"


def test_rechterueckrufe_ignores_company_names():
    # Firmennamen mit R+Zahl sind KEINE R-Codes
    assert normalize_rechterueckrufe("R7 MUSIC LTD") == ""
    assert normalize_rechterueckrufe("R4 USA") == ""
    assert normalize_rechterueckrufe("R2R Music") == ""


def test_rechterueckrufe_rejects_out_of_range():
    assert normalize_rechterueckrufe("R0;R14;R99") == ""


def test_normalize_record_idempotent():
    rec = LabelRecord("0309", "  Foo   Bar ", "", "ja", "Nordwand R6 Tontraeger", "R9;R6")
    once = normalize_record(rec)
    twice = normalize_record(once)
    assert once == twice
    assert once.labelcode == "00309"
    assert once.label == "Foo Bar"
    assert once.plm == "Ja"
    assert once.hersteller == "Nordwand R6 Tontraeger"
    assert once.rechterueckrufe == "R6;R9"


def test_labelcode_joins_digit_groups_split_by_space_or_thousands_dot():
    """'LC 24 584' wurde LC00024 (ROYALBUNKER) statt LC24584 (MOM & POP), ein falscher,
    aber gültiger Code. Tausender-Gruppen (Leerzeichen, geschützte Leerzeichen, Punkt) gehören zusammen."""
    assert normalize_labelcode("LC 24 584") == "24584"
    assert normalize_labelcode("LC 100 148") == "100148"
    assert normalize_labelcode("LC 24 584") == "24584"
    assert normalize_labelcode("LC 24 584") == "24584"
    assert normalize_labelcode("24.584") == "24584"
    assert normalize_labelcode("LC  24   584") == "24584"


def test_labelcode_keeps_separate_codes_separate():
    """Gegenprobe: zwei Codes oder Code + Jahr verschmelzen nicht zu einem neuen Code."""
    assert normalize_labelcode("LC 0309 / LC 1234") == "00309"
    assert normalize_labelcode("LC 0309, LC 1234") == "00309"
    assert normalize_labelcode("LC 0309 (2003)") == "00309"
    assert normalize_labelcode("LC 0121 0392") == "00121"           # zweite Gruppe 4-stellig: keine Tausender
    assert normalize_labelcode("LC24584 2003") == "24584"


def test_filename_lc_empty_without_digits():
    """Speicher-/Dateinamen-Form: ohne Ziffern kein nacktes 'LC'."""
    assert to_filename_lc("") == ""
    assert to_filename_lc("unbekannt") == ""
    assert to_filename_lc("LC 24 584") == "LC24584"
    assert to_filename_lc("121") == "LC00121"


def test_strict_labelcode_nimmt_nur_genau_einen_sauberen_code():
    """normalize_labelcode nahm still den ersten Ziffernblock — 'LC 24-584' wurde LC00024,
    'LC2458O' LC02458, 'LC00121 / LC00392' nur LC00121, jeweils ein gültiger, aber falscher Code. Für Eingaben
    von Menschen gilt deshalb strict_labelcode: genau ein Code, sonst None (nicht lesbar)."""
    from core.normalize import strict_labelcode
    ok = {"LC24584": "24584", "LC 24584": "24584", "lc24584": "24584", "LC-24584": "24584", "LC: 24584": "24584",
          "121": "00121", "LC 0309": "00309", "LC 24 584": "24584", "24.584": "24584", "LC 100 148": "100148",
          "LC104994": "104994", "  LC 01485  ": "01485", "LC 24 584": "24584", "": ""}
    for raw, want in ok.items():
        assert strict_labelcode(raw) == want, raw
    for raw in ("LC 24-584", "LC 245 84", "LC2458O", "LC 2458x", "LC00121 / LC00392", "LC24584 2003", "LC",
                "LC 1234567", "Atlantic", "LC 24 58", "24,584", "LC0O121"):
        assert strict_labelcode(raw) is None, raw
