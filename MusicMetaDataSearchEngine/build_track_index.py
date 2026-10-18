#!/usr/bin/env python3
"""Lokalen Track-Index aus den offiziellen Dumps bauen — eigener Index neben
labels.db (Pflichtfelder Interpret, Titel, Label, LC, Komponist, Texter; beide Dumps; per Script).

    python build_track_index.py discogs     --dump discogs_20260901_releases.xml.gz [--out <db>] [--limit N]
    python build_track_index.py musicbrainz --dump <ordner mit mbdump/>             [--out <db>]
    python build_track_index.py extract-mb  --tar mbdump.tar.bz2 --dest <ordner>      (bsdtar, ~30 min)
    python build_track_index.py status      [--out <db>]

Ziel-DB: <Datenordner>/track_index.db (core/trackindex.py, außerhalb von Cloud-synchronisierten Ordnern). Jede Quelle baut in eine
Staging-Tabelle und ersetzt am Ende nur ihre eigenen Zeilen; die andere Quelle bleibt stehen.

DISCOGS (`_releases.xml.gz`, 10,5 GB gz, CC0): wird NIE entpackt — gzip + xml.etree.iterparse streamen,
je <release> `clear()`. Nur Releases mit <identifier type="Label Code">, deren Code (normalize_labelcode)
im GVL-Master steht; ein Discogs-LC ohne GVL-Treffer wird verworfen (Projektregel, wie beim Live-Call).
Je Track eine Zeile: Interpret (Track-Artists, sonst Release-Artists; Discogs-Suffix „ (2)“ entfernt,
<join> wie „&“/„Feat.“ übernommen), Titel, Label(s), LC, Komponist/Texter aus den extraartists-Rollen
(Release-Ebene, ggf. auf <tracks> begrenzt, plus Track-Ebene), Album = Release-Titel, Jahr, Katalognummer.
Rollen (Discogs-Schreibweise, Klammerzusätze ignoriert):
    Komponist: Written-By, Written By, Composed By, Music By, Composer, Songwriter
    Texter:    Written-By, Written By, Lyrics By, Words By, Lyricist, Songwriter
„Written-By“ steht bei Discogs für Musik UND Text und landet deshalb in beiden Feldern.

MUSICBRAINZ (Full Export mbdump.tar.bz2, CC0): Tabellen im COPY-Textformat (Spaltenreihenfolge laut
admin/sql/CreateTables.sql, master 14.09.2026 — beim ersten Datensatz verifiziert):
    label(id, gid, name, …, label_code=Spalte 10) · release_label(id, release, label, catalog_number)
    release(id, gid, name, artist_credit) · medium(id, release) · track(id, gid, recording, medium, position,
    number, name, artist_credit) · artist_credit(id, name) · artist(id, gid, name)
    link_type(id, …, entity_type0=5, entity_type1=6, name=7) · link(id, link_type) · l_recording_work /
    l_artist_work(id, link, entity0, entity1)
Nur Labels mit label_code im GVL-Master -> deren Releases -> Medien -> Tracks (Titel + Artist-Credit).
Komponist/Texter über Recording -> Werk -> Artist-Werk-Relationen: link_type composer -> Komponist,
lyricist/librettist -> Texter, writer (unspezifisch) -> beide. Kein Jahr (steht in release_country/
release_unknown_country, nicht Pflicht). Die Rohtabellen landen in <out>.build.db (wird gelöscht).
"""
from __future__ import annotations
import argparse
import gzip
import re
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from core import trackindex
from core.normalize import normalize_labelcode
from build_label_aliases_mb import LABEL_COLS, _check_label_row, read_table
from reconcile.csv_io import read_csv

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER = PROJECT / "data" / "GVL_Labelcodes_synced.csv"

# --- Discogs -----------------------------------------------------------------------------------

