"""Geteilte Domain-Modelle. Genutzt von App-Backend UND Reconcile-CLI."""
from __future__ import annotations

from dataclasses import dataclass

# Spalten-Reihenfolge identisch zur GVL-CSV / zum GVL-PDF.
COLUMNS = ("labelcode", "label", "kurzname", "plm", "hersteller", "rechterueckrufe")


@dataclass(frozen=True)
class LabelRecord:
    """Ein GVL-Datensatz. labelcode ist IMMER ein String (führende Nullen)."""

    labelcode: str
    label: str
    kurzname: str
    plm: str  # "Ja" oder ""
    hersteller: str
    rechterueckrufe: str  # ";"-getrennte R-Codes oder ""

    def as_row(self) -> dict[str, str]:
        return {
            "labelcode": self.labelcode,
            "label": self.label,
            "kurzname": self.kurzname,
            "plm": self.plm,
            "hersteller": self.hersteller,
            "rechterueckrufe": self.rechterueckrufe,
        }

    def content_key(self) -> tuple[str, str, str, str, str]:
        """Vergleichsschlüssel ohne labelcode (für 1:n-Diff pro Code)."""
        return (self.label, self.kurzname, self.plm, self.hersteller, self.rechterueckrufe)
