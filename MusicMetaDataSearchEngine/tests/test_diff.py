from core.models import LabelRecord
from reconcile.diff import diff
from reconcile.merge import classify_change


def R(lc, label, kurz="", plm="", hers="", rr=""):
    return LabelRecord(lc, label, kurz, plm, hers, rr)


def test_neu_geaendert_entfernt():
    csv = [R("00001", "Alt Label", hers="Firma A"), R("00002", "Bleibt", hers="Firma B")]
    pdf = [
        R("00001", "Neu Label", hers="Firma A"),  # GEÄNDERT (label)
        R("00002", "Bleibt", hers="Firma B"),      # unverändert
        R("00003", "Frisch", hers="Firma C"),      # NEU
    ]
    rep = diff(pdf, csv, "2026-06-23")
    assert [r.labelcode for r in rep.neu] == ["00003"]
    assert [lc for lc, _, _ in rep.geaendert] == ["00001"]
    assert rep.entfernt == []


def test_entfernt():
    csv = [R("00009", "Weg", hers="X")]
    pdf = []
    rep = diff(pdf, csv, "v")
    assert [r.labelcode for r in rep.entfernt] == ["00009"]


def test_duplikate_not_a_conflict():
    # gleicher Code, zwei Label -> DUPLIKAT, kein GEÄNDERT, wenn beide Seiten gleich
    csv = [R("13013", "Label A", hers="H1"), R("13013", "Label B", hers="H2")]
    pdf = [R("13013", "Label B", hers="H2"), R("13013", "Label A", hers="H1")]  # Reihenfolge egal
    rep = diff(pdf, csv, "v")
    assert rep.duplikate.get("13013") == 2
    assert rep.geaendert == []
    assert rep.neu == [] and rep.entfernt == []


def test_classify_change():
    assert classify_change([R("1", "Foo", hers="H")], [R("1", "Foo", plm="Ja", hers="H")]) == "plm_only"
    assert classify_change([R("1", "KDB", hers="H")], [R("1", "K D B", hers="H")]) == "spacing_only"
    assert classify_change([R("1", "Foo", hers="Alt")], [R("1", "Foo", hers="Neu")]) == "substantive"
    assert classify_change([R("1", "A"), R("1", "B")], [R("1", "A")]) == "multi"


def test_normalization_applied_before_diff():
    # CSV unnormalisiert, PDF normalisiert -> kein falsches GEÄNDERT
    csv = [R("00001", " Foo ", plm="ja", rr="R9;R6")]
    pdf = [R("00001", "Foo", plm="Ja", rr="R6;R9")]
    rep = diff(pdf, csv, "v")
    assert rep.geaendert == []
