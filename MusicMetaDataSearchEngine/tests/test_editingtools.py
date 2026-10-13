import base64

from core import editingtools as et
from core.editingtools import Resp


def _ok(data):
    return Resp(ok=True, status=200, data=data)


def test_auth_header_format():
    h = et.auth_header("SECRET")
    assert h["Authorization"] == "Basic " + base64.b64encode(b"apikey:SECRET").decode()
    assert h["Content-Type"] == "application/json"


def test_unwrap_data_block_and_flat():
    assert et._unwrap({"code": "201", "data": {"projectId": "p1"}}) == {"projectId": "p1"}
    assert et._unwrap({"code": "200", "data": [{"fileName": "a"}]}) == [{"fileName": "a"}]  # entries = Liste!
    assert et._unwrap({"scanComplete": True}) == {"scanComplete": True}   # kein data-Key -> flach
    assert et._unwrap(None) == {}


def test_parties_role_mapping_and_names():
    parties = [
        {"role": "Composer", "name": "Anna Comp"},
        {"role": "CA", "name": "Beat Both"},          # Composer/Author-Kürzel -> Komponist
        {"role": "Author", "name": "Tom Text"},
        {"role": "Publisher", "name": "Verlag X"},
        {"role": "Composer", "name": "Anna Comp"},     # Duplikat
        {"role": "Arranger", "_firstName_": "Ida", "_lastName_": "Arr"},
    ]
    comp, lyr, pub = et._parties(parties)
    assert comp == "Anna Comp; Beat Both; Ida Arr"
    assert lyr == "Tom Text" and pub == "Verlag X"


def test_normalize_entry_flat_and_parties_merged():
    # Antwortform: FLACHE composer/author-Felder + optional interestedParties -> gemergt.
    entry = {"fileName": "UPM_1234.wav", "scanStatus": "matched",
             "metadata": {"trackTitle": "Rising", "artist": "UPM Artist", "albumTitle": "Cinematic 7",
                          "albumCatalogNumber": "UPM7", "labelName": "Universal Production Music",
                          "labelCode": "4369", "isrc": "DEUPM1234567", "iswc": "T-123",
                          "composer": "C One", "author": "A Two",
                          "interestedParties": [{"role": "Composer", "name": "C Three"}],
                          "source": "UPM"}}
    n = et.normalize_entry(entry)
    assert n["matched"] is True
    assert n["titel"] == "Rising" and n["interpret"] == "UPM Artist"
    assert n["labelname"] == "Universal Production Music" and n["labelcode_raw"] == "4369"
    assert n["komponist"] == "C One; C Three" and n["texter"] == "A Two"     # flach + interestedParties
    assert n["isrc"] == "DEUPM1234567" and n["source"] == "UPM"


def test_normalize_entry_no_match_blanks_filename_title():
    # Kein Match: trackTitle defaultet auf den Dateinamen -> NICHT als Titel durchreichen.
    entry = {"fileName": "UPM_TEST_0001.wav", "scanStatus": "The scan is complete, but no matching metadata was found.",
             "metadata": {"trackTitle": "UPM_TEST_0001.wav", "artist": "", "labelName": "", "labelCode": "",
                          "isrc": "", "composer": "", "author": "", "source": ""}}
    n = et.normalize_entry(entry)
    assert n["matched"] is False and n["titel"] == "" and n["interpret"] == "" and n["labelcode_raw"] == ""


def test_build_entries_synthesizes_tc_and_skips_empty():
    ents = et.build_entries([{"fileName": "a.wav", "duration_sec": 65}, {"fileName": "  "}], fps=25)
    assert len(ents) == 1
    assert ents[0]["fileName"] == "a.wav" and ents[0]["timecodeIn"] == "00:00:00:00"
    assert ents[0]["timecodeOut"] == "00:01:05:00"


def test_sec_to_tc():
    assert et._sec_to_tc(0) == "00:00:00:00"
    assert et._sec_to_tc(3661) == "01:01:01:00"


class _Fake:
    """Skriptbarer http: zählt Calls, scanComplete nach `complete_after` Polls."""
    def __init__(self, *, complete_after=1, entries=None, create=None, entries_resp=None):
        self.calls = []
        self.polls = 0
        self.complete_after = complete_after
        self.entries = entries if entries is not None else []
        self.create = create
        self.entries_resp = entries_resp

    def __call__(self, method, url, body=None):
        self.calls.append((method, url))
        if method == "POST" and url.endswith("/project"):
            return self.create if self.create is not None else _ok({"projectId": "P1"})
        if method == "GET" and url.endswith("/entries"):
            return self.entries_resp if self.entries_resp is not None else _ok(self.entries)  # data = LISTE
        if method == "GET":   # get_project (poll)
            self.polls += 1
            return _ok({"scanComplete": self.polls >= self.complete_after})
        if method == "DELETE":
            return _ok({})
        return Resp(ok=False, error="unexpected")


def test_scan_files_happy_path_polls_then_returns_normalized():
    # editingtools gibt den fileName BEREINIGT zurück (.COPY/.new gestrippt) -> query_filename
    # muss den ORIGINAL gesendeten Namen tragen, sonst scheitert der Merge im Frontend.
    entry = {"fileName": "UPM_1.wav", "scanStatus": "matched",     # bereinigt
             "metadata": {"trackTitle": "T", "labelName": "UPM", "labelCode": "4369",
                          "interestedParties": [{"role": "Composer", "name": "C"}]}}
    fake = _Fake(complete_after=2, entries=[entry])
    res = et.scan_files([{"fileName": "UPM_1.wav.COPY.01.new.01", "duration_sec": 30}],
                        http=fake, sleep=lambda *_: None)                        # roh gesendet
    assert res["ok"] and res["scan_complete"] and res["polls"] == 2
    e0 = res["entries"][0]
    assert e0["titel"] == "T" and e0["komponist"] == "C"
    assert e0["query_filename"] == "UPM_1.wav.COPY.01.new.01"      # Original zum Zurück-Mergen
    methods = [m for m, _ in fake.calls]
    assert methods[0] == "POST" and "DELETE" in methods       # angelegt + aufgeräumt


def test_scan_files_no_files():
    assert et.scan_files([{"fileName": ""}], http=lambda *a, **k: _ok({}), sleep=lambda *_: None)["error"] == "no_files"


def test_scan_files_create_auth_error():
    fake = _Fake(create=Resp(ok=False, status=401, error="auth"))
    res = et.scan_files([{"fileName": "a.wav"}], http=fake, sleep=lambda *_: None)
    assert not res["ok"] and res["error"] == "auth"


def test_scan_files_entries_pro_gated_surfaces_error():
    # Scan fertig, aber Get-Entries 402 (PRO/Quota) -> klarer Fehler, Projekt trotzdem gelöscht.
    fake = _Fake(complete_after=1, entries_resp=Resp(ok=False, status=402, error="quota"))
    res = et.scan_files([{"fileName": "a.wav", "duration_sec": 10}], http=fake, sleep=lambda *_: None)
    assert not res["ok"] and res["error"] == "quota" and res["scan_complete"] is True
    assert "DELETE" in [m for m, _ in fake.calls]


def test_scan_files_gives_up_after_max_polls_but_still_reads_entries():
    fake = _Fake(complete_after=99, entries=[])              # nie scanComplete
    res = et.scan_files([{"fileName": "a.wav", "duration_sec": 10}], http=fake,
                        sleep=lambda *_: None, max_polls=3)
    assert res["ok"] is True and res["scan_complete"] is False and res["polls"] == 3
