"""PDF ingestion (spec §6.1). Each study PDF gets (a) a local per-page text
layer for anchor verification and (b) is later sent natively to the model.
Scanned (no-text-layer) PDFs are marked so the anchor gate can degrade to
vision-only (spec §6.4)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

SCANNED_MIN_CHARS = 100


@dataclass
class PdfDoc:
    study_id: str
    path: Path
    pages: list[str]
    scanned: bool


def load_pdf(path: Path) -> PdfDoc:
    path = Path(path)
    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    total_chars = sum(len(p.strip()) for p in pages)
    return PdfDoc(
        study_id=path.stem,
        path=path,
        pages=pages,
        scanned=total_chars < SCANNED_MIN_CHARS,
    )


def load_studies(studies_dir: Path) -> list[PdfDoc]:
    return [load_pdf(p) for p in sorted(Path(studies_dir).glob("*.pdf"))]