ROLE_COMPOSER = {"written-by", "written by", "composed by", "music by", "composer", "songwriter"}
ROLE_LYRICIST = {"written-by", "written by", "lyrics by", "words by", "lyricist", "songwriter"}
_BRACKET = re.compile(r"\s*\[[^\]]*\]")
_DISCOGS_SUFFIX = re.compile(r"\s+\(\d+\)$")          # 'Swag (2)' -> 'Swag' (Discogs-Disambiguierung)
_RANGE = re.compile(r"^([A-Za-z]*)(\d+)\s*(?:to|-|–)\s*([A-Za-z]*)(\d+)$")
_SEP = "; "


def load_gvl(master_csv: str | Path = DEFAULT_MASTER) -> dict[str, str]:
    """Code -> GVL-Labelname (erster Name je Code; nur diese Codes zählen)."""
    out: dict[str, str] = {}
    for r in read_csv(master_csv):
        out.setdefault(normalize_labelcode(r.labelcode), r.label)
    return out


def clean_name(name: str) -> str:
    return _DISCOGS_SUFFIX.sub("", (name or "").strip())


def artist_credit(container) -> str:
    """<artists><artist><name>A</name><join>&</join></artist><artist><name>B</name></artist> -> 'A & B'."""
    if container is None:
        return ""
    artists = container.findall("artist")
    text = ""
    for i, a in enumerate(artists):
        text += clean_name(a.findtext("name"))
        join = (a.findtext("join") or "").strip()
        if join and i < len(artists) - 1:
            text += ", " if join == "," else f" {join} "
    return re.sub(r"\s+", " ", text).strip(" ,")


def split_roles(role: str) -> list[str]:
    return [r.strip().lower() for r in _BRACKET.sub("", role or "").split(",") if r.strip()]


def writer_roles(role: str) -> tuple[bool, bool]:
    roles = split_roles(role)
    return any(r in ROLE_COMPOSER for r in roles), any(r in ROLE_LYRICIST for r in roles)


def expand_positions(spec: str) -> set[str] | None:
    """Discogs <tracks>-Angabe ('A1, B2', 'A1 to A4', '1-3') -> Positionen. None = nicht auflösbar
    (z.B. 'A1 to B3' über Seiten hinweg) -> der Credit wird auf keinen Track angewandt (nicht raten)."""
    out: set[str] = set()
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        m = _RANGE.match(tok)
        if not m:
            out.add(tok.lower())
            continue
        p1, n1, p2, n2 = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if (p2 and p2 != p1) or n2 < n1 or n2 - n1 > 99:
            return None
        out.update(f"{p1}{n}".lower() for n in range(n1, n2 + 1))
    return out


def label_codes(elem, gvl: dict[str, str]) -> list[str]:
    codes: list[str] = []
    for ident in elem.findall("identifiers/identifier"):
        if (ident.get("type") or "").strip().lower() != "label code":
            continue
        for part in re.split(r"[/,;]| and ", ident.get("value") or ""):
            code = normalize_labelcode(part)
            if code and code in gvl and code not in codes:
                codes.append(code)
    return codes


def _add_writers(target: dict[str, list[str]], elem, positions: set[str] | None = None) -> None:
    """extraartists eines Elements in target['komponist'|'texter'] (Namen, dedupliziert) — optional nur für
    die genannten Track-Positionen (positions als Teil des Schlüssels)."""
    ea = elem.find("extraartists")
    if ea is None:
        return
    for a in ea.findall("artist"):
        comp, lyr = writer_roles(a.findtext("role") or "")
        if not (comp or lyr):
            continue
        name = clean_name(a.findtext("name"))
        if not name:
            continue
        scope = a.findtext("tracks")
        pos = expand_positions(scope) if scope and scope.strip() else None
        if scope and scope.strip() and pos is None:
            continue                                  # Bereich nicht auflösbar -> nicht raten
        key = "*" if pos is None else pos
        for field, ok in (("komponist", comp), ("texter", lyr)):
            if ok:
                target.setdefault(field, []).append((key, name))


def _writers_for(rel_writers: dict, track_writers: dict, position: str, field: str) -> str:
    names: list[str] = []
    for key, name in rel_writers.get(field, []):
        if (key == "*" or position.lower() in key) and name not in names:
            names.append(name)
    for _k, name in track_writers.get(field, []):
        if name not in names:
            names.append(name)
    return _SEP.join(names)


