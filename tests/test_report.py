from srx.bench import BenchSummary, FieldScore
from srx.report import render_html, render_markdown, write_report

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


def test_write_report(tmp_path):
    p = write_report(tmp_path, "<p>hi</p>")
    assert p.name == "report.html"
    assert p.read_text(encoding="utf-8") == "<p>hi</p>"
