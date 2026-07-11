from srx.bench import BenchSummary, FieldScore
from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction
from srx.report import (
    render_extraction_html,
    render_html,
    render_markdown,
    write_report,
)
from srx.schema import FieldSpec, Schema

SUMMARY = BenchSummary(
    overall_agreement=0.75, numeric_agreement=0.8,
    per_field={"n_total": {"n": 4, "agreement": 0.75, "taxonomy": {"correct": 3, "wrong-value": 1}}},
    taxonomy_counts={"correct": 3, "wrong-value": 1}, n_scored=4, n_studies=4,
)

SCORE = FieldScore(
    study_id="smith-2019", field_id="n_total", taxonomy="correct",
    engine_display="124", gold_display="124", flagged=False, in_denominator=True,
    provenance={"quote": 'randomised <script>alert(1)</script>', "page": 3,
                "method": "pass-agreed", "anchor_status": "verified", "confidence": 0.9},
)


def test_html_provenance_is_hover_tooltip_and_escaped():
    html = render_html(SUMMARY, [SCORE], run_id="r1")
    assert 'class="prov"' in html
    assert "td.val:hover .prov" in html        # hover reveal wired in CSS
    assert "td.val:focus .prov" in html        # keyboard focus works too
    assert "<details" not in html              # dropdown removed (user amendment)
    body, prov = html.split('<span class="prov"', 1)
    assert "randomised" not in body            # provenance never inline (spec §4)
    assert "randomised" in prov
    assert "<script>" not in html              # escaped
    assert "&lt;script&gt;" in html
    assert "systematic review" in html.lower()
    assert "r1" in html


def test_html_diff_column_when_prev_given():
    html = render_html(SUMMARY, [SCORE], run_id="r2", prev=("r1", {"n_total": 0.5}))
    assert "Δ" in html and "+25.0%" in html and "r1" in html


def test_markdown_summary():
    md = render_markdown(SUMMARY, run_id="r1")
    assert "75.0%" in md and "n_total" in md


def test_extraction_report_without_gold():
    schema = Schema(fields=[FieldSpec(id="n_total", type="int"),
                            FieldSpec(id="mean_age", type="numeric", unit="years"),
                            FieldSpec(id="funding", type="text")])
    extraction = StudyExtraction(
        study_id="smith-2019", scanned=False,
        accepted={"n_total": ExtractedValue(
            field_id="n_total", value=124, unit=None,
            quote="124 patients were randomised", page=3,
            method="pass-agreed", anchor_status="verified", confidence=0.9)},
        flagged={"mean_age": FlaggedField(
            field_id="mean_age", reason="pass-disagreement-unresolved",
            candidates=[Candidate(value=61.2, unit="years",
                                  quote="mean age 61.2", page=4, confidence=0.8)])},
        not_found=["funding"], cost_usd=0.1)
    html = render_extraction_html([extraction], schema, run_id="r9")
    assert "td.val:hover .prov" in html          # same tooltip mechanism
    body, prov = html.split('<span class="prov"', 1)
    assert "randomised" not in body              # provenance never inline
    assert "randomised" in prov
    assert "⚑ review" in html                    # flagged cell marker
    assert "pass-disagreement-unresolved" in html
    assert "Flagged for review" in html and "Coverage" in html
    assert "r9" in html and "systematic review" in html.lower()


def test_write_report(tmp_path):
    p = write_report(tmp_path, "<p>hi</p>")
    assert p.name == "report.html"
    assert p.read_text(encoding="utf-8") == "<p>hi</p>"