def iter_tracks(tracklist):
    """Alle <track> mit Position — auch <sub_tracks> von Index-Tracks; Überschriften (ohne Position) nicht."""
    if tracklist is None:
        return
    for tr in tracklist.iter("track"):
        pos = (tr.findtext("position") or "").strip()
        title = (tr.findtext("title") or "").strip()
        if pos and title:
            yield pos, title, tr


def parse_release(elem, gvl: dict[str, str]) -> list[tuple]:
    """Ein <release> -> Staging-Zeilen (leer, wenn kein GVL-Labelcode dran hängt)."""
    codes = label_codes(elem, gvl)
    if not codes:
        return []
    rid = elem.get("id") or ""
    album = (elem.findtext("title") or "").strip()
    rel_artist = artist_credit(elem.find("artists"))
    labels = elem.findall("labels/label")
    label_names = _SEP.join(dict.fromkeys(clean_name(l.get("name")) for l in labels if l.get("name")))
    catno = (labels[0].get("catno") or "").strip() if labels else ""
    released = (elem.findtext("released") or "").strip()
    jahr = released[:4] if re.match(r"^\d{4}", released) and released[:4] != "0000" else ""
    rel_writers: dict[str, list] = {}
    _add_writers(rel_writers, elem)
    rows: list[tuple] = []
    for pos, title, tr in iter_tracks(elem.find("tracklist")):
        interpret = artist_credit(tr.find("artists")) or rel_artist
        if not interpret or interpret.lower() == "various":
            continue
        tw: dict[str, list] = {}
        _add_writers(tw, tr)
        komp = _writers_for(rel_writers, tw, pos, "komponist")
        text = _writers_for(rel_writers, tw, pos, "texter")
        for code in codes:
            rows.append(trackindex.make_row(interpret=interpret, titel=title, labelcode=code,
                                            label=label_names or gvl.get(code, ""), komponist=komp, texter=text,
                                            album=album, jahr=jahr, catno=catno, source="discogs", source_id=rid))
    return rows


class _Counting:
    """Read-Wrapper, der die gelesenen (komprimierten) Bytes zählt — Fortschritt ohne Entpacken."""
    def __init__(self, f):
        self.f, self.pos = f, 0

    def read(self, n=-1):
        b = self.f.read(n)
        self.pos += len(b)
        return b


