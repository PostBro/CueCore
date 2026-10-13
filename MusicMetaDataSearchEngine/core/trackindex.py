"""Lokaler Track-Index aus den offiziellen Dumps.

Eigene SQLite `track_index.db` neben labels.db (Datenordner, außerhalb von Cloud-synchronisierten Ordnern) — NICHT die songs.db:
die bleibt die verifizierte Referenz und wächst nur durch bestätigte Cues. Der Index ist eine
Roh-Quelle wie ein Discogs-Live-Call, nur ohne Netz und Rate-Limit; der Resolver fragt ihn NACH der
songs.db und VOR dem Netz.

Eine Zeile je (Interpret+Titel, Labelcode, Quelle) mit den Pflichtfeldern Interpret, Titel, Label,
LC (nur GVL-Master-Codes, beim Bau geprüft), Komponist, Texter — dazu Album, Jahr, Katalognummer,
Anzahl Releases als Ranking-Signal. Schlüssel ist `nkey` = key(interpret) + US + key(titel),
dieselbe Formel wie songsdb (Test sichert das), damit ein Treffer hier genau dort greift, wo auch
die songs.db greifen würde. key() ist Unicode-fähig und für jeden bisher nicht leeren
Teil unverändert; Zeilen mit leerem Teil liegen in älteren Indizes noch unter der alten Formel, lookup fragt
sie bis zum Neubau mit ab.

Bau: build_track_index.py (streamend, Dump wird nie entpackt); hier liegen Schema, Staging-Tabelle,
Aggregation, Lookup und Statistik. Der Bau schreibt je Quelle erst in `staging`, `finalize()`
ersetzt dann die Zeilen dieser Quelle in einem Rutsch (GROUP BY nkey, labelcode) — ein zweiter Lauf
derselben Quelle überschreibt, eine andere Quelle bleibt stehen.
"""
from __future__ import annotations
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .labelmatch import norm
from .songsdb import _legacy_nkey, key, same_song

DB_NAME = "track_index.db"
SOURCES = ("discogs", "musicbrainz")
_US = "\x1f"

_COLS = ("nkey", "interpret", "titel", "labelcode", "label", "komponist", "texter",
         "album", "jahr", "catno", "source", "source_id")


def nkey(artist: str, title: str) -> str:
    """Identisch mit songsdb._nkey (Test: test_trackindex.test_nkey_matches_songsdb)."""
    return key(artist or "") + _US + key(title or "")


def path_for(data_dir: str | Path) -> Path:
    return Path(data_dir) / DB_NAME


