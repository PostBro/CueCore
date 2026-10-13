#!/usr/bin/env python3
"""Dünner CLI-Einstieg für den GVL-PDF-Abgleich (reconcile/).

  python gvl_sync.py                          # Default-Pfade (../GVL-Labelcodes.pdf)
  python gvl_sync.py --pdf /pfad/zum/neuen.pdf

Baut den Master neu (data/GVL_Labelcodes_synced.csv), archiviert den Vorgänger-
Stand unter data/archive/ (sonst wäre jeder Lauf ein stiller Overwrite des
Masters), schreibt Diff-CSV + Report.
Danach labels.db neu bauen: python build_label_db.py
"""
from __future__ import annotations

from reconcile.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
