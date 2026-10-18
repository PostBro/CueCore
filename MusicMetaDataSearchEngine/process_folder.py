#!/usr/bin/env python3
"""MusicMeta Pipeline: Ordner scannen -> dedupe gegen den Zielordner für verifizierte Dateien -> Label->LC matchen
-> umbenennen -> in den Zielordner verschieben -> (optional) DB aktualisieren.

Aufruf:
  python process_folder.py                      # fragt nach dem Ordner
  python process_folder.py "/pfad/zum/ordner"   # Ordner als Argument
  Flags: --delete-unmatched  (nicht zuordenbare Files loeschen statt liegen lassen)
         --delete-dupes       (Dubletten aus dem Quellordner loeschen; sonst nur melden)
         --dry-run            (nichts schreiben, nur Bericht)
         --no-db              (DB-Update am Ende ueberspringen)

Label/LC-Quelle: data/GVL_Labelcodes_synced.csv. Matching-Logik (inkl. der
verifizierten Sonderfaelle/ALIASES) lebt in core/labelmatch.py — geteilt mit dem
App-Resolver, damit sie genau einmal existiert.
"""
from __future__ import annotations
import csv, re, subprocess, sys
from pathlib import Path

from core.labelmatch import attach_mb_aliases, build_index, match_label, norm

REPO = Path(__file__).parent
CSV_PATH = REPO.parent / "data" / "GVL_Labelcodes_synced.csv"


def load_index():
    with open(CSV_PATH, encoding="utf-8") as fh:
        idx = build_index(
            (row["labelcode"], row["label"], row.get("hersteller", "")) for row in csv.DictReader(fh)
        )
    attach_mb_aliases(idx, CSV_PATH.parent / "label_aliases_mb.csv")   # MusicBrainz-Schreibvarianten (GVL-geprüft)
    return idx


INDEX = load_index()

BRACKET = re.compile(r"\[(?P<label>.+)_LC ?(?P<lc>\d{5,6})?\][^\[\]]*\.mp3$")


def parse(name: str):
    m = BRACKET.search(name)
    if not m:
        return None
    label = m["label"].lstrip("[")
    lc = ("LC" + m["lc"]) if m["lc"] else ""
    rest = name[:m.start()].rstrip()
    if rest.endswith(" -"):
        rest = rest[:-2].rstrip()
    artist, _, title = rest.partition(" - ")
    return artist.strip(), title.strip(), label.strip(), lc


SAFE = str.maketrans({c: " " for c in '/\\:*?"<>|'})
def sanitize(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(SAFE)).strip()


def main():
    args = [a for a in sys.argv[1:]]
    flags = {a for a in args if a.startswith("--")}
    folder = next((a for a in args if not a.startswith("--")), None)
    if not folder:
        folder = input("Welchen Ordner scannen? ").strip().strip('"')
    src = Path(folder)
    if not src.is_dir():
        print(f"Kein Ordner: {src}"); sys.exit(1)
    verified = src / "LC verified"; verified.mkdir(exist_ok=True)     # Zielordner für verifizierte Dateien
    dry = "--dry-run" in flags
    del_unmatched = "--delete-unmatched" in flags
    del_dupes = "--delete-dupes" in flags

    # bereits verifizierte (artist,title) -> Dedupe-Set
    seen = set()
    for f in verified.glob("*.mp3"):
        p = parse(f.name)
        if p: seen.add((norm(p[0]), norm(p[1])))

    files = sorted(p for p in src.glob("*.mp3"))
    moved = dupes = unmatched = kept_lc = 0
    un_list = []
    for f in files:
        p = parse(f.name)
        if not p:
            unmatched += 1; un_list.append(f.name)
            if del_unmatched and not dry: f.unlink()
            continue
        artist, title, label, lc = p
        key = (norm(artist), norm(title))
        if key in seen:                      # Dublette
            dupes += 1
            if del_dupes and not dry: f.unlink()
            continue
        if not lc:                           # LC fehlt -> matchen
            code = match_label(label, INDEX)
            if code is None:
                unmatched += 1; un_list.append(f.name)
                if del_unmatched and not dry: f.unlink()
                continue
            lc = "LC" + code
            label = INDEX.code2name.get(code, label)
        else:
            kept_lc += 1
        dst_name = f"{sanitize(artist)} - {sanitize(title)} - [{label}_{lc}].mp3"
        dst = verified / dst_name
        if dst.exists():
            dupes += 1
            if del_dupes and not dry: f.unlink()
            continue
        if not dry:
            subprocess.run(["mid3v2", "--TPUB", label, "--TXXX", f"LC:{lc}", str(f)],
                           capture_output=True)
            f.rename(dst)
        seen.add(key); moved += 1

    print(f"\nOrdner: {src}")
    print(f"  verschoben+verifiziert: {moved}  (davon LC schon im Namen: {kept_lc})")
    print(f"  Dubletten (schon im Zielordner): {dupes}" + ("  [geloescht]" if del_dupes else "  [liegen geblieben]"))
    print(f"  nicht zuordenbar: {unmatched}" + ("  [geloescht]" if del_unmatched else "  [liegen geblieben]"))
    if un_list and not del_unmatched:
        print("  --- nicht zuordenbar ---")
        for u in un_list[:60]: print("   ?", u)
    if "--no-db" not in flags and not dry:
        print("\nDB-Update ...")
        subprocess.run([sys.executable, str(REPO / "build_songs_db.py"), str(verified)])


if __name__ == "__main__":
    main()
