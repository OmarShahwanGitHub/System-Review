from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction


def test_study_extraction_json_round_trip():
    ev = ExtractedValue(
        field_id="n_total", value=124, unit=None,
        quote="A total of 124 patients were randomised",
        page=3, method="pass-agreed", anchor_status="verified", confidence=0.95,
    )
    ff = FlaggedField(
        field_id="mean_age",
        reason="pass-disagreement-unresolved",
        candidates=[
            Candidate(value=61.2, unit="years", quote="mean age 61.2", page=4, confidence=0.8),
            Candidate(value=62.1, unit="years", quote="mean age 62.1", page=4, confidence=0.7),
        ],
    )
    se = StudyExtraction(
        study_id="smith-2019", scanned=False,
        accepted={"n_total": ev}, flagged={"mean_age": ff},
        not_found=["dropout_rate"], cost_usd=0.42,
    )
    back = StudyExtraction.from_json(se.to_json())
    assert back == se
    assert back.accepted["n_total"].quote.startswith("A total")
    assert back.flagged["mean_age"].candidates[1].value == 62.1


def test_candidate_dict_round_trip():
    c = Candidate(value="placebo", unit=None, quote="matched placebo", page=2, confidence=0.9)
    assert Candidate.from_dict(c.to_dict()) == c
