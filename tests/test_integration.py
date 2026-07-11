"""End-to-end: synthetic project -> extract (fake API) -> bench -> report.
Covers the spec §7 taxonomy on planted data and the success-gate invariant
that no accepted value is ever unanchored."""
import json

from conftest import make_pdf, make_scanned_pdf

from srx.bench import aggregate, exact_judge, score_all
from srx.claude_client import Usage
from srx.goldload import load_gold
from srx.pipeline import RunConfig, load_run_extractions, run_extraction
from srx.project import open_project
from srx.report import render_html
from srx.runstore import RunStore
from srx.schema import load_schema

SCHEMA_YAML = """\
fields:
  - id: n_total
    type: int
    description: Total randomised
  - id: mean_age
    type: numeric
    unit: years
    description: Mean age, intervention arm
  - id: blinding
    type: categorical
    allowed_values: [open-label, double-blind]
    description: Blinding status
"""

# Planted document truth. beta's n_total gold cell (90) deliberately contradicts
# the document (88) -> engine must classify gold-suspect. beta has no gold
# mean_age -> engine's anchored value is spurious-anchored.
DOCS = {
    "alpha": {"n_total": (124, "A total of 124 patients were randomised."),
              "mean_age": (61.2, "Mean age was 61.2 years in the intervention arm."),
              "blinding": ("double-blind", "This was a double-blind placebo controlled trial.")},
    "beta": {"n_total": (88, "We randomised 88 participants across two sites."),
             "mean_age": (54.3, "Mean age was 54.3 years in the intervention arm."),
             "blinding": ("open-label", "The study used an open-label design.")},
}

GOLD_CSV = (
    "study_id,N,Age,Blinding\n"
    "alpha,124,61.2,double-blind\n"
    "beta,90,,open-label\n"
    "gamma,50,48.0,open-label\n"
)

MAPPING = ('study_id_column: study_id\n'
           'columns:\n  "N": n_total\n  "Age": {field: mean_age, unit: years}\n'
           '  "Blinding": blinding\n')


class FakeClient:
    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        content = messages[0]["content"]
        text = content if isinstance(content, str) else " ".join(
            b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")
        study = text.split("Study file: ")[1].split()[0]
        if tool_name == "record_extraction":
            truth = DOCS.get(study, DOCS["alpha"])  # scanned gamma gets alpha's answers
            result = {}
            for fid, (value, quote) in truth.items():
                result[fid] = {"value": value, "unit": "years" if fid == "mean_age" else None,
                               "quote": quote, "page": 1, "confidence": 0.9}
            return result, Usage(1000, 100, 0.25)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.02)
        raise AssertionError(tool_name)


def build_project(tmp_path):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    for sid, truth in DOCS.items():
        page = " ".join(quote for _, quote in truth.values())
        make_pdf(root / "studies" / f"{sid}.pdf", [page])
    make_scanned_pdf(root / "studies" / "gamma.pdf")
    (root / "gold" / "gold.csv").write_text(GOLD_CSV)
    (root / "gold" / "mapping.yaml").write_text(MAPPING)
    return root


def test_end_to_end(tmp_path):
    project = open_project(build_project(tmp_path))
    schema = load_schema(project.schema_path)
    store = RunStore(project.db_path)

    run_id = run_extraction(project, RunConfig(), FakeClient(), store)
    extractions = load_run_extractions(project.runs_dir / run_id)
    assert [e.study_id for e in extractions] == ["alpha", "beta", "gamma"]

    # success-gate invariant: nothing accepted without a verified anchor
    for e in extractions:
        for ev in e.accepted.values():
            assert ev.anchor_status == "verified"

    # scanned study: nothing accepted, everything flagged vision-only
    gamma = extractions[2]
    assert gamma.scanned and gamma.accepted == {}
    assert all(f.reason == "vision-only" for f in gamma.flagged.values())

    gold = load_gold(project.gold_csv, project.mapping_yaml, schema)
    scores = score_all(extractions, gold, schema, exact_judge)
    summary = aggregate(scores, schema)
    tax = {(s.study_id, s.field_id): s.taxonomy for s in scores}

    assert tax[("alpha", "n_total")] == "correct"
    assert tax[("alpha", "mean_age")] == "correct"
    assert tax[("alpha", "blinding")] == "correct"
    assert tax[("beta", "n_total")] == "gold-suspect"      # doc says 88, gold says 90
    assert tax[("beta", "mean_age")] == "spurious-anchored"
    assert tax[("gamma", "n_total")] == "missed"           # flagged -> missed
    assert summary.taxonomy_counts["missed"] >= 3          # gamma's gold fields

    store.record_bench(run_id, summary.per_field)
    html = render_html(summary, scores, run_id,
                       (run_id, {fid: 0.5 for fid in summary.per_field}))
    assert 'class="prov"' in html and "Δ" in html

    # scores.json shape used by `sr report`
    payload = {"summary": summary.to_dict(), "scores": [s.to_dict() for s in scores]}
    assert json.loads(json.dumps(payload))["summary"]["n_studies"] == 3
    store.close()
