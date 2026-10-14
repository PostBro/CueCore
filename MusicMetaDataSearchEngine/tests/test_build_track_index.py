"""build_track_index.py: Discogs-Streaming-Parser und MusicBrainz-Tabellenimport an kleinen Fixtures."""
import gzip
import xml.etree.ElementTree as ET

import build_track_index as bti
from core import trackindex

GVL = {"01801": "V2 Records", "02518": "Compost Records", "00121": "Atlantic"}

RELEASE_XML = """<release id="126"><artists><artist><id>108</id><name>Ian Pooley (2)</name><join>&amp;</join></artist>
<artist><id>109</id><name>Swag</name></artist></artists><title>What's Your Number</title>
<labels><label name="V2 Records" catno="VVR5003316" id="26786"/></labels>
<extraartists><artist><name>Ian Pooley (2)</name><role>Written-By, Producer</role></artist>
<artist><name>Only B</name><role>Lyrics By</role><tracks>B1, B2</tracks></artist>
<artist><name>Nobody</name><role>Composed By</role><tracks>A1 to B2</tracks></artist>
<artist><name>Range Guy</name><role>Music By</role><tracks>B1 to B2</tracks></artist></extraartists>
<released>1998-12-07</released>
<tracklist><track><position>A1</position><title>What's Your Number (Paradise Mixx)</title>
<extraartists><artist><name>Bob Sinclar</name><role>Remix</role></artist></extraartists></track>
<track><position>A2</position><title>Track Artist Song</title><artists><artist><name>Other Act</name></artist></artists>
<extraartists><artist><name>Track Writer</name><role>Written By [Uncredited]</role></artist></extraartists></track>
<track><position></position><title>Side B</title></track>
<track><position>B1</position><title>Suite</title><sub_tracks><track><position>B1a</position><title>Part One</title></track></sub_tracks></track>
<track><position>B2</position><title></title></track>
</tracklist>
<identifiers><identifier type="Barcode" value="5 033197 033167"/><identifier type="Label Code" value="LC 1801"/>
<identifier type="Label Code" value="LC 99999"/></identifiers></release>"""


def _rows(xml=RELEASE_XML, gvl=GVL):
    return {(r[1], r[2], r[3]): r for r in bti.parse_release(ET.fromstring(xml), gvl)}


def test_parse_release_artists_tracks_writers_and_codes():
    rows = _rows()
    assert set(rows) == {("Ian Pooley & Swag", "What's Your Number (Paradise Mixx)", "01801"),
                         ("Other Act", "Track Artist Song", "01801"),
                         ("Ian Pooley & Swag", "Suite", "01801"),
                         ("Ian Pooley & Swag", "Part One", "01801")}   # Überschrift + leerer Titel + LC 99999 raus
    a1 = rows[("Ian Pooley & Swag", "What's Your Number (Paradise Mixx)", "01801")]
    # (nkey, interpret, titel, labelcode, label, komponist, texter, album, jahr, catno, source, source_id)
    assert a1[4] == "V2 Records" and a1[7] == "What's Your Number" and a1[8] == "1998" and a1[9] == "VVR5003316"
    assert a1[5] == "Ian Pooley" and a1[6] == "Ian Pooley"            # Written-By = Musik UND Text, Suffix ' (2)' weg
    assert a1[10] == "discogs" and a1[11] == "126" and a1[0] == trackindex.nkey("Ian Pooley & Swag", a1[2])
    a2 = rows[("Other Act", "Track Artist Song", "01801")]
    assert a2[5] == "Ian Pooley; Track Writer" and a2[6] == "Ian Pooley; Track Writer"
    b1 = rows[("Ian Pooley & Swag", "Suite", "01801")]
    assert b1[6] == "Ian Pooley; Only B"                              # <tracks>B1, B2</tracks> greift
    assert b1[5] == "Ian Pooley; Range Guy"                           # 'B1 to B2' aufgelöst, 'A1 to B2' nicht (Nobody fehlt)
    sub = rows[("Ian Pooley & Swag", "Part One", "01801")]
    assert "Only B" not in sub[6] and sub[5] == "Ian Pooley"


def test_parse_release_without_gvl_code_or_various_is_empty():
    assert bti.parse_release(ET.fromstring(RELEASE_XML.replace("LC 1801", "LC 55555")), GVL) == []
    xml = RELEASE_XML.replace("<name>Ian Pooley (2)</name><join>&amp;</join></artist>\n<artist><id>109</id><name>Swag</name>",
                              "<name>Various</name>")
    rows = bti.parse_release(ET.fromstring(xml), GVL)
    assert [r[1] for r in rows] == ["Other Act"]                     # nur der Track mit eigenem Interpret


