#!/usr/bin/env python3
"""App-Icons aus der Bildmarke (web/cuecore-mark-dark.png) bauen: cuecore.icns (macOS, über iconutil)
und cuecore.ico (Windows). Einmalig, Ergebnis ist committet; neu laufen lassen, wenn die Marke sich ändert.

  python packaging/make_icons.py        (braucht Pillow; iconutil nur auf macOS)
"""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
MARK = ROOT.parent / "MusicMetaDataSearchEngine" / "web" / "cuecore-mark-dark.png"
BG = (20, 20, 22, 255)          # dunkles Panel, dazu die weiße Klammer + roter Puls der Marke
SIZE = 1024


def master() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=int(SIZE * 0.22), fill=BG)
    mark = Image.open(MARK).convert("RGBA")
    h = int(SIZE * 0.60)
    mark = mark.resize((int(mark.width * h / mark.height), h), Image.LANCZOS)
    img.alpha_composite(mark, ((SIZE - mark.width) // 2, (SIZE - mark.height) // 2))
    return img


def main() -> int:
    m = master()
    m.save(ROOT / "cuecore-icon-1024.png")
    m.save(ROOT / "cuecore.ico", format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = ROOT / "cuecore.iconset"
        shutil.rmtree(iconset, ignore_errors=True)
        iconset.mkdir()
        for s in (16, 32, 128, 256, 512):
            m.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
            m.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "cuecore.icns")], check=True)
        shutil.rmtree(iconset)
    print("Icons:", sorted(p.name for p in ROOT.glob("cuecore*")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
