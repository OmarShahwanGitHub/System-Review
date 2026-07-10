"""Shared fixtures. Synthetic PDFs only — tests never depend on copyrighted
study PDFs (spec §9)."""
from pathlib import Path

from fpdf import FPDF


def make_pdf(path: Path, pages: list[str]) -> Path:
    pdf = FPDF()
    pdf.set_font("helvetica", size=11)
    for text in pages:
        pdf.add_page()
        pdf.multi_cell(w=180, text=text)
    pdf.output(str(path))
    return path


def make_scanned_pdf(path: Path) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.rect(20, 20, 150, 200)  # shapes only: no extractable text layer
    pdf.output(str(path))
    return path
