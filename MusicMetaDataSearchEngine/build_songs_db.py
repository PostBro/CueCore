#!/usr/bin/env python3
"""Baut die Songs-Datenbank für die MusicMetaDataSearchEngine.

Quelle: kuratierte MP3s in 'LC verified/'. Felder:
  interpret, titel, komponist, texter, labelname, labelcode
- interpret/titel: aus ID3 (TPE1/TIT2), Fallback Dateiname
- labelname/labelcode: aus dem Dateinamen-Klammerblock [Label_LCxxxxx] (kuratiert, maßgeblich)
- komponist/texter: aus ID3 (TCOM / TEXT), sonst leer -> Recherche-Bedarf

Ausgabe: data/songs.db (SQLite, Tabelle 'songs') + data/songs.csv
Dubletten (gleicher interpret+titel): gleicher LC -> eine Zeile; verschiedener LC -> als Konflikt geflaggt.
"""
from __future__ import annotations
import csv, os, re, shutil, sqlite3, subprocess, sys, tempfile
from pathlib import Path

SRCS = [Path(a) for a in sys.argv[1:]]          # Ordner "LC verified" als Argument (kein fester Pfad im Repo)
if not SRCS:                                     # ohne Ordner nichts überschreiben (der Seed käme leer heraus)
    sys.exit("Aufruf: python build_songs_db.py <Ordner 'LC verified'> [weitere Ordner]")
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

# Klammerblock [Label_LCxxxxx] am Ende; davor ' - [' oder ' ['; LC evtl. mit Leerzeichen ('LC 08477')
BRACKET = re.compile(r"\[(?P<label>[^\[\]]+?)_LC ?(?P<lc>\d{5,6})\][^\[\]]*\.(mp3|m4a)$")

def ffprobe_tags(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format_tags=artist,title,composer,TCOM,lyricist,TEXT,author",
             "-of", "default=noprint_wrappers=1", str(path)],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return {}
    d = {}
    for line in out.splitlines():
        if line.startswith("TAG:") and "=" in line:
            k, v = line[4:].split("=", 1)
            d[k.lower()] = v.strip()
    return d

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().casefold())

rows, skipped = [], []
allfiles = []
for SRC in SRCS:
    allfiles += sorted(SRC.glob("*.mp3")) + sorted(SRC.glob("*.m4a"))
for f in allfiles:
    m = BRACKET.search(f.name)
    if m:
        label, lc, start = m["label"], "LC" + m["lc"], m.start()
    else:
        # Fallback A: Label mit inneren Klammern (z.B. '[PIAS] Recordings Germany')
        m2 = re.search(r"\[(?P<label>.+)_LC ?(?P<lc>\d{5,6})\][^\[\]]*\.(mp3|m4a)$", f.name)
        # Fallback B: fehlender Labelcode ('..._LC]') -> leerer LC
        m3 = re.search(r"\[(?P<label>.+?)_LC\][^\[\]]*\.(mp3|m4a)$", f.name)
        if m2:
            label, lc, start = m2["label"].lstrip("["), "LC" + m2["lc"], m2.start()
        elif m3:
            label, lc, start = m3["label"], "", m3.start()
        else:
            skipped.append(f.name); continue
    rest = f.name[:start].rstrip()
    if rest.endswith(" -"):          # ' - [' Variante
        rest = rest[:-2].rstrip()
    # interpret/titel aus Dateiname (erstes ' - ' trennt), ID3 bevorzugt fuer saubere Schreibweise
    fn_artist, _, fn_title = rest.partition(" - ")
    t = ffprobe_tags(f)
    interpret = t.get("artist") or fn_artist
    titel = t.get("title") or fn_title
    komponist = t.get("composer") or t.get("tcom") or ""
    texter = t.get("lyricist") or t.get("text") or t.get("author") or ""
    rows.append({
        "interpret": interpret.strip(), "titel": titel.strip(),
        "komponist": komponist.strip(), "texter": texter.strip(),
        "labelname": label.strip(), "labelcode": lc, "_file": f.name,
    })

# Dedupe nach (interpret, titel)
by_key: dict[tuple, list] = {}
for r in rows:
    by_key.setdefault((norm(r["interpret"]), norm(r["titel"])), []).append(r)

final, conflicts, dup_files_same_lc = [], [], []
for key, group in by_key.items():
    if len(group) == 1:
        final.append(group[0]); continue
    codes = {g["labelcode"] for g in group}
    if len(codes) == 1:
        final.append(group[0])  # behalte ersten, Rest = Dublette
        dup_files_same_lc.extend(g["_file"] for g in group[1:])
    else:
        for g in group:
            conflicts.append(g)
        final.append(group[0])  # vorlaeufig ersten behalten, Konflikt separat melden

# SQLite — lokal im Temp-Ordner bauen (Cloud-Sync-Ordner + SQLite = disk I/O error), dann kopieren
db = OUT / "songs.db"
tmpdb = Path(tempfile.gettempdir()) / "songs_build.db"
if tmpdb.exists():
    tmpdb.unlink()
con = sqlite3.connect(tmpdb)
con.execute("DROP TABLE IF EXISTS songs")
con.execute("""CREATE TABLE songs (
  id INTEGER PRIMARY KEY,
  interpret TEXT NOT NULL,
  titel TEXT NOT NULL,
  komponist TEXT,
  texter TEXT,
  labelname TEXT,
  labelcode TEXT,
  UNIQUE(interpret, titel))""")
con.executemany(
    "INSERT OR IGNORE INTO songs (interpret,titel,komponist,texter,labelname,labelcode) VALUES (?,?,?,?,?,?)",
    [(r["interpret"], r["titel"], r["komponist"], r["texter"], r["labelname"], r["labelcode"]) for r in final])
con.commit()
n_songs = con.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
n_komp = con.execute("SELECT COUNT(*) FROM songs WHERE komponist<>''").fetchone()[0]
n_text = con.execute("SELECT COUNT(*) FROM songs WHERE texter<>''").fetchone()[0]
con.close()
if db.exists():           # Cloud-Sync-Lock: bestehende Datei erst entfernen, sonst Timeout
    db.unlink()
shutil.copy(tmpdb, db)    # fertige DB ins Zielverzeichnis kopieren

# CSV-Export (ebenfalls erst loeschen wegen Cloud-Sync-Lock)
csvp = OUT / "songs.csv"
if csvp.exists():
    csvp.unlink()
with open(csvp, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["interpret", "titel", "komponist", "texter", "labelname", "labelcode"])
    for r in sorted(final, key=lambda x: (norm(x["interpret"]), norm(x["titel"]))):
        w.writerow([r["interpret"], r["titel"], r["komponist"], r["texter"], r["labelname"], r["labelcode"]])

print(f"DB: {db}")
print(f"Songs in DB: {n_songs}  | mit Komponist: {n_komp}  | mit Texter: {n_text}")
print(f"MP3 gescannt: {len(rows)}  | ohne Schema/uebersprungen: {len(skipped)}")
print(f"Dubletten (gleicher LC, loeschbar): {len(dup_files_same_lc)}")
for x in dup_files_same_lc: print("  DUP:", x)
print(f"Konflikt-Songs (gleicher Titel, verschiedener LC): {len(conflicts)}")
for c in conflicts: print(f"  CONFLICT: {c['interpret']} - {c['titel']} -> {c['labelname']} {c['labelcode']}")
if skipped:
    print("Uebersprungen (kein [Label_LCxxxxx]):")
    for s in skipped[:20]: print("  ?", s)
