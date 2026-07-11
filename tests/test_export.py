import csv

from srx.export import write_evidence_csv
from srx.models import Candidate, ExtractedValue, FlaggedField, StudyExtraction
from srx.schema import FieldSpec, Schema

SCHEMA = Schema(fields=[
    FieldSpec(id="n_total", type="int"),
    FieldSpec(id="mean_age", type="numeric", unit="years"),
    FieldSpec(id="funding", type="text"),
])


def _extraction():
    return StudyExtraction(
        study_id="smith-2019", scanned=False,
        accepted={"n_total": ExtractedValue(
            field_id="n_total", value=124, unit=None,
            quote="124 patients, were randomised", page=3,
            method="pass-agreed", anchor_status="verified", confidence=0.9)},
        flagged={"mean_age": FlaggedField(
            field_id="mean_age", reason="pass-disagreement-unresolved",
            candidates=[
                Candidate(value=61.2, unit="years", quote="mean age 61.2", page=4, confidence=0.8),
                Candidate(value=16.2, unit="years", quote="mean age 16.2", page=4, confidence=0.6),
            ])},
        not_found=["funding"], cost_usd=0.1)


def test_evidence_csv_clean_values_and_flag_markers(tmp_path):
    evidence, _ = write_evidence_csv([_extraction()], SCHEMA, tmp_path)
    rows = evidence.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "study_id,n_total,mean_age,funding"
    assert rows[1] == "smith-2019,124,[FLAGGED: pass-disagreement-unresolved],"
    # quotes live only in the annex — never inline in the clean sheet (spec §4)
    assert "randomised" not in evidence.read_text(encoding="utf-8")


def test_provenance_annex_rows(tmp_path):
    _, annex = write_evidence_csv([_extraction()], SCHEMA, tmp_path)
    with open(annex, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    keys = {(r["field_id"], r["status"], r["value"]) for r in rows}
    assert ("n_total", "accepted", "124") in keys
    assert ("mean_age", "flagged", "61.2") in keys
    assert ("mean_age", "flagged", "16.2") in keys     # both candidates preserved
    assert ("funding", "not-reported", "") in keys
    accepted = next(r for r in rows if r["status"] == "accepted")
    assert accepted["quote"] == "124 patients, were randomised"
    assert accepted["anchor_status"] == "verified"
    flagged = next(r for r in rows if r["status"] == "flagged")
    assert flagged["flag_reason"] == "pass-disagreement-unresolved"