def test_label_codes_split_and_normalized():
    el = ET.fromstring('<release><identifiers><identifier type="Label Code" value="LC 0121 / LC 02518"/>'
                       '<identifier type="label code" value="2518"/></identifiers></release>')
    assert bti.label_codes(el, GVL) == ["00121", "02518"]


def test_role_parsing_and_position_ranges():
    assert bti.writer_roles("Written-By [All Tracks By], Producer") == (True, True)
    assert bti.writer_roles("Lyrics By") == (False, True) and bti.writer_roles("Music By") == (True, False)
    assert bti.writer_roles("Remix, Producer [Additional]") == (False, False)
    assert bti.expand_positions("A1, B2") == {"a1", "b2"}
    assert bti.expand_positions("1-3") == {"1", "2", "3"} and bti.expand_positions("A1 to A3") == {"a1", "a2", "a3"}
    assert bti.expand_positions("A1 to B3") is None
    assert bti.artist_credit(ET.fromstring("<artists><artist><name>A</name><join>,</join></artist>"
                                           "<artist><name>B (3)</name><join>Feat.</join></artist>"
                                           "<artist><name>C</name></artist></artists>")) == "A, B Feat. C"


def test_build_discogs_streams_gz_and_finalizes(tmp_path, capsys):
    dump = tmp_path / "discogs_test_releases.xml.gz"
    with gzip.open(dump, "wt", encoding="utf-8") as f:
        f.write("<releases>" + RELEASE_XML + RELEASE_XML.replace('id="126"', 'id="127"')
                + '<release id="1"><title>No LC</title><tracklist/></release></releases>')
    master = tmp_path / "master.csv"
    master.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n01801,V2 Records,,,V2,\n",
                      encoding="utf-8")
    out = tmp_path / "track_index.db"
    st = bti.build_discogs(dump, out, master, progress_every=0)
    assert st["releases_seen"] == 3 and st["releases_with_lc"] == 2 and st["releases_lc_in_gvl"] == 2
    assert st["rows_staged"] == 8 and st["rows"] == 4
    con = trackindex.open_index(out)
    hits = trackindex.lookup(con, "ian pooley & swag", "part one")
    assert len(hits) == 1 and hits[0]["n_releases"] == 2 and hits[0]["labelcode"] == "01801"
    meta = trackindex.stats(con, out)["meta"]
    assert meta["discogs_dump"] == "discogs_test_releases.xml.gz" and meta["discogs_releases_seen"] == "3"
    assert "Releases 3" in capsys.readouterr().out
    con.close()
    # CLI: status nennt die Suchtabellen, titles baut sie additiv nach (Titel-only-Suche)
    assert bti.main(["--out", str(out), "status"]) == 0 and "titles+FTS ja" in capsys.readouterr().out
    assert bti.main(["--out", str(out), "titles"]) == 0 and "Titel-Tabelle + FTS5-Trigram gebaut: 4" in capsys.readouterr().out
    assert bti.main(["--out", str(tmp_path / "nope.db"), "titles"]) == 1


# --- MusicBrainz ---------------------------------------------------------------------------------

