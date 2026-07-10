from pathlib import Path

from srx.claude_client import Usage
from srx.ingest import PdfDoc
from srx.models import Candidate
from srx.reconcile import reconcile_study
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
])

PAGES = [
    "Methods section text here.",
    "A total of 124 patients were randomised. Mean age was 61.2 years overall.",
]
PDF = PdfDoc(study_id="smith-2019", path=Path("smith-2019.pdf"), pages=PAGES, scanned=False)


def cand(value, quote, page=2, unit=None, conf=0.9):
    return Candidate(value=value, unit=unit, quote=quote, page=page, confidence=conf)


class FakeAdjudicator:
    def __init__(self, choice):
        self.choice = choice
        self.calls = 0

    def tool_call(self, **kwargs):
        self.calls += 1
        return {"choice": self.choice, "rationale": "because"}, Usage(500, 50, 0.005)


def test_agreement_accepts_with_verified_anchor():
    a = {"n_total": cand(124, "124 patients were randomised", conf=0.95)}
    b = {"n_total": cand(124, "total of 124 patients", conf=0.90)}
    adj = FakeAdjudicator("a")
    accepted, flagged, not_found, usage = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=adj, model="m")
    ev = accepted["n_total"]
    assert ev.method == "pass-agreed"
    assert ev.anchor_status == "verified"
    assert ev.confidence == 0.90            # min of the two passes
    assert adj.calls == 0                   # no adjudication needed
    assert "mean_age" in not_found
    assert usage.cost_usd == 0.0


def test_agreement_with_fabricated_quotes_is_flagged_never_accepted():
    a = {"n_total": cand(124, "we enrolled one hundred and forty patients")}
    b = {"n_total": cand(124, "patients were recruited from twelve sites")}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=FakeAdjudicator("a"), model="m")
    assert "n_total" not in accepted
    assert flagged["n_total"].reason == "anchor-failed"
    assert len(flagged["n_total"].candidates) == 2


def test_disagreement_adjudicated_to_b():
    a = {"mean_age": cand(16.2, "mean age was 61.2 years", unit="years")}
    b = {"mean_age": cand(61.2, "Mean age was 61.2 years", unit="years")}
    adj = FakeAdjudicator("b")
    accepted, flagged, _, usage = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=adj, model="m")
    assert adj.calls == 1
    assert accepted["mean_age"].value == 61.2
    assert accepted["mean_age"].method == "adjudicated"
    assert usage.cost_usd > 0


def test_disagreement_neither_flags():
    a = {"mean_age": cand(59.0, "mean age was 61.2 years")}
    b = {"mean_age": cand(61.2, "Mean age was 61.2 years")}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b=b, client=FakeAdjudicator("neither"), model="m")
    assert "mean_age" not in accepted
    assert flagged["mean_age"].reason == "pass-disagreement-unresolved"


def test_one_sided_null_adjudicated_not_reported():
    a = {"mean_age": cand(61.2, "Mean age was 61.2 years")}
    accepted, flagged, not_found, _ = reconcile_study(
        schema=SCHEMA, pdf=PDF, pass_a=a, pass_b={}, client=FakeAdjudicator("not-reported"), model="m")
    assert "mean_age" in not_found
    assert "mean_age" not in accepted and "mean_age" not in flagged


def test_scanned_pdf_flags_everything_vision_only():
    scanned = PdfDoc(study_id="old", path=Path("old.pdf"), pages=["", ""], scanned=True)
    a = {"n_total": cand(124, "124 patients", page=1)}
    b = {"n_total": cand(124, "124 patients", page=1)}
    accepted, flagged, _, _ = reconcile_study(
        schema=SCHEMA, pdf=scanned, pass_a=a, pass_b=b, client=FakeAdjudicator("a"), model="m")
    assert accepted == {}
    assert flagged["n_total"].reason == "vision-only"
