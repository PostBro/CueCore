"""ISRC, ISWC und GEMA-Werknr. aus echten Library-Lieferungen (APL Music, RipCue Music, Extrakte im Repo) kommen
durch: songs.db -> Resolver-Ergebnis -> Tagger-Karte -> Cue-Sheet-Export."""
import csv
from pathlib import Path

from core import edl_import, intake, librarytracks, songsdb
from core.edl import EDLEvent
from core.normalize import isrc_plain
from core.resolver import local_result

DATA = Path(__file__).resolve().parents[2] / "data"
TRACKS = {  # Datei aus der Lieferung -> erwartete Codes (aus derselben Datei abgelesen)
    "apl_2026-10-16_teil1.csv": ("BLA_027_TK001_WHEN_WE_ARRIVE_Kenneth_Brahmstedt_(BMI)_BL_Works_(BMI)",
                                 "QZ-YD8-23-00468", "T-340.990.243-4", "44096012"),
    "ripcue_2026-10-18.csv": ("RCM0001_01_Code Of Fashion (Main)_Stefan Schnabel_VIP Catwalk_RipCue Music_LC50845.mp3",
                              "DEUS31500001", "T-803.850.024-3", "17531281"),
}


def _master_names(codes):
    with open(DATA / "GVL_Labelcodes_synced.csv", encoding="utf-8") as f:
        return {r["labelcode"]: r["label"] for r in csv.DictReader(f) if r["labelcode"] in codes}


def _db(tmp_path):
    con = songsdb.open(tmp_path / "songs.db")
    for name, (fname, *_) in TRACKS.items():
        rows = [r for r in librarytracks.read_csv(DATA / "library_tracks" / name) if r["file"] == fname]
        assert len(rows) == 1, name
        songs, _ = librarytracks.to_song_rows(rows, _master_names({rows[0]["labelcode"]}).get)
        songsdb.import_library_tracks(con, songs, library=rows[0]["library"], date_verified="2026-10-22")
    return con


def test_codes_reach_resolve_result_and_tagger_card(tmp_path):
    con = _db(tmp_path)
    for fname, isrc, iswc, gema in TRACKS.values():
        hit = songsdb.find_by_file(con, fname)
        res = local_result(hit["interpret"], hit["titel"], hit)
        assert (res.isrcs, res.iswcs, res.gema_werknummer) == ([isrc_plain(isrc)], [iswc], gema), fname   # ISRC in Datenform
        p = tmp_path / "x.mp3"
        p.write_bytes(b"")
        card = intake.resolve_card(p, hit["interpret"], hit["titel"], resolve_fn=lambda a, t, i="": res,
                                   label_lookup=lambda c: ("", ""))
        assert card["isrcs"] == [isrc_plain(isrc)] and card["iswcs"] == [iswc] and card["gema_werknummer"] == gema


def test_codes_reach_cue_sheet_export(tmp_path):
    con = _db(tmp_path)
    events = [EDLEvent(f"00{i}", "AX", "A1", "C", "00:00:00:00", "00:00:05:00", "00:00:00:00", "00:00:05:00",
                       from_clip_name=fname if fname.endswith(".mp3") else fname + ".wav") for i, (fname, *_) in enumerate(TRACKS.values(), 1)]
    cards = edl_import.cross_check(events, con, dedupe=False)
    rows, _ = edl_import._export_rows(cards)
    col = {c: edl_import.EXPORT_COLUMNS.index(c) for c in ("ISRC", "ISWC", "GEMA-Werknr.")}
    for row, (fname, isrc, iswc, gema) in zip(rows, TRACKS.values()):
        assert (row[col["ISRC"]], row[col["ISWC"]], row[col["GEMA-Werknr."]]) == (isrc_plain(isrc), iswc, gema), fname
    out = tmp_path / "cue.csv"
    edl_import.export_csv(cards, out)
    text = out.read_text(encoding="utf-8-sig")
    assert all(v in text for _, i, w, g in TRACKS.values() for v in (isrc_plain(i), w, g))