def _mb_fixture(tmp_path):
    mb = tmp_path / "mbdump"
    mb.mkdir()
    N = "\\N"
    def w(name, rows):
        (mb / name).write_text("".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    gid = "12345678-1234-1234-1234-123456789abc"
    # label: id gid name b_y b_m b_d e_y e_m e_d label_code type area comment edits last_updated ended
    w("label", [["1", gid, "Atlantic", N, N, N, N, N, N, "121", N, N, "", "0", N, "f"],
                ["2", gid, "NoCode", N, N, N, N, N, N, N, N, N, "", "0", N, "f"],
                ["3", gid, "Not GVL", N, N, N, N, N, N, "99999", N, N, "", "0", N, "f"]])
    w("release_label", [["1", "10", "1", "ATL 1", N], ["2", "11", "2", "X", N], ["3", "12", "3", "Y", N]])
    w("release", [["10", gid, "Album A", "100", N, N, N, N, N, N, "", "0", "-1", N],
                  ["11", gid, "Album B", "100", N, N, N, N, N, N, "", "0", "-1", N],
                  ["12", gid, "Album C", "100", N, N, N, N, N, N, "", "0", "-1", N]])
    w("medium", [["20", "10", "1", N, "", "0", N, "2", gid], ["21", "11", "1", N, "", "0", N, "1", gid]])
    # track: id gid recording medium position number name artist_credit length edits last_updated is_data_track
    w("track", [["30", gid, "40", "20", "1", "1", "Song One", "101", N, "0", N, "f"],
                ["31", gid, "41", "20", "2", "2", "Song Two", "102", N, "0", N, "f"],
                ["32", gid, "42", "21", "1", "1", "Not Indexed", "101", N, "0", N, "f"]])
    w("artist_credit", [["100", "Various Artists", "1", "0", N, "0", gid], ["101", "The Band", "1", "0", N, "0", gid],
                        ["102", "Solo Act feat. X", "2", "0", N, "0", gid]])
    w("artist", [["200", gid, "Jane Composer", "Composer, Jane"], ["201", gid, "Joe Lyrics", "Lyrics, Joe"],
                 ["202", gid, "Writer Both", "Both, Writer"]])
    # link_type: id parent child_order gid entity_type0 entity_type1 name description link_phrase ...
    w("link_type", [["300", N, "0", gid, "artist", "work", "composer", "", "", "", "", N, "f", "f", N, N],
                    ["301", N, "0", gid, "artist", "work", "lyricist", "", "", "", "", N, "f", "f", N, N],
                    ["302", N, "0", gid, "artist", "work", "writer", "", "", "", "", N, "f", "f", N, N],
                    ["303", N, "0", gid, "artist", "work", "arranger", "", "", "", "", N, "f", "f", N, N],
                    ["304", N, "0", gid, "recording", "work", "performance", "", "", "", "", N, "f", "f", N, N]])
    w("link", [["400", "300", N, N, N, N, N, N, "0", N, "f"], ["401", "301", N, N, N, N, N, N, "0", N, "f"],
               ["402", "302", N, N, N, N, N, N, "0", N, "f"], ["403", "303", N, N, N, N, N, N, "0", N, "f"],
               ["404", "304", N, N, N, N, N, N, "0", N, "f"]])
    w("l_recording_work", [["500", "404", "40", "600", "0", N, "0", "", ""], ["501", "404", "41", "601", "0", N, "0", "", ""]])
    w("l_artist_work", [["700", "400", "200", "600", "0", N, "0", "", ""], ["701", "401", "201", "600", "0", N, "0", "", ""],
                        ["702", "402", "202", "601", "0", N, "0", "", ""], ["703", "403", "200", "601", "0", N, "0", "", ""]])
    return tmp_path


def test_build_musicbrainz_joins_tracks_labels_and_writers(tmp_path, capsys):
    dump = _mb_fixture(tmp_path)
    master = tmp_path / "master.csv"
    master.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n00121,Atlantic,,,Warner,\n",
                      encoding="utf-8")
    out = tmp_path / "track_index.db"
    st = bti.build_musicbrainz(dump, out, master)
    assert st["labels_gvl"] == 1 and st["release_labels"] == 1 and st["tracks"] == 2 and st["rows"] == 2
    assert st["artist_work_links"] == 3                       # arranger fliegt
    con = trackindex.open_index(out)
    one = trackindex.lookup(con, "The Band", "Song One")[0]
    assert one["labelcode"] == "00121" and one["label"] == "Atlantic" and one["album"] == "Album A"
    assert one["komponist"] == "Jane Composer" and one["texter"] == "Joe Lyrics" and one["catno"] == "ATL 1"
    two = trackindex.lookup(con, "Solo Act feat. X", "Song Two")[0]
    assert two["komponist"] == "Writer Both" and two["texter"] == "Writer Both"   # 'writer' -> beide
    assert trackindex.lookup(con, "The Band", "Not Indexed") == []
    assert not (tmp_path / "track_index.build.db").exists()
    assert "link_type artist->work" in capsys.readouterr().out


def test_build_musicbrainz_refuses_wrong_column_order(tmp_path):
    dump = _mb_fixture(tmp_path)
    (dump / "mbdump" / "label").write_text("Atlantic\t1\tx\n", encoding="utf-8")
    master = tmp_path / "master.csv"
    master.write_text("labelcode,label,kurzname,plm,hersteller,rechterueckrufe\n00121,Atlantic,,,Warner,\n",
                      encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit):
        bti.build_musicbrainz(dump, tmp_path / "t.db", master)
