from conftest import make_pdf

from srx.claude_client import Usage
from srx.schema import schema_from_yaml_text
from srx.schema_propose import propose_schema_from_pdfs

PROPOSAL = {"fields": [
    {"id": "n_total", "type": "int", "description": "Total randomised"},
    {"id": "Mean Age (I)", "type": "numeric", "unit": "years",
     "description": "Mean age, intervention arm", "level": "study"},
    {"id": "n_total", "type": "int", "description": "duplicate - must be dropped"},
    {"id": "blinding", "type": "categorical", "description": "Blinding",
     "allowed_values": ["open-label", "double-blind"]},
]}


class FakeProposer:
    def __init__(self):
        self.calls = []

    def tool_call(self, *, model, system, messages, tool_name, tool_schema, **kw):
        self.calls.append((model, tool_name, messages))
        return PROPOSAL, Usage(5000, 400, 0.04)


def test_propose_schema_from_pdfs(tmp_path):
    studies = tmp_path / "studies"
    studies.mkdir()
    for name in ("alpha", "beta"):
        make_pdf(studies / f"{name}.pdf", ["Randomised trial text. " * 20])
    client = FakeProposer()
    text, usage = propose_schema_from_pdfs(client, "m", studies,
                                           review_hint="femur fractures")
    schema = schema_from_yaml_text(text)
    assert [f.id for f in schema.fields] == ["n_total", "mean_age_i", "blinding"]
    assert schema.by_id["mean_age_i"].unit == "years"
    assert schema.by_id["blinding"].allowed_values == ["open-label", "double-blind"]
    assert usage.cost_usd == 0.04

    model, tool_name, messages = client.calls[0]
    assert tool_name == "design_schema"
    docs = [b for b in messages[0]["content"] if b.get("type") == "document"]
    assert len(docs) == 2                              # both sample PDFs attached
    text_block = " ".join(b["text"] for b in messages[0]["content"]
                          if b.get("type") == "text")
    assert "alpha" in text_block and "femur fractures" in text_block