class Progress:
    def __init__(self, total_bytes: int, every: float = 15.0, out=None):
        self.total, self.every, self.out = total_bytes, every, out or sys.stdout
        self.t0 = self.last = time.time()

    def tick(self, pos: int, msg: str, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last < self.every:
            return
        self.last = now
        frac = pos / self.total if self.total else 0.0
        el = now - self.t0
        eta = (el / frac - el) if frac > 0.001 else 0.0
        print(f"[{el/60:6.1f} min] {100*frac:5.1f}%  {msg}  ETA {eta/60:.0f} min", file=self.out, flush=True)


def build_discogs(dump: str | Path, out_db: str | Path, master: str | Path = DEFAULT_MASTER, *,
                  limit: int | None = None, progress_every: float = 15.0, out=None) -> dict:
    out = out or sys.stdout
    dump, out_db = Path(dump), Path(out_db)
    gvl = load_gvl(master)
    con = trackindex.open_index(out_db, create=True)
    trackindex.begin_staging(con)
    stats = {"releases_seen": 0, "releases_with_lc": 0, "releases_lc_in_gvl": 0, "rows_staged": 0}
    raw = _Counting(open(dump, "rb"))
    prog = Progress(dump.stat().st_size, progress_every, out)
    batch: list[tuple] = []
    root = None
    t0 = time.time()
    with gzip.GzipFile(fileobj=raw) as gz:
        for ev, el in ET.iterparse(gz, events=("start", "end")):
            if ev == "start":
                if root is None:
                    root = el
                continue
            if el.tag != "release":
                continue
            stats["releases_seen"] += 1
            if any((i.get("type") or "").strip().lower() == "label code" for i in el.findall("identifiers/identifier")):
                stats["releases_with_lc"] += 1
                rows = parse_release(el, gvl)
                if rows:
                    stats["releases_lc_in_gvl"] += 1
                    batch.extend(rows)
                    stats["rows_staged"] += len(rows)
            root.clear()
            if len(batch) >= 5000:
                trackindex.stage_rows(con, batch)
                con.commit()
                batch = []
            prog.tick(raw.pos, f"Releases {stats['releases_seen']:,} · mit LC {stats['releases_with_lc']:,} · "
                               f"GVL {stats['releases_lc_in_gvl']:,} · Zeilen {stats['rows_staged']:,}")
            if limit and stats["releases_seen"] >= limit:
                break
    trackindex.stage_rows(con, batch)
    con.commit()
    prog.tick(raw.pos, "Aggregation (GROUP BY nkey, labelcode) …", force=True)
    n = trackindex.finalize(con, "discogs", {"dump": dump.name, **stats,
                                             "seconds": int(time.time() - t0)})
    con.execute("VACUUM")
    con.close()
    stats["rows"] = n
    print(f"discogs: {n:,} Zeilen im Index ({stats['releases_lc_in_gvl']:,} Releases mit GVL-LC von "
          f"{stats['releases_seen']:,}) -> {out_db}", file=out, flush=True)
    return stats


# --- MusicBrainz ---------------------------------------------------------------------------------

MB_TABLES = ("label", "release_label", "release", "medium", "track", "artist_credit", "artist",
             "link_type", "link", "l_recording_work", "l_artist_work")
# Spaltenindizes laut CreateTables.sql (master, 14.09.2026); beim ersten Datensatz je Tabelle geprüft.
COLS = {
    "release_label": {"id": 0, "release": 1, "label": 2, "catalog_number": 3},
    "release": {"id": 0, "gid": 1, "name": 2, "artist_credit": 3},
    "medium": {"id": 0, "release": 1},
    "track": {"id": 0, "gid": 1, "recording": 2, "medium": 3, "name": 6, "artist_credit": 7},
    "artist_credit": {"id": 0, "name": 1},
    "artist": {"id": 0, "gid": 1, "name": 2},
    "link_type": {"id": 0, "gid": 3, "entity_type0": 4, "entity_type1": 5, "name": 6},
    "link": {"id": 0, "link_type": 1},
    "l_recording_work": {"id": 0, "link": 1, "entity0": 2, "entity1": 3},
    "l_artist_work": {"id": 0, "link": 1, "entity0": 2, "entity1": 3},
}
MB_ROLES = {"composer": "komponist", "lyricist": "texter", "librettist": "texter", "writer": "beide"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _check(table: str, row: list, gid_col: int | None, int_cols: tuple[int, ...]) -> None:
    ok = len(row) > max([gid_col or 0, *int_cols])
    ok = ok and (gid_col is None or (row[gid_col] and _UUID.match(row[gid_col])))
    ok = ok and all(row[c] is None or row[c].isdigit() for c in int_cols)
    if not ok:
        raise SystemExit(f"mbdump/{table}: Spaltenreihenfolge weicht von CreateTables.sql ab — nicht raten, "
                         f"Schema prüfen (erster Datensatz: {row[:8]})")


def extract_mb(tar: str | Path, dest: str | Path) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["tar", "-xjf", str(tar), "-C", str(dest), *(f"mbdump/{t}" for t in MB_TABLES)]
    print("bsdtar liest das ganze Archiv sequenziell (7,5 GB bz2, ~30 min):", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _import(con: sqlite3.Connection, table: str, path: Path, cols: dict, keep, *, gid_col=None,
            int_cols=(), out=None, label: str = "") -> int:
    out = out or sys.stdout
    """Eine COPY-Datei gefiltert in build.<table> laden. keep(row) -> Tupel oder None."""
    names = list(cols)
    con.execute(f"DROP TABLE IF EXISTS build.mb_{table}")
    con.execute(f"CREATE TABLE build.mb_{table} ({', '.join(names)})")
    n = 0
    batch: list[tuple] = []
    first = True
    t0 = time.time()
    for row in read_table(path):
        if first:
            _check(table, row, gid_col, int_cols)
            first = False
        t = keep(row)
        if t is None:
            continue
        batch.append(t)
        n += 1
        if len(batch) >= 20000:
            con.executemany(f"INSERT INTO build.mb_{table} VALUES ({','.join('?' * len(names))})", batch)
            batch = []
    con.executemany(f"INSERT INTO build.mb_{table} VALUES ({','.join('?' * len(names))})", batch)
    con.commit()
    print(f"  {table:<18} {n:>12,} Zeilen  ({time.time() - t0:.0f} s) {label}", file=out, flush=True)
    return n


def build_musicbrainz(dump_dir: str | Path, out_db: str | Path, master: str | Path = DEFAULT_MASTER, *,
                      out=None) -> dict:
    out = out or sys.stdout
    dump_dir, out_db = Path(dump_dir), Path(out_db)
    mb = dump_dir / "mbdump"
    missing = [t for t in MB_TABLES if not (mb / t).exists()]
    if missing:
        raise SystemExit(f"fehlt unter {mb}: {', '.join(missing)} (build_track_index.py extract-mb …)")
    gvl = load_gvl(master)
    con = trackindex.open_index(out_db, create=True)
    build_db = out_db.with_suffix(".build.db")
    if build_db.exists():
        build_db.unlink()
    con.execute("ATTACH DATABASE ? AS build", (str(build_db),))
    t_all = time.time()
    stats: dict = {}
    print("MusicBrainz: Tabellen filtern …", file=out, flush=True)

    # 1. Labels mit GVL-Code
    L = LABEL_COLS
    def keep_label(r):
        code = normalize_labelcode(r[L["label_code"]] or "") if r[L["label_code"]] else ""
        return (int(r[L["id"]]), r[L["name"]], code) if code and code in gvl else None
    stats["labels_gvl"] = _import(con, "label", mb / "label", {"id": 0, "name": 1, "code": 2}, keep_label,
                                  gid_col=L["gid"], int_cols=(L["id"],), out=out, label="(mit GVL-Code)")
    _check_label_row(next(read_table(mb / "label")))
    label_ids = {r[0] for r in con.execute("SELECT id FROM build.mb_label")}

    # 2. release_label -> Releases dieser Labels
    C = COLS["release_label"]
    def keep_rl(r):
        lid = int(r[C["label"]]) if r[C["label"]] else 0
        return (int(r[C["release"]]), lid, r[C["catalog_number"]] or "") if lid in label_ids else None
    stats["release_labels"] = _import(con, "release_label", mb / "release_label",
                                      {"release": 0, "label": 1, "catno": 2}, keep_rl,
                                      int_cols=(C["id"], C["release"]), out=out)
    release_ids = {r[0] for r in con.execute("SELECT release FROM build.mb_release_label")}

    # 3. release -> Titel + Artist-Credit
    C = COLS["release"]
    def keep_release(r):
        rid = int(r[C["id"]])
        return (rid, r[C["name"]], int(r[C["artist_credit"]])) if rid in release_ids else None
    stats["releases"] = _import(con, "release", mb / "release", {"id": 0, "name": 1, "artist_credit": 2},
                                keep_release, gid_col=C["gid"], int_cols=(C["id"], C["artist_credit"]), out=out)

    # 4. medium -> Release
    C = COLS["medium"]
    def keep_medium(r):
        rid = int(r[C["release"]])
        return (int(r[C["id"]]), rid) if rid in release_ids else None
    stats["media"] = _import(con, "medium", mb / "medium", {"id": 0, "release": 1}, keep_medium,
                             int_cols=(C["id"], C["release"]), out=out)
    medium_ids = {r[0] for r in con.execute("SELECT id FROM build.mb_medium")}
    del release_ids

    # 5. track -> Recording, Medium, Titel, Artist-Credit
    C = COLS["track"]
    def keep_track(r):
        mid = int(r[C["medium"]])
        if mid not in medium_ids:
            return None
        return (int(r[C["recording"]]), mid, r[C["name"]], int(r[C["artist_credit"]]))
    stats["tracks"] = _import(con, "track", mb / "track", {"recording": 0, "medium": 1, "name": 2, "artist_credit": 3},
                              keep_track, gid_col=C["gid"], int_cols=(C["id"], C["recording"], C["medium"]), out=out)
    del medium_ids

    # 6. artist_credit + artist (komplett; Join in SQL)
    C = COLS["artist_credit"]
    stats["artist_credits"] = _import(con, "artist_credit", mb / "artist_credit", {"id": 0, "name": 1},
                                      lambda r: (int(r[C["id"]]), r[C["name"]]), int_cols=(C["id"],), out=out)
    C = COLS["artist"]
    stats["artists"] = _import(con, "artist", mb / "artist", {"id": 0, "name": 1},
                               lambda r: (int(r[C["id"]]), r[C["name"]]), gid_col=C["gid"], int_cols=(C["id"],), out=out)

    # 7. Writer-Relationen: link_type (artist->work, Name in MB_ROLES) -> link -> l_artist_work
    C = COLS["link_type"]
    role_of_type: dict[int, str] = {}
    seen_names: set[str] = set()
    first = True
    for r in read_table(mb / "link_type"):
        if first:
            _check("link_type", r, C["gid"], (C["id"],)); first = False
        if r[C["entity_type0"]] == "artist" and r[C["entity_type1"]] == "work":
            seen_names.add(r[C["name"]])
            if r[C["name"]] in MB_ROLES:
                role_of_type[int(r[C["id"]])] = MB_ROLES[r[C["name"]]]
    missing_roles = [n for n in ("composer", "lyricist", "writer") if n not in seen_names]
    if missing_roles:
        raise SystemExit(f"link_type artist->work ohne {missing_roles} — Namen im Dump: {sorted(seen_names)}")
    print(f"  link_type artist->work: {sorted(seen_names)}", file=out, flush=True)
    C = COLS["link"]
    role_of_link: dict[int, str] = {}
    first = True
    for r in read_table(mb / "link"):
        if first:
            _check("link", r, None, (C["id"], C["link_type"])); first = False
        role = role_of_type.get(int(r[C["link_type"]]))
        if role:
            role_of_link[int(r[C["id"]])] = role
    C = COLS["l_artist_work"]
    def keep_aw(r):
        role = role_of_link.get(int(r[C["link"]]))
        return (int(r[C["entity0"]]), int(r[C["entity1"]]), role) if role else None
    stats["artist_work_links"] = _import(con, "l_artist_work", mb / "l_artist_work",
                                         {"artist": 0, "work": 1, "role": 2}, keep_aw,
                                         int_cols=(C["id"], C["link"], C["entity0"], C["entity1"]), out=out)
    C = COLS["l_recording_work"]
    stats["recording_work_links"] = _import(con, "l_recording_work", mb / "l_recording_work",
                                            {"recording": 0, "work": 1},
                                            lambda r: (int(r[C["entity0"]]), int(r[C["entity1"]])),
                                            int_cols=(C["id"], C["link"], C["entity0"], C["entity1"]), out=out)

    print("MusicBrainz: Indizes + Autoren je Recording …", file=out, flush=True)
    con.executescript("""
        CREATE INDEX build.ix_rl ON mb_release_label(release);
        CREATE INDEX build.ix_med ON mb_medium(id);
        CREATE INDEX build.ix_rel ON mb_release(id);
        CREATE INDEX build.ix_lab ON mb_label(id);
        CREATE INDEX build.ix_ac ON mb_artist_credit(id);
        CREATE INDEX build.ix_ar ON mb_artist(id);
        CREATE INDEX build.ix_aw ON mb_l_artist_work(work);
        CREATE INDEX build.ix_rw ON mb_l_recording_work(recording);
        CREATE TABLE build.mb_writers AS
          SELECT recording,
                 (SELECT group_concat(name, '; ') FROM (SELECT DISTINCT a.name AS name
                     FROM mb_l_recording_work rw JOIN mb_l_artist_work aw ON aw.work = rw.work
                     JOIN mb_artist a ON a.id = aw.artist
                     WHERE rw.recording = x.recording AND aw.role IN ('komponist', 'beide') ORDER BY a.name)) AS komponist,
                 (SELECT group_concat(name, '; ') FROM (SELECT DISTINCT a.name AS name
                     FROM mb_l_recording_work rw JOIN mb_l_artist_work aw ON aw.work = rw.work
                     JOIN mb_artist a ON a.id = aw.artist
                     WHERE rw.recording = x.recording AND aw.role IN ('texter', 'beide') ORDER BY a.name)) AS texter
          FROM (SELECT DISTINCT recording FROM mb_track) x;
        CREATE INDEX build.ix_w ON mb_writers(recording);
    """)
    print("MusicBrainz: Zeilen ins Staging …", file=out, flush=True)
    trackindex.begin_staging(con)
    cur = con.execute("""
        SELECT ac.name, t.name, l.code, l.name, w.komponist, w.texter, r.name, rl.catno, r.id
        FROM mb_track t
        JOIN mb_medium m ON m.id = t.medium
        JOIN mb_release r ON r.id = m.release
        JOIN mb_release_label rl ON rl.release = r.id
        JOIN mb_label l ON l.id = rl.label
        JOIN mb_artist_credit ac ON ac.id = t.artist_credit
        LEFT JOIN mb_writers w ON w.recording = t.recording""")
    n_staged = 0
    batch: list[tuple] = []
    for interpret, titel, code, lname, komp, text, album, catno, rid in cur:
        if not interpret or not titel or interpret.lower() == "various artists":
            continue
        batch.append(trackindex.make_row(interpret=interpret, titel=titel, labelcode=code, label=lname,
                                         komponist=komp or "", texter=text or "", album=album or "",
                                         catno=catno or "", source="musicbrainz", source_id=str(rid)))
        n_staged += 1
        if len(batch) >= 20000:
            trackindex.stage_rows(con, batch)
            batch = []
    trackindex.stage_rows(con, batch)
    con.commit()
    stats["rows_staged"] = n_staged
    con.execute("DETACH DATABASE build")
    build_db.unlink(missing_ok=True)
    ts = mb / "TIMESTAMP"
    n = trackindex.finalize(con, "musicbrainz", {"dump": dump_dir.name, **stats,
                                                 "seconds": int(time.time() - t_all)})
    con.execute("VACUUM")
    con.close()
    stats["rows"] = n
    print(f"musicbrainz: {n:,} Zeilen im Index ({n_staged:,} Track×Label-Zeilen, {stats['labels_gvl']:,} Labels "
          f"mit GVL-Code) -> {out_db}", file=out, flush=True)
    return stats


# --- CLI -----------------------------------------------------------------------------------------

def cmd_status(out_db: Path) -> int:
    con = trackindex.open_index(out_db)
    st = trackindex.stats(con, out_db)
    if not st["present"]:
        print(f"kein Index unter {out_db}")
        return 1
    print(f"{out_db}: {st['rows']:,} Zeilen, {st['size_bytes'] / 1e6:,.0f} MB")
    for src in trackindex.SOURCES:
        print(f"  {src:<12} {st['by_source'][src]:>12,}  Dump {st['meta'].get(src + '_dump', '—')}  "
              f"gebaut {st['meta'].get(src + '_built_at', '—')}")
    print(f"  Suchtabellen: artists {'ja' if st['artists'] else 'FEHLT (compact)'}, "
          f"titles+FTS {'ja' if st['titles'] else 'FEHLT (titles)'}")
    return 0


def cmd_titles(out_db: Path) -> int:
    """Nur die Titel-Tabelle + FTS5-Trigram (Titel-only-Suche) nachbauen — additiv, kein VACUUM.
    Auf dem 17-Mio.-Index vom 14.09.2026: 65 s, +1,58 GB."""
    con = trackindex.open_index(out_db)
    if con is None:
        print(f"kein Index unter {out_db}")
        return 1
    before = out_db.stat().st_size
    t = time.time()
    n = trackindex.ensure_titles(con)
    print(f"  Titel-Tabelle + FTS5-Trigram gebaut: {n:,} Interpret+Titel-Schlüssel, {time.time() - t:.0f} s, "
          f"{before / 1e6:,.0f} -> {out_db.stat().st_size / 1e6:,.0f} MB")
    if trackindex.has_artists(con):
        t = time.time()
        na = trackindex.ensure_artists_fts(con)
        print(f"  Interpreten-FTS5-Trigram gebaut: {na:,} Interpreten, {time.time() - t:.0f} s, "
              f"jetzt {out_db.stat().st_size / 1e6:,.0f} MB")
    return 0


def cmd_compact(out_db: Path) -> int:
    con = trackindex.open_index(out_db)
    if con is None:
        print(f"kein Index unter {out_db}")
        return 1
    print(f"{out_db}: {out_db.stat().st_size / 1e6:,.0f} MB vorher, VACUUM läuft (Minuten bei 17 Mio. Zeilen) …")
    r = trackindex.compact(con, out_db)
    print(f"  Interpreten-Tabelle (unscharfe Suche) und Titel-Tabelle + FTS5 (Titel-only) gebaut; "
          f"Index idx_tracks_nkey {'entfernt' if r['dropped_index'] else 'war nicht vorhanden'}, "
          f"jetzt {r['bytes_after'] / 1e6:,.0f} MB "
          f"({(r['bytes_before'] - r['bytes_after']) / 1e6:,.0f} MB gespart)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(trackindex.path_for(trackindex.default_data_dir())),
                    help="Ziel-SQLite (Default: Datenordner der App/track_index.db)")
    ap.add_argument("--master", default=str(DEFAULT_MASTER), help="GVL-Master-CSV (nur diese Codes zählen)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discogs", help="Discogs-Release-Dump streamend einlesen")
    d.add_argument("--dump", required=True, help="discogs_<datum>_releases.xml.gz (bleibt gepackt)")
    d.add_argument("--limit", type=int, default=None, help="nur die ersten N Releases (Probe)")
    d.add_argument("--progress", type=float, default=15.0, help="Sekunden zwischen Fortschrittszeilen")
    m = sub.add_parser("musicbrainz", help="MusicBrainz-Tabellen (extrahiert) einlesen")
    m.add_argument("--dump", required=True, help="Ordner mit mbdump/ (s. extract-mb)")
    x = sub.add_parser("extract-mb", help="benötigte Tabellen aus mbdump.tar.bz2 ziehen")
    x.add_argument("--tar", required=True)
    x.add_argument("--dest", required=True)
    sub.add_parser("status", help="Zeilen, Größe, Dump-Stände")
    sub.add_parser("compact", help="Interpreten- und Titel-Tabelle (Suche) bauen, redundanten nkey-Index älterer Builds entfernen, VACUUM (kein Datenverlust)")
    sub.add_parser("titles", help="Titel-Tabelle + FTS5-Trigram (Titel-only) und Interpreten-FTS (nach)bauen — additiv, ~2 min")
    a = ap.parse_args(argv)
    if a.cmd == "discogs":
        build_discogs(a.dump, a.out, a.master, limit=a.limit, progress_every=a.progress)
    elif a.cmd == "musicbrainz":
        build_musicbrainz(a.dump, a.out, a.master)
    elif a.cmd == "extract-mb":
        extract_mb(a.tar, a.dest)
    elif a.cmd == "compact":
        return cmd_compact(Path(a.out))
    elif a.cmd == "titles":
        return cmd_titles(Path(a.out))
    else:
        return cmd_status(Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
