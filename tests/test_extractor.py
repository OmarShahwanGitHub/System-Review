from srx.claude_client import Usage
from srx.extractor import FRAMINGS, build_tool_schema, extract_study, run_pass
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int", description="Total randomised"),
    FieldSpec(id="blinding", type="categorical",
              allowed_values=["open-label", "double-blind"]),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
])

ENTRY = {"unit": None, "quote": "124 patients were randomised", "page": 2, "confidence": 0.95}


class FakeToolClient:
    """Duck-types ClaudeClient.tool_call. Returns canned tool input, records prompts."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        text = " ".join(
            b["text"] for b in messages[0]["content"] if b.get("type") == "text"
        )
        self.prompts.append(text)
        return self.results.pop(0), Usage(1000, 100, 0.01)


def test_build_tool_schema_shapes():
    ts = build_tool_schema(SCHEMA)
    assert set(ts["required"]) == {"n_total", "blinding", "mean_age"}
    n = ts["properties"]["n_total"]
    assert n["properties"]["value"]["type"] == ["integer", "null"]
    assert set(n["required"]) == {"value", "quote", "page", "confidence"}
    assert "open-label" in ts["properties"]["blinding"]["description"]


def test_run_pass_parses_candidates_and_skips_nulls():
    result = {
        "n_total": {"value": 124, **ENTRY},
        "blinding": {"value": None, "unit": None, "quote": "", "page": 1, "confidence": 0.0},
        "mean_age": {"value": 61.2, "unit": "years", "quote": "mean age 61.2 years",
                     "page": 4, "confidence": 0.9},
    }
    client = FakeToolClient([result])
    cands, usage = run_pass(client, "m", b"%PDF", "smith-2019", SCHEMA, FRAMINGS[0])
    assert cands["n_total"].value == 124
    assert cands["n_total"].page == 2
    assert "blinding" not in cands
    assert cands["mean_age"].unit == "years"
    assert usage.input_tokens == 1000
    assert "Study file: smith-2019" in client.prompts[0]


def test_extract_study_runs_two_passes_with_different_framings():
    result = {"n_total": {"value": 124, **ENTRY}}
    client = FakeToolClient([result, result])
    a, b, usage = extract_study(client, "m", b"%PDF", "smith-2019", SCHEMA)
    assert a["n_total"].value == b["n_total"].value == 124
    assert usage.input_tokens == 2000
    assert client.prompts[0] != client.prompts[1]  # framings differ
    # pass-b lists fields in reversed order
    assert client.prompts[1].index("mean_age") < client.prompts[1].index("n_total")