def open_index(path: str | Path, *, create: bool = False) -> sqlite3.Connection | None:
    """Verbindung (row_factory Row, threadsicher geteilt). Fehlt die Datei und create=False -> None:
    die App läuft ohne Index genauso, nur ohne die lokale Zwischenstufe."""
    p = Path(path)
    if not p.exists() and not create:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    ensure_schema(con)
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY, nkey TEXT NOT NULL, interpret TEXT NOT NULL, titel TEXT NOT NULL,
        labelcode TEXT NOT NULL, label TEXT, komponist TEXT, texter TEXT, album TEXT, jahr TEXT,
        catno TEXT, source TEXT NOT NULL, source_id TEXT, n_releases INTEGER NOT NULL DEFAULT 1,
        UNIQUE(nkey, labelcode, source))""")
    # Kein eigener Index auf nkey: der UNIQUE-Autoindex (nkey, labelcode, source) bedient den Lookup
    # (nkey=?) und die Zählung je source als Covering-Index. Ein zweiter Index kostete beim Bau
    # 0,93 GB von 5,67 GB (gemessen per dbstat) ohne Nutzen — s. compact().
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()


def ensure_artists(con: sqlite3.Connection) -> int:
    """Interpreten-Tabelle für die unscharfe Suche: eine Zeile je normiertem Interpret (rund
    900.000 bei 17 Mio. Zeilen, ~30 MB). Wird nach jedem finalize und bei compact neu gebaut.
    LIKE über 900k kurze Strings ist schnell genug; über 17 Mio. Titelzeilen wäre es das nicht."""
    con.execute("CREATE TABLE IF NOT EXISTS artists (na TEXT PRIMARY KEY, interpret TEXT NOT NULL, n INTEGER NOT NULL)")
    con.execute("DELETE FROM artists")
    con.execute("""INSERT INTO artists SELECT substr(nkey, 1, instr(nkey, ?) - 1), MIN(interpret), SUM(n_releases)
                   FROM tracks GROUP BY 1""", (_US,))
    con.commit()
    ensure_artists_fts(con)
    return con.execute("SELECT COUNT(*) FROM artists").fetchone()[0]


def ensure_artists_fts(con: sqlite3.Connection) -> int:
    """FTS5-Trigram über die normierten Interpreten (external content auf artists, rowid der Tabelle): die
    Kandidatensuche lief per LIKE über 903k Zeilen, ~100 ms je Wort — der Rest der 0,3 s je Suche im
    Datenbank-Tab nach dem songs_fts. Nach jedem ensure_artists neu (DELETE+INSERT vergibt die
    rowids neu); für alte Builds per build_track_index.py titles nachbaubar. Rückgabe: Interpreten."""
    con.execute("DROP TABLE IF EXISTS artists_fts")
    con.execute("CREATE VIRTUAL TABLE artists_fts USING fts5(na, content='artists', tokenize='trigram remove_diacritics 1')")
    con.execute("INSERT INTO artists_fts(artists_fts) VALUES ('rebuild')")
    con.commit()
    return con.execute("SELECT COUNT(*) FROM artists").fetchone()[0]


def has_artists(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artists'").fetchone() is not None


def has_artists_fts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='artists_fts'").fetchone() is not None


def ensure_titles(con: sqlite3.Connection) -> int:
    """Titel-Tabelle für die Titel-only-Suche: eine Zeile je
    Interpret+Titel-Schlüssel (nt, na, n = Releases) plus FTS5-Trigram über den normierten Titel — Teilwort
    („Number“ -> „What's Your Number“) in Millisekunden. Gemessen auf dem vollständigen Index (10,96 Mio.
    Schlüssel): B-Tree-Tabelle 0,70 GB, exakt/Präfix 0 ms, aber Substring = Scan 1,1–1,3 s warm; FTS5-Trigram
    1,58 GB (Basistabelle 0,67 + FTS 0,91), Substring 2–25 ms, Bau 65 s. Recall vor Präzision -> Trigram.
    Wird nach jedem finalize und bei compact neu gebaut (external content, deshalb komplett neu)."""
    con.execute("DROP TABLE IF EXISTS title_fts")
    con.execute("DROP TABLE IF EXISTS titles")
    con.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY, nt TEXT NOT NULL, na TEXT NOT NULL, n INTEGER NOT NULL)")
    con.execute("""INSERT INTO titles (nt, na, n)
                   SELECT substr(nkey, instr(nkey, ?) + 1), substr(nkey, 1, instr(nkey, ?) - 1), SUM(n_releases)
                   FROM tracks GROUP BY nkey""", (_US, _US))
    con.execute("CREATE VIRTUAL TABLE title_fts USING fts5(nt, content='titles', content_rowid='id', tokenize='trigram')")
    con.execute("INSERT INTO title_fts(title_fts) VALUES('rebuild')")
    con.execute("INSERT INTO title_fts(title_fts) VALUES('optimize')")
    con.commit()
    return con.execute("SELECT COUNT(*) FROM titles").fetchone()[0]


def has_titles(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='title_fts'").fetchone() is not None


def compact(con: sqlite3.Connection, path: str | Path | None = None) -> dict:
    """Redundanten nkey-Index älterer Builds entfernen und die Datei per VACUUM schrumpfen.
    Kein Datenverlust: Lookup und Zählung laufen über den UNIQUE-Autoindex. Liefert Bytes vorher/nachher."""
    before = Path(path).stat().st_size if path and Path(path).exists() else 0
    had = con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_tracks_nkey'").fetchone()
    con.execute("DROP INDEX IF EXISTS idx_tracks_nkey")
    con.commit()
    ensure_artists(con)
    ensure_titles(con)
    con.execute("VACUUM")
    after = Path(path).stat().st_size if path and Path(path).exists() else 0
    return {"dropped_index": bool(had), "bytes_before": before, "bytes_after": after}


# --- Bau: Staging + Aggregation ------------------------------------------------------------

def begin_staging(con: sqlite3.Connection) -> None:
    """Staging-Tabelle frisch anlegen (ohne Index — Einfügen soll schnell sein)."""
    con.execute("DROP TABLE IF EXISTS staging")
    con.execute("CREATE TABLE staging (" + ", ".join(f"{c} TEXT" for c in _COLS) + ")")
    con.commit()


def stage_rows(con: sqlite3.Connection, rows: list[tuple]) -> None:
    """rows: Tupel in _COLS-Reihenfolge (nkey wird hier berechnet, wenn leer übergeben)."""
    if not rows:
        return
    con.executemany(f"INSERT INTO staging VALUES ({','.join('?' * len(_COLS))})", rows)


def make_row(*, interpret: str, titel: str, labelcode: str, label: str = "", komponist: str = "",
             texter: str = "", album: str = "", jahr: str = "", catno: str = "", source: str,
             source_id: str = "") -> tuple:
    return (nkey(interpret, titel), interpret, titel, labelcode, label, komponist or "", texter or "",
            album or "", jahr or "", catno or "", source, source_id or "")


def finalize(con: sqlite3.Connection, source: str, meta: dict | None = None) -> int:
    """Staging -> tracks für EINE Quelle: alte Zeilen der Quelle raus, dann eine Zeile je
    (nkey, labelcode). MAX() über Komponist/Texter nimmt den gefüllten Wert vor dem leeren
    (Leerstring sortiert zuerst); MIN(jahr) = frühestes Release; n_releases = Zeilen im Staging.
    Rückgabe: Zeilen dieser Quelle."""
    if source not in SOURCES:
        raise ValueError(f"unbekannte Quelle {source!r}")
    con.commit()                      # offene Staging-Inserts abschließen, dann ein Rutsch
    con.execute("BEGIN")
    con.execute("DELETE FROM tracks WHERE source=?", (source,))
    con.execute("""INSERT INTO tracks (nkey, interpret, titel, labelcode, label, komponist, texter, album,
                                       jahr, catno, source, source_id, n_releases)
                   SELECT nkey, MIN(interpret), MIN(titel), labelcode, MIN(label), MAX(komponist), MAX(texter),
                          MIN(album), MIN(NULLIF(jahr, '')), MIN(catno), ?, MIN(source_id), COUNT(*)
                   FROM staging WHERE nkey <> ? AND interpret <> '' AND titel <> '' AND labelcode <> ''
                   GROUP BY nkey, labelcode""", (source, _US))
    n = con.execute("SELECT COUNT(*) FROM tracks WHERE source=?", (source,)).fetchone()[0]
    con.execute("DROP TABLE IF EXISTS staging")
    for k, v in (meta or {}).items():
        con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"{source}_{k}", str(v)))
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"{source}_rows", str(n)))
    con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"{source}_built_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))
    con.commit()
    ensure_artists(con)
    ensure_titles(con)
    return n


def set_meta(con: sqlite3.Connection, **values: str) -> None:
    con.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    [(k, str(v)) for k, v in values.items()])
    con.commit()


# --- Lesen ---------------------------------------------------------------------------------

def lookup(con: sqlite3.Connection, artist: str, title: str) -> list[dict]:
    """Alle Index-Zeilen zu Interpret+Titel (beide Quellen, alle Codes), meist belegte zuerst. Unter einem
    nkey mit leerem/verlustbehaftetem Teil liegen fremde Songs (Titel ohne a-z/0-9) — nur Zeilen mit
    demselben Wortlaut zählen (songsdb.same_song). Weicht der Schlüssel nach der alten Formel ab
    (Teil ohne a-z/0-9), wird er mitgefragt — der vorhandene Index ist noch damit gebaut."""
    keys = list(dict.fromkeys((nkey(artist, title), _legacy_nkey(artist, title))))
    rows = con.execute(
        "SELECT interpret, titel, labelcode, label, komponist, texter, album, jahr, catno, source, "
        f"source_id, n_releases FROM tracks WHERE nkey IN ({','.join('?' * len(keys))}) "
        "ORDER BY n_releases DESC, source, labelcode", keys).fetchall()
    return [dict(r) for r in rows if same_song(artist, title, r["interpret"], r["titel"])]


def find_similar(con: sqlite3.Connection, artist: str, title: str, *, limit: int = 8,
                 min_ratio: float = 0.55) -> list[dict]:
    """„Meintest du…?“ aus dem Index, wenn der exakte Schlüssel nicht trifft: alle Titel des
    (normierten) Interpreten per Bereichsabfrage über den UNIQUE-Autoindex (nkey beginnt mit
    norm(interpret)+US — kein Voll-Scan über 17 Mio. Zeilen), dann Titel-Ähnlichkeit wie
    songsdb.find_similar (Substring = starker Treffer). Ohne Interpret keine Suche (17 Mio. Titel).
    Eine Zeile je Interpret+Titel mit allen Codes/Quellen (labelcodes, sources), meist belegte zuerst."""
    import difflib
    na, nt = key(artist or ""), key(title or "")
    if not na or not nt:
        return []
    # Interpret-Kandidaten: exakt, sonst unscharf über die artists-Tabelle (Teil-Name „Pooley“, Tippfehler,
    # „feat.“-Zusätze) — wie songsdb.find_similar, nur über die kleine Interpreten-Tabelle statt über 17 Mio. Zeilen.
    artists: list[tuple[str, float]] = [(na, 1.0)]
    if has_artists(con):
        exact = con.execute("SELECT 1 FROM artists WHERE na=?", (na,)).fetchone()
        tokens = [t for t in na.split() if len(t) >= 3]
        if not exact and tokens:
            # jedes Token einzeln (Teil-Name); trifft nichts, auch Token-Hälften (Tippfehler wie „Poley“ -> „ley“).
            # Recall vor Präzision: lieber acht Vorschläge, der richtige muss dabei sein.
            # Reihenfolge bewahren (meist belegte zuerst): bei gleicher Ähnlichkeit — „ian pooley“ und 27 × „ian
            # pooley feat …“ stehen alle bei 0.85 — entschied vorher die zufällige Set-Reihenfolge, ob der Interpret
            # selbst in die Top 8 kam (mal 8 Treffer, mal keiner).
            probes = list(tokens)
            cands: dict[str, None] = {}
            for tok in probes:
                cands.update(dict.fromkeys(_artist_like(con, tok, limit=400)))
            # Token-Hälften gegen Tippfehler („Daughtr“ trifft als Teilwort nur „Daughtry“, „Daughter“ erst über
            # „dau“/„ghtr“) — immer, nicht nur bei null Treffern; die Ähnlichkeit sortiert danach
            tok = max(tokens, key=len)
            if len(tok) >= 5:
                for half in (tok[:len(tok) // 2], tok[len(tok) // 2:]):
                    if len(half) >= 2:
                        cands.update(dict.fromkeys(_artist_like(con, half, limit=400)))
            scored = []
            for cna in cands:
                ratio = difflib.SequenceMatcher(None, na, cna).ratio()
                if na in cna or cna in na:
                    ratio = max(ratio, 0.85)
                if ratio >= 0.6:
                    scored.append((cna, ratio))
            scored.sort(key=lambda x: -x[1])          # stabil: Gleichstand bleibt nach Releases sortiert
            artists = scored[:8] or artists
    rows = []
    for cna, ascore in artists:
        lo, hi = cna + _US, cna + chr(ord(_US) + 1)
        rows += [(r, ascore) for r in con.execute(
            "SELECT nkey, interpret, titel, labelcode, label, komponist, texter, album, jahr, source, n_releases "
            "FROM tracks WHERE nkey >= ? AND nkey < ? ORDER BY n_releases DESC", (lo, hi)).fetchall()]
    by_key: dict[str, dict] = {}
    for r, ascore in rows:
        rt = r["nkey"].split(_US, 1)[1]
        tscore = difflib.SequenceMatcher(None, nt, rt).ratio()
        if rt and (nt in rt or rt in nt):        # leerer Titelschlüssel steckt in jedem Titel
            tscore = max(tscore, 0.9)
        score = 0.7 * tscore + 0.3 * ascore
        if score < min_ratio:
            continue
        e = by_key.get(r["nkey"])
        if e is None:
            e = by_key[r["nkey"]] = {"interpret": r["interpret"], "titel": r["titel"], "labelcodes": [],
                                     "labels": [], "sources": [], "komponist": r["komponist"] or "",
                                     "texter": r["texter"] or "", "album": r["album"] or "",
                                     "jahr": r["jahr"] or "", "n_releases": 0, "score": score}
        if r["labelcode"] not in e["labelcodes"]:       # rows kommen nach n_releases DESC -> wichtigster Code zuerst
            e["labelcodes"].append(r["labelcode"]); e["labels"].append(r["label"] or "")
        if r["source"] not in e["sources"]:
            e["sources"].append(r["source"])
        e["komponist"] = e["komponist"] or (r["komponist"] or "")
        e["texter"] = e["texter"] or (r["texter"] or "")
        e["n_releases"] += r["n_releases"]
    # Reihenfolge: Ähnlichkeit, dann der kürzere Titel (Original vor „(Jazzanova Renumber)“), dann Releases
    out = sorted(by_key.values(), key=lambda e: (-round(e["score"], 2), len(e["titel"]), -e["n_releases"]))
    return out[:limit]


_LC_RE = re.compile(r"^(?:lc)?(\d{5,6})$")


def _artist_like(con: sqlite3.Connection, text: str, *, limit: int = 300) -> list[str]:
    """Normierte Interpreten, die `text` enthalten, meist belegte zuerst: Trigram-FTS ab drei Zeichen (Teilwort,
    ohne Akzente, Millisekunden), sonst LIKE über die artists-Tabelle wie früher (auch ohne artists_fts)."""
    if has_artists_fts(con) and len(text) >= 3:
        rows = con.execute("SELECT na FROM artists WHERE rowid IN (SELECT rowid FROM artists_fts WHERE artists_fts MATCH ?) "
                           "ORDER BY n DESC LIMIT ?", ('"' + text.replace('"', '""') + '"', limit))
    else:
        rows = con.execute("SELECT na FROM artists WHERE na LIKE ? ORDER BY n DESC LIMIT ?", ("%" + text + "%", limit))
    return [r[0] for r in rows]


def _artist_candidates(con: sqlite3.Connection, words: list[str]) -> list[str]:
    """Normierte Interpreten aus der artists-Tabelle zu den Suchwörtern: erst der ganze Text als Interpret
    („ian pooley“), dann Wort für Wort, längste zuerst (kürzere wie „ian“ treffen tausende Interpreten und
    würden mit 300er-Limit die richtigen verdrängen); Zweibuchstabler nur als letzter Ausweg."""
    cands: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        for cna in _artist_like(con, text):
            if cna not in seen:
                seen.add(cna); cands.append(cna)
    if words:
        add(" ".join(words))
        for w in sorted(words, key=len, reverse=True):
            # Längste Wörter zuerst; reichen die Kandidaten, bleiben kurze Allerweltswörter („ian“: 300 Interpreten,
            # deren Titel dann alle gegen die Restwörter geprüft würden — 0,7 s bei „ian pooley balmes“) draußen.
            if len(w) >= 3 and len(cands) < 20:
                add(w)
        if not cands:
            for w in words:
                if len(w) == 2:
                    add(w)
    return cands


def search(con: sqlite3.Connection, query: str, *, limit: int = 100, album: str = "") -> dict:
    """Freitextsuche für den Datenbank-Tab (Interpret, Titel, Labelcode gemischt, wie songsdb._where).
    Läuft über die artists-Tabelle (900k) statt über 17 Mio. Zeilen: mindestens ein Token muss einen
    Interpreten treffen, die übrigen Token müssen im Titel stehen oder der Labelcode sein. `album` grenzt
    auf Zeilen ein, deren Album-Text den (normierten) Albumnamen enthält („25“ trifft „25 (Deluxe)“) —
    Album-Suche Interpret + Album, kein eigener Index, der Text hängt an jeder Zeile.
    Eine Zeile je Interpret+Titel+Code, meist belegte zuerst. {rows, total, capped, needs_artist}."""
    nq = re.sub(r"\blc (?=\d)", "lc", norm(query or ""))        # „LC 00171“ ist EIN Code, kein Wort „lc“
    tokens = [t for t in nq.split() if t]
    empty = {"rows": [], "total": 0, "capped": False, "needs_artist": False}
    if not has_artists(con):
        return empty
    # Ohne Interpreten-Wort (nur ein Code, oder Text ohne a-z/0-9 wie „宇多田ヒカル“) kann der Index nichts
    # finden — das sagt needs_artist, statt „0 Treffer“ zu melden.
    if not tokens:
        return {**empty, "needs_artist": bool((query or "").strip())}
    codes = {m.group(1).zfill(5) for t in tokens if (m := _LC_RE.match(t))}
    words = [t for t in tokens if not _LC_RE.match(t)]
    nalb = norm(album or "")
    cands = _artist_candidates(con, words)
    if not cands:
        return {**empty, "needs_artist": True}
    rows: list[dict] = []
    cap = limit * 3                      # Rohzeilen (Quellen doppelt); die Ausgabe wird auf limit gekappt
    for cna in cands[:80]:
        rest = [w for w in words if w not in cna]
        lo, hi = cna + _US, cna + chr(ord(_US) + 1)
        for r in con.execute(
                "SELECT nkey, interpret, titel, labelcode, label, komponist, texter, album, jahr, source, n_releases "
                "FROM tracks WHERE nkey >= ? AND nkey < ? ORDER BY n_releases DESC", (lo, hi)):
            rt = r["nkey"].split(_US, 1)[1]
            if codes and r["labelcode"] not in codes:
                continue
            if not all(w in rt for w in rest):
                continue
            if nalb and nalb not in norm(r["album"] or ""):
                continue
            rows.append(dict(r))
            if len(rows) >= cap:
                break
        if len(rows) >= cap:
            break
    # je Interpret+Titel+Code eine Zeile, Quellen zusammengefasst
    by: dict[tuple, dict] = {}
    for r in rows:
        k = (r["nkey"], r["labelcode"])
        e = by.get(k)
        if e is None:
            e = by[k] = {"interpret": r["interpret"], "titel": r["titel"], "labelcode": r["labelcode"],
                         "label": r["label"] or "", "komponist": r["komponist"] or "", "texter": r["texter"] or "",
                         "album": r["album"] or "", "jahr": r["jahr"] or "", "sources": [], "n_releases": 0}
        if r["source"] not in e["sources"]:
            e["sources"].append(r["source"])
        e["komponist"] = e["komponist"] or (r["komponist"] or "")
        e["texter"] = e["texter"] or (r["texter"] or "")
        e["n_releases"] += r["n_releases"]
    out = sorted(by.values(), key=lambda e: (-e["n_releases"], e["interpret"], e["titel"]))
    capped = len(rows) >= cap or len(out) > limit
    return {"rows": out[:limit], "total": len(out), "capped": capped, "needs_artist": False}


_CONTRACTIONS = ("re", "ll", "ve", "s", "t", "m", "d")


def _term_variants(t: str) -> list[str]:
    """Trigram-Terme zu einem Suchwort: das Wort selbst, dazu die Schreibweise mit Apostroph-Lücke, wie norm()
    sie im Index hinterlässt („whats“ -> „what s“, „dont“ -> „don t“, „youre“ -> „you re“) — der Nutzer tippt
    Kontraktionen oft ohne Apostroph, der Index hat an der Stelle ein Leerzeichen."""
    out = [t]
    for suf in _CONTRACTIONS:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            out.append(t[:-len(suf)] + " " + suf)
    return out


def _query_variants(tokens: list[str], cap: int = 16) -> list[str]:
    """Alle Schreibweisen des ganzen Suchtexts aus den Wort-Varianten (Produkt, gedeckelt) — für das Ranking
    „exakter Titel zuerst“, damit „dont stop“ Fleetwood Macs „Don't Stop“ (Index: „don t stop“) vorn hält."""
    out = [""]
    for t in tokens:
        out = [(o + " " + v).strip() for o in out for v in _term_variants(t)][:cap]
    return out


