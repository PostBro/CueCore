"""XML-Schnittlisten: Final Cut Pro 7 XML (xmeml — Premiere „Final Cut Pro XML", Resolve FCP 7 XML)
und FCPXML 1.8–1.10 (Resolve, Final Cut Pro X). Struktur nach Apple-Doku (siehe core/edl.py)."""
from core import edl
from core.edl import parse_edl, parse_fcpxml, parse_xmeml, rational_seconds

XMEML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
 <sequence id="sequence-1">
  <name>Testprojekt 12</name>
  <duration>90000</duration>
  <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
  <media>
   <video><track><clipitem id="v1"><name>Kamera A</name><start>0</start><end>500</end><in>0</in><out>500</out>
     <file id="file-v"><name>A001.mov</name><pathurl>file:///Volumes/Media/A001.mov</pathurl></file></clipitem></track></video>
   <audio>
    <track>
     <clipitem id="a1"><name>AURORA - Runaway</name><duration>3000</duration><rate><timebase>25</timebase></rate>
      <start>100</start><end>2600</end><in>0</in><out>2500</out>
      <file id="file-1"><name>AURORA - Runaway.wav</name><pathurl>file:///Volumes/Media/Musik/AURORA%20-%20Runaway.wav</pathurl></file>
      <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>
     <clipitem id="a2"><name>Score CEZ4030_1</name><start>-1</start><end>3600</end><in>250</in><out>1250</out>
      <file id="file-2"><pathurl>file:///C:/Musik/CEZ4030_1.mp3</pathurl></file></clipitem>
    </track>
    <track>
     <clipitem id="a3"><name>AURORA - Runaway</name><start>100</start><end>2600</end><in>0</in><out>2500</out>
      <file id="file-1"/><sourcetrack><mediatype>audio</mediatype><trackindex>2</trackindex></sourcetrack></clipitem>
     <clipitem id="a4"><name>Kaputt</name><start>-1</start><end>-1</end><in>-1</in><out>-1</out></clipitem>
    </track>
   </audio>
  </media>
 </sequence>
</xmeml>"""


def test_parse_xmeml_audio_clipitems_frames_transitions_and_file_ids():
    ev = parse_xmeml(XMEML)
    assert [e.clip_name for e in ev] == ["AURORA - Runaway.wav", "CEZ4030_1.mp3", "AURORA - Runaway.wav"]
    assert [e.from_clip_name for e in ev] == ["AURORA - Runaway", "Score CEZ4030_1", "AURORA - Runaway"]
    assert [e.gema_duration_sec(25) for e in ev] == [100, 40, 100]        # 2500 Frames / 25; Transition: out-in
    assert ev[0].record_in == "00:00:04:00" and ev[0].record_out == "00:01:44:00"
    assert ev[0].track_type == "A1" and ev[2].track_type == "A2" and ev[1].media_ref.endswith("CEZ4030_1.mp3")
    # Stereo-Paar (gleicher Name, gleiche Position auf zwei Spuren) -> ein Cue
    assert [e.event_num for e in edl.dedupe_multitrack(ev)] == ["001", "002"]


def test_parse_xmeml_ntsc_rate_uses_29_97():
    txt = XMEML.replace("<timebase>25</timebase><ntsc>FALSE</ntsc>", "<timebase>30</timebase><ntsc>TRUE</ntsc>")
    ev = parse_xmeml(txt)
    assert abs(ev[0].duration_sec() - 2500 / (30000 / 1001)) < 1e-9 and ev[0].gema_duration_sec() == 84


FCPXML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
 <resources>
  <format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s" width="1920" height="1080"/>
  <asset id="r2" name="A001" start="0s" duration="600s" hasVideo="1" hasAudio="0" format="r1"><media-rep kind="original-media" src="file:///Volumes/Media/A001.mov"/></asset>
  <asset id="r3" name="AURORA - Runaway" start="0s" duration="245s" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">
   <media-rep kind="original-media" src="file:///Volumes/Media/Musik/AURORA%20-%20Runaway.wav"/></asset>
  <asset id="r4" name="CEZ4030_1" start="0s" duration="120s" hasAudio="1" src="file:///Volumes/Media/Musik/CEZ4030_1.mp3"/>
  <asset id="r5" name="Birdy - Skinny Love" start="0s" duration="200s" hasAudio="1" hasVideo="0"/>
 </resources>
 <library><event name="Ev"><project name="Testprojekt 12">
  <sequence format="r1" duration="900s" tcStart="3600s" tcFormat="NDF">
   <spine>
    <asset-clip name="A001" ref="r2" offset="0s" duration="60s" start="0s"/>
    <asset-clip name="Runaway (Musik)" ref="r3" offset="4s" start="0s" duration="1001/10s" lane="-1" audioRole="music"/>
    <gap name="Gap" offset="60s" duration="10s">
     <asset-clip name="CEZ4030_1" ref="r4" lane="-2" offset="60s" duration="2501/25s" start="10s"/>
    </gap>
    <clip name="Skinny Love (Cover)" offset="70s" duration="30s" start="0s">
     <audio ref="r5" duration="30s" role="music"/>
    </clip>
    <audio ref="r5" name="Skinny Love Sting" offset="100s" duration="1s" lane="-1"/>
    <asset-clip name="Nullbreit" ref="r3" offset="120s" duration="0s"/>
   </spine>
  </sequence>
 </project></event></library>
</fcpxml>"""


