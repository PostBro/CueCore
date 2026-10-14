"""Dateinamen-Schema (Single Source of Truth) — auch von der Pipeline genutzbar.

  Interpret - Trackname - [Labelname_LCXXXXX].mp3

` - ` (Space-Bindestrich-Space) trennt Interpret, Trackname UND den Klammerblock.
Kein Leerzeichen zwischen LC und Nummer. LC mit führenden Nullen (5- oder 6-stellig).
"""
from __future__ import annotations
import re

from .normalize import to_filename_lc

_SAFE = str.maketrans({c: " " for c in '/\\:*?"<>|'})


def sanitize(s: str) -> str:
    """Dateisystem-unsichere Zeichen -> Space, Mehrfach-Whitespace zusammen."""
    return re.sub(r"\s+", " ", s.translate(_SAFE)).strip()


def build_filename(artist: str, title: str, label: str, labelcode: str, ext: str = "mp3") -> str:
    """Zielname nach Schema. labelcode = reine Ziffern (oder mit 'LC'), wird normalisiert."""
    lc = to_filename_lc(labelcode)            # -> 'LCXXXXX', kein Leerzeichen, zero-padded
    return f"{sanitize(artist)} - {sanitize(title)} - [{sanitize(label)}_{lc}].{ext}"