def search_title(con: sqlite3.Connection, title: str, *, limit: int = 20, offset: int = 0) -> dict:
    """Titel-only: alle Interpreten zu einem Titel aus titles + FTS5-Trigram (ensure_titles). Jedes Wort ab drei
    Zeichen als Trigram-Term (AND, Reihenfolge egal, Teilwort trifft), kürzere Wörter per LIKE auf der Zeile;
    unter drei Zeichen insgesamt nur der exakte Titel per Scan (0,67 GB, selten). Reihenfolge: exakter Titel,
    dann Präfix, dann Releases. Je Interpret+Titel eine Zeile mit allen Codes/Quellen aus tracks (Lookup über
    den UNIQUE-Autoindex). {rows, total, capped, offset}; total = Schlüssel, die den Titel enthalten;
    `offset` blättert („mehr zeigen“ bei häufigen Titeln — „Runaway“ hat 1.879 Schlüssel)."""
    nq = norm(title or "")
    offset = max(0, int(offset or 0))
    empty = {"rows": [], "total": 0, "capped": False, "offset": offset}
    if not nq or not has_titles(con):
        return empty
    tokens = nq.split()
    terms = [t for t in tokens if len(t) >= 3]
    short = [t for t in tokens if len(t) < 3]
    if terms:
        match = " AND ".join("(" + " OR ".join('"' + v + '"' for v in _term_variants(t)) + ")" for t in terms)
        where = "title_fts MATCH ?" + " AND t.nt LIKE ?" * len(short)
        args: list = [match] + ["%" + t + "%" for t in short]
        base = f"FROM title_fts f JOIN titles t ON t.id = f.rowid WHERE {where}"
    else:
        base = "FROM titles t WHERE t.nt = ?"
        args = [nq]
    total = con.execute(f"SELECT COUNT(*) {base}", args).fetchone()[0]
    exact = _query_variants(tokens)          # „dont stop“ zählt wie „don t stop“ als exakter Titel (und Präfix)
    prefix = " OR ".join("t.nt LIKE ?" for _ in exact)
    keys = con.execute(f"SELECT t.nt, t.na, t.n {base} ORDER BY (t.nt IN ({','.join('?' * len(exact))})) DESC, "
                       f"({prefix}) DESC, t.n DESC LIMIT ? OFFSET ?",
                       (*args, *exact, *[v + "%" for v in exact], limit, offset)).fetchall()
    rows: list[dict] = []
    for nt, na, n in keys:
        e = {"interpret": "", "titel": "", "labelcodes": [], "labels": [], "sources": [], "komponist": "",
             "texter": "", "album": "", "jahr": "", "n_releases": n}
        for r in con.execute("SELECT interpret, titel, labelcode, label, komponist, texter, album, jahr, source "
                             "FROM tracks WHERE nkey=? ORDER BY n_releases DESC, source, labelcode", (na + _US + nt,)):
            e["interpret"] = e["interpret"] or r["interpret"]
            e["titel"] = e["titel"] or r["titel"]
            if r["labelcode"] not in e["labelcodes"]:
                e["labelcodes"].append(r["labelcode"]); e["labels"].append(r["label"] or "")
            if r["source"] not in e["sources"]:
                e["sources"].append(r["source"])
            for k in ("komponist", "texter", "album", "jahr"):
                e[k] = e[k] or (r[k] or "")
        if e["interpret"]:
            rows.append(e)
    return {"rows": rows, "total": total, "capped": total > offset + len(keys), "offset": offset,
            "next_offset": offset + len(keys)}


