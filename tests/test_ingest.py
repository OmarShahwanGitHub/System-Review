from conftest import make_pdf, make_scanned_pdf

from srx.ingest import load_pdf, load_studies


def test_load_pdf_text_layer(tmp_path):
    p = make_pdf(
        tmp_path / "smith-2019.pdf",
        [
            "Randomised controlled trial of drug X. " * 5,
            "A total of 124 patients were randomised to intervention or placebo.",
        ],
    )
    doc = load_pdf(p)
    assert doc.study_id == "smith-2019"
    assert len(doc.pages) == 2
    assert "124 patients were randomised" in doc.pages[1]
    assert doc.scanned is False


def test_scanned_detection(tmp_path):
    p = make_scanned_pdf(tmp_path / "old-1987.pdf")
    doc = load_pdf(p)
    assert doc.scanned is True
    assert all(not page.strip() for page in doc.pages)


def test_load_studies_sorted(tmp_path):
    studies = tmp_path / "studies"
    studies.mkdir()
    make_pdf(studies / "b.pdf", ["text " * 40])
    make_pdf(studies / "a.pdf", ["text " * 40])
    (studies / "notes.txt").write_text("ignore me")
    docs = load_studies(studies)
    assert [d.study_id for d in docs] == ["a", "b"]
