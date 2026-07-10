import pytest
import yaml

from srx.schema import Schema, SchemaError, draft_schema_from_csv, load_schema

GOOD = """\
fields:
  - id: n_total
    type: int
    description: Total number randomised
  - id: mean_age_int
    type: numeric
    unit: years
    accepted_units: [years, months]
  - id: blinding
    type: categorical
    allowed_values: [open-label, single-blind, double-blind]
"""


def test_load_schema(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text(GOOD)
    s = load_schema(p)
    assert [f.id for f in s.fields] == ["n_total", "mean_age_int", "blinding"]
    assert s.by_id["mean_age_int"].unit == "years"
    assert s.by_id["blinding"].allowed_values == ["open-label", "single-blind", "double-blind"]
    assert s.by_id["n_total"].level == "study"
    assert len(s.hash()) == 64
    assert s.hash() == load_schema(p).hash()


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a, type: int}\n  - {id: a, type: text}\n")
    with pytest.raises(SchemaError, match="duplicate"):
        load_schema(p)


def test_unknown_type_rejected(tmp_path):
    p = tmp_path / "schema.yaml"
    p.write_text("fields:\n  - {id: a, type: floaty}\n")
    with pytest.raises(SchemaError, match="floaty"):
        load_schema(p)


def test_draft_from_csv(tmp_path):
    p = tmp_path / "gold.csv"
    p.write_text(
        "study_id,N randomised,Country\n"
        "smith-2019,124,UK\n"
        "jones-2020,88,Canada\n"
    )
    draft = yaml.safe_load(draft_schema_from_csv(p))
    ids = {f["id"]: f for f in draft["fields"]}
    assert "study_id" not in ids
    assert ids["n_randomised"]["type"] == "numeric"
    assert ids["country"]["type"] == "text"