def albums(con: sqlite3.Connection, artist: str, *, limit: int = 40) -> list[dict]:
    """Alben eines Interpreten für die Album-Chips der Interpret-only-Liste: [{album, n}] mit n = Zahl der
    Titel (Schlüssel) auf dem Album, meist Titel zuerst. Interpret exakt oder unscharf wie search();
    Bereichsabfrage je Interpret über den UNIQUE-Autoindex, GROUP BY in SQL. Leere Alben sind kein Chip."""
    words = [t for t in norm(artist or "").split() if t and not _LC_RE.match(t)]
    if not words or not has_artists(con):
        return []
    # Exakter Interpret gewinnt allein („adele“ ist sonst auch in „madeleine“); sonst nur Interpreten, die ALLE
    # Wörter tragen („pooley“ -> „ian pooley“). search() filtert Fremd-Interpreten über die Rest-Wörter im Titel,
    # hier gibt es keinen Titel — ohne diese Schranke kämen die Alben von Whitesnake („white“) und tausend „the …“
    # als Chips (gemessen: 668 ms, falsche Alben).
    na = " ".join(words)
    exact = con.execute("SELECT 1 FROM artists WHERE na=?", (na,)).fetchone()
    cands = [na] if exact else [c for c in _artist_candidates(con, words) if all(w in c for w in words)][:20]
    counts: dict[str, dict] = {}
    for cna in cands:
        lo, hi = cna + _US, cna + chr(ord(_US) + 1)
        for alb, n in con.execute(
                "SELECT album, COUNT(DISTINCT nkey) FROM tracks WHERE nkey >= ? AND nkey < ? AND album <> '' "
                "GROUP BY album", (lo, hi)):
            e = counts.setdefault(norm(alb), {"album": alb, "n": 0})
            e["n"] += n
    return sorted(counts.values(), key=lambda e: (-e["n"], e["album"]))[:limit]


