import json

from conftest import make_pdf

from srx.claude_client import ClaudeError, Usage
from srx.pipeline import RunConfig, load_run_extractions, run_extraction
from srx.project import open_project
from srx.runstore import RunStore

SCHEMA_YAML = """\
fields:
  - id: n_total
    type: int
    description: Total randomised
"""

PAGE = (
    "Randomised controlled trial of drug X versus placebo, single centre. "
    "A total of 124 patients were randomised to intervention or placebo. "
    "Follow-up continued for twelve months with no major protocol deviations."
)


def entry(value, quote):
    return {"value": value, "unit": None, "quote": quote, "page": 1, "confidence": 0.9}


class FakeClient:
    """Routes tool calls by tool_name + 'Study file:' marker in the prompt text."""

    def __init__(self, fail_studies=()):
        self.fail_studies = set(fail_studies)
        self.extract_calls = []

    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        content = messages[0]["content"]
        text = content if isinstance(content, str) else " ".join(
            b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text")
        study = text.split("Study file: ")[1].split()[0]
        if tool_name == "record_extraction":
            if study in self.fail_studies:
                raise ClaudeError("boom")
            self.extract_calls.append(study)
            return {"n_total": entry(124, "124 patients were randomised")}, Usage(1000, 100, 1.0)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.1)
        raise AssertionError(f"unexpected tool {tool_name}")


def make_project(tmp_path, n_studies=2):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    names = ["alpha", "beta", "gamma"][:n_studies]
    for name in names:
        make_pdf(root / "studies" / f"{name}.pdf", [PAGE])
    return root


def test_full_run_writes_checkpoints_and_extraction(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    run_id = run_extraction(project, RunConfig(), FakeClient(), store)
    run_dir = project.runs_dir / run_id
    assert (run_dir / "studies" / "alpha.json").exists()
    extractions = load_run_extractions(run_dir)
    assert [e.study_id for e in extractions] == ["alpha", "beta"]
    assert extractions[0].accepted["n_total"].value == 124
    assert extractions[0].accepted["n_total"].anchor_status == "verified"
    row = store.run_row(run_id)
    assert row["status"] == "done"
    assert row["cost_usd"] > 0
    assert store.study_status(run_id, "beta") == "done"


def test_cost_cap_stops_cleanly_and_resume_completes(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    client = FakeClient()
    # each study costs ~2.0 (two passes at 1.0); cap below that stops after study 1
    run_id = run_extraction(project, RunConfig(cost_cap_usd=1.5), client, store)
    assert store.run_row(run_id)["status"] == "cost-capped"
    assert client.extract_calls.count("alpha") == 2      # two passes
    assert "beta" not in client.extract_calls
    # resume with a higher cap: alpha's checkpoint is reused, only beta runs
    run_id2 = run_extraction(
        project, RunConfig(cost_cap_usd=100.0, resume=run_id), client, store)
    assert run_id2 == run_id
    assert client.extract_calls.count("alpha") == 2      # NOT re-extracted
    assert client.extract_calls.count("beta") == 2
    assert store.run_row(run_id)["status"] == "done"
    assert [e.study_id for e in load_run_extractions(project.runs_dir / run_id)] == ["alpha", "beta"]


def test_errored_study_is_recorded_and_run_continues(tmp_path):
    project = open_project(make_project(tmp_path))
    store = RunStore(project.db_path)
    run_id = run_extraction(project, RunConfig(), FakeClient(fail_studies={"alpha"}), store)
    assert store.study_status(run_id, "alpha") == "errored"
    assert store.study_status(run_id, "beta") == "done"
    assert [e.study_id for e in load_run_extractions(project.runs_dir / run_id)] == ["beta"]
    assert store.run_row(run_id)["status"] == "done"