def test_parse_fcpxml_story_elements_rational_times_and_assets():
    ev = parse_fcpxml(FCPXML)
    assert [e.from_clip_name for e in ev] == ["Runaway (Musik)", "CEZ4030_1", "Skinny Love (Cover)", "Skinny Love Sting"]
    assert [e.clip_name for e in ev] == ["AURORA - Runaway.wav", "CEZ4030_1.mp3", "Birdy - Skinny Love", "Birdy - Skinny Love"]
    assert [e.duration_sec() for e in ev] == [100.1, 100.04, 30.0, 1.0]
    assert [e.gema_duration_sec(25) for e in ev] == [101, 101, 30, 1]
    assert ev[0].record_in == "00:00:04:00" and ev[1].record_in == "00:01:00:00"   # offset in Elternzeit, fps aus format


def test_rational_seconds():
    assert rational_seconds("1001/24000s") == 1001 / 24000 and rational_seconds("5s") == 5.0 and rational_seconds("0s") == 0.0
    assert rational_seconds("5") is None and rational_seconds("x/ys") is None and rational_seconds("") is None


def test_parse_edl_dispatches_xml_by_root_and_survives_garbage():
    assert [e.clip_name for e in parse_edl(XMEML)][0] == "AURORA - Runaway.wav"
    assert [e.clip_name for e in parse_edl("\ufeff" + FCPXML)][1] == "CEZ4030_1.mp3"
    assert parse_edl("<html><body>nix</body></html>") == []
    assert parse_edl("<xmeml version='5'><sequence>") == []                 # kaputtes XML -> leer, kein Crash
    assert parse_xmeml(FCPXML) == [] and parse_fcpxml(XMEML) == []
    assert parse_edl("TITLE: x\n001  AX  AA  C  00:00:00:00 00:00:05:00 01:00:00:00 01:00:05:00\n* FROM CLIP NAME: a.wav\n")[0].clip_name == "a.wav"


# --- Pro Tools „Session Info as Text" (Reference Guide 2025.12.1 S. 689/690: tab-getrennte Track-EDLs
# mit Event-Nummer, Clip-Name, Start, End, Dauer; die Spaltenüberschriften liest der Parser aus der Datei)
PT = ("SESSION NAME:\tTestprojekt 12 Mix\nSAMPLE RATE:\t48000.000000\nBIT DEPTH:\t24-bit\nSESSION START TIMECODE:\t01:00:00:00\n"
      "TIMECODE FORMAT:\t25 Frame\n# OF AUDIO TRACKS:\t2\n# OF AUDIO CLIPS:\t3\n# OF AUDIO FILES:\t2\n\n\n"
      "T R A C K  L I S T I N G\nTRACK NAME:\tMusik 1\nCOMMENTS:\t\nUSER DELAY:\t0 Samples\nSTATE: \nPLUG-INS: \t\n"
      "CHANNEL \tEVENT   \tCLIP NAME                     \tSTART TIME    \tEND TIME      \tDURATION      \tSTATE\n"
      "1       \t1       \tAURORA - Runaway              \t01:00:04:00   \t01:01:44:12   \t00:01:40:12   \tUnmuted\n"
      "1       \t2       \tCEZ4030_1-01                  \t01:02:00:00   \t01:02:10:00   \t00:00:10:00   \tUnmuted\n"
      "\nTRACK NAME:\tMusik 2\nCOMMENTS:\t\nUSER DELAY:\t0 Samples\nSTATE: \n"
      "CHANNEL \tEVENT   \tCLIP NAME                     \tSTART TIME    \tEND TIME      \tDURATION      \tSTATE\n"
      "1       \t1       \tBirdy - Skinny Love           \t01:03:00:00   \t01:03:30:00   \t\tUnmuted\n")


def test_parse_protools_session_text_reads_track_edls():
    from core.edl import parse_protools_text
    ev = parse_protools_text(PT)
    assert [e.clip_name for e in ev] == ["AURORA - Runaway", "CEZ4030_1-01", "Birdy - Skinny Love"]
    assert [e.track_type for e in ev] == ["Musik 1", "Musik 1", "Musik 2"]
    assert [e.gema_duration_sec(25) for e in ev] == [101, 10, 30]       # 1:40:12 -> 100.48 s; ohne Dauer: End - Start
    assert ev[0].record_in == "01:00:04:00"
    assert parse_edl(PT)[1].clip_name == "CEZ4030_1-01"                   # Dispatcher erkennt SESSION NAME + TRACK NAME
    # Min:Secs und Samples als Time Format
    ms = PT.replace("01:00:04:00   ", "0:04.000      ").replace("01:01:44:12   ", "1:44.480      ").replace("00:01:40:12   ", "1:40.480      ")
    assert abs(parse_protools_text(ms)[0].duration_sec() - 100.48) < 1e-9
    smp = PT.replace("00:01:40:12   ", "4823040       ")
    assert abs(parse_protools_text(smp)[0].duration_sec() - 100.48) < 1e-9
    # Bars|Beats ist nicht umrechenbar -> Zeile fällt weg, kein Crash
    bb = PT.replace("00:01:40:12   ", "3|2|000       ").replace("01:00:04:00   ", "1|1|000       ").replace("01:01:44:12   ", "2|1|000       ")
    assert [e.clip_name for e in parse_protools_text(bb)] == ["CEZ4030_1-01", "Birdy - Skinny Love"]
    assert parse_protools_text("nur text") == []
