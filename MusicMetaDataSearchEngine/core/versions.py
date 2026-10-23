"""Versionen im Titel erkennen: Remix, Live, Acoustic, Instrumental, Radio Edit …

Eine Version kann ein anderes Label tragen als die Originalfassung (Remix auf einem anderen Label, Live-Album), beim
Remix kommen Urheber dazu (Remixer). Deshalb wird die Version nur ERKANNT und angezeigt; sie entscheidet nichts
still. Ausgewertet wird nur der Zusatz am Titelende: in Klammern („Song (Mark Ronson Remix)“) oder nach einem
Bindestrich („Song - Live“). „Live Forever“ oder „Remix Culture“ als Titel bleiben unberührt.
"""
from __future__ import annotations
import re

# Reihenfolge = Vorrang, wenn ein Zusatz mehreres nennt („Live Acoustic“ -> Live)
_KINDS = (
    ("Remix", re.compile(r"\b(remix|rmx|re-?mix)\b", re.I)),
    ("Live", re.compile(r"\blive\b", re.I)),
    ("Acoustic", re.compile(r"\b(acoustic|unplugged)\b", re.I)),
    ("Instrumental", re.compile(r"\b(instrumental|instr)\b", re.I)),
    ("Radio Edit", re.compile(r"\bradio (edit|mix|version)\b", re.I)),
    ("Extended", re.compile(r"\bextended\b", re.I)),
    ("Remaster", re.compile(r"\bremaster(ed)?\b", re.I)),
    ("Demo", re.compile(r"\bdemo\b", re.I)),
    ("Karaoke", re.compile(r"\bkaraoke\b", re.I)),
)
# Diese Versionen sind oft eigene Veröffentlichungen mit eigenem Label (und beim Remix eigenen Urhebern)
OWN_RELEASE = frozenset({"Remix", "Live"})

_TAIL = re.compile(r"(?:\s*[\(\[]([^\)\]]+)[\)\]]|\s+-\s+([^-()\[\]]+))\s*$")


def _suffixes(title: str) -> list[str]:
    """Zusätze am Titelende, äußerster zuerst: 'Song (Live) [Remastered]' -> ['Remastered', 'Live']."""
    out, t = [], (title or "").strip()
    while True:
        m = _TAIL.search(t)
        if not m:
            return out
        out.append((m.group(1) or m.group(2) or "").strip())
        t = t[:m.start()].strip()


def detect_version(title: str) -> str:
    """Erkannte Version als Anzeigetext, '' ohne Versions-Zusatz. Beim Remix mit Remixer, wenn genannt:
    'New Love Cassette (Mark Ronson Remix)' -> 'Remix (Mark Ronson)'."""
    for suffix in _suffixes(title):
        for kind, rx in _KINDS:
            m = rx.search(suffix)
            if not m:
                continue
            if kind == "Remix":
                who = suffix[:m.start()].strip(" -")
                return f"Remix ({who})" if who and not re.search(r"\b(version|mix)\b", who, re.I) else "Remix"
            return kind
    return ""


def version_kind(version: str) -> str:
    """'Remix (Mark Ronson)' -> 'Remix'."""
    return version.split(" (", 1)[0]
