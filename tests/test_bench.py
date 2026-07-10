import pytest

from srx.bench import BenchSummary, aggregate, exact_judge, score_all
from srx.goldload import GoldValue
from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="dose", type="numeric", unit="g"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
    FieldSpec(id="funding", type="text"),
])


def ev(fid, value, unit=None, quote="q", method="pass-agreed"):
    return ExtractedValue(field_id=fid, value=value, unit=unit, quote=quote,
                          page=1, method=method, anchor_status="verified", confidence=0.9)


def gv(raw, value=None, unit=None, precision=None):
    return GoldValue(raw=raw, value=value if value is not None else raw,
                     unit=unit, precision=precision)


def flag(fid, reason):
    return FlaggedField(field_id=fid, reason=reason,
                        candidates=[Candidate(value=1, unit=None, quote="q", page=1, confidence=0.5)])


def study(accepted=None, flagged=None, not_found=None):
    return StudyExtraction(study_id="s1", scanned=False, accepted=accepted or {},
                           flagged=flagged or {}, not_found=not_found or [], cost_usd=0.0)


def by_field(scores):
    return {s.field_id: s for s in scores}


def test_taxonomy_classification():
    extraction = study(
        accepted={
            "n_total": ev("n_total", 124),                                  # correct
            "dose": ev("dose", 1500, unit="mg"),                            # unit-mismatch vs 1.5 g
            "mean_age": ev("mean_age", 61.2, unit="years",
                           quote="mean age was 61.2 years"),                 # gold says 16.2 -> gold-suspect
            "funding": ev("funding", "industry sponsored"),                 # gold absent -> spurious-anchored
        },
    )
    gold = {"s1": {
        "n_total": gv("124", 124.0, precision=0),
        "dose": gv("1.5", 1.5, unit="g", precision=1),
        "mean_age": gv("16.2", 16.2, unit="years", precision=1),
    }}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "correct"
    assert s["dose"].taxonomy == "unit-mismatch"
    assert s["mean_age"].taxonomy == "gold-suspect"      # 61.2 appears in the quote
    assert s["funding"].taxonomy == "spurious-anchored"
    assert all(x.in_denominator for x in s.values())


def test_flagged_scores_as_missed_and_hallucinated_is_informational():
    extraction = study(flagged={
        "n_total": flag("n_total", "pass-disagreement-unresolved"),  # gold present -> missed
        "dose": flag("dose", "anchor-failed"),                       # gold absent -> spurious-hallucinated
    })
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0)}}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "missed" and s["n_total"].flagged
    assert s["dose"].taxonomy == "spurious-hallucinated"
    assert not s["dose"].in_denominator
    assert "mean_age" not in s                            # both null -> no row


def test_wrong_value_when_quote_lacks_engine_digits():
    extraction = study(accepted={"n_total": ev("n_total", 128, quote="patients were randomised")})
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0)}}
    s = by_field(score_all([extraction], gold, SCHEMA, exact_judge))
    assert s["n_total"].taxonomy == "wrong-value"


def test_text_field_uses_judge():
    extraction = study(accepted={"funding": ev("funding", "funded by Pfizer Inc")})
    gold = {"s1": {"funding": gv("Pfizer")}}
    always_yes = lambda spec, e, g: True
    s = by_field(score_all([extraction], gold, SCHEMA, always_yes))
    assert s["funding"].taxonomy == "correct"


def test_aggregate_and_round_trip():
    extraction = study(accepted={"n_total": ev("n_total", 124)},
                       flagged={"mean_age": flag("mean_age", "vision-only")})
    gold = {"s1": {"n_total": gv("124", 124.0, precision=0),
                   "mean_age": gv("61.2", 61.2, precision=1)}}
    scores = score_all([extraction], gold, SCHEMA, exact_judge)
    summary = aggregate(scores, SCHEMA)
    assert summary.overall_agreement == pytest.approx(0.5)   # correct=1 of denominator=2
    assert summary.numeric_agreement == pytest.approx(0.5)
    assert summary.per_field["n_total"]["agreement"] == 1.0
    assert summary.taxonomy_counts["missed"] == 1
    assert BenchSummary.from_dict(summary.to_dict()) == summary
