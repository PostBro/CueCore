"""Song-Datenbank exportieren (Firma-zu-Firma-Roundtrip): Excel / CSV / PDF.

Alle drei Formate teilen dieselbe Spaltenreihenfolge (EXPORT_COLUMNS). CSV wird als
UTF-8-mit-BOM geschrieben (Excel öffnet Umlaute dann korrekt) und lässt sich ohne
Verlust wieder über core/ingest importieren (Roundtrip). PDF wird über PyMuPDFs
Story-Engine (HTML->PDF, automatische Paginierung) im Querformat gerendert.
"""
from __future__ import annotations
import csv
import html
from pathlib import Path

from .normalize import export_lc

# (songs.db-Feld, Spaltenüberschrift) — gilt für alle Formate.
EXPORT_COLUMNS = [
    ("interpret", "Interpret"), ("titel", "Titel"), ("komponist", "Komponist"),
    ("texter", "Texter"), ("verlag", "Verlag"), ("album", "Album"), ("track_number", "Track-Nr."),
    ("labelname", "Label"), ("labelcode", "LC"), ("isrc", "ISRC"),
    ("iswc", "ISWC"), ("gema_werknummer", "GEMA-Werknr."),
]


def _val(row: dict, field: str) -> str:
    v = row.get(field)
    if v is None:
        return ""
    if field == "labelcode":
        return export_lc(str(v))                 # einheitlich 'LC' + Ziffern, ohne Leerzeichen
    return str(v)


def export_csv(rows: list[dict], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([head for _, head in EXPORT_COLUMNS])
        for r in rows:
            w.writerow([_val(r, field) for field, _ in EXPORT_COLUMNS])


def export_xlsx(rows: list[dict], path: str | Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Songs"
    ws.append([head for _, head in EXPORT_COLUMNS])
    for r in rows:
        ws.append([_val(r, field) for field, _ in EXPORT_COLUMNS])
    wb.save(path)


# Spaltenbreiten in Prozent der Satzbreite (Summe 100), in pt umgerechnet: die Story wertet Prozent nicht aus,
# und ohne feste Breiten liefen die Spalten der einzelnen Stücke (s. export_pdf) auseinander. Jede Kopfzeile
# passt fett, LC104994 und eine ISRC ohne Umbruch.
#              Interpret Titel Komp. Texter Verlag Album Nr. Label LC ISRC ISWC GEMA
_PDF_WIDTHS = (10, 12, 9, 7, 8, 8, 6, 7, 6, 9, 9, 9)
assert len(_PDF_WIDTHS) == len(EXPORT_COLUMNS) and sum(_PDF_WIDTHS) == 100
_PDF_FONT, _PDF_PAD = 7.5, 8.5          # Schriftgröße; Innenabstand links+rechts + Rahmen in pt
_PDF_CHUNK = 100                        # Zeilen je Story


def _pdf_parts(rows: list[dict], inner: list[float]) -> tuple[str, list[str]]:
    """(Kopf, Stücke): der Tabellenkopf für jede Folgeseite, dazu Titel + Kopf für Seite 1, dann je _PDF_CHUNK
    Zeilen eine eigene Tabelle mit denselben Breiten. Der Kopf ist eine eigene Tabelle ohne <thead>: die Story
    malte dessen Hintergrund auf jeder Folgeseite als grauen Balken quer über eine Datenzeile."""
    from .edl_import import _fit_break
    style = ("<style>"
             "body{font-family:sans-serif;margin:0}"
             "h2{font-size:14pt;margin:0 0 8pt}"
             "p.meta{font-size:8pt;color:#555;margin:0 0 10pt}"
             f"table{{border-collapse:collapse;width:100%;font-size:{_PDF_FONT:g}pt;table-layout:fixed}}"
             "th,td{border:0.5pt solid #999;padding:2pt 4pt;text-align:left;vertical-align:top}"
             "th{background:#eee;font-size:7.5pt}tr.first td{border-top:0}"
             "</style>")

    def cells(values: list[str], tag: str, sized: bool) -> str:
        return "".join(f'<{tag} style="width:{w:.2f}pt">{v}</{tag}>' if sized else f"<{tag}>{v}</{tag}>"
                       for v, w in zip(values, inner))

    head = f"<table><tr>{cells([html.escape(head) for _, head in EXPORT_COLUMNS], 'th', True)}</tr></table>"
    parts = [f"{style}<h2>CueCore — Song-Datenbank</h2><p class='meta'>{len(rows)} Einträge</p>{head}"]
    for i in range(0, len(rows), _PDF_CHUNK):
        trs = []
        for j, r in enumerate(rows[i:i + _PDF_CHUNK]):
            vals = [html.escape(_fit_break(_val(r, f), w, _PDF_FONT)) for (f, _), w in zip(EXPORT_COLUMNS, inner)]
            trs.append(f"<tr class='first'>{cells(vals, 'td', True)}</tr>" if j == 0 else f"<tr>{cells(vals, 'td', False)}</tr>")
        parts.append(f"{style}<table>{''.join(trs)}</table>")
    return style + head, parts


def export_pdf(rows: list[dict], path: str | Path) -> None:
    """PDF in Stücken: eine einzige Story-Tabelle über alle Zeilen wuchs überproportional, die ganze
    Track-Datenbank (66.421 Zeilen) brauchte rund 22 Minuten und bis zu 3,4 GB. Jetzt eine Story je
    _PDF_CHUNK Zeilen, jede setzt dort weiter, wo die vorige auf der Seite aufgehört hat — linear.
    Jede Folgeseite beginnt mit dem Tabellenkopf (vorher nur Seite 1): eine Kopf-Story, je Seite zurückgespult."""
    import fitz
    media = fitz.paper_rect("a4-l")           # A4 quer (viele Spalten)
    where = media + (36, 36, -36, -36)         # 0.5" Ränder
    inner = [pct * where.width / 100 - _PDF_PAD for pct in _PDF_WIDTHS]
    head_html, parts = _pdf_parts(rows, inner)
    head = fitz.Story(html=head_html)
    writer = fitz.DocumentWriter(str(path))
    dev = writer.begin_page(media)
    free = where
    for part in parts:
        story = fitz.Story(html=part)
        more = 1
        while more:
            more, filled = story.place(free)
            story.draw(dev)
            if more:
                writer.end_page()
                dev = writer.begin_page(media)
                head.reset()
                _, top = head.place(where)
                head.draw(dev)
                free = fitz.Rect(where.x0, top[3], where.x1, where.y1)
            else:
                free = fitz.Rect(where.x0, filled[3], where.x1, where.y1)
    writer.end_page()
    writer.close()
