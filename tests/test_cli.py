import json

from conftest import make_pdf

from srx.claude_client import Usage
from srx.cli import main

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


class FakeClient:
    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        if tool_name == "record_extraction":
            return {"n_total": {"value": 124, "unit": None,
                                "quote": "124 patients were randomised",
                                "page": 1, "confidence": 0.9}}, Usage(1000, 100, 0.5)
        if tool_name == "adjudicate":
            return {"choice": "a", "rationale": "r"}, Usage(100, 10, 0.05)
        if tool_name == "judge_equivalence":
            return {"equivalent": True, "rationale": "r"}, Usage(100, 10, 0.05)
        raise AssertionError(tool_name)


def make_project(tmp_path):
    root = tmp_path / "proj"
    (root / "studies").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "schema.yaml").write_text(SCHEMA_YAML)
    make_pdf(root / "studies" / "alpha.pdf", [PAGE])
    (root / "gold" / "gold.csv").write_text("study_id,N randomised\nalpha,124\n")
    (root / "gold" / "mapping.yaml").write_text(
        'study_id_column: study_id\ncolumns:\n  "N randomised": n_total\n')
    return root


def test_extract_then_bench_then_report(tmp_path, capsys):
    root = str(make_project(tmp_path))
    factory = lambda: FakeClient()
    assert main(["extract", "--project", root], client_factory=factory) == 0
    out = capsys.readouterr().out
    assert "run " in out.lower()
    assert main(["bench", "--project", root, "--no-judge"], client_factory=factory) == 0
    out = capsys.readouterr().out
    assert "100.0%" in out and "report.html" in out
    assert main(["report", "--project", root], client_factory=factory) == 0


def test_bench_without_gold_errors_cleanly(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "gold" / "gold.csv").unlink()
    assert main(["extract", "--project", str(root)], client_factory=lambda: FakeClient()) == 0
    capsys.readouterr()
    assert main(["bench", "--project", str(root), "--no-judge"],
                client_factory=lambda: FakeClient()) == 2
    assert "gold" in capsys.readouterr().err.lower()


def test_schema_init(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "schema.yaml").unlink()
    assert main(["schema", "init", "--project", str(root)]) == 0
    text = (root / "schema.yaml").read_text()
    assert "n_randomised" in text
    # refuses to overwrite
    assert main(["schema", "init", "--project", str(root)]) == 2


def test_missing_project_exits_2(tmp_path, capsys):
    assert main(["extract", "--project", str(tmp_path / "nope")],
                client_factory=lambda: FakeClient()) == 2
    assert "studies" in capsys.readouterr().err


def test_bench_warns_for_gold_study_without_extraction(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "gold" / "gold.csv").write_text("study_id,N randomised\nalpha,124\nghost,50\n")
    factory = lambda: FakeClient()
    assert main(["extract", "--project", str(root)], client_factory=factory) == 0
    assert main(["bench", "--project", str(root), "--no-judge"], client_factory=factory) == 0
    captured = capsys.readouterr()
    assert "ghost" in captured.err
    assert "50.0%" in captured.out
