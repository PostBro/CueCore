"""Guard: die hartcodierten ALIASES/MAJOR_TOKENS müssen mit dem GVL-Master
konsistent bleiben. Fängt Drift bei einem Master-Re-Sync (Code entfernt/umgezogen)
und Tippfehler in den Maps. LCs werden nie aus dem Gedächtnis gesetzt, sondern gegen die CSV geprüft.
"""
import csv
from pathlib import Path

import pytest

from core.labelmatch import ALIASES, MAJOR_TOKENS, _is_major_hersteller, build_index

MASTER = Path(__file__).resolve().parents[2] / "data" / "GVL_Labelcodes_synced.csv"


@pytest.fixture(scope="module")
def master_idx():
    if not MASTER.exists():
        pytest.skip(f"GVL-Master nicht vorhanden: {MASTER}")
    with open(MASTER, encoding="utf-8") as fh:
        return build_index(
            (r["labelcode"], r["label"], r.get("hersteller", "")) for r in csv.DictReader(fh)
        )


def test_aliases_codes_exist_in_master(master_idx):
    missing = sorted({c for c in ALIASES.values() if c not in master_idx.code2name})
    assert not missing, f"ALIASES-Codes fehlen im GVL-Master: {missing}"


def test_major_tokens_exist_and_are_major(master_idx):
    problems = []
    for tok, code in MAJOR_TOKENS.items():
        if code not in master_idx.code2name:
            problems.append(f"{tok} -> {code} fehlt im Master")
        elif not _is_major_hersteller(master_idx.code2hersteller.get(code, "")):
            problems.append(
                f"{tok} -> {code} ({master_idx.code2name.get(code)}) "
                f"kein Major-Hersteller: {master_idx.code2hersteller.get(code)!r}"
            )
    assert not problems, problems