def stats(con: sqlite3.Connection | None, path: str | Path | None = None) -> dict:
    """Für Rail/Einstellungen: Zeilen je Quelle, Dateigröße, Dump-Stände. Ohne Index -> present=False."""
    out = {"present": con is not None, "path": str(path) if path else "", "rows": 0,
           "by_source": {s: 0 for s in SOURCES}, "size_bytes": 0, "meta": {}, "artists": False, "titles": False}
    if path and Path(path).exists():
        out["size_bytes"] = Path(path).stat().st_size
    if con is None:
        return out
    out["meta"] = {k: v for k, v in con.execute("SELECT key, value FROM meta ORDER BY key")}
    out["artists"], out["titles"] = has_artists(con), has_titles(con)     # Suchtabellen (Interpret-only / Titel-only)
    for src in SOURCES:                      # Zeilen aus meta (finalize schreibt sie) — COUNT(*) über Millionen
        n = out["meta"].get(f"{src}_rows")   # Zeilen bei jedem App-Start wäre zu teuer
        if n is None:
            n = con.execute("SELECT COUNT(*) FROM tracks WHERE source=?", (src,)).fetchone()[0]
        out["by_source"][src] = int(n)
    out["rows"] = sum(out["by_source"].values())
    return out


def default_data_dir() -> Path:
    """Beschreibbarer Datenordner der App (außerhalb von Cloud-synchronisierten Ordnern und vom Bundle, kein Admin) — dieselbe Regel wie
    app._user_data_dir: $CUECORE_DB gibt den Ordner vor, Windows %LOCALAPPDATA%\\CueCore, sonst
    ~/Library/Application Support/MusicMeta."""
    import os
    import sys
    env = os.environ.get("CUECORE_DB")
    if env:
        return Path(env).parent
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CueCore"
    return Path.home() / "Library" / "Application Support" / "MusicMeta"
